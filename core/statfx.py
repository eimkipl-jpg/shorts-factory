"""Графика умного монтажа, нарисованная средствами ASS (без картинок и лишних библиотек): цифры, графики, сравнения,
списки, крупные фразы и субтитры с подсветкой слова. Шрифты - Fira Sans (core.fontpack), если не установились - DejaVu Sans.

stat = {"kind": ..., "label": "подпись", "source": "откуда цифра", "full": true|false}
  number:  "value": "3.2%", необязательно "delta": "+0.4 п.п."     - крупная цифра, считается от нуля
  bars:    "items": [{"label": "2019", "value": 5.2, "text": "$5.20"}, ...], "unit": "$"   - столбцы растут по очереди
  line:    "values": [1.2, 1.4, 4.7, 8.0], "labels": ["2019", ..., "2023"], "unit": "%"  - линия прорисовывается
  compare: "left": {"label": "2015", "value": "$4"}, "right": {"label": "2024", "value": "$6.5"}  - было/стало, со стрелкой
  list:    "items": ["шаг 1", "шаг 2", "шаг 3"]                     - нумерованные пункты появляются по очереди
  fact:    "text": "Короткая мысль", "accent": "слово жёлтым", "icon": "bread"  - крупная фраза-вывод (иконка необязательна)
  flow:    "steps": [{"text": "Война", "icon": "sword"}, {"text": "Долги", "icon": "hand-coins"}]  - цепочка причин со стрелками
  icons:   "items": [{"icon": "bread", "label": "Буханка хлеба", "value": "200 млрд"}]  - 1-3 крупные иконки с подписями
Иконки - шрифт Phosphor (core.iconpack), название - простое английское слово.
full: true (у list, compare, fact всегда) - карточка на весь кадр на цветном фоне; иначе плашка поверх видео.
"""
import re

from core import fontpack, iconpack
from core.progress import _bgr, _rrect, _t

W, H = 1080, 1920
CX = W / 2
PX, PW = 60, 960
TOP, BOT = 190, 1195          # область карточки на весь кадр; ниже - субтитры
SUB_Y = 1330                  # центр строки субтитров (выше подписи и кнопок Shorts)
PANEL = "#0B1220"
WHITE, MUTED, GREEN, RED, BLUE = "#FFFFFF", "#C9D3E3", "#34D399", "#F87171", "#7DB4FF"
SUB_ACCENT = "#FFD60A"
LAYER_BG, LAYER_PANEL, LAYER_TXT, LAYER_SUB = -9, -8, -7, 5
SRC = {"ru": "Источник: ", "en": "Source: "}
COUNT_S = 0.9
STEPS = 14

KINDS = ("number", "bars", "line", "list", "compare", "fact", "flow", "icons")
FULL_ONLY = ("list", "compare", "fact", "flow", "icons")     # эти карточки всегда на весь кадр, видео под ними не нужно
# фон сцены (градиент ffmpeg) и акцентный цвет графики на нём
DARK_TEXT, DARK_MUTED = WHITE, MUTED
THEMES = {
    "dark": {"palettes": [("0x0b1220", "0x1e3a8a"), ("0x1a0b2e", "0x5b21b6"), ("0x04202b", "0x0f766e"), ("0x1f0b0b", "0x9a3412"),
                          ("0x111827", "0x374151"), ("0x0b1a14", "0x166534")],
             "accents": ["#FBBF24", "#FDE047", "#FDE047", "#FFE08A", "#34D399", "#FBBF24"],
             "text": "#FFFFFF", "muted": "#C9D3E3", "decor": "&HDA&"},
    "light": {"palettes": [("0xF8FAFC", "0xDBEAFE"), ("0xFFF7ED", "0xFED7AA"), ("0xF0FDF4", "0xBBF7D0"), ("0xFAF5FF", "0xE9D5FF"),
                           ("0xF8FAFC", "0xCBD5E1"), ("0xFEFCE8", "0xFDE68A")],
              "accents": ["#2563EB", "#EA580C", "#16A34A", "#9333EA", "#0F172A", "#B45309"],
              "text": "#0F172A", "muted": "#475569", "decor": "&HE6&"},
    "black": {"palettes": [("0x050505", "0x161616")] * 6,
              "accents": ["#FACC15"] * 6, "text": "#FFFFFF", "muted": "#A3A3A3", "decor": None},
}
THEME = "dark"
PALETTES = THEMES["dark"]["palettes"]
ACCENTS = THEMES["dark"]["accents"]


def set_theme(name: str) -> None:
    """Оформление карточек на весь кадр: dark (цветные тёмные фоны), light (светлые), black (чёрный минимализм)."""
    global THEME, PALETTES, ACCENTS
    THEME = name if name in THEMES else "dark"
    PALETTES = THEMES[THEME]["palettes"]
    ACCENTS = THEMES[THEME]["accents"]


# ---------- основа ----------

def _esc(s) -> str:
    return str(s).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _ev(a: float, b: float, layer: int, tags: str, body: str) -> str:
    return f"Dialogue: {layer},{_t(a)},{_t(b)},Prog,,0,0,0,,{{{tags}}}{body}"


def _head() -> str:
    return fontpack.head()


def _body() -> str:
    return fontpack.body()


def _w(text: str, size: float, head: bool = True) -> float:
    """Ширина строки; для запасного DejaVu - с поправкой (он шире Fira)."""
    k = 1.0 if fontpack.ensure() else 1.1
    return fontpack.width(str(text), size, head) * k


def _wrap(text: str, size: float, maxw: float, head: bool = True) -> list[str]:
    lines, cur = [], []
    for word in str(text).split():
        cand = " ".join(cur + [word])
        if cur and _w(cand, size, head) > maxw:
            lines.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        lines.append(" ".join(cur))
    return lines


def _balanced(text: str, size: float, maxw: float, head: bool = True) -> list[str]:
    """Перенос с ровными строками: без одинокого слова в последней строке."""
    lines = _wrap(text, size, maxw, head)
    n, w = len(lines), maxw
    while n > 1 and w > maxw * 0.45:
        cand = _wrap(text, size, w - 20, head)
        if len(cand) > n:
            break
        lines, w = cand, w - 20
    return lines


def _fit(text: str, size: float, maxw: float, head: bool = True, floor: float = 40) -> float:
    while size > floor and _w(text, size, head) > maxw:
        size -= 4
    return size


def _txt(a, b, x, y, text, size, color, head=True, an=5, delay=0.0, fad=(220, 180), rise=34, pop=False, alpha=None,
         fsp=0, layer=LAYER_TXT) -> str:
    """Текст, который выезжает снизу на rise пикселей и проявляется."""
    font = _head() if head else _body()
    tags = (f"\\an{an}\\fn{font}\\fs{size:.0f}\\b0\\bord0\\shad0\\c{_bgr(color)}\\q2"
            + (f"\\fsp{fsp}" if fsp else "")
            + (f"\\move({x:.0f},{y + rise:.0f},{x:.0f},{y:.0f},0,320)" if rise else f"\\pos({x:.0f},{y:.0f})")
            + (f"\\fad({fad[0]},{fad[1]})" if fad else "")
            + (f"\\alpha{alpha}" if alpha else "")
            + ("\\fscx86\\fscy86\\t(0,260,\\fscx100\\fscy100)" if pop else ""))
    return _ev(a + delay, b, layer, tags, _esc(text))


def _rect(w, h) -> str:
    return f"m 0 0 l {w:.0f} 0 {w:.0f} {h:.0f} 0 {h:.0f}"


def _shape(a, b, x, y, path, color, an=7, layer=LAYER_TXT, extra="", delay=0.0, alpha=None, p=1) -> str:
    return _ev(a + delay, b, layer, f"\\an{an}\\pos({x:.0f},{y:.0f})\\bord0\\shad0\\c{_bgr(color)}"
               + (f"\\alpha{alpha}" if alpha else "") + f"\\fad(160,180){extra}\\p{p}", path)


def _icon(a, b, x, y, name, size, color, delay=0.0, an=5, pop=True, layer=LAYER_TXT, alpha=None) -> list[str]:
    """Иконка Phosphor (символ шрифта). Пусто, если иконки нет или шрифт не установился."""
    ch = iconpack.resolve(name) if name else None
    if not ch or not fontpack.icons_ready():
        return []
    tags = (f"\\an{an}\\pos({x:.0f},{y:.0f})\\fn{iconpack.FAMILY}\\fs{size:.0f}\\b0\\bord0\\shad0\\c{_bgr(color)}"
            + (f"\\alpha{alpha}" if alpha else "") + "\\fad(160,180)"
            + ("\\fscx20\\fscy20\\t(0,220,\\fscx112\\fscy112)\\t(220,340,\\fscx100\\fscy100)" if pop else ""))
    return [_ev(a + delay, b, layer, tags, ch)]


def _icon_badge(a, b, x, y, name, r, acc, delay=0.0) -> list[str]:
    """Иконка в круглой подложке акцентного цвета."""
    ic = _icon(a, b, x, y, name, r * 1.3, DARK_TEXT, delay=delay + 0.05)
    if not ic:
        return []
    return [_shape(a, b, x, y, _rrect(2 * r, 2 * r, r), acc, an=5, alpha="&H30&", delay=delay, p=4,
                   extra="\\fscx20\\fscy20\\t(0,220,\\fscx108\\fscy108)\\t(220,320,\\fscx100\\fscy100)")] + ic


def _fmt_num(v: float, lang: str = "ru") -> str:
    s = f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", " ")
    return s.replace(".", ",") if lang == "ru" else s


def _with_unit(txt: str, unit: str) -> str:
    if not unit or any(ch in txt for ch in "%$€£₽"):
        return txt
    if unit in ("$", "€", "£", "₽"):
        return f"{unit}{txt}"
    return f"{txt}{unit}" if unit == "%" else f"{txt} {unit}"


def split_number(value: str):
    """'$1,250.5M' -> ('$', 1250.5, 1, ',', '.', 'M'); None, если в значении нет числа."""
    m = re.search(r"(\d{1,3}(?:[,\s]\d{3})+|\d+)(?:([.,])(\d+))?", value)
    if not m:
        return None
    intp, dec_sep, dec = m.group(1), m.group(2), m.group(3) or ""
    group_sep = next((ch for ch in intp if not ch.isdigit()), "")
    num = float(re.sub(r"\D", "", intp) + ("." + dec if dec else ""))
    return value[:m.start()], num, len(dec), group_sep, dec_sep or ".", value[m.end():]


def _fmt_count(num: float, decimals: int, group_sep: str, dec_sep: str) -> str:
    s = f"{num:,.{decimals}f}"
    s = s.replace(",", "\x00").replace(".", dec_sep).replace("\x00", group_sep or ",")
    return s if group_sep or num < 1000 else s.replace(",", "")


def _count_up(a, b, x, y, value, size, color, an=5, layer=LAYER_TXT) -> list[str]:
    """Число набегает от нуля за COUNT_S секунд (ширина цифр в Fira одинаковая, текст не прыгает)."""
    parts = split_number(value)
    if re.fullmatch(r"\s*1[0-9]{3}\s*|\s*20[0-9]{2}\s*", value):   # год не «набегает» от нуля
        parts = None
    t0 = a + 0.2
    font = _head()
    base = f"\\an{an}\\pos({x:.0f},{y:.0f})\\fn{font}\\fs{size:.0f}\\b0\\bord0\\shad0\\c{_bgr(color)}\\q2"
    if not parts or parts[1] == 0 or b - t0 < COUNT_S + 0.4:
        return [_ev(a, b, layer, base + "\\fad(200,180)\\fscx84\\fscy84\\t(0,260,\\fscx100\\fscy100)", _esc(value))]
    pre, num, dec, gs, ds, suf = parts
    ev = []
    for k in range(STEPS):
        p = (k + 1) / STEPS
        cur = num * (1 - (1 - p) ** 3)
        ta, tb = t0 + COUNT_S * k / STEPS, t0 + COUNT_S * (k + 1) / STEPS
        last = k == STEPS - 1
        txt = value if last else pre + _fmt_count(cur, dec, gs, ds) + suf
        ev.append(_ev(ta if k else a, b if last else tb, layer,
                      base + ("\\fad(160,0)" if k == 0 else "") + ("\\fad(0,180)\\fscx94\\fscy94\\t(0,180,\\fscx100\\fscy100)" if last else ""),
                      _esc(txt)))
    return ev


def _kicker(a, b, y, label, accent, maxw=PW - 60, size=56) -> tuple[list[str], float]:
    """Подпись над графикой: короткая полоска акцента и текст в 1-2 строки."""
    if not label:
        return [], 0.0
    lines = _wrap(label, size, maxw, head=False)[:2]
    ev = [_shape(a, b, CX, y, _rect(90, 8), accent, an=8)]
    for k, ln in enumerate(lines):
        ev.append(_txt(a, b, CX, y + 52 + k * (size * 1.25), ln, size, MUTED, head=False, delay=0.05))
    return ev, 30 + len(lines) * size * 1.25 + 26


def _source(a, b, y, src: str, lang: str) -> list[str]:
    if not src:
        return []
    return [_txt(a, b, CX, y, SRC.get(lang, SRC["en"]) + src, 34, MUTED, head=False, delay=0.3, alpha="&H40&", rise=0)]


def _panel(a, b, y0, h, accent) -> list[str]:
    return [
        _ev(a, b, LAYER_PANEL, f"\\an7\\pos({PX},{y0:.0f})\\bord0\\shad0\\c{_bgr(PANEL)}\\alpha&H30&\\fad(220,200)\\p4", _rrect(PW, h, 36)),
        _ev(a, b, LAYER_PANEL, f"\\an7\\pos({PX + 30},{y0 + 32:.0f})\\bord0\\shad0\\c{_bgr(accent)}\\fad(220,200)\\p1", _rect(8, h - 64)),
    ]


# ---------- виды графики: каждая функция возвращает (события, высота блока) ----------

def _number(a, b, y0, st, lang, acc, full) -> tuple[list[str], float]:
    value = str(st["value"])
    size = _fit(value, 340 if full else 190, PW - (40 if full else 140))
    ev, kh = _kicker(a, b, y0, st.get("label", ""), acc) if full else ([], 0)
    if full and st.get("icon"):
        ic = _icon(a, b, CX, y0 + kh + 110, st["icon"], 210, acc)
        if ic:
            ev += ic
            kh += 230
    if not full and st.get("label"):
        lines = _wrap(st["label"], 42, PW - 140, head=False)[:2]
        ev += [_txt(a, b, CX, y0 + 48 + k * 52, ln, 42, MUTED, head=False) for k, ln in enumerate(lines)]
        kh = 30 + len(lines) * 52
    vy = y0 + kh + size * 0.55
    ev += _count_up(a, b, CX, vy, value, size, acc if full else WHITE)
    y = vy + size * 0.55 + 16
    delta = str(st.get("delta") or "")
    if delta:
        up = not delta.strip().startswith(("-", "−"))
        txt = ("▲ " if up else "▼ ") + delta.lstrip("+-−")
        dw = _w(txt, 50) + 56
        col = GREEN if up else RED
        ev.append(_shape(a, b, CX, y + 4, _rrect(dw, 76, 38), col, an=8, alpha="&H90&", delay=0.8, p=4))
        ev.append(_txt(a, b, CX, y + 42, txt, 50, col, delay=0.8, rise=0))
        y += 100
    if st.get("source"):
        ev += _source(a, b, y + 28, st["source"], lang)
        y += 56
    return ev, y - y0 + 20


def _bars(a, b, y0, st, lang, acc, full) -> tuple[list[str], float]:
    items = st["items"]
    unit = st.get("unit", "")
    if full:
        ev, kh = _kicker(a, b, y0, st.get("label", ""), acc)
    else:
        lines = _wrap(st.get("label", ""), 42, PW - 140, head=False)[:2] if st.get("label") else []
        ev = [_txt(a, b, CX, y0 + 48 + k * 52, ln, 42, MUTED, head=False) for k, ln in enumerate(lines)]
        kh = 30 + len(lines) * 52
    pitch, bh, lab = (205, 92, 54) if full else (96, 46, 36)
    vmax = max(float(it["value"]) for it in items) or 1.0
    vsize = 78 if full else 44
    texts = [it.get("text") or _with_unit(_fmt_num(float(it["value"]), lang), unit) for it in items]
    left = PX + (30 if full else 70)
    max_len = PW - (left - PX) - max(_w(t, vsize) for t in texts) - 60
    top = y0 + kh + 10
    for k, (it, txt) in enumerate(zip(items, texts)):
        v = float(it["value"])
        col = acc if k == len(items) - 1 else WHITE
        alpha = None if k == len(items) - 1 else "&H50&"
        ln = max(10.0, max_len * v / vmax)
        d = 0.25 + 0.18 * k
        ry = top + k * pitch
        ev.append(_txt(a, b, left, ry + lab * 0.6, str(it["label"]), lab, MUTED, head=False, an=4, delay=d, rise=0))
        by = ry + lab * 1.25
        ev.append(_ev(a + d, b, LAYER_TXT, f"\\an7\\pos({left:.0f},{by:.0f})\\bord0\\shad0\\c{_bgr(col)}"
                      + (f"\\alpha{alpha}" if alpha else "") + "\\fscx2\\t(0,650,0.6,\\fscx100)\\fad(120,180)\\p4",
                      _rrect(ln, bh, min(14, bh / 2))))
        ev.append(_txt(a, b, left + ln + 22, by + bh / 2, txt, vsize, col if k == len(items) - 1 else WHITE, an=4,
                       delay=d + 0.5, rise=0, pop=True))
    y = top + len(items) * pitch
    if st.get("source"):
        ev += _source(a, b, y + 10, st["source"], lang)
        y += 50
    return ev, y - y0 + 10


def _line(a, b, y0, st, lang, acc, full) -> tuple[list[str], float]:
    vals = [float(v) for v in st["values"]]
    unit = st.get("unit", "")
    labels = st.get("labels") or []
    if full:
        ev, kh = _kicker(a, b, y0, st.get("label", ""), acc)
    else:
        lines = _wrap(st.get("label", ""), 42, PW - 140, head=False)[:2] if st.get("label") else []
        ev = [_txt(a, b, CX, y0 + 48 + k * 52, ln, 42, MUTED, head=False) for k, ln in enumerate(lines)]
        kh = 30 + len(lines) * 52
    plot_w, plot_h = (880, 540) if full else (800, 300)
    px0 = CX - plot_w / 2
    px1 = px0 + plot_w
    py0 = y0 + kh + 90
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pts = [(px0 + plot_w * i / (len(vals) - 1), py0 + plot_h * (1 - (v - lo) / span)) for i, v in enumerate(vals)]
    th = 6 if full else 5
    rel = [(x - px0, y - py0) for x, y in pts]
    top_s = " ".join(f"{x:.0f} {y - th:.0f}" for x, y in rel)
    bot_s = " ".join(f"{x:.0f} {y + th:.0f}" for x, y in reversed(rel))
    area = f"m {rel[0][0]:.0f} {plot_h:.0f} l " + " ".join(f"{x:.0f} {y:.0f}" for x, y in rel) + f" {rel[-1][0]:.0f} {plot_h:.0f}"
    clip0 = f"\\clip({px0 - 30:.0f},{py0 - 60:.0f},{px0 - 30:.0f},{py0 + plot_h + 20:.0f})"
    clip1 = f"\\clip({px0 - 30:.0f},{py0 - 60:.0f},{px1 + 40:.0f},{py0 + plot_h + 20:.0f})"
    reveal = f"{clip0}\\t(0,1200,0.7,{clip1})"
    ev.append(_shape(a, b, px0, py0 + plot_h, _rect(plot_w, 3), WHITE, alpha="&HB0&"))
    ev.append(_ev(a + 0.2, b, LAYER_TXT, f"\\an7\\pos({px0:.0f},{py0:.0f})\\bord0\\shad0\\c{_bgr(acc)}\\alpha&HC8&{reveal}\\fad(80,180)\\p1", area))
    ev.append(_ev(a + 0.2, b, LAYER_TXT, f"\\an7\\pos({px0:.0f},{py0:.0f})\\bord0\\shad0\\c{_bgr(acc)}{reveal}\\fad(80,180)\\p1",
                  f"m {rel[0][0]:.0f} {rel[0][1] - th:.0f} l {top_s} {bot_s}"))
    r = 15 if full else 11
    ev.append(_ev(a + 1.3, b, LAYER_TXT, f"\\an5\\pos({pts[-1][0]:.0f},{pts[-1][1]:.0f})\\bord0\\shad0\\c{_bgr(acc)}"
                  "\\fscx10\\fscy10\\t(0,200,\\fscx100\\fscy100)\\fad(0,180)\\p4", _rrect(2 * r, 2 * r, r)))
    first_t = _with_unit(_fmt_num(vals[0], lang), unit)
    last_t = _with_unit(_fmt_num(vals[-1], lang), unit)
    fs1, fs2 = (48, 92) if full else (34, 56)
    ev.append(_txt(a, b, px0 + 6, max(pts[0][1] - 44, py0 - 40), first_t, fs1, MUTED, head=False, an=1, delay=0.3, rise=0))
    ev.append(_txt(a, b, px1, max(pts[-1][1] - 30, py0 - 50), last_t, fs2, acc, an=3, delay=1.3, rise=0, pop=True))
    y = py0 + plot_h
    if labels:
        ev.append(_txt(a, b, px0, y + 40, str(labels[0]), 36, MUTED, head=False, an=4, delay=0.2, rise=0))
        ev.append(_txt(a, b, px1, y + 40, str(labels[-1]), 36, MUTED, head=False, an=6, delay=0.2, rise=0))
        y += 70
    if st.get("source"):
        ev += _source(a, b, y + 24, st["source"], lang)
        y += 56
    return ev, y - y0 + 10


def _compare(a, b, y0, st, lang, acc, full=True) -> tuple[list[str], float]:
    L, R = st["left"], st["right"]
    ev, kh = _kicker(a, b, y0, st.get("label", ""), acc)
    colw = 400
    xl, xr = CX - 250, CX + 250
    size = min(_fit(str(L["value"]), 200, colw), _fit(str(R["value"]), 200, colw))
    top = y0 + kh + 20
    for item, col, x, d in ((L, WHITE, xl, 0.2), (R, acc, xr, 0.7)):
        ev.append(_txt(a, b, x, top + 30, item["label"], 52, MUTED, head=False, delay=d))
        ev.append(_txt(a, b, x, top + 70 + size * 0.55, item["value"], size, col, delay=d + 0.1, pop=True, rise=0))
    ev.append(_txt(a, b, CX, top + 70 + size * 0.55, "→", 90, MUTED, delay=0.5, rise=0, alpha="&H30&"))
    y = top + 70 + size * 1.1 + 20
    pl, pr = split_number(str(L["value"])), split_number(str(R["value"]))
    if pl and pr and pl[1] > 0:
        ch = (pr[1] - pl[1]) / pl[1] * 100
        txt = f"{'+' if ch >= 0 else '−'}{abs(ch):.0f}%"
        dw = _w(txt, 56) + 60
        ev.append(_shape(a, b, CX, y, _rrect(dw, 84, 42), acc, an=8, alpha="&H80&", delay=1.0, p=4))
        ev.append(_txt(a, b, CX, y + 43, txt, 56, WHITE, delay=1.0, rise=0, pop=True))
        y += 110
    if st.get("source"):
        ev += _source(a, b, y + 20, st["source"], lang)
        y += 50
    return ev, y - y0 + 10


def _list(a, b, y0, st, lang, acc, full=True) -> tuple[list[str], float]:
    items = st["items"]
    ev, kh = _kicker(a, b, y0, st.get("label", ""), acc)
    size = 82
    rows = [_wrap(it, size, PW - 220)[:2] for it in items]
    if any(len(_wrap(it, size, PW - 220)) > 2 for it in items):
        size = 66
        rows = [_wrap(it, size, PW - 200)[:2] for it in items]
    heights = [max(200, len(r) * size * 1.18 + 70) for r in rows]
    y = y0 + kh + 10
    step = min(1.1, max(0.45, (b - a - 1.0) / max(len(items), 1)))
    for k, (ln, rh) in enumerate(zip(rows, heights)):
        d = 0.2 + step * k
        cy = y + rh / 2
        icon = (st.get("icons") or [None] * len(items))[k]
        ic = _icon(a, b, PX + 90, cy, icon, 150, acc, delay=d) if icon else []
        ev += ic or [_txt(a, b, PX + 40, cy, str(k + 1), 130, acc, an=4, delay=d, rise=0, pop=True)]
        tx = PX + (210 if ic else 160)
        for j, t in enumerate(ln):
            ev.append(_txt(a, b, tx, cy + (j - (len(ln) - 1) / 2) * size * 1.18, t, size, WHITE, an=4, delay=d + 0.1))
        y += rh
    return ev, y - y0


def _fact(a, b, y0, st, lang, acc, full=True) -> tuple[list[str], float]:
    text = str(st["text"])
    size = 166 if len(text) <= 22 else 136
    while size > 64 and len(_wrap(text, size, PW - 40)) > 4:
        size -= 6
    lines = _balanced(text, size, PW - 40)
    pitch = size * 1.16
    acc_word = str(st.get("accent") or "").strip().lower()
    ev = []
    if st.get("icon"):
        ic = _icon(a, b, CX, y0 + 150, st["icon"], 300, acc)
        if ic:
            ev += ic
            y0 += 330
    for k, ln in enumerate(lines):
        y = y0 + k * pitch + pitch / 2
        if acc_word and acc_word in ln.lower():
            i = ln.lower().index(acc_word)
            body = (_esc(ln[:i]) + f"{{\\c{_bgr(acc)}}}" + _esc(ln[i:i + len(acc_word)]) + f"{{\\c{_bgr(WHITE)}}}"
                    + _esc(ln[i + len(acc_word):]))
        else:
            body = _esc(ln)
        ev.append(_ev(a + 0.12 * k, b, LAYER_TXT,
                      f"\\an5\\fn{_head()}\\fs{size}\\b0\\bord0\\shad0\\c{_bgr(WHITE)}\\q2"
                      f"\\move({CX:.0f},{y + 50:.0f},{CX:.0f},{y:.0f},0,340)\\fad(240,180)", body))
    h = len(lines) * pitch
    ev.append(_shape(a, b, CX, y0 + h + 30, _rect(120, 10), acc, an=8, delay=0.35))
    h += 50 + (330 if st.get("icon") and fontpack.icons_ready() and iconpack.resolve(st["icon"]) else 0)
    if st.get("label"):
        ev.append(_txt(a, b, CX, y0 + h + 30, st["label"], 38, MUTED, head=False, delay=0.4))
        h += 70
    return ev, h


def _flow(a, b, y0, st, lang, acc, full=True) -> tuple[list[str], float]:
    """Цепочка «причина -> следствие»: 2-4 шага с иконками, стрелки, шаги появляются по очереди."""
    steps = st["steps"]
    ev, kh = _kicker(a, b, y0, st.get("label", ""), acc)
    n = len(steps)
    row = 300 if n <= 3 else 240
    r = 98 if n <= 3 else 80
    size = 86 if n <= 3 else 74
    y = y0 + kh + 20
    step_t = min(1.2, max(0.4, (b - a - 0.8) / n))
    for k, stp in enumerate(steps):
        d = 0.15 + step_t * k
        cy = y + r
        badge = _icon_badge(a, b, PX + 40 + r, cy, stp.get("icon"), r, acc, delay=d)
        ev += badge or [_shape(a, b, PX + 40 + r, cy, _rrect(2 * r, 2 * r, r), acc, an=5, alpha="&H30&", delay=d, p=4),
                        _txt(a, b, PX + 40 + r, cy, str(k + 1), r * 1.1, WHITE, delay=d, rise=0, pop=True)]
        lines = _wrap(stp["text"], size, PW - 2 * r - 90)[:2]
        for j, t in enumerate(lines):
            ev.append(_txt(a, b, PX + 2 * r + 80, cy + (j - (len(lines) - 1) / 2) * size * 1.15, t, size,
                           acc if k == n - 1 else WHITE, an=4, delay=d + 0.1))
        if k < n - 1:
            ev.append(_txt(a, b, PX + 40 + r, cy + r + (row - 2 * r) / 2, "↓", 72, MUTED, delay=d + step_t * 0.6, rise=-20, alpha="&H40&"))
        y += row
    return ev, y - y0


def _icons(a, b, y0, st, lang, acc, full=True) -> tuple[list[str], float]:
    """1-3 крупные иконки с подписями (и значениями): «что на кону», герои истории, предметы."""
    items = st["items"]
    ev, kh = _kicker(a, b, y0, st.get("label", ""), acc)
    n = len(items)
    size = {1: 460, 2: 320, 3: 240}[n]
    xs = {1: [CX], 2: [CX - 250, CX + 250], 3: [CX - 325, CX, CX + 325]}[n]
    top = y0 + kh + 20
    h = 0.0
    for k, (it, x) in enumerate(zip(items, xs)):
        d = 0.2 + 0.35 * k
        col = acc if (n == 1 or k == n - 1) else WHITE
        ev += _icon(a, b, x, top + size * 0.55, it.get("icon"), size, col, delay=d)
        y = top + size * 1.12
        if it.get("value"):
            vs = _fit(str(it["value"]), 140 if n == 1 else 96, (PW / n) - 30)
            ev.append(_txt(a, b, x, y + vs * 0.5, it["value"], vs, col, delay=d + 0.2, rise=0, pop=True))
            y += vs * 1.05
        if it.get("label"):
            ls = 62 if n == 1 else 50
            for j, t in enumerate(_wrap(it["label"], ls, (PW / n) - 30, head=False)[:2]):
                ev.append(_txt(a, b, x, y + 30 + j * ls * 1.2, t, ls, MUTED, head=False, delay=d + 0.25))
            y += 30 + 2 * ls * 1.2
        h = max(h, y - y0)
    return ev, h


def _decor(a, b, variant, static: bool = False) -> list[str]:
    """Три мягких круга на фоне (дрейфуют или стоят - для запечённого фона карточки)."""
    alpha = THEMES[THEME]["decor"]
    if not alpha:
        return []
    ev = []
    pal = [BLUE, "#FBBF24", GREEN, "#A78BFA"]
    for k in range(3):
        r = 230 + 90 * ((variant + k) % 3)
        x0, y0 = (140 + 380 * k + 70 * variant) % 1000, 300 + 560 * k
        dx, dy = (90 if (variant + k) % 2 else -90), (-130 if k % 2 else 130)
        pos = f"\\pos({x0},{y0})" if static else f"\\move({x0},{y0},{x0 + dx},{y0 + dy})"
        ev.append(_ev(a, b, LAYER_BG, f"\\an5{pos}\\bord0\\shad0\\blur45\\c{_bgr(pal[(variant + k) % 4])}"
                      f"\\alpha{alpha}" + ("" if static else "\\fad(300,300)") + "\\p4", _rrect(2 * r, 2 * r, r)))
    return ev


FNS = {"number": _number, "bars": _bars, "line": _line, "fact": _fact, "list": _list, "compare": _compare, "flow": _flow,
       "icons": _icons}


def is_full(st: dict | None) -> bool:
    return bool(st) and (bool(st.get("full")) or st.get("kind") in FULL_ONLY)


def decor_static(variant: int) -> list[str]:
    """Фон карточки один раз: круги без движения (движение даёт наезд камеры на всю сцену)."""
    return _decor(0.0, 5.0, variant, static=True)


def events(st: dict, a: float, b: float, lang: str = "ru", variant: int = 0, decor: bool = True) -> list[str]:
    """Строки Dialogue для ASS. a, b - начало и конец показа в секундах; variant - номер оформления (фон и акцент)."""
    global WHITE, MUTED
    if b - a < 1.0:
        return []
    fn = FNS[st["kind"]]
    acc = ACCENTS[variant % len(ACCENTS)]
    if not is_full(st):                      # плашка поверх видео, в верхней части кадра
        body, h = fn(a, b, 200, st, lang, WHITE if st["kind"] == "number" else acc, False)
        return _panel(a, b, 180, h + 40, acc) + body
    WHITE, MUTED = THEMES[THEME]["text"], THEMES[THEME]["muted"]
    try:
        h = fn(a, b, TOP, st, lang, acc, True)[1]
        y0 = max(TOP - 40, TOP + (BOT - TOP - h) / 2)
        return (_decor(a, b, variant) if decor else []) + fn(a, b, y0, st, lang, acc, True)[0]
    finally:
        WHITE, MUTED = DARK_TEXT, DARK_MUTED


# ---------- субтитры ----------

def subtitle_events(groups: list[dict], upper: bool = True, accent: str = SUB_ACCENT, y: float = SUB_Y, scale: float = 1.0,
                    style: str = "hl") -> list[str]:
    """groups: [{"a": начало, "b": конец, "words": [(время, слово), ...]}]. style: hl - текущее слово подсвечено,
    yellow - все жёлтые, plain - все белые. Группа «выпрыгивает»."""
    ev = []
    font = _head()
    white, acc = _bgr(DARK_TEXT), _bgr(accent)
    if style == "yellow":
        white = acc
    elif style == "plain":
        acc = white
    for g in groups:
        a, b, words = g["a"], g["b"], g["words"]
        if not words or b - a < 0.05:
            continue
        shown = [w.upper() if upper else w for _, w in words]
        size = _fit(" ".join(shown), 86 * scale, PW - 40, floor=56 * scale)
        lines = [list(range(len(shown)))]
        if _w(" ".join(shown), size) > PW - 40 and len(shown) > 1:
            cut = (len(shown) + 1) // 2
            lines = [list(range(cut)), list(range(cut, len(shown)))]
        parts = []
        for li, idxs in enumerate(lines):
            seg = []
            for i in idxs:
                ms = max(0, int(round((words[i][0] - a) * 1000)))
                nxt = int(round(((words[i + 1][0] if i + 1 < len(words) else b) - a) * 1000))
                if ms <= 20:
                    tag = f"\\c{acc}" + (f"\\t({nxt},{nxt + 1},\\c{white})" if i + 1 < len(words) else "")
                else:
                    tag = f"\\c{white}\\t({ms},{ms + 1},\\c{acc})" + (f"\\t({nxt},{nxt + 1},\\c{white})" if i + 1 < len(words) else "")
                seg.append(f"{{{tag}}}{_esc(shown[i])}")
            parts.append(" ".join(seg))
        base = (f"\\an5\\pos({CX:.0f},{y:.0f})\\fn{font}\\fs{size:.0f}\\b0\\bord7\\3c&H000000&\\shad4\\4c&H000000&\\4a&H60&\\q2"
                "\\fscx88\\fscy88\\t(0,110,\\fscx100\\fscy100)")
        ev.append(_ev(a, b, LAYER_SUB, base, "\\N".join(parts)))
    return ev


def calm_subtitle_events(groups: list[dict], y: float = SUB_Y, scale: float = 1.0) -> list[str]:
    """Спокойные субтитры: до двух строк обычным регистром, белые, без подсветки слова и без «прыжков».
    groups - как у subtitle_events, но крупнее (до 8 слов)."""
    ev = []
    font = _head()
    size = 64 * scale
    for g in groups:
        a, b, words = g["a"], g["b"], g["words"]
        if not words or b - a < 0.05:
            continue
        text = " ".join(w for _, w in words)
        lines = _balanced(text, size, PW - 60)[:2] if _w(text, size) > PW - 60 else [text]
        tags = (f"\\an5\\pos({CX:.0f},{y:.0f})\\fn{font}\\fs{size:.0f}\\b0\\bord4\\3c&H000000&\\shad3\\4c&H000000&\\4a&H70&"
                f"\\c{_bgr(DARK_TEXT)}\\q2\\fad(60,40)")
        ev.append(_ev(a, b, LAYER_SUB, tags, "\\N".join(_esc(x) for x in lines)))
    return ev


def _stem5(w: str) -> str:
    return re.sub(r"[^\wё]", "", w.lower())[:5]


def kinetic_events(words: list[tuple[float, str]], end: float, accents: list[str], accent: str = SUB_ACCENT,
                   cy: float = 860, layer: int = LAYER_TXT) -> list[str]:
    """Крупный кинетический текст вместо ведущего: слова диктора проявляются по одному в центре кадра, ключевые - своей
    страницей, крупнее и цветом. words: [(время от начала сцены, слово)], end - конец сцены (с), accents - ключевые слова.
    Страница - одна строка ASS: слова ещё не сказанные прозрачны и проявляются в свой момент (расстояния между словами
    ставит сам libass, поэтому они ровные)."""
    acc_stems = {_stem5(x) for a in accents for x in str(a).split() if _stem5(x)}
    font = _head()
    pages: list[list[tuple[float, str, bool]]] = []
    cur: list[tuple[float, str, bool]] = []
    for t, w in words:
        brk = w[-1:] in ".!?…"
        w = re.sub(r"[,.;:…—–-]+$", "", w.strip("«»\"'“”„()[]")) or w
        if not w or not re.search(r"\w", w):
            continue
        is_acc = _stem5(w) in acc_stems
        if cur and (is_acc != cur[-1][2] or len(cur) >= 6):
            pages.append(cur)
            cur = []
        cur.append((t, w, is_acc))
        if brk:
            pages.append(cur)
            cur = []
    if cur:
        pages.append(cur)
    ev = []
    white, acc = _bgr("#FFFFFF"), _bgr(accent)
    for pi, page in enumerate(pages):
        a = page[0][0]
        b = (pages[pi + 1][0][0] - 0.02) if pi + 1 < len(pages) else end
        if b - a < 0.08:
            continue
        hot = all(ac for _, _, ac in page)
        size = 150 if hot else 100
        shown = [w.upper() if hot else w for _, w, _ in page]
        text = " ".join(shown)
        while size > 60 and max(_w(w, size) for w in shown) > PW:
            size -= 6
        lines = _balanced(text, size, PW) if _w(text, size) > PW else [text]
        out, k = [], 0
        for li, line in enumerate(lines):
            seg = []
            for _ in line.split():
                t, _w0, ac = page[k]
                ms = max(0, int(round((t - a) * 1000)))
                col = acc if (ac or hot) else white
                seg.append(f"{{\\c{col}\\alpha&HFF&\\t({ms},{ms + 140},\\alpha&H00&)}}{_esc(shown[k])}")
                k += 1
            out.append(" ".join(seg))
        tags = (f"\\an5\\pos({CX:.0f},{cy:.0f})\\fn{font}\\fs{size:.0f}\\b0\\bord0\\shad5\\4c&H000000&\\4a&H40&\\q2"
                "\\fscx92\\fscy92\\t(0,180,\\fscx100\\fscy100)")
        ev.append(_ev(a, b, layer, tags, "\\N".join(out)))
    return ev


# ---------- проверка поля stat из сценария ----------

def st_has_numbers(st: dict) -> bool:
    """Нужна ли проверка источника: в обычной фразе-утверждении и списке шагов цифр может не быть."""
    txt = " ".join([str(st.get("text", "")), *map(str, st.get("items", [])), *[x.get("text", "") for x in st.get("steps", [])]])
    return bool(re.search(r"\d", txt))


def normalize(raw, where: str, warn: list[str]) -> dict | None:
    """Проверяет stat из сценария. None - показывать нечего (причина в warn)."""
    if not isinstance(raw, dict):
        warn.append(f"{where}: stat должен быть объектом, пропущен")
        return None
    kind = raw.get("kind") or ("bars" if raw.get("items") else "line" if raw.get("values") else "number")
    if kind not in KINDS:
        warn.append(f"{where}: stat.kind «{kind}» неизвестен (number, bars, line, list, compare, fact), пропущен")
        return None
    out = {"kind": kind, "label": str(raw.get("label") or "").strip()[:80], "source": str(raw.get("source") or "").strip()[:48],
           "unit": str(raw.get("unit") or "").strip()[:3], "full": bool(raw.get("full"))}
    if kind == "number":
        val = str(raw.get("value") if raw.get("value") is not None else "").strip()
        if not val or len(val) > 16:
            warn.append(f"{where}: stat.value пуст или длиннее 16 знаков, пропущен")
            return None
        out["value"] = val
        delta = str(raw.get("delta") or "").strip()
        if delta:
            out["delta"] = delta[:16]
    elif kind == "bars":
        items = []
        for it in (raw.get("items") or [])[:5]:
            try:
                items.append({"label": str(it.get("label", ""))[:16], "value": float(it["value"]),
                              **({"text": str(it["text"])[:12]} if it.get("text") else {})})
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
        if len(items) < 2 or any(i["value"] < 0 for i in items):
            warn.append(f"{where}: для bars нужно 2-5 столбцов вида {{label, value}} (value не меньше 0), пропущен")
            return None
        out["items"] = items
    elif kind == "line":
        try:
            vals = [float(v) for v in (raw.get("values") or [])][:14]
        except (TypeError, ValueError):
            vals = []
        if len(vals) < 3:
            warn.append(f"{where}: для line нужно 3-14 чисел в values, пропущен")
            return None
        out["values"] = vals
        out["labels"] = [str(x)[:10] for x in (raw.get("labels") or [])][:14]
    elif kind == "fact":
        text = str(raw.get("text") or "").strip()
        if not 3 <= len(text) <= 70:
            warn.append(f"{where}: stat.text для fact нужен от 3 до 70 знаков (это короткий вывод, а не фраза диктора), пропущен")
            return None
        out["text"] = text
        if raw.get("accent"):
            out["accent"] = str(raw["accent"])[:30]
    elif kind == "list":
        items, icons = [], []
        for x in (raw.get("items") or [])[:4]:
            text = str(x.get("text", "") if isinstance(x, dict) else x).strip()[:60]
            if text:
                items.append(text)
                icons.append(str(x.get("icon", "")).strip() if isinstance(x, dict) else "")
        if len(items) < 2:
            warn.append(f"{where}: для list нужно 2-4 коротких пункта в items, пропущен")
            return None
        out["items"] = items
        if any(icons):
            out["icons"] = icons
    elif kind == "flow":
        steps = []
        for x in (raw.get("steps") or raw.get("items") or [])[:4]:
            text = str(x.get("text", "") if isinstance(x, dict) else x).strip()[:40]
            if text:
                steps.append({"text": text, "icon": str(x.get("icon", "")).strip() if isinstance(x, dict) else ""})
        if len(steps) < 2:
            warn.append(f"{where}: для flow нужно 2-4 шага вида {{text, icon}}, пропущен")
            return None
        out["steps"] = steps
    elif kind == "icons":
        items = []
        for x in (raw.get("items") or [])[:3]:
            if isinstance(x, dict) and x.get("icon"):
                val = str(x.get("value") or "").strip()
                label = str(x.get("label", "")).strip()[:40]
                if val and not re.search(r"\d", val):        # текст вместо числа - это подпись, а не значение
                    label, val = (label + " " + val).strip()[:40], ""
                items.append({"icon": str(x["icon"]).strip(), "label": label, **({"value": val[:20]} if val else {})})
        if not items:
            warn.append(f"{where}: для icons нужно 1-3 элемента вида {{icon, label}}, пропущен")
            return None
        out["items"] = items
    elif kind == "compare":
        sides = []
        for k in ("left", "right"):
            it = raw.get(k)
            if not isinstance(it, dict) or it.get("value") in (None, ""):
                warn.append(f"{where}: для compare нужны left и right вида {{label, value}}, пропущен")
                return None
            sides.append({"label": str(it.get("label", ""))[:20], "value": str(it["value"])[:12]})
        out["left"], out["right"] = sides
    if raw.get("icon") and kind in ("fact", "number"):
        out["icon"] = str(raw["icon"]).strip()[:40]
    for name in [out.get("icon"), *(out.get("icons") or []), *[x.get("icon") for x in out.get("steps", []) + (out["items"] if kind == "icons" else [])]]:
        if name and not iconpack.exists(name):
            warn.append(f"{where}: иконки «{name}» нет, будет без неё (простые английские слова: bread, bank, house, sword, printer)")
    if kind in FULL_ONLY:
        out["full"] = True
    if (kind in ("fact", "list", "flow") or (kind == "icons" and not any(i.get("value") for i in out["items"]))
            or (kind == "compare" and not re.search(r"\d", out["left"]["value"] + out["right"]["value"]))) and not st_has_numbers(out):
        return out
    if not out["source"]:
        warn.append(f"{where}: у цифры нет source - проверьте её достоверность до публикации")
    return out
