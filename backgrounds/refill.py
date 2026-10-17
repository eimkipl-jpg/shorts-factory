"""Автопополнение банка фонов из ОТКРЫТЫХ источников (Pexels, Pixabay) по их официальным API.
Запускается по cron. Скачивает ролики, удаляет звук, нарезает на фрагменты по BG_SEGMENT_SECONDS,
складывает в backgrounds/<тема>/. Следит за общим размером банка.

Лицензии: Pexels и Pixabay разрешают бесплатное использование, в том числе коммерческое, без обязательной
атрибуции, но условия могут меняться - проверьте актуальные Terms на сайтах перед публикацией.
"""
import logging
import shutil
import uuid
from pathlib import Path

import requests

from common import config
from common.media import run, video_duration

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("refill")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}
TIMEOUT = 60


def search_pexels(query: str, page: int) -> list[str]:
    if not config.PEXELS_API_KEY:
        return []
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": config.PEXELS_API_KEY},
        params={"query": query, "orientation": "portrait", "per_page": 15, "page": page},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    urls = []
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width", 0) >= 720]
        if files:
            urls.append(min(files, key=lambda f: f["width"])["link"])  # достаточно ~720p, экономим место
    return urls


def search_pixabay(query: str, page: int) -> list[str]:
    if not config.PIXABAY_API_KEY:
        return []
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": config.PIXABAY_API_KEY, "q": query, "per_page": 15, "page": page},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    urls = []
    for hit in r.json().get("hits", []):
        v = hit.get("videos", {})
        # Ролики Pixabay альбомные и режутся под 9:16 - берём максимальное разрешение, чтобы кадр не мылился
        for size in ("large", "medium", "small"):
            if v.get(size, {}).get("url"):
                urls.append(v[size]["url"])
                break
    return urls


def download(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            shutil.copyfileobj(r.raw, f)


def process_clip(src: Path, theme_dir: Path) -> int:
    """Убирает звук и режет на сегменты. Возвращает число созданных фрагментов."""
    dur = video_duration(src)
    seg = config.BG_SEGMENT_SECONDS
    stem = uuid.uuid4().hex[:8]
    if dur <= seg * 1.5:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-an", "-c:v", "copy", str(theme_dir / f"{stem}_000.mp4")])
        return 1
    run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-an", "-c:v", "copy",
        "-f", "segment", "-segment_time", str(seg), "-reset_timestamps", "1",
        str(theme_dir / f"{stem}_%03d.mp4"),
    ])
    return len(list(theme_dir.glob(f"{stem}_*.mp4")))


def bank_size_gb() -> float:
    total = sum(f.stat().st_size for f in config.BACKGROUNDS_DIR.rglob("*") if f.is_file())
    return total / 1024**3


def trim_bank() -> None:
    """Если банк раздулся, удаляем самые старые фоны."""
    files = sorted(
        (f for f in config.BACKGROUNDS_DIR.rglob("*") if f.suffix.lower() in VIDEO_EXT),
        key=lambda f: f.stat().st_mtime,
    )
    while files and bank_size_gb() > config.BG_MAX_GB:
        files.pop(0).unlink(missing_ok=True)


def refill_theme(tag: str, query: str) -> None:
    theme_dir = config.BACKGROUNDS_DIR / tag
    theme_dir.mkdir(parents=True, exist_ok=True)
    have = len([f for f in theme_dir.iterdir() if f.suffix.lower() in VIDEO_EXT])
    if have >= config.BG_PER_THEME_TARGET:
        log.info("%s: фонов достаточно (%s)", tag, have)
        return
    tmp = config.TMP_DIR / f"refill_{tag}"
    tmp.mkdir(parents=True, exist_ok=True)
    page = 1
    try:
        while have < config.BG_PER_THEME_TARGET and page <= 3:
            urls = search_pexels(query, page) + search_pixabay(query, page)
            if not urls:
                break
            for url in urls:
                if have >= config.BG_PER_THEME_TARGET or bank_size_gb() > config.BG_MAX_GB:
                    break
                src = tmp / f"{uuid.uuid4().hex}.mp4"
                try:
                    download(url, src)
                    have += process_clip(src, theme_dir)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s: пропуск %s (%s)", tag, url, e)
                finally:
                    src.unlink(missing_ok=True)
            page += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    log.info("%s: теперь фонов %s", tag, have)


def main() -> None:
    config.ensure_dirs()
    if not (config.PEXELS_API_KEY or config.PIXABAY_API_KEY):
        raise SystemExit("Укажите PEXELS_API_KEY и/или PIXABAY_API_KEY в .env (бесплатные ключи).")
    for tag, query in config.bg_themes().items():
        refill_theme(tag, query)
    trim_bank()
    log.info("Размер банка: %.2f ГБ", bank_size_gb())


if __name__ == "__main__":
    main()
