"""Нарезка: собирает вертикальное видео 9:16 из нескольких случайных клипов вашей папки.

Источники: <RCLONE_BASE_PATH>/_clips/<тема>/*.mp4 и <RCLONE_BASE_PATH>/_backgrounds/<тема>/*.mp4 на Диске,
а также фоны, уже лежащие на сервере. _music/ - музыка. Перед сборкой Диск синхронизируется с VPS.
"""
import logging
import random
import shutil
import subprocess
from pathlib import Path

from common import config, library
from common.media import run, video_duration, video_only

log = logging.getLogger("mix")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}
MUSIC_DIR_NAME = "_music"
SKIP_DIRS = {"_music", "_own", "_sfx", "_satisfying"}


def _rclone(mode: str, remote_path: str, local: Path) -> None:
    """Drive -> сервер. Отсутствие папки на Диске не считается ошибкой: работаем по тому, что есть локально."""
    local.mkdir(parents=True, exist_ok=True)
    if not config.DRIVE_ENABLED:
        return
    src = f"{config.RCLONE_REMOTE}:{remote_path}"
    res = subprocess.run(["rclone", mode, src, str(local), "--retries", "3"], capture_output=True, text=True)
    if res.returncode != 0:
        log.warning("rclone %s %s не удался (работаем по локальным файлам): %s", mode, src, res.stderr[-300:])


def sync_clips() -> None:
    """Клипы: зеркало (удалили на Диске - исчезло и здесь).
    Фоны: только добавление, чтобы не стереть фоны, скачанные скриптом пополнения."""
    _rclone("sync", config.CLIPS_REMOTE_PATH, config.CLIPS_DIR)
    _rclone("copy", config.BG_REMOTE_PATH, config.BACKGROUNDS_DIR)


def _theme_dirs() -> dict[str, list[Path]]:
    """Тема -> папки с этим названием в клипах и в фонах (нарезке подходят оба источника)."""
    themes: dict[str, list[Path]] = {}
    for root in (config.CLIPS_DIR, config.BACKGROUNDS_DIR):
        if root.exists():
            for d in root.iterdir():
                if d.is_dir() and d.name not in SKIP_DIRS:
                    themes.setdefault(d.name.lower(), []).append(d)
    return themes


def list_themes() -> list[str]:
    return sorted({d.name for dirs in _theme_dirs().values() for d in dirs})


def norm_themes(theme) -> list[str]:
    """Тема(ы) в виде списка: None/'' -> [], 'a' -> ['a'], 'a, b' -> ['a','b'], ['a','b'] -> ['a','b'] (без повторов)."""
    if not theme:
        return []
    items = theme.split(",") if isinstance(theme, str) else list(theme)
    out: list[str] = []
    for t in items:
        t = str(t).strip()
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out


def pack_themes(items) -> "str | list[str] | None":
    """Обратно в хранимый вид: пусто -> None, одна -> строка (как в старых рецептах), несколько -> список."""
    items = norm_themes(items)
    return None if not items else (items[0] if len(items) == 1 else items)


def themes_label(theme, empty: str = "любая") -> str:
    items = norm_themes(theme)
    return " + ".join(items) if items else empty


def _clips(theme) -> list[Path]:
    themes = _theme_dirs()
    wanted = norm_themes(theme)
    if wanted:
        dirs, missing = [], []
        for t in wanted:
            found = themes.get(t.lower())
            if found:
                dirs += [d for d in found if d not in dirs]
            else:
                missing.append(t)
        if not dirs:
            raise RuntimeError(f"нет папки «{', '.join(missing)}» ни среди клипов, ни среди фонов. Есть: {', '.join(list_themes()) or 'пусто'}")
    else:
        dirs = [config.CLIPS_DIR] + [d for ds in themes.values() for d in ds]
    files, seen = [], set()
    for d in dirs:
        if d.exists():
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in VIDEO_EXT and f not in seen:
                    seen.add(f)
                    files.append(f)
    files = video_only(files)   # аудиофайлы с расширением .mp4/.webm и битые файлы ломали сборку
    if not files:
        raise RuntimeError("не найдено ни одного видео (ни в клипах, ни в фонах; файлы без видеодорожки пропущены)")
    return files


def _music(kind: str) -> Path | None:
    """kind: off | auto | тег (по тегу из библиотеки). Для нарезки музыка идёт на полной громкости."""
    if not kind or kind == "off":
        return None
    from core.render import pick_music
    return pick_music(kind)


def own_clips() -> list[Path]:
    d = config.CLIPS_DIR / "_own"
    return video_only([f for f in d.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXT]) if d.exists() else []


VF_VERTICAL = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30"


def _cut(clip: Path, start: float, length: float, out: Path, loop: bool = False) -> None:
    """Кусок клипа без звука, приведённый к 9:16."""
    run([
        "ffmpeg", "-y", "-loglevel", "error",
        *(["-stream_loop", "-1"] if loop else []), "-ss", f"{start:.2f}", "-i", str(clip), "-t", f"{length:.2f}", "-an",
        "-vf", VF_VERTICAL, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(out),
    ])


def _plain_parts(task_dir: Path, pool: list[Path], n: int, total: float) -> tuple[list[Path], list[Path]]:
    picked = random.sample(pool, n) if len(pool) >= n else pool + random.choices(pool, k=n - len(pool))
    random.shuffle(picked)
    for c in picked:
        library.touch(c)
    seg = total / n
    parts = []
    for i, clip in enumerate(picked):
        dur = video_duration(clip)
        offset = random.uniform(0, dur - seg - 0.1) if dur > seg + 0.1 else 0.0
        part = task_dir / "parts" / f"p{i:02}.mp4"
        _cut(clip, offset, seg, part, loop=dur <= seg + 0.1)
        parts.append(part)
    return parts, picked


def _dub_parts(task_dir: Path, n: int, total: float, target: str, meta: dict):
    """Клипы из _own (или из темы, если она указана): для каждого берётся окно с речью, перевод озвучивается и ставится на тайм-коды."""
    from core import dub

    voice = dub.pick_voice(target, meta.get("voice"))
    pool = _clips(meta["theme"]) if meta.get("theme") else own_clips()
    if not pool:
        raise RuntimeError("в папке _own нет видео: положите свои ролики в ShortsFactory/_clips/_own/ на Диске")
    pool = dub.order_for_dub(pool, target)
    seg_target = total / n
    work = task_dir / "parts"
    parts, voices, groups, used, skipped = [], [], [], [], []
    offset = 0.0
    for clip in pool:
        if len(parts) >= n:
            break
        info = dub.analyze(clip)
        if not info["segments"]:
            skipped.append(f"{clip.name}: нет речи")
            continue
        if info["lang"] == target:
            skipped.append(f"{clip.name}: уже на языке {target}")
            continue
        segs = dub.pick_window(info["segments"], seg_target)
        w_start = max(segs[0][0] - 0.15, 0.0)
        rel = [[s - w_start, e - w_start, t] for s, e, t in segs]
        texts = dub.translate([r[2] for r in rel], info["lang"], target)
        i = len(parts)
        placed, voice_end = dub.build_voice(rel, texts, voice, work, f"c{i:02}")
        clip_len = video_duration(clip)
        length = min(max(rel[-1][1] + 0.3, voice_end + 0.15), clip_len - w_start)
        part = work / f"p{i:02}.mp4"
        _cut(clip, w_start, length, part)
        real = video_duration(part)  # фактическая длина куска - по ней ровняется голос
        vtrack = work / f"v{i:02}.wav"
        dub.mix_voice_track(placed, real, vtrack)
        groups += dub.subtitle_groups(placed, texts, offset, real)
        parts.append(part)
        voices.append(vtrack)
        used.append(clip)
        offset += real
    if not parts:
        raise RuntimeError("нет подходящих клипов с речью на другом языке. Пропущены: " + "; ".join(skipped[:6]))
    dub.unload_translators()
    for c in used:
        library.touch(c)
    meta["skipped"] = skipped
    return parts, voices, groups, used


def build_mix(task_dir: Path, meta: dict) -> Path:
    n = int(meta.get("clips", config.MIX_DEFAULT_CLIPS))
    total = float(meta.get("seconds", config.MIX_DEFAULT_SECONDS))
    theme = meta.get("theme") or None
    target = (meta.get("dub") or "").lower() or None

    sync_clips()
    library.scan()
    (task_dir / "parts").mkdir(exist_ok=True)

    voices, groups = [], []
    if target:
        parts, voices, groups, picked = _dub_parts(task_dir, n, total, target, meta)
    else:
        parts, picked = _plain_parts(task_dir, _clips(theme), n, total)
    duration = sum(video_duration(p) for p in parts)
    (task_dir / "list.txt").write_text("".join(f"file 'parts/{p.name}'\n" for p in parts), "utf-8")

    music = _music(meta.get("music", "off"))
    use_subs = bool(groups) and meta.get("subs", True)
    if use_subs:
        from core import subtitles
        subtitles.write_ass(groups, task_dir / "subs.ass")

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt"]
    idx = 1
    voice_idx = music_idx = None
    if voices:
        (task_dir / "voice_list.txt").write_text("".join(f"file 'parts/{v.name}'\n" for v in voices), "utf-8")
        cmd += ["-f", "concat", "-safe", "0", "-i", "voice_list.txt"]
        voice_idx, idx = idx, idx + 1
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        music_idx = idx
    vol = int(meta.get("music_vol", 12)) / 100 if voices else 1.0
    fade = max(0.0, duration - 1.5)
    filters = ["[0:v]subtitles=subs.ass[v]" if use_subs else "[0:v]null[v]"]
    if voices and music:
        filters.append(f"[{music_idx}:a]volume={vol:.2f},afade=t=out:st={fade:.2f}:d=1.5[m]")
        filters.append(f"[{voice_idx}:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    elif voices:
        filters.append(f"[{voice_idx}:a]anull[a]")
    elif music:
        filters.append(f"[{music_idx}:a]volume={vol:.2f},afade=t=out:st={fade:.2f}:d=1.5[a]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[v]"]
    if voices or music:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    else:
        cmd += ["-an"]
    out = task_dir / "final.mp4"
    cmd += ["-t", f"{duration:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(out)]
    run(cmd, cwd=task_dir)

    shutil.rmtree(task_dir / "parts", ignore_errors=True)
    for junk in ("list.txt", "voice_list.txt", "subs.ass"):
        (task_dir / junk).unlink(missing_ok=True)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg не создал final.mp4")
    meta["sources"] = [c.name for c in picked]
    meta["music"] = music.name if music else None
    meta["duration"] = round(duration, 1)
    return out
