"""Вспомогательные функции: ffprobe и запуск ffmpeg."""
import json
import subprocess
from pathlib import Path


def probe(path: Path) -> dict:
    """Возвращает ffprobe-описание файла. Бросает ValueError, если файл не медиа."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise ValueError(f"ffprobe не смог прочитать файл: {res.stderr.strip()[:200]}")
    return json.loads(res.stdout)


def audio_duration(path: Path) -> float:
    info = probe(path)
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ValueError("в файле нет аудиодорожки")
    return float(info["format"]["duration"])


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def video_duration(path: Path) -> float:
    """Длина именно видеодорожки. Длина контейнера бывает больше (звук длиннее картинки), и тогда клип
    планировался длиннее, чем есть кадров: картинка замирала или зацикливалась, а звук шёл дальше (рассинхрон)."""
    info = probe(path)
    fmt = _num(info["format"].get("duration"))
    for s in info.get("streams", []):
        if s.get("codec_type") != "video" or (s.get("disposition") or {}).get("attached_pic"):
            continue
        d = _num(s.get("duration"))
        if d <= 0:
            tags = s.get("tags") or {}
            raw = tags.get("DURATION") or tags.get("duration") or ""
            try:
                h, m, sec = raw.split(":")
                d = int(h) * 3600 + int(m) * 60 + float(sec)
            except ValueError:
                d = 0.0
        if d <= 0:
            fr, rate = _num(s.get("nb_frames")), s.get("avg_frame_rate") or "0/1"
            try:
                a, b = rate.split("/")
                fps = float(a) / float(b)
            except (ValueError, ZeroDivisionError):
                fps = 0.0
            if fr > 0 and fps > 0:
                d = fr / fps
        if d > 0:
            return min(d, fmt) if fmt > 0 else d
    return fmt


_VIDEO_CACHE: dict[str, bool] = {}
_VIDEO_CACHE_LOADED = False


def _video_cache_file() -> Path:
    from common import config
    return config.ROOT / "cache" / "has_video.json"


def has_video(path: Path) -> bool:
    """Есть ли в файле настоящая видеодорожка (не обложка). Результат кэшируется на диске по пути, размеру и дате файла."""
    global _VIDEO_CACHE_LOADED
    try:
        st = path.stat()
    except OSError:
        return False
    key = f"{path}|{st.st_size}|{st.st_mtime_ns}"
    if not _VIDEO_CACHE_LOADED:
        try:
            _VIDEO_CACHE.update(json.loads(_video_cache_file().read_text("utf-8")))
        except (OSError, ValueError):
            pass
        _VIDEO_CACHE_LOADED = True
    if key in _VIDEO_CACHE:
        return _VIDEO_CACHE[key]
    try:
        info = probe(path)
        ok = any(s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
                 for s in info.get("streams", []))
    except ValueError:
        ok = False
    _VIDEO_CACHE[key] = ok
    try:
        f = _video_cache_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(f".{id(_VIDEO_CACHE)}.tmp")
        tmp.write_text(json.dumps(_VIDEO_CACHE), "utf-8")
        tmp.replace(f)
    except OSError:
        pass
    return ok


def video_only(files: list[Path]) -> list[Path]:
    """Только файлы с видеодорожкой: аудио без картинки и битые файлы ломали сборку («Stream specifier ':v' ... matches no streams»)."""
    import logging
    ok = [f for f in files if has_video(f)]
    bad = [f.name for f in files if f not in set(ok)]
    if bad:
        logging.getLogger("media").warning("пропущены файлы без видеодорожки (%d): %s", len(bad), ", ".join(bad[:10]))
    return ok


def run(cmd: list[str], cwd: Path | None = None) -> None:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} завершился с ошибкой:\n{res.stderr[-1500:]}")
