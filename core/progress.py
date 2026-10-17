"""Полоска прогресса снизу кадра: растёт ровно на всю длительность ролика, в конце «достаёт» кнопки лайка и подписки.

Рисуется векторной графикой ASS (то же самое, что субтитры и хук), поэтому не требует картинок и лишних входов ffmpeg:
события добавляются в subs.ass. Три дизайна: line (тонкая линия), capsule (капсула), neon (светящиеся сегменты).
Иконки лайка и колокольчика - контуры Material Icons (Apache License 2.0), переведённые в команды ASS.
"""
import re

from common.looks import PROGRESS_COLORS, PROGRESS_DEFAULT

W, H = 1080, 1920
SCALE = 8            # \p4: координаты рисунка делятся на 8, поэтому пишем их в 8 раз крупнее (дробные пиксели без дробей)
KAPPA = 0.5523       # круглые углы из кубических кривых

# вертикальное положение центра полоски: у самого низа или выше интерфейса Shorts (подписи канала и описание)
BAR_Y = {"bottom": 1858, "high": 1610}

LABELS = {   # (до нажатия, после нажатия)
    "ru": ("ПОДПИСАТЬСЯ", "ПОДПИСАН"), "en": ("SUBSCRIBE", "SUBSCRIBED"), "de": ("ABONNIEREN", "ABONNIERT"),
    "es": ("SUSCRIBIRSE", "SUSCRITO"), "fr": ("S'ABONNER", "ABONNÉ"),
}

# контуры иконок Material Icons 24x24: thumb_up и notifications
ICON_THUMB = ("M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59"
              "C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z")
ICON_BELL = ("M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5"
             "s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z")

_TOKEN = re.compile(r"[MmLlHhVvCcSsZz]|-?(?:\d+\.?\d*|\.\d+)")


# ---------- геометрия ----------

def _n(v: float) -> int:
    return int(round(v * SCALE))


def _svg_to_ass(d: str, size: float, cx: float = 12.0, cy: float = 12.0) -> str:
    """Контур SVG (M L H V C S Z, абсолютные и относительные) -> путь ASS высотой ~size пикселей.
    libass неверно выравнивает рисунки с отрицательными координатами, поэтому контур сдвигается так,
    чтобы его рамка начиналась в (0,0); с \\an5 рисунок тогда центрируется ровно в точке \\pos."""
    toks = _TOKEN.findall(d)
    k = size / 24.0
    segs: list[tuple[str, list[tuple[float, float]]]] = []
    i, cmd = 0, ""
    x = y = sx = sy = 0.0
    lc = None   # последняя вторая контрольная точка для S

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                x, y = sx, sy
                lc = None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            sx, sy = x, y
            segs.append(("m", [(x, y)]))
            cmd = "l" if rel else "L"   # дальнейшие пары - линии
            lc = None
        elif c in "LHV":
            if c == "L":
                nx, ny = num(), num()
                x, y = (x + nx, y + ny) if rel else (nx, ny)
            elif c == "H":
                nx = num()
                x = x + nx if rel else nx
            else:
                ny = num()
                y = y + ny if rel else ny
            segs.append(("l", [(x, y)]))
            lc = None
        elif c == "C":
            a = [num() for _ in range(6)]
            if rel:
                a = [a[0] + x, a[1] + y, a[2] + x, a[3] + y, a[4] + x, a[5] + y]
            segs.append(("b", [(a[0], a[1]), (a[2], a[3]), (a[4], a[5])]))
            lc = (a[2], a[3])
            x, y = a[4], a[5]
        elif c == "S":
            a = [num() for _ in range(4)]
            if rel:
                a = [a[0] + x, a[1] + y, a[2] + x, a[3] + y]
            c1 = (2 * x - lc[0], 2 * y - lc[1]) if lc else (x, y)
            segs.append(("b", [c1, (a[0], a[1]), (a[2], a[3])]))
            lc = (a[0], a[1])
            x, y = a[2], a[3]
        else:
            raise ValueError(f"команда пути не поддерживается: {cmd}")

    # рамка по самой кривой (а не по контрольным точкам): семплируем кубические кривые
    xs, ys, cur = [], [], (0.0, 0.0)
    for op, pts in segs:
        if op == "b":
            p0, (p1, p2, p3) = cur, pts
            for n in range(0, 21):
                t = n / 20
                u = 1 - t
                xs.append(u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0])
                ys.append(u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])
        else:
            xs.append(pts[0][0])
            ys.append(pts[0][1])
        cur = pts[-1]
    ox, oy = min(xs), min(ys)
    return " ".join(f"{op} " + " ".join(f"{_n((px - ox) * k)} {_n((py - oy) * k)}" for px, py in pts) for op, pts in segs)


def _rrect(w: float, h: float, r: float) -> str:
    """Скруглённый прямоугольник, рамка которого начинается в (0,0)."""
    r = min(r, w / 2, h / 2)
    x0, y0, x1, y1 = 0.0, 0.0, float(w), float(h)
    k = r * KAPPA
    P = lambda a, b: f"{_n(a)} {_n(b)}"  # noqa: E731
    if r <= 0.01:
        return f"m {P(x0, y0)} l {P(x1, y0)} {P(x1, y1)} {P(x0, y1)}"
    return (f"m {P(x0 + r, y0)} l {P(x1 - r, y0)} b {P(x1 - r + k, y0)} {P(x1, y0 + r - k)} {P(x1, y0 + r)} "
            f"l {P(x1, y1 - r)} b {P(x1, y1 - r + k)} {P(x1 - r + k, y1)} {P(x1 - r, y1)} "
            f"l {P(x0 + r, y1)} b {P(x0 + r - k, y1)} {P(x0, y1 - r + k)} {P(x0, y1 - r)} "
            f"l {P(x0, y0 + r)} b {P(x0, y0 + r - k)} {P(x0 + r - k, y0)} {P(x0 + r, y0)}")


def _circle(r: float) -> str:
    return _rrect(2 * r, 2 * r, r)


# ---------- цвета и время ----------

def _bgr(hexcolor: str) -> str:
    h = hexcolor.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _mix(a: str, b: str, t: float) -> str:
    pa = [int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    pb = [int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def _t(sec: float) -> str:
    cs = max(int(round(sec * 100)), 0)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _ev(a: float, b: float, tags: str, body: str, style: str = "Prog", layer: int = 3) -> str:
    return f"Dialogue: {layer},{_t(a)},{_t(b)},{style},,0,0,0,,{{{tags}}}{body}"


def _draw(a: float, b: float, tags: str, path: str, layer: int = 3) -> str:
    return _ev(a, b, f"\\an5\\bord0\\shad0\\p4{tags}", path, layer=layer)


def _ms(sec: float) -> int:
    return int(round(sec * 1000))


def labels(lang: str | None, custom: str | None = None) -> tuple[str, str]:
    c = (custom or "").strip()[:20]
    if c:
        up = c.upper()
        return up, up
    return LABELS.get((lang or "ru")[:2].lower(), LABELS["en"])


# ---------- общая часть: кнопки лайка и подписки ----------

def _buttons(design: str, t0: float, t1: float, y: int, acc: str, acc2: str, text_pair: tuple[str, str]) -> list[str]:
    """Кнопки лайка и подписки: появляются в t0, «нажимаются» через ~1 с, висят до t1."""
    ev = []
    R = 56                       # радиус круглой кнопки лайка
    pw, ph = 470, 112            # размеры кнопки подписки
    gap = 26
    total = 2 * R + gap + pw
    left = W / 2 - total / 2
    x_like = left + R
    x_sub = left + 2 * R + gap + pw / 2
    x_bell = x_sub - pw / 2 + 62
    x_txt = x_sub + 34
    x_mid = W / 2
    span = max(t1 - t0, 0.5)
    press = min(0.95, span * 0.45)          # когда «нажимаются»
    mp = _ms(press)
    bump = (f"\\t({mp},{mp + 110},\\fscx86\\fscy86)\\t({mp + 110},{mp + 260},\\fscx108\\fscy108)"
            f"\\t({mp + 260},{mp + 380},\\fscx100\\fscy100)")
    fade = "\\fad(120,0)"

    if design == "line":
        like_bg, like_fg, like_fg2 = "#000000", "#FFFFFF", "#3EA6FF"
        sub_bg, sub_bg2, sub_fg, sub_fg2 = "#FF1F3D", "#2E2E2E", "#FFFFFF", "#FFFFFF"
        like_alpha, neon = "\\1a&H33&", False
    elif design == "capsule":
        like_bg, like_fg, like_fg2 = "#FFFFFF", "#1B1B1B", acc
        sub_bg, sub_bg2, sub_fg, sub_fg2 = "#FFFFFF", acc, "#1B1B1B", "#1B1B1B"
        like_alpha, neon = "", False
    else:   # neon: тёмные кнопки со светящимся контуром
        like_bg, like_fg, like_fg2 = "#0B0B1A", acc, "#FFFFFF"
        sub_bg, sub_bg2, sub_fg, sub_fg2 = "#0B0B1A", acc2, acc2, "#0B0B1A"
        like_alpha, neon = "", True

    def where(x: float, anchor: float) -> str:
        """Как кнопка выезжает: капсула - из центра в стороны, неон - падает сверху, линия - появляется на месте.
        anchor - центр кнопки, к которой относится деталь (иконка, надпись), чтобы они ехали вместе."""
        if design == "capsule":
            return f"\\move({x_mid + (x - anchor):.0f},{y},{x:.0f},{y},0,430)"
        if design == "neon":
            return f"\\move({x:.0f},{y - 110},{x:.0f},{y},0,380)"
        return f"\\pos({x:.0f},{y})"

    pop = ("\\fscx70\\fscy70\\t(0,380,\\fscx100\\fscy100)" if design == "neon"
           else "\\fscx1\\fscy1\\t(0,260,\\fscx114\\fscy114)\\t(260,430,\\fscx100\\fscy100)")
    rim = lambda col: (f"\\bord6\\3c{_bgr(col)}\\blur2" if neon else "\\bord0")  # noqa: E731

    # лайк: круг (у «линии» с тонким светлым кольцом, чтобы не терялся на тёмной картинке), иконка меняет цвет при нажатии
    like_ring = rim(like_fg) if neon else ("\\bord3\\3c&HFFFFFF&\\3a&H50&" if design == "line" else "\\bord0")
    ev.append(_ev(t0, t1, f"\\an5\\shad0\\p4{where(x_like, x_like)}{fade}\\1c{_bgr(like_bg)}{like_ring}{like_alpha}{pop}{bump}",
                  _circle(R)))
    ev.append(_ev(t0, t1, f"\\an5\\bord0\\shad0\\p4{where(x_like, x_like)}{fade}\\1c{_bgr(like_fg)}{pop}{bump}"
                          f"\\t({mp},{mp + 200},\\1c{_bgr(like_fg2)})", _svg_to_ass(ICON_THUMB, 58, 12, 11.5)))
    # подписка: капсула меняет цвет, надпись «ПОДПИСАТЬСЯ» -> «ПОДПИСАН»
    ev.append(_ev(t0, t1, f"\\an5\\shad0\\p4{where(x_sub, x_sub)}{fade}\\1c{_bgr(sub_bg)}{rim(sub_fg)}{pop}{bump}"
                          f"\\t({mp},{mp + 220},\\1c{_bgr(sub_bg2)})", _rrect(pw, ph, ph / 2)))
    ev.append(_ev(t0, t1, f"\\an5\\bord0\\shad0\\p4{where(x_bell, x_sub)}{fade}\\1c{_bgr(sub_fg)}{pop}{bump}"
                          f"\\t({mp},{mp + 220},\\1c{_bgr(sub_fg2)})", _svg_to_ass(ICON_BELL, 52, 12, 12)))
    swap = t0 + press + 0.12
    ev.append(_ev(t0, swap, f"\\an5\\bord0\\shad0{where(x_txt, x_sub)}{fade}\\fs40\\b1\\1c{_bgr(sub_fg)}{pop}",
                  text_pair[0], style="ProgText", layer=4))
    ev.append(_ev(swap, t1, f"\\an5\\bord0\\shad0\\pos({x_txt:.0f},{y})\\fs40\\b1\\1c{_bgr(sub_fg2)}",
                  text_pair[1], style="ProgText", layer=4))
    return ev


# ---------- три дизайна полоски ----------

def _palette(design: str, color: str) -> tuple[str, str]:
    """(основной цвет, второй цвет)."""
    if color in PROGRESS_COLORS and color != "auto":
        base = PROGRESS_COLORS[color][1]
        return base, _mix(base, "#FFFFFF", 0.35)
    return {"line": ("#FF1F3D", "#FF6B81"), "capsule": ("#FFC400", "#FFE27A"), "neon": ("#00E5FF", "#FF2BD6")}[design]


def _line(t_fill: float, y: int, acc: str, acc2: str, end_at: float) -> list[str]:
    ev = []
    h = 14
    path = _rrect(W, h, 0)
    ms = _ms(t_fill)
    ev.append(_draw(0, end_at, f"\\pos({W // 2},{y})\\fad(250,{250 if end_at < 1e6 else 0})\\1c&H000000&\\1a&H80&", path, layer=3))
    clip0, clip1 = f"\\clip(0,{y - 20},0,{y + 20})", f"\\clip(0,{y - 20},{W},{y + 20})"
    ev.append(_draw(0, end_at, f"\\pos({W // 2},{y}){clip0}\\1c{_bgr(acc)}\\t(0,{ms},{clip1})", path, layer=4))
    # светящаяся «головка» бежит по линии
    ev.append(_draw(0, min(t_fill + 0.2, end_at),
                    f"\\move(0,{y},{W},{y},0,{ms})\\1c&HFFFFFF&\\bord9\\3c{_bgr(acc2)}\\3a&H60&\\blur6\\fad(0,200)", _circle(11), layer=5))
    return ev


def _capsule(t_fill: float, y: int, acc: str, acc2: str, end_at: float) -> list[str]:
    ev = []
    w, h = 760, 38
    ms = _ms(t_fill)
    ev.append(_draw(0, end_at, f"\\pos({W // 2},{y})\\fad(250,250)\\1c&H000000&\\1a&H66&\\bord4\\3c&HFFFFFF&\\3a&H30&", _rrect(w, h, h / 2)))
    iw, ih = w - 14, h - 14
    x0, x1 = W / 2 - iw / 2, W / 2 + iw / 2
    clip0 = f"\\clip({x0:.0f},{y - 30},{x0:.0f},{y + 30})"
    clip1 = f"\\clip({x0:.0f},{y - 30},{x1:.0f},{y + 30})"
    ev.append(_draw(0, end_at, f"\\pos({W // 2},{y}){clip0}\\fad(250,250)\\1c{_bgr(acc)}\\t(0,{ms},{clip1})", _rrect(iw, ih, ih / 2), layer=4))
    # блик по верхней половине заливки
    ev.append(_draw(0, end_at, f"\\pos({W // 2},{y - 5}){clip0}\\fad(250,250)\\1c&HFFFFFF&\\1a&H90&\\t(0,{ms},{clip1})",
                    _rrect(iw - 16, ih / 2.4, ih / 4.8), layer=5))
    return ev


def _neon(t_fill: float, y: int, acc: str, acc2: str, end_at: float, n: int = 20) -> list[str]:
    ev = []
    sw, sh, gap = 40, 22, 10
    total = n * sw + (n - 1) * gap
    left = W / 2 - total / 2
    path = _rrect(sw, sh, 6)
    for i in range(n):
        cx = left + sw / 2 + i * (sw + gap)
        col = _mix(acc, acc2, i / max(n - 1, 1))
        t_on = t_fill * (i + 1) / n
        ms_on = _ms(t_on)
        # погасший сегмент -> вспышка -> горит неоном
        tags = (f"\\pos({cx:.0f},{y})\\fad(200,250)\\1c&H402A2A&\\1a&H30&\\bord2\\3c{_bgr(_mix(col, '#000000', 0.55))}"
                f"\\t({ms_on},{ms_on + 90},\\1c&HFFFFFF&\\3c&HFFFFFF&\\fscx125\\fscy125\\blur5)"
                f"\\t({ms_on + 90},{ms_on + 260},\\1c{_bgr(col)}\\3c{_bgr(col)}\\fscx100\\fscy100\\blur3)")
        ev.append(_draw(0, end_at, tags, path))
    return ev


DESIGN_FUNCS = {"line": _line, "capsule": _capsule, "neon": _neon}


def ass_events(cfg: dict | None, duration: float, lang: str | None = None) -> list[str]:
    """Строки Dialogue для subs.ass: полоска + кнопки. Пусто, если дизайн не выбран."""
    p = {**PROGRESS_DEFAULT, **(cfg or {})}
    design = p.get("design", "off")
    if design not in DESIGN_FUNCS or duration < 1.5:
        return []
    y = BAR_Y.get(p.get("pos", "bottom"), BAR_Y["bottom"])
    acc, acc2 = _palette(design, p.get("color", "auto"))
    buttons = bool(p.get("end", True)) and duration >= 4.0
    reveal = max(1.7, min(2.7, duration * 0.3)) if buttons else 0.0
    t_fill = duration - reveal
    bar_end = (t_fill + 0.3) if buttons else duration
    if design == "neon":
        ev = _neon(t_fill, y, acc, acc2, bar_end)
    else:
        ev = DESIGN_FUNCS[design](t_fill, y, acc, acc2, bar_end)
    if buttons:
        by = y - (118 if design != "line" or True else 0)
        ev += _buttons(design, t_fill + 0.05, duration, by, acc, acc2, labels(lang, p.get("label")))
    return ev
