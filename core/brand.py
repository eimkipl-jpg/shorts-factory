"""Бренд-профиль креатора: название, логотип, цвет, призыв в конце ролика, хэштеги.

Хранится в состоянии пользователя (st["brand"]), логотип - <каталог>/brands/<user_id>/logo.png (не чистится по возрасту).
Цвет становится цветом подсветки субтитров, логотип и призыв накладываются на ролик, хэштеги дописываются к описанию."""
import re
from pathlib import Path

from common import config

COLORS = {"yellow": ("жёлтый", "#FFD60A"), "orange": ("оранжевый", "#FF8A3D"), "red": ("красный", "#FF4D4D"),
          "pink": ("розовый", "#FF5FA2"), "violet": ("фиолетовый", "#A974FF"), "blue": ("синий", "#4D9BFF"),
          "teal": ("бирюзовый", "#2ED3C6"), "green": ("зелёный", "#4ADE80"), "white": ("белый", "#FFFFFF")}
LOGO_POS = {"tr": "справа сверху", "tl": "слева сверху", "off": "не показывать"}
DEFAULT = {"name": "", "color": "yellow", "logo": "", "logo_pos": "tr", "cta": "", "tags": ""}
CTA_SECONDS = 3.0


def get(st: dict) -> dict:
    return {**DEFAULT, **(st.get("brand") or {})}


def folder(uid: int) -> Path:
    return config.ROOT / "brands" / str(uid)


def color_hex(b: dict | None) -> str:
    return COLORS.get((b or {}).get("color") or "yellow", COLORS["yellow"])[1]


def is_set(b: dict | None) -> bool:
    b = b or {}
    return bool(b.get("name") or b.get("logo") or b.get("cta") or b.get("tags") or (b.get("color") not in (None, "yellow")))


def logo_path(b: dict | None) -> Path | None:
    b = b or {}
    if b.get("logo_pos", "tr") == "off" or not b.get("logo"):
        return None
    p = config.ROOT / b["logo"]
    root = (config.ROOT / "brands").resolve()
    return p if p.is_file() and root in p.resolve().parents else None


def clean_tags(text: str) -> str:
    """«деньги, финансы #инвестиции» -> «#деньги #финансы #инвестиции» (до 15 штук)."""
    words = [w for w in re.split(r"[\s,;]+", text or "") if w.strip("#")]
    tags = []
    for w in words:
        t = "#" + re.sub(r"[^\w]", "", w.strip("#"))
        if len(t) > 1 and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    return " ".join(tags[:15])


def with_tags(text: str, b: dict | None) -> str:
    tags = (b or {}).get("tags") or ""
    return (text.rstrip() + "\n\n" + tags).strip() if tags else text


def logo_filter(b: dict, fmt: str, base: str, logo_in: str, out: str) -> str:
    from core import formats
    w, h = formats.size(fmt)
    lw = int(min(w, h) * 0.16)
    m = int(min(w, h) * 0.045)
    x = f"W-w-{m}" if b.get("logo_pos", "tr") == "tr" else f"{m}"
    y = f"{int(m * 1.4)}"
    return (f"[{logo_in}]scale={lw}:-1,format=rgba,colorchannelmixer=aa=0.88[lgo];"
            f"[{base}][lgo]overlay={x}:{y}:format=auto[{out}]")


def cta_events(b: dict | None, duration: float, fmt: str, y_frac: float | None = None) -> list[str]:
    """Плашка с призывом в цвете бренда последние 3 секунды (на холсте 1080 x play_res_y).
    y_frac - высота центра плашки (доля кадра); по умолчанию - над строкой субтитров (для готовых роликов умного монтажа)."""
    from core import formats, statfx
    text = ((b or {}).get("cta") or "").strip()
    if not text or duration < CTA_SECONDS + 1:
        return []
    a, e = duration - CTA_SECONDS, duration - 0.05
    pry = formats.play_res_y(fmt)
    size = 56 * (1.0 if fmt != "16:9" else 0.7)
    lines = statfx._balanced(text, size, 860)[:2] if statfx._w(text, size) > 860 else [text]
    tw = max(statfx._w(ln, size) for ln in lines)
    ph = size * 1.25 * len(lines) + 46
    pw = min(1000, tw + 90)
    y = pry * (y_frac if y_frac else {"9:16": 0.58, "4:5": 0.5, "1:1": 0.47, "16:9": 0.5}.get(fmt, 0.55))
    col = color_hex(b)
    dark = "#111111" if col in ("#FFFFFF", "#FFD60A", "#4ADE80", "#2ED3C6") else "#FFFFFF"
    ev = [statfx._shape(a, e, 540 - pw / 2, y - ph / 2, statfx._rect(pw, ph), col, an=7, layer=6)]
    for k, ln in enumerate(lines):
        ly = y - (len(lines) - 1) * size * 0.625 + k * size * 1.25
        ev.append(statfx._txt(a, e, 540, ly, ln, size, dark, head=True, an=5, rise=20, layer=7))
    return ev


def write_ass(events: list[str], fmt: str, path: Path) -> None:
    from core import formats, fontpack, subtitles
    fontpack.ensure()
    subtitles.write_ass([], path, None, None, events)
    t = path.read_text("utf-8").replace("PlayResY: 1920", f"PlayResY: {formats.play_res_y(fmt)}")
    path.write_text(t, "utf-8")


def _ass_sec(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def clip_events(events: list[str], until: float) -> list[str]:
    """Субтитры уступают место плашке призыва: всё, что начинается после until, убираем, пересекающееся - обрезаем."""
    from core import statfx
    out = []
    for ev in events:
        parts = ev.split(",", 3)
        if len(parts) < 4 or not ev.startswith("Dialogue"):
            out.append(ev)
            continue
        a, e = _ass_sec(parts[1]), _ass_sec(parts[2])
        if a >= until - 0.05:
            continue
        if e > until:
            parts[2] = statfx._t(until)
        out.append(",".join(parts))
    return out
