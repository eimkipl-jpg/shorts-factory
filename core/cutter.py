"""Нарезчик: режет длинное видео на фрагменты по сменам кадра и (по желанию) оставляет самые интересные.

Режимы:
  cuts - все фрагменты между переходами (слишком короткие склеиваются с соседями, слишком длинные делятся поровну);
  best - то же, но оставляются top лучших: оценка = громкость звука + движение в кадре.
Без нейросетей: смены кадра находит ffmpeg (фильтр scene), «интересность» - простая эвристика.

Результат: mp4 в <ROOT>/clips/<тема>/ (со звуком), запись в библиотеку и копия на Диск
(иначе папка клипов, зеркалящаяся с Диска, удалила бы их при следующей синхронизации).
Имя файла содержит время в исходнике: повторная нарезка тех же моментов ничего не дублирует.
"""
import logging
import math
import re
import shutil
import subprocess
from array import array
from pathlib import Path

from common import config, library
from common.media import video_duration

log = logging.getLogger("cutter")

SENS = {"low": 0.45, "mid": 0.30, "high": 0.18}   # порог смены кадра: чем ниже, тем больше переходов
EDGE = 0.12                                       # отступ от перехода, чтобы не захватить кадр соседней сцены
MAX_CLIPS = 60


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------- анализ ----------

def _detect_plane(video: Path, plane: str, threshold: float) -> list[float]:
    """Смена кадра по одному каналу: y - яркость, u/v - цвет. Фильтр scene смотрит только на первый канал,
    поэтому сцены одинаковой яркости, но разного цвета, по яркости не различить."""
    res = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an",
                "-vf", f"scale=320:-2,format=yuv420p,extractplanes={plane},select='gt(scene,{threshold})',showinfo",
                "-f", "null", "-"])
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", res.stderr)]


def detect_cuts_ffmpeg(video: Path, threshold: float = 0.30) -> list[float]:
    """Запасной детектор на чистом ffmpeg: по яркости и по цвету."""
    from concurrent.futures import ThreadPoolExecutor
    jobs = (("y", threshold), ("u", threshold * 0.5), ("v", threshold * 0.5))
    with ThreadPoolExecutor(3) as ex:
        found = list(ex.map(lambda j: _detect_plane(video, *j), jobs))
    return _cluster(sorted({t for part in found for t in part}))


ADAPT = {0.45: 4.5, 0.30: 3.0, 0.18: 2.0}   # порог чувствительности -> adaptive_threshold PySceneDetect


def detect_cuts(video: Path, threshold: float = 0.30) -> list[float]:
    """Переходы через готовую библиотеку PySceneDetect (AdaptiveDetector: сравнивает каждый кадр с соседними,
    поэтому устойчив к движению и вспышкам). Если библиотеки нет, работает запасной детектор на ffmpeg."""
    try:
        from scenedetect import AdaptiveDetector, detect
    except ImportError:
        log.warning("scenedetect не установлен, использую запасной детектор ffmpeg")
        return detect_cuts_ffmpeg(video, threshold)
    thr = ADAPT.get(threshold, 3.0)
    scenes = detect(str(video), AdaptiveDetector(adaptive_threshold=thr, min_scene_len="0.3s"))
    sec = lambda t: t.seconds if hasattr(t, "seconds") else t.get_seconds()  # noqa: E731
    return [math.floor(sec(a) * 1000) / 1000 for a, _ in scenes[1:]]  # вниз: граница не должна уйти за первый кадр новой сцены


def _cluster(times: list[float], gap: float = 0.12) -> list[float]:
    """Плавный переход (растворение, вспышка) даёт пачку срабатываний подряд: считаем их одним переходом
    и берём последнее, то есть момент, когда новая сцена уже установилась."""
    out: list[float] = []
    for t in times:
        if out and t - out[-1] < gap:
            out[-1] = t
        else:
            out.append(t)
    return out


def motion_profile(video: Path, step: float = 0.5) -> list[float]:
    """Интенсивность движения по окнам step секунд (среднее различие соседних кадров при 4 к/с)."""
    res = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an",
                "-vf", "fps=4,scale=160:-2,select='gte(scene,0)',metadata=print:file=-", "-f", "null", "-"])
    t, scores = None, {}
    for line in res.stdout.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
        if m and t is not None:
            scores.setdefault(int(t / step), []).append(float(m.group(1)))
    n = (max(scores) + 1) if scores else 0
    return [sum(scores.get(i, [0.0])) / max(len(scores.get(i, [0.0])), 1) for i in range(n)]


def has_audio(video: Path) -> bool:
    res = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(video)], 60)
    return bool(res.stdout.strip())


def audio_profile(video: Path, step: float = 0.5) -> list[float]:
    """Громкость (RMS) по окнам step секунд; пусто, если звука нет."""
    if not has_audio(video):
        return []
    rate = 8000
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                       capture_output=True, timeout=3600)
    samples = array("h")
    samples.frombytes(p.stdout[: len(p.stdout) // 2 * 2])
    win = int(rate * step)
    out = []
    for i in range(0, len(samples) - win + 1, win):
        chunk = samples[i:i + win]
        out.append(math.sqrt(sum(x * x for x in chunk) / win))
    return out


# ---------- сегментация ----------

def effective_cuts(cuts: list[float], duration: float) -> list[float]:
    return [c for c in cuts if 0.3 < c < duration - 0.3]


def segment(cuts: list[float], duration: float, min_len: float, max_len: float) -> list[tuple[float, float]]:
    """Сцены строго между переходами. Сцены короче min_len отбрасываются (никогда не склеиваются с соседними,
    иначе в фрагмент попадают кадры чужой сцены), длиннее max_len делятся на равные части."""
    bounds = [0.0] + effective_cuts(cuts, duration) + [duration]
    out: list[tuple[float, float]] = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        length = b - a
        if length < min_len:
            continue
        parts = max(1, math.ceil(length / max_len))
        size = length / parts
        for k in range(parts):
            out.append((a + k * size, a + (k + 1) * size))
    return out


def _avg(profile: list[float], a: float, b: float, step: float = 0.5) -> float:
    lo, hi = int(a / step), max(int(b / step), int(a / step) + 1)
    vals = profile[lo:hi]
    return sum(vals) / len(vals) if vals else 0.0


def _norm(values: list[float]) -> list[float]:
    if not values:
        return values
    lo, hi = min(values), max(values)
    return [0.0] * len(values) if hi - lo < 1e-9 else [(v - lo) / (hi - lo) for v in values]


def rank(segs: list[tuple[float, float]], audio: list[float], motion: list[float]) -> list[float]:
    """Оценка интересности 0..1: громкость и движение поровну (если звука нет - только движение)."""
    a = _norm([_avg(audio, s, e) for s, e in segs]) if audio else None
    m = _norm([_avg(motion, s, e) for s, e in segs]) if motion else None
    if a and m:
        return [0.5 * x + 0.5 * y for x, y in zip(a, m)]
    return a or m or [0.0] * len(segs)


def plan(video: Path, mode: str = "cuts", min_len: float = 3.0, max_len: float = 12.0,
         sens: str = "mid", top: int = 8) -> dict:
    """Что будет нарезано: {'segments': [(start, end, score)], 'duration', 'cuts'}."""
    duration = video_duration(video)
    if not duration or duration < min_len:
        raise RuntimeError("видео слишком короткое для нарезки")
    cuts = detect_cuts(video, SENS.get(sens, 0.30))
    segs = segment(cuts, duration, min_len, max_len)
    if not segs:
        raise RuntimeError(f"не удалось выделить ни одного фрагмента: найдено переходов {len(cuts)}, все сцены короче {min_len:g} с. "
                           "Выберите меньшую минимальную длину или более высокую чувствительность")
    scores = [0.0] * len(segs)
    # все границы, на которых надо разрезать за один проход (переходы + точки деления длинных сцен)
    all_edges = sorted({0.0, duration, *effective_cuts(cuts, duration), *(x for s_e in segs for x in s_e)})
    if mode == "best":
        scores = rank(segs, audio_profile(video), motion_profile(video))
        order = sorted(range(len(segs)), key=lambda i: -scores[i])[:max(1, min(top, MAX_CLIPS))]
        keep = sorted(order)  # в порядке появления в видео
        segs, scores = [segs[i] for i in keep], [scores[i] for i in keep]
    segs, scores = segs[:MAX_CLIPS], scores[:MAX_CLIPS]
    return {"segments": [(s, e, sc) for (s, e), sc in zip(segs, scores)], "duration": duration, "cuts": len(cuts), "cut_times": cuts,
            "edges": all_edges}


# ---------- нарезка ----------

def _cut_file(video: Path, start: float, end: float, out: Path, trim_start: bool = True, trim_end: bool = True) -> None:
    """Запасной способ: отдельная перекодировка куска (-ss до -i)."""
    s, e = start + (EDGE if trim_start else 0.0), end - (EDGE if trim_end else 0.0)
    res = _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s:.3f}", "-i", str(video), "-t", f"{e - s:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)], 1800)
    if res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg не смог вырезать {start:.0f}-{end:.0f} с: {res.stderr[-200:]}")


def split_all(video: Path, edges: list[float], tmp: Path) -> list[Path] | None:
    """Режет видео по ВСЕМ границам за один проход декодирования: на каждой границе кодировщик принудительно ставит
    ключевой кадр, а muxer начинает новый файл ровно на нём. Так границы берутся из того же потока кадров,
    по которому искались переходы, и сдвига между «нашли» и «вырезали» нет. Возвращает файлы по порядку или None."""
    inner = [t for t in edges if 0.001 < t < edges[-1] - 0.001]
    if not inner:
        return None
    tmp.mkdir(parents=True, exist_ok=True)
    times = ",".join(f"{t:.6f}" for t in inner)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-pix_fmt", "yuv420p", "-force_key_frames", times,
           "-c:a", "aac", "-b:a", "128k", "-f", "segment", "-segment_times", times, "-reset_timestamps", "1",
           "-segment_format", "mp4", "-segment_format_options", "movflags=+faststart", str(tmp / "seg_%05d.mp4")]
    res = _run(cmd, 7200)
    files = sorted(tmp.glob("seg_*.mp4"))
    if res.returncode != 0 or len(files) != len(edges) - 1:
        log.warning("одним проходом не вышло (файлов %s из %s): %s", len(files), len(edges) - 1, res.stderr[-200:])
        return None
    for f, (a, b) in zip(files, zip(edges, edges[1:])):  # длительности должны совпасть с планом
        d = video_duration(f)
        if d is None or abs(d - (b - a)) > 0.35:
            log.warning("длительность %s (%.2f) не совпала с планом (%.2f)", f.name, d or 0, b - a)
            return None
    return files


def contact_sheet(video: Path, cuts: list[float], out: Path, limit: int = 24) -> Path:
    """Картинка для проверки: у каждого найденного перехода два кадра, «до» слева и «после» справа."""
    tmp = out.parent / (out.stem + "_frames")
    tmp.mkdir(parents=True, exist_ok=True)
    n = 0
    for c in cuts[:limit]:
        for k, t in enumerate((max(c - 0.06, 0.0), c + 0.06)):
            f = tmp / f"f_{n:03d}.jpg"
            _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                  "-vf", "scale=240:-2", str(f)], 120)
            if f.exists():
                n += 1
    if n == 0:
        raise RuntimeError("не удалось получить кадры для проверки")
    cols = 4
    res = _run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "1", "-i", str(tmp / "f_%03d.jpg"),
                "-vf", f"pad=iw+6:ih+6:3:3:black,tile={cols}x{(n + cols - 1) // cols}", "-frames:v", "1", "-q:v", "4", str(out)], 120)
    for f in tmp.glob("*.jpg"):
        f.unlink(missing_ok=True)
    tmp.rmdir()
    if res.returncode != 0 or not out.exists():
        raise RuntimeError("не удалось собрать картинку: " + res.stderr[-150:])
    return out


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02}"


def run(video: Path, theme: str, mode: str = "cuts", min_len: float = 3.0, max_len: float = 12.0,
        sens: str = "mid", top: int = 8, title: str = "", progress=None, out_dir: Path | None = None) -> dict:
    """Нарезает, регистрирует в библиотеке и копирует на Диск. Возвращает сводку.
    out_dir задан (бот для креаторов) - фрагменты кладутся туда, без библиотеки и Диска: их забирает и отправляет воркер."""
    from core import sources
    theme = sources.slug(theme)
    p = plan(video, mode, min_len, max_len, sens, top)
    private = out_dir is not None
    out_dir = out_dir or config.CLIPS_DIR / theme
    out_dir.mkdir(parents=True, exist_ok=True)
    base = sources.slug(title or video.stem)[:30] or "video"
    made, skipped = [], 0
    todo = []
    for s_, e_, score in p["segments"]:
        out = out_dir / f"{base}_{int(s_):04d}-{int(e_):04d}.mp4"
        if out.exists() and out.stat().st_size > 0:
            skipped += 1
        else:
            todo.append((out, s_, e_, score))
    if todo:
        edges = p["edges"]
        tmp = (out_dir / "_tmp") if private else config.TMP_DIR / f"cut_{base}"
        shutil.rmtree(tmp, ignore_errors=True)
        pieces = split_all(video, edges, tmp) if len(todo) else None
        try:
            if pieces:
                for out, s_, e_, score in todo:
                    idx = min(range(len(edges) - 1), key=lambda i: abs(edges[i] - s_) + abs(edges[i + 1] - e_))
                    shutil.move(str(pieces[idx]), str(out))
                    made.append((out, s_, e_, score))
            else:  # запасной путь: каждый фрагмент отдельно
                cut_set = p["cut_times"]
                near = lambda t: any(abs(t - c) < 0.05 for c in cut_set)  # noqa: E731
                for i, (out, s_, e_, score) in enumerate(todo, 1):
                    _cut_file(video, s_, e_, out, trim_start=near(s_), trim_end=near(e_))
                    made.append((out, s_, e_, score))
                    if progress:
                        progress(i, len(todo))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if private:
        return {"theme": theme, "made": len(made), "skipped": skipped, "cuts": p["cuts"], "duration": p["duration"],
                "drive_ok": False, "drive_msg": "", "paths": [o for o, *_ in made],
                "files": [(o.name, s, e, round(sc, 2)) for o, s, e, sc in made]}
    library.scan()
    src = f"нарезка из: {title or video.name}"
    for out, s, e, score in made:
        a = library.by_path(out)
        if a:
            library.set_info(a["id"], source=src, license_="мои материалы")
            library.set_title(a["id"], f"{title or video.stem} · {_mmss(s)}–{_mmss(e)}")
    ok, msg = sources.push_to_drive(out_dir, f"{config.CLIPS_REMOTE_PATH}/{theme}")
    return {"theme": theme, "made": len(made), "skipped": skipped, "cuts": p["cuts"], "duration": p["duration"],
            "drive_ok": ok, "drive_msg": msg,
            "files": [(o.name, s, e, round(sc, 2)) for o, s, e, sc in made]}


if __name__ == "__main__":  # python -m core.cutter <видео> [режим] [мин] [макс] [low|mid|high]: что будет нарезано, без нарезки
    import sys
    args = sys.argv[1:]
    if not args:
        raise SystemExit("python -m core.cutter <видео> [cuts|best] [мин] [макс] [low|mid|high]")
    pl = plan(Path(args[0]), args[1] if len(args) > 1 else "cuts", float(args[2]) if len(args) > 2 else 2.0,
              float(args[3]) if len(args) > 3 else 10.0, args[4] if len(args) > 4 else "mid")
    print(f"длительность {pl['duration']:.1f} с, переходов найдено {pl['cuts']}: " + ", ".join(f"{t:.2f}" for t in pl["cut_times"][:60]))
    for s_, e_, sc in pl["segments"]:
        print(f"  {_mmss(s_)} - {_mmss(e_)}  ({e_ - s_:.1f} с)" + (f"  оценка {sc:.2f}" if sc else ""))
