"""Скачивание видео и метрики по ссылке через yt-dlp (YouTube, TikTok, Instagram, VK и другие площадки из списка yt-dlp).

Безопасность: принимаются только http(s)-ссылки на публичные адреса; «универсальный» извлекатель yt-dlp выключен
(--ies default,-generic), чтобы по ссылке нельзя было заставить сервер ходить во внутреннюю сеть или по любым URL.
yt-dlp запускается тем же Python, что и бот (python -m yt_dlp)."""
import ipaddress
import json
import logging
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("fetch")

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
BASE = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--ies", "default,-generic", "--no-warnings",
        "--socket-timeout", "20", "--retries", "3"]


class FetchError(RuntimeError):
    """Понятная человеку причина, почему не скачалось."""


def available() -> bool:
    try:
        return subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_url(text: str) -> str:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(").,;!?»") if m else ""


def check_url(url: str) -> str:
    """Возвращает пустую строку, если ссылку можно брать, иначе причину отказа."""
    try:
        u = urlparse(url)
    except ValueError:
        return "ссылка не распознана"
    if u.scheme not in ("http", "https") or not u.hostname:
        return "нужна ссылка вида https://…"
    host = u.hostname.lower()
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return "такие адреса не поддерживаются"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return "сайт не найден, проверьте ссылку"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return "такие адреса не поддерживаются"
    return ""


def _reason(stderr: str) -> str:
    """Ошибка yt-dlp -> короткое объяснение по-русски."""
    t = (stderr or "").lower()
    pairs = [
        ("unsupported url", "эта площадка или тип ссылки не поддерживается"),
        ("private video", "видео закрыто (приватное)"),
        ("is private", "видео закрыто (приватное)"),
        ("sign in to confirm", "площадка требует вход - с сервера скачать не дала (часто у YouTube); попробуйте позже или другую ссылку"),
        ("login required", "нужен вход в аккаунт - скачать нельзя"),
        ("confirm your age", "видео с ограничением по возрасту - скачать нельзя"),
        ("age-restricted", "видео с ограничением по возрасту - скачать нельзя"),
        ("not available", "видео недоступно (удалено или закрыто в регионе)"),
        ("unavailable", "видео недоступно (удалено или закрыто в регионе)"),
        ("copyright", "видео заблокировано правообладателем"),
        ("http error 429", "площадка временно ограничила запросы - попробуйте позже"),
        ("http error 403", "площадка отказала в доступе - попробуйте позже"),
        ("max-filesize", "файл слишком большой"),
        ("does not pass filter", "видео слишком длинное"),
        ("live event", "прямые эфиры не скачиваются"),
        ("is_live", "прямые эфиры не скачиваются"),
    ]
    for k, v in pairs:
        if k in t:
            return v
    last = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
    return (last[-1][-200:] if last else "неизвестная ошибка").replace("ERROR: ", "")


def info(url: str, timeout: int = 90) -> dict:
    """Метаданные без скачивания: название, автор, длительность, просмотры, лайки, комментарии, дата."""
    res = subprocess.run(BASE + ["-J", "--skip-download", url], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise FetchError(_reason(res.stderr))
    try:
        d = json.loads(res.stdout)
    except ValueError as e:
        raise FetchError("площадка вернула непонятный ответ") from e
    if d.get("_type") == "playlist":
        raise FetchError("это плейлист или канал - пришлите ссылку на один ролик")
    keys = ("title", "uploader", "channel", "duration", "view_count", "like_count", "comment_count", "repost_count",
            "upload_date", "channel_follower_count", "extractor_key", "webpage_url", "width", "height", "is_live")
    return {k: d.get(k) for k in keys}


def download(url: str, out_dir: Path, max_minutes: int, height: int = 1080, timeout: int = 1500) -> Path:
    """Скачивает один ролик в out_dir/video.mp4 (mp4, до height пикселей по короткой стороне). Длиннее max_minutes - отказ."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("video.*"):
        f.unlink(missing_ok=True)
    fmt = (f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}][ext=mp4]/"
           f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b")
    cmd = BASE + ["-f", fmt, "--merge-output-format", "mp4", "--max-filesize", "1500M",
                  "--match-filters", f"duration<=?{max_minutes * 60} & !is_live",
                  "-o", str(out_dir / "video.%(ext)s"), url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FetchError("скачивание заняло слишком много времени") from e
    files = [f for f in out_dir.glob("video.*") if f.suffix not in (".part", ".ytdl") and f.stat().st_size > 0]
    if res.returncode != 0 or not files:
        why = _reason(res.stderr)
        if not files and "does not pass filter" in (res.stdout + res.stderr).lower():
            why = f"видео длиннее {max_minutes} мин"
        raise FetchError(why)
    video = files[0]
    if video.suffix != ".mp4":       # редкий случай: webm/mkv - приводим к mp4 для Telegram
        mp4 = out_dir / "video.mp4"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "22", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(mp4)],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise FetchError("не удалось перевести видео в mp4")
        video.unlink(missing_ok=True)
        video = mp4
    return video


def fmt_num(n) -> str:
    if n is None:
        return "—"
    n = int(n)
    for div, suf in ((1_000_000_000, " млрд"), (1_000_000, " млн"), (1_000, " тыс.")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".").replace(".", ",") + suf
    return str(n)


def metrics_text(d: dict) -> str:
    """Метрики одного ролика для сообщения в Telegram."""
    v, lk, cm = d.get("view_count"), d.get("like_count"), d.get("comment_count")
    lines = [f"📊 {d.get('title') or 'Без названия'}"[:200],
             f"👤 {d.get('channel') or d.get('uploader') or '—'}" + (f" · подписчиков {fmt_num(d['channel_follower_count'])}"
                                                                    if d.get("channel_follower_count") else ""),
             f"🌐 {d.get('extractor_key') or ''}"]
    if d.get("upload_date"):
        u = str(d["upload_date"])
        lines.append(f"📅 {u[6:8]}.{u[4:6]}.{u[:4]}")
    if d.get("duration"):
        lines.append(f"⏱ {int(d['duration']) // 60}:{int(d['duration']) % 60:02}")
    lines.append(f"\n👁 Просмотры: {fmt_num(v)}\n❤️ Лайки: {fmt_num(lk)}\n💬 Комментарии: {fmt_num(cm)}"
                 + (f"\n🔁 Репосты: {fmt_num(d['repost_count'])}" if d.get("repost_count") is not None else ""))
    if v:
        er = ((lk or 0) + (cm or 0) + (d.get("repost_count") or 0)) / v * 100
        lines.append(f"\n📈 Вовлечённость (ER): {er:.2f}%".replace(".", ","))
        if lk:
            lines.append(f"👍 Лайк на каждые {max(1, round(v / lk))} просмотров")
        if d.get("channel_follower_count"):
            lines.append(f"🚀 Охват к подписчикам: {v / d['channel_follower_count'] * 100:.0f}%")
    lines.append("\nПлощадки показывают не все цифры: прочерк значит, что площадка их скрыла.")
    return "\n".join(lines)
