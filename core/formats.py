"""Форматы видео и финальная доводка роликов бота для креаторов: соотношение сторон, логотип и призыв бренда.

ReelsEditor монтирует сразу в нужном формате. Умный монтаж рисует графику под 9:16, поэтому его ролик переводится в другой
формат одним проходом ffmpeg: целиком на размытом фоне (ничего не теряется) или обрезкой по центру (без 16:9)."""
import subprocess
from pathlib import Path

FORMATS = {"9:16": (1080, 1920, "Shorts, Reels, TikTok"), "4:5": (1080, 1350, "лента Instagram и VK"),
           "1:1": (1080, 1080, "квадрат: лента, реклама"), "16:9": (1920, 1080, "YouTube, VK Видео, сайт")}
FITS = {"blur": "целиком на размытом фоне", "crop": "обрезать по центру"}
SUB_SCALE = {"9:16": 1.0, "4:5": 0.92, "1:1": 0.85, "16:9": 0.62}


def norm(fmt: str | None) -> str:
    return fmt if fmt in FORMATS else "9:16"


def size(fmt: str | None) -> tuple[int, int]:
    w, h, _ = FORMATS[norm(fmt)]
    return w, h


def play_res_y(fmt: str | None) -> int:
    """Высота «холста» субтитров при ширине 1080: так libass масштабирует ASS без искажений в любом формате."""
    w, h = size(fmt)
    return int(round(1080 * h / w))


def fit_filter(fmt: str, fit: str, inp: str = "0:v", out: str = "fv") -> str:
    """Готовый 9:16 -> формат fmt. Для 16:9 всегда размытый фон: обрезка оставила бы полоску."""
    w, h = size(fmt)
    if fit == "crop" and fmt != "16:9":
        return f"[{inp}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[{out}]"
    return (f"[{inp}]split[fa][fb];[fa]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=30:3,"
            f"eq=brightness=-0.06[fbg];[fb]scale={w}:{h}:force_original_aspect_ratio=decrease[ffg];"
            f"[fbg][ffg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")


def finalize(src: Path, fmt: str | None, fit: str, brand: dict | None, duration: float, work: Path) -> Path:
    """Формат + логотип + призыв в конце одним проходом. Нечего делать - возвращает исходный файл."""
    from core import brand as brandmod
    fmt = norm(fmt)
    logo = brandmod.logo_path(brand)
    cta = brandmod.cta_events(brand, duration, fmt)
    if fmt == "9:16" and not logo and not cta:
        return src
    work.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    filt, cur = [], "0:v"
    if fmt != "9:16":
        filt.append(fit_filter(fmt, fit, cur, "fv"))
        cur = "fv"
    if logo:
        cmd += ["-i", str(logo)]
        filt.append(brandmod.logo_filter(brand, fmt, cur, "1:v", "lv"))
        cur = "lv"
    if cta:
        brandmod.write_ass(cta, fmt, work / "brand.ass")
        filt.append(f"[{cur}]ass=brand.ass[av]")
        cur = "av"
    filt.append(f"[{cur}]format=yuv420p[vout]")
    out = work / f"final_{fmt.replace(':', 'x')}.mp4"
    res = subprocess.run(cmd + ["-filter_complex", ";".join(filt), "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264",
                                "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", out.name],
                         capture_output=True, text=True, cwd=work, timeout=1200)
    if res.returncode != 0 or not out.exists():
        raise RuntimeError("не удалось перевести ролик в формат: " + res.stderr[-300:])
    return out
