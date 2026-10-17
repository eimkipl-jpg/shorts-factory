"""Параметры внешнего вида и звуковых эффектов ролика: значения по умолчанию и подписи для меню.

Используется и мастером (gateway), и сборщиком (core.compose), поэтому лежит в common.
"""
import copy

DEFAULT_LOOK = {
    "layout": "fill",          # fill | blur | split | frame
    "crop_bars": "off",        # автообрезка чёрных полос по краям клипов: off | auto (все стороны) | sides (только по бокам)
    "speed": "1",              # 1 | 0.8 | 1.25 | 1.5 | mix
    "fx": [],                  # vignette | grain | sharp
    "sub": "classic",          # вид субтитров: classic | white | neon | box
    "subpos": "center",        # center | bottom | top
    "hook": {"mode": "off", "text": "", "pos": "top", "size": "l", "style": "outline", "start": 0.15, "dur": "auto"},
    # hook.mode: off | title | text | random - крупный текст в первые секунды; pos: top|upper|center|lower|bottom;
    # size: m|l|xl; style: outline|gold|white|black|red; start - когда появляется (с); dur - сколько висит (с) или "auto"
    "cta": {"mode": "off", "text": "", "pos": "bottom", "size": "l", "style": "gold", "when": "end", "dur": "auto"},
    # cta.when: end (в конце) | mid (в середине) | start (в начале, с 0.5 с); dur - сколько висит (с) или "auto"
    "caption": {"mode": "off", "text": "", "pos": "bottom", "size": "m", "style": "outline"},
    # caption: постоянная подпись на всё время ролика (например, название снизу); mode: off | title | text
    "progress": {"design": "off", "pos": "bottom", "color": "auto", "end": True, "label": ""},
    # progress: полоска прогресса во всю длительность; design: off | line | capsule | neon; end - кнопки лайка и подписки в конце
    "frame": {"size": "mid", "color": "random"},            # для компоновки «рамка»; color: random|white|black|navy|yellow|red|blur|#rrggbb
    "logo": "off",             # off | tr | tl | br | bl
}

DEFAULT_SFX = {"cuts": False, "start": False, "tag": None, "vol": 60}

LAYOUTS = {
    "fill": "Заполнить кадр",
    "blur": "Размытый фон",
    "split": "Сплит (2 клипа)",
    "frame": "Рамка",
}
CROP_BARS = {"off": "нет", "auto": "со всех сторон", "sides": "только по бокам"}
SPEEDS = {"1": "1×", "0.8": "0.8×", "1.25": "1.25×", "1.5": "1.5×", "mix": "микс"}
FX = {"vignette": "виньетка", "grain": "зерно", "sharp": "резкость"}
FRAME_SIZES = {"thin": "тонкая", "mid": "средняя", "thick": "толстая"}
FRAME_PX = {"thin": 24, "mid": 60, "thick": 110}
FRAME_COLORS = {"random": "случайный", "white": "белый", "black": "чёрный", "navy": "тёмно-синий",
                "yellow": "жёлтый", "red": "красный", "blur": "размытый фон"}
FRAME_HEX = {"white": "white", "black": "black", "navy": "0x1b1b2f", "yellow": "0xffd60a", "red": "0xe63946"}
HOOK_POS = {"top": "верх", "upper": "выше центра", "center": "центр", "lower": "ниже центра", "bottom": "низ"}
TEXT_STYLES = {"outline": "белый с контуром", "gold": "золотая плашка", "white": "белая плашка", "black": "чёрная плашка",
               "red": "красная плашка"}
CTA_WHEN = {"end": "в конце", "mid": "в середине", "start": "в начале"}
HOOK_SIZES = {"m": "M", "l": "L", "xl": "XL"}
HOOK_STARTS = (0, 1, 2, 3)
HOOK_DURS = ("auto", 2, 3, 5)
HOOK_PRESETS = ["Смотри до конца", "Ты не поверишь", "Подожди до конца", "Это нужно видеть", "Вот это поворот"]
CLIP_LENS = {"full": "как есть", "1-3": "1–3 с", "2-6": "2–6 с", "4-10": "4–10 с", "eq": "равные"}
MIN_HEIGHTS = {0: "любое", 480: "от 480p", 720: "от 720p"}
ORIG_VOLS = (100, 70, 40, 15)
POS = {"center": "центр", "bottom": "низ", "top": "верх"}
LOGO_POS = {"off": "нет", "tr": "справа сверху", "tl": "слева сверху", "br": "справа снизу", "bl": "слева снизу"}
CTA_PRESETS = ["Подпишись!", "Продолжение в профиле", "Поделись с другом", "Лайк, если согласен"]
CAPTION_MODES = {"off": "нет", "title": "из заголовка", "text": "свой текст"}
CAPTION_SIZES = {"s": "S", "m": "M", "l": "L"}
PROGRESS_DEFAULT = DEFAULT_LOOK["progress"]
PROGRESS_DESIGNS = {"off": "нет", "line": "Линия", "capsule": "Капсула", "neon": "Неон"}
PROGRESS_POS = {"bottom": "у нижнего края", "high": "выше интерфейса Shorts"}
# цвет полоски: (название, hex); auto - свои цвета у каждого дизайна
PROGRESS_COLORS = {"auto": ("по дизайну", ""), "red": ("красный", "#FF1F3D"), "gold": ("золотой", "#FFC400"),
                   "cyan": ("голубой", "#00E5FF"), "pink": ("розовый", "#FF2BD6"), "green": ("зелёный", "#2BFF88"),
                   "white": ("белый", "#FFFFFF")}


def merged_look(data: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_LOOK)
    for k, v in (data or {}).items():
        if k in ("hook", "cta", "frame", "caption", "progress") and isinstance(v, dict):
            out[k].update(v)
        elif k in out:
            out[k] = copy.deepcopy(v)
    return out


def merged_sfx(data: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_SFX)
    out.update({k: v for k, v in (data or {}).items() if k in out})
    return out
