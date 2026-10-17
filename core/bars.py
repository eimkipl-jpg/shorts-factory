"""Автообрезка чёрных полос по краям клипа (вшитые в картинку «поля» у горизонтальных или вертикальных роликов).

Рамка определяется ffmpeg cropdetect по трём фрагментам клипа (начало, середина, конец): берётся объединение найденных
областей, поэтому тёмная сцена не обрежет лишнего. Результат запоминается на время работы процесса.
Защита от ошибок: полосы меньше ~1,5% размера игнорируются, а если после обрезки остаётся меньше 35% кадра
(похоже на очень тёмное видео, а не на поля), клип остаётся как есть.
Чёрным считается свет до ~26 из 255 (чистый чёрный и почти чёрный); серые или цветные поля не обрезаются.
"""
import logging
import re
import subprocess
from pathlib import Path

from common.media import probe, video_duration

log = logging.getLogger("bars")

MIN_BAR = 0.015      # полосы тоньше этой доли стороны не трогаем
MIN_KEEP = 0.25      # после обрезки должно остаться хотя бы столько площади кадра (вертикальное видео в кадре 16:9 - около 32%)
EDGE_PAD = 2         # пикселей добавочно с каждой стороны найденной полосы (края полос часто размыты)
LIMIT = 26           # яркость (0-255), ниже которой пиксель считается чёрным
WINDOW = 1.6         # длина каждого проверяемого фрагмента, с

_CROP = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
_cache: dict[tuple, tuple | None] = {}


def _frame_size(clip: Path) -> tuple[int, int] | None:
    """Размер кадра после автоповорота (как его видят фильтры ffmpeg)."""
    try:
        info = probe(clip)
    except ValueError:
        return None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and s.get("width") and s.get("height"):
            w, h = int(s["width"]), int(s["height"])
            rot = s.get("tags", {}).get("rotate")
            for sd in s.get("side_data_list", []) or []:
                rot = sd.get("rotation", rot)
            try:
                if rot is not None and abs(int(float(rot))) % 180 == 90:
                    w, h = h, w
            except (TypeError, ValueError):
                pass
            return w, h
    return None


def _detect_window(clip: Path, start: float) -> tuple[int, int, int, int] | None:
    """(x1, y1, x2, y2) непустой области в одном фрагменте или None."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{max(start, 0):.2f}", "-t", f"{WINDOW}", "-i", str(clip), "-an",
           "-vf", f"fps=5,cropdetect=limit={LIMIT}:round=2:reset=0", "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.SubprocessError:
        return None
    last = None
    for m in _CROP.finditer(res.stderr):
        last = tuple(int(g) for g in m.groups())
    if not last:
        return None
    w, h, x, y = last
    return x, y, x + w, y + h


def _box(clip: Path) -> tuple[int, int, int, int, int, int] | None:
    """(x1, y1, x2, y2, W, H): область с картинкой по всему клипу (объединение трёх фрагментов)."""
    try:
        key = (str(clip), clip.stat().st_mtime_ns, clip.stat().st_size)
    except OSError:
        return None
    if key in _cache:
        return _cache[key]
    size = _frame_size(clip)
    res = None
    if size:
        W, H = size
        try:
            dur = video_duration(clip)
        except (ValueError, KeyError):
            dur = 0.0
        points = [0.05, 0.5, 0.85] if dur > 4 * WINDOW else [0.0]
        boxes = [b for b in (_detect_window(clip, max(dur * p - WINDOW / 2, 0.0) if dur else 0.0) for p in points) if b]
        if boxes:
            res = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes), W, H)
    _cache[key] = res
    return res


def crop_filter(clip: Path, mode: str | None) -> str:
    """Фильтр ffmpeg «crop=w:h:x:y» для клипа или пустая строка. mode: off | auto (все стороны) | sides (только по бокам)."""
    if mode not in ("auto", "sides"):
        return ""
    box = _box(clip)
    if not box:
        return ""
    x1, y1, x2, y2, W, H = box
    if mode == "sides":
        y1, y2 = 0, H
    left, right, top, bottom = x1, W - x2, y1, H - y2
    if max(left / W, right / W, top / H, bottom / H) < MIN_BAR:
        return ""   # полос нет
    # небольшой запас внутрь только там, где полоса есть
    x1 = x1 + EDGE_PAD if left / W >= MIN_BAR else 0
    x2 = x2 - EDGE_PAD if right / W >= MIN_BAR else W
    y1 = y1 + EDGE_PAD if top / H >= MIN_BAR else 0
    y2 = y2 - EDGE_PAD if bottom / H >= MIN_BAR else H
    x1, y1 = x1 // 2 * 2, y1 // 2 * 2
    w, h = (x2 - x1) // 2 * 2, (y2 - y1) // 2 * 2
    if w < 64 or h < 64 or (w * h) / (W * H) < MIN_KEEP:
        log.warning("обрезка полос пропущена для %s: после неё осталось бы %dx%d из %dx%d (похоже на тёмное видео)",
                    clip.name, w, h, W, H)
        return ""
    return f"crop={w}:{h}:{x1}:{y1}"
