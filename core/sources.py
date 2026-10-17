"""Поиск и загрузка материалов: Pexels и Pixabay (официальные API) и скачивание по ссылке (yt-dlp).

Файл попадает в <ROOT>/clips/<тема>/ и сразу копируется на Диск: папка клипов зеркалится с Диска
(rclone sync), и всё, чего нет на Диске, при следующей синхронизации было бы удалено.

Лицензии: Pexels License и Pixabay Content License разрешают бесплатное использование, но условия меняются -
перед публикацией проверьте актуальные Terms. Ссылка на страницу ролика сохраняется в библиотеке как источник.
"""
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

from common import config, library

log = logging.getLogger("sources")

TIMEOUT = 30
MAX_MB = 150
LICENSES = {"pexels": "Pexels License", "pixabay": "Pixabay Content License"}


class NoKeys(RuntimeError):
    """Не задан ни один ключ API."""


def slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.strip().lower(), flags=re.UNICODE).strip("-_")
    return s[:30] or "search"


# ---------- поиск ----------

def _slug_words(url: str) -> str:
    """Описание ролика из ссылки Pexels: .../video/brown-dog-standing-3191572/ -> 'brown dog standing'."""
    m = re.search(r"/video/([a-z][^/]*?)-?\d*/?$", url or "")
    return re.sub(r"[-_]+", " ", m.group(1)).strip() if m else ""


PEXELS_LOCALE = {"ru": "ru-RU", "en": "en-US", "de": "de-DE", "es": "es-ES", "fr": "fr-FR"}


def _pexels_videos(query: str, page: int, per_page: int, extra: dict, orientation: str | None) -> list[dict]:
    params = {"query": query, "per_page": per_page, "page": page, **extra}
    if orientation:
        params["orientation"] = orientation
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": config.PEXELS_API_KEY},
                     params=params, timeout=TIMEOUT)
    r.raise_for_status()
    items = []
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
        if not files:
            continue
        if orientation == "portrait":
            good = [f for f in files if f["width"] >= 720] or files
            pick = min(good, key=lambda f: abs(f["width"] - 1080))  # ближе всего к Full HD по ширине
        else:      # горизонтальное: в вертикальный кадр уходит центр, поэтому берём повыше разрешение
            good = [f for f in files if (f.get("height") or 0) >= 1080] or files
            pick = min(good, key=lambda f: abs((f.get("height") or 0) - 1440))
        items.append({
            "provider": "pexels", "id": str(v["id"]), "url": pick["link"], "thumb": v.get("image"),
            "page": v.get("url", ""), "author": (v.get("user") or {}).get("name", ""),
            "duration": v.get("duration"), "w": pick["width"], "h": pick.get("height"),
            "tags": _slug_words(v.get("url", "")),
        })
    return items


def search_pexels(query: str, page: int = 1, per_page: int = 6, lang: str | None = None) -> list[dict]:
    """Сначала вертикальные ролики; если их мало - добавляем горизонтальные (их центр хорошо ложится в 9:16)."""
    extra = {"locale": PEXELS_LOCALE[lang]} if lang in PEXELS_LOCALE else {}
    items = _pexels_videos(query, page, per_page, extra, "portrait")
    if len(items) < 4:
        seen = {it["id"] for it in items}
        try:
            items += [it for it in _pexels_videos(query, page, per_page, extra, None) if it["id"] not in seen]
        except requests.RequestException:
            pass
    return items


def search_pixabay(query: str, page: int = 1, per_page: int = 6, lang: str | None = None) -> list[dict]:
    extra = {"lang": lang} if lang in ("ru", "en", "de", "es", "fr") else {}
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": config.PIXABAY_API_KEY, "q": query, "per_page": max(per_page, 3), "page": page,
                "safesearch": "true", "video_type": "film", **extra},   # только живая съёмка: без мультфильмов и 3D-анимации
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    items = []
    for hit in r.json().get("hits", []):
        vids = hit.get("videos", {})
        pick = next((vids[k] for k in ("large", "medium", "small") if vids.get(k, {}).get("url")), None)
        if not pick:
            continue
        thumb = pick.get("thumbnail") or (
            f"https://i.vimeocdn.com/video/{hit['picture_id']}_640x360.jpg" if hit.get("picture_id") else None)
        items.append({
            "provider": "pixabay", "id": str(hit["id"]), "url": pick["url"], "thumb": thumb,
            "page": hit.get("pageURL", ""), "author": hit.get("user", ""),
            "duration": hit.get("duration"), "w": pick.get("width"), "h": pick.get("height"),
            "tags": str(hit.get("tags") or ""),
        })
    return items


def search(query: str, page: int = 1, per_page: int = 6, lang: str | None = None) -> tuple[list[dict], list[str]]:
    """Результаты обоих источников вперемешку и список ошибок по источникам."""
    providers = []
    if config.PEXELS_API_KEY:
        providers.append(("Pexels", search_pexels))
    if config.PIXABAY_API_KEY:
        providers.append(("Pixabay", search_pixabay))
    if not providers:
        raise NoKeys("не заданы PEXELS_API_KEY и PIXABAY_API_KEY в .env (бесплатные ключи: pexels.com/api и pixabay.com/api/docs)")
    lists, errors = [], []
    for name, fn in providers:
        try:
            lists.append(fn(query, page, per_page, lang) if lang else fn(query, page, per_page))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[:120]}")
    merged = []
    for i in range(max((len(x) for x in lists), default=0)):
        for lst in lists:
            if i < len(lst):
                merged.append(lst[i])
    return merged[:per_page], errors


# ---------- загрузка ----------

def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=TIMEOUT) as r:
            r.raise_for_status()
            size = int(r.headers.get("content-length") or 0)
            if size > MAX_MB * 1024 * 1024:
                raise RuntimeError(f"файл {size // 1024 // 1024} МБ, лимит {MAX_MB} МБ")
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    done += len(chunk)
                    if done > MAX_MB * 1024 * 1024:
                        raise RuntimeError(f"файл больше {MAX_MB} МБ")
                    f.write(chunk)
        tmp.rename(dest)
    finally:
        tmp.unlink(missing_ok=True)


def push_to_drive(local: Path, remote_dir: str) -> tuple[bool, str]:
    """Копирует файл на Диск (copy, ничего не удаляет). Возвращает (успех, сообщение)."""
    if not config.DRIVE_ENABLED:
        return False, "Диск отключён"
    try:
        res = subprocess.run(
            ["rclone", "copy", str(local), f"{config.RCLONE_REMOTE}:{remote_dir}", "--retries", "3"],
            capture_output=True, text=True, timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)[:200]
    return res.returncode == 0, res.stderr[-200:]


def register(path: Path, source: str, license_: str, tags: list[str] | None = None) -> dict | None:
    library.scan()
    asset = library.by_path(path)
    if asset:
        library.set_info(asset["id"], source=source, license_=license_)
        if tags:
            library.add_tags(asset["id"], tags)
        asset = library.get(asset["id"])
    return asset


def add_item(item: dict, theme: str) -> dict:
    """Скачивает найденный ролик в тему, кладёт на Диск, записывает источник и лицензию в библиотеку."""
    dest_dir = config.CLIPS_DIR / theme
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{item['provider']}_{item['id']}.mp4"
    existed = dest.exists()
    if not existed:
        _download(item["url"], dest)
    asset = register(dest, item.get("page") or item["url"], LICENSES.get(item["provider"], ""))
    ok, msg = push_to_drive(dest, f"{config.CLIPS_REMOTE_PATH}/{theme}")
    return {"path": dest, "asset": asset, "existed": existed, "drive_ok": ok, "drive_msg": msg}


# ---------- по ссылке ----------

def _ytdlp_cmd() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        raise RuntimeError("yt-dlp не установлен на сервере (pip install yt-dlp)") from None


YT_ID = re.compile(r"^[\w-]{11}$")


def _oembed_title(video_id: str) -> str | None:
    try:
        r = requests.get("https://www.youtube.com/oembed",
                         params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}, timeout=15)
        if r.status_code == 200:
            return (r.json().get("title") or "").strip() or None
    except (requests.RequestException, ValueError):
        pass
    return None


def _ytdlp_title(video_id: str) -> str | None:
    try:
        res = subprocess.run([*_ytdlp_cmd(), "--skip-download", "--no-warnings", "--no-playlist", "--print", "%(title)s",
                              f"https://www.youtube.com/watch?v={video_id}"], capture_output=True, text=True, timeout=90)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    return lines[-1] if res.returncode == 0 and lines else None


def fill_titles(progress=None) -> dict:
    """Для «моих видео» с именем-ID YouTube (0NhbnKSkGqM.mp4) подтягивает настоящее название и сохраняет в библиотеке.
    Файлы не переименовываются (они зеркалятся с Диска). progress(done, total) вызывается по ходу."""
    library.scan()
    todo = []
    for a in library.search("own", limit=100000):
        if a["title"]:
            continue
        stem = Path(a["path"]).stem
        m = re.search(r"\[([\w-]{11})\]$", stem)  # «Название [id]» - так называет add_url
        if m:
            library.set_title(a["id"], stem[:m.start()].strip() or stem)
        elif YT_ID.match(stem):
            todo.append((a, stem))
    ok, failed = 0, []
    for i, (a, vid) in enumerate(todo, 1):
        title = _oembed_title(vid) or _ytdlp_title(vid)
        if title:
            library.set_title(a["id"], title)
            ok += 1
        else:
            failed.append(Path(a["path"]).name)
        if progress and (i % 10 == 0 or i == len(todo)):
            progress(i, len(todo))
    return {"found": ok, "failed": failed, "total": len(todo)}


def add_url(url: str, dest_dir: Path | None = None, remote_dir: str | None = None) -> dict:
    """Скачивает видео по ссылке в _own (ваши ролики) со звуком и копирует на Диск.
    dest_dir/remote_dir - другая папка (например, проект из банка идей): тогда файл в библиотеку не регистрируется."""
    if not re.match(r"^https?://\S+$", url.strip()):
        raise RuntimeError("нужна ссылка вида https://...")
    custom = dest_dir is not None
    dest_dir = dest_dir or config.CLIPS_DIR / "_own"
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [*_ytdlp_cmd(), "--no-playlist", "--no-simulate", "--windows-filenames",
           "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
           "-o", str(dest_dir / "%(title).60s [%(id)s].%(ext)s"),
           "--print", "%(title)s", "--print", "after_move:filepath", url.strip()]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if res.returncode != 0:
        raise RuntimeError("yt-dlp: " + (res.stderr.strip().splitlines() or ["ошибка"])[-1][:300])
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    path = Path(lines[-1]) if lines else None
    if not path or not path.exists():
        raise RuntimeError("yt-dlp отработал, но файл не найден")
    asset = None if custom else register(path, url.strip(), "мои материалы")
    if asset and len(lines) >= 2:
        library.set_title(asset["id"], lines[-2])
    ok, msg = push_to_drive(path, remote_dir or f"{config.CLIPS_REMOTE_PATH}/_own")
    return {"path": path, "asset": asset, "drive_ok": ok, "drive_msg": msg}
