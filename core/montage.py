"""Монтажные стили: переходы, зум, цветокоррекция и эффекты. Готовые стили + свои (конструктор в боте).

Параметры стиля:
  transition - список переходов xfade (в каждой склейке берётся случайный из списка) или None (жёсткая склейка)
  d          - длительность перехода, с
  zoom       - off | slow («дыхание») | drift (медленный наезд) | pulse (пульс) | punch (всплески)
  grade      - ключ из GRADES (цветокоррекция) или None
  fx         - список ключей из FX_FILTERS (виньетка, зерно, VHS и т.д.)
Свои стили лежат в ROOT/montage_styles.json: {"u_<номер>": {"label": ..., "transition": [...], ...}}.
"""
from __future__ import annotations

import json
import random
import re

from common import config

USER_FILE = config.ROOT / "montage_styles.json"

# ---------- переходы (все есть в ffmpeg 4.4+) ----------
TRANS_LABELS = {
    "fade": "Плавное", "dissolve": "Растворение", "fadeblack": "Через чёрный", "fadewhite": "Через белый", "fadeslow": "Медленное",
    "slideleft": "Сдвиг ←", "slideright": "Сдвиг →", "slideup": "Сдвиг ↑", "slidedown": "Сдвиг ↓",
    "wipeleft": "Шторка ←", "wiperight": "Шторка →", "smoothleft": "Мягкий ←", "smoothright": "Мягкий →",
    "circleopen": "Круг +", "circleclose": "Круг −", "radial": "Радиал", "pixelize": "Пиксели", "zoomin": "Наезд",
    "hlslice": "Полосы ↔", "vuslice": "Полосы ↕", "diagtl": "Диагональ", "hblur": "Размытие", "squeezeh": "Сжатие",
    "distance": "Дистанция", "coverleft": "Накрытие ←", "revealright": "Раскрытие →",
}
TRANSITIONS_ALL = list(TRANS_LABELS)

ZOOMS = {"off": "без зума", "slow": "дыхание", "drift": "медленный наезд", "pulse": "пульс", "punch": "всплески"}
DURATIONS = (0.0, 0.15, 0.25, 0.4, 0.6, 0.9)

# ---------- цветокоррекция ----------
GRADES = {
    "warm": "colorbalance=rs=.08:gs=.02:bs=-.08:rm=.06:bm=-.05,eq=saturation=1.1",
    "cold": "colorbalance=rs=-.06:bs=.10,eq=saturation=1.05",
    "bw": "hue=s=0,eq=contrast=1.1",
    "contrast": "eq=contrast=1.2:saturation=1.25",
    "vintage": "curves=preset=vintage",
    "natural": "eq=contrast=1.05:saturation=1.08",
    "pop": "eq=contrast=1.15:saturation=1.5:brightness=0.02",
    "teal": "colorbalance=rs=-.08:gs=.03:bs=.12:rh=.10:bh=-.10,eq=contrast=1.1:saturation=1.12",
    "faded": "curves=preset=lighter,eq=contrast=0.92:saturation=0.85",
    "noir": "hue=s=0,eq=contrast=1.35:brightness=-0.03",
    "dreamy": "eq=brightness=0.04:saturation=1.15:contrast=0.95,gblur=sigma=0.7",
    "neon": "eq=contrast=1.2:saturation=1.8,colorbalance=bs=.10:rs=.06",
    "sunset": "colorbalance=rs=.14:gs=.02:bs=-.12:rh=.08,eq=saturation=1.2:contrast=1.08",
    "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    "toxic": "colorbalance=gs=.15:rs=-.10:bs=-.10,eq=saturation=1.15:contrast=1.1",
    "icy": "colorbalance=bs=.14:rs=-.10,eq=saturation=0.9:contrast=1.1",
    "golden": "colorbalance=rs=.10:gs=.05:bs=-.14,eq=saturation=1.25:brightness=0.02",
    "vhs": "eq=saturation=1.25:contrast=1.05,curves=preset=vintage",
    "cross": "curves=preset=cross_process",
    "strong": "curves=preset=strong_contrast",
}
GRADE_LABELS = {
    "warm": "Тёплый", "cold": "Холодный", "bw": "Чёрно-белый", "contrast": "Контраст", "vintage": "Винтаж", "natural": "Натуральный",
    "pop": "Яркий поп", "teal": "Кино (бирюза-оранж)", "faded": "Выцветший", "noir": "Нуар", "dreamy": "Мечтательный", "neon": "Неон",
    "sunset": "Закат", "sepia": "Сепия", "toxic": "Токсичный", "icy": "Ледяной", "golden": "Золотой", "vhs": "VHS-плёнка",
    "cross": "Кросс-процесс", "strong": "Жёсткий контраст",
}

# ---------- эффекты ----------
FX_FILTERS = {
    "vignette": "vignette=angle=PI/4.5",
    "grain": "noise=alls=6:allf=t",
    "sharp": "unsharp=5:5:0.9:5:5:0.0",
    "soft": "gblur=sigma=0.9",
    "vhs": "chromashift=crh=4:crv=-2,noise=alls=12:allf=t",
    "bars": "drawbox=x=0:y=0:w=iw:h=ih*0.08:color=black:t=fill,drawbox=x=0:y=ih*0.92:w=iw:h=ih*0.08:color=black:t=fill",
    "flicker": "eq=brightness='0.035*sin(t*31)':eval=frame",
    "shake": "crop=w=iw-30:h=ih-54:x='15+13*sin(t*13)':y='27+24*cos(t*9)',scale=1080:1920:flags=lanczos",
}
FX_LABELS = {"vignette": "Виньетка", "grain": "Зерно", "sharp": "Резкость", "soft": "Мягкий фокус", "vhs": "VHS-сдвиг",
             "bars": "Кино-полосы", "flicker": "Мерцание", "shake": "Тряска"}


def zoom_filter(mode) -> str | None:
    """Фильтр зума; True из старых стилей = «дыхание»."""
    if mode is True:
        mode = "slow"
    base = "scale=w='trunc(1080*({e})/2)*2':h=-2:eval=frame,crop=1080:1920"
    expr = {
        "slow": "1.05+0.04*sin(t*1.1)",
        "drift": "min(1.14,1+0.004*t)",
        "pulse": "1.045+0.035*sin(t*3.2)",
        "punch": "1.0+0.07*abs(sin(t*1.7))",
    }.get(mode)
    return base.format(e=expr) if expr else None


# ---------- готовые стили ----------
STYLES = {
    "none": {"label": "без стиля", "transition": None, "d": 0.0, "zoom": "off", "grade": None, "fx": []},
    "dynamic": {"label": "динамичный", "transition": ["slideleft", "slideright", "wipeleft", "circleopen", "pixelize"], "d": 0.3, "zoom": "slow", "grade": "contrast", "fx": []},
    "calm": {"label": "спокойный", "transition": ["fade", "dissolve"], "d": 0.6, "zoom": "slow", "grade": "warm", "fx": []},
    "retro": {"label": "ретро", "transition": ["dissolve", "fade"], "d": 0.5, "zoom": "off", "grade": "vintage", "fx": []},
    "mono": {"label": "чёрно-белый", "transition": ["fadeblack", "fade"], "d": 0.5, "zoom": "off", "grade": "bw", "fx": []},
    "hype": {"label": "хайп", "transition": ["slideleft", "slideright", "slideup", "zoomin", "hlslice", "wipeleft", "pixelize"], "d": 0.2, "zoom": "pulse", "grade": "pop", "fx": ["sharp"]},
    "cinema": {"label": "кино", "transition": ["fadeblack", "fade", "dissolve"], "d": 0.8, "zoom": "drift", "grade": "teal", "fx": ["vignette", "grain"]},
    "vhs": {"label": "VHS", "transition": ["pixelize", "hblur", "dissolve", "fade"], "d": 0.25, "zoom": "off", "grade": "vhs", "fx": ["vhs"]},
    "glitch": {"label": "глитч", "transition": ["hlslice", "vuslice", "pixelize", "hblur"], "d": 0.18, "zoom": "punch", "grade": "neon", "fx": ["vhs"]},
    "clean": {"label": "чистый", "transition": ["fade"], "d": 0.35, "zoom": "off", "grade": "natural", "fx": ["sharp"]},
    "sport": {"label": "спорт", "transition": ["wipeleft", "wiperight", "slideleft", "coverleft", "revealright"], "d": 0.22, "zoom": "punch", "grade": "contrast", "fx": ["sharp"]},
    "dream": {"label": "мечта", "transition": ["dissolve", "fadewhite", "fadeslow"], "d": 0.9, "zoom": "drift", "grade": "dreamy", "fx": ["vignette", "soft"]},
    "noir": {"label": "нуар", "transition": ["fadeblack", "fade"], "d": 0.6, "zoom": "drift", "grade": "noir", "fx": ["vignette", "grain"]},
    "pop": {"label": "поп", "transition": ["circleopen", "circleclose", "radial", "squeezeh"], "d": 0.3, "zoom": "pulse", "grade": "pop", "fx": []},
    "sunset": {"label": "закат", "transition": ["smoothleft", "smoothright", "dissolve", "fade"], "d": 0.6, "zoom": "drift", "grade": "sunset", "fx": ["vignette"]},
    "cyber": {"label": "киберпанк", "transition": ["diagtl", "distance", "hblur", "pixelize"], "d": 0.28, "zoom": "slow", "grade": "neon", "fx": ["grain"]},
    "doc": {"label": "документальный", "transition": ["fade", "dissolve"], "d": 0.5, "zoom": "slow", "grade": "faded", "fx": ["grain"]},
    "shock": {"label": "шок", "transition": ["fadewhite", "fadeblack", "zoomin", "pixelize"], "d": 0.15, "zoom": "punch", "grade": "strong", "fx": ["sharp"]},
    "smooth": {"label": "плавный", "transition": ["smoothleft", "smoothright", "fade", "dissolve"], "d": 0.5, "zoom": "slow", "grade": "warm", "fx": []},
}
BUILTIN_ORDER = [k for k in STYLES if k != "none"]


# ---------- свои стили ----------
def sanitize(spec: dict) -> dict:
    """Приводит описание стиля к допустимым значениям."""
    tr = [t for t in (spec.get("transition") or []) if t in TRANS_LABELS]
    try:
        d = float(spec.get("d", 0.3))
    except (TypeError, ValueError):
        d = 0.3
    zoom = spec.get("zoom", "off")
    if zoom is True:
        zoom = "slow"
    grade = spec.get("grade")
    return {
        "label": str(spec.get("label") or "Мой стиль")[:30],
        "transition": tr or None,
        "d": min(1.5, max(0.0, d)) if tr else 0.0,
        "zoom": zoom if zoom in ZOOMS else "off",
        "grade": grade if grade in GRADES else None,
        "fx": [f for f in (spec.get("fx") or []) if f in FX_FILTERS],
    }


def user_styles() -> dict[str, dict]:
    try:
        data = json.loads(USER_FILE.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return {k: sanitize(v) for k, v in data.items() if isinstance(v, dict) and re.fullmatch(r"u_\d+", k)} if isinstance(data, dict) else {}


def _write(d: dict) -> None:
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(USER_FILE)


def save_user_style(label: str, spec: dict, style_id: str | None = None) -> str:
    d = user_styles()
    if style_id is None:
        # одноимённый стиль обновляем, иначе заводим новый номер
        style_id = next((k for k, v in d.items() if v["label"].lower() == label.strip().lower()), None)
    if style_id is None:
        style_id = "u_%d" % (max([int(k[2:]) for k in d] + [0]) + 1)
    d[style_id] = sanitize({**spec, "label": label.strip() or "Мой стиль"})
    _write(d)
    return style_id


def delete_user_style(style_id: str) -> bool:
    d = user_styles()
    if style_id not in d:
        return False
    d.pop(style_id)
    _write(d)
    return True


def labels() -> dict[str, str]:
    """Для кнопок: готовые, «случайный» и свои (со звёздочкой)."""
    out = {"none": STYLES["none"]["label"], "random": "случайный"}
    out.update({k: STYLES[k]["label"] for k in BUILTIN_ORDER})
    out.update({k: "★ " + v["label"] for k, v in user_styles().items()})
    return out


def all_names() -> list[str]:
    return list(labels())


def describe(spec: dict) -> str:
    tr = spec.get("transition") or []
    parts = [("переходы: " + ", ".join(TRANS_LABELS[t] for t in tr[:4]) + ("…" if len(tr) > 4 else "") + f" ({spec.get('d', 0)} с)") if tr else "переходы: жёсткая склейка",
             "зум: " + ZOOMS.get(spec.get("zoom") if spec.get("zoom") is not True else "slow", "без зума"),
             "цвет: " + (GRADE_LABELS.get(spec.get("grade"), spec.get("grade")) if spec.get("grade") else "без коррекции")]
    if spec.get("fx"):
        parts.append("эффекты: " + ", ".join(FX_LABELS[f] for f in spec["fx"] if f in FX_LABELS))
    return "\n".join(parts)


def random_spec() -> dict:
    """Случайная, но осмысленная комбинация."""
    tr = random.sample(TRANSITIONS_ALL, random.randint(2, 5))
    return {
        "transition": tr, "d": random.choice([0.2, 0.3, 0.45, 0.6, 0.8]),
        "zoom": random.choice(list(ZOOMS)), "grade": random.choice([None, *GRADES]),
        "fx": random.sample(["vignette", "grain", "sharp", "soft"], random.choice([0, 0, 1, 1, 2])),
    }


def resolve(name: str) -> dict:
    """Конкретные параметры для сборки. Для «random» собирает случайную комбинацию каждый раз заново."""
    if name == "random":
        base = random_spec()
    elif name in STYLES:
        base = dict(STYLES[name])
    else:
        base = dict(user_styles().get(name) or STYLES["none"])
        if name not in user_styles():
            name = "none"
    tr = base.get("transition")
    pool = list(tr) if isinstance(tr, list) and tr else None
    base["transition_pool"] = pool if pool and len(pool) > 1 else None   # в каждой склейке свой переход из списка
    base["transition"] = random.choice(pool) if pool else (tr if isinstance(tr, str) else None)
    if base["transition"] is None:
        base["d"] = 0.0
    base["zoom"] = "slow" if base.get("zoom") is True else (base.get("zoom") or "off")
    base["fx"] = list(base.get("fx") or [])
    base["name"] = name
    return base
