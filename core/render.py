"""Выбор фона из банка и финальный рендер 9:16 с выжженными субтитрами."""
import random
from pathlib import Path

from common import config, library
from common.media import run, video_duration

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}


def pick_background(tags: list[str]) -> Path:
    """Тег из metadata (#gameplay -> папка gameplay) или случайная тема, если тега нет."""
    wanted = [t.lstrip("#").lower() for t in tags]
    themes = [d for d in config.BACKGROUNDS_DIR.iterdir() if d.is_dir()]
    matching = [d for d in themes if d.name.lower() in wanted]
    pool_dirs = matching or themes
    files = [f for d in pool_dirs for f in d.iterdir() if f.suffix.lower() in VIDEO_EXT]
    if not files and matching:  # в теме пусто - берём любой фон
        files = [f for d in themes for f in d.iterdir() if f.suffix.lower() in VIDEO_EXT]
    if not files:
        raise RuntimeError("Банк фонов пуст: запустите backgrounds/refill.py")
    return library.choose(files)


def pick_music(kind: str) -> Path | None:
    """kind: off | auto | тег. Берём наименее использованный трек из библиотеки."""
    if not kind or kind == "off":
        return None
    tags = [] if kind == "auto" else [kind]
    tracks = library.search("music", tags, limit=500)
    if not tracks:  # индекс мог не знать о новых файлах
        library.scan()
        tracks = library.search("music", tags, limit=500)
    files = [config.ROOT / t["path"] for t in tracks if (config.ROOT / t["path"]).exists()]
    return library.choose(files) if files else None


def render(task_dir: Path, audio: Path, ass: Path | None, tags: list[str], duration: float,
           music: str = "off", music_vol: int = 12) -> Path:
    bg = pick_background(tags)
    bg_len = video_duration(bg)
    # Случайный отступ, но так, чтобы хватило длины; если фон короче аудио - зацикливаем.
    offset = random.uniform(0, max(0.0, bg_len - duration - 0.5)) if bg_len > duration + 0.5 else 0.0
    loop = ["-stream_loop", "-1"] if bg_len <= duration + 0.5 else []

    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30"
    if ass is not None:
        vf += f",subtitles={ass.name}"
    track = pick_music(music)

    out = task_dir / "final.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *loop, "-ss", f"{offset:.2f}", "-i", str(bg), "-i", str(audio)]
    if track:
        cmd += ["-stream_loop", "-1", "-i", str(track)]
        fade = max(0.0, duration - 1.5)
        fc = (
            f"[0:v]{vf}[v];"
            f"[2:a]volume={music_vol / 100:.2f},afade=t=out:st={fade:.2f}:d=1.5[m];"
            "[1:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-vf", vf, "-map", "0:v:0", "-map", "1:a:0"]
    cmd += [
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd, cwd=task_dir)  # cwd = папка задачи, чтобы subtitles=subs.ass не требовал экранирования пути
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg не создал final.mp4")
    return out
