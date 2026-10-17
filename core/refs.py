"""UGC-реклама и разбор референса без встроенной нейросети: бот готовит промт, человек несёт его во внешнюю нейросеть.

UGC: описание товара -> промт на N вариантов ролика с разными хуками ({"variants": [...]}, каждый - сценарий умного монтажа).
Разбор: чужой ролик -> склейки, темп речи, хук первых 3 секунд, раскадровка -> промт «сделай похожий на мою тему»."""
import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("refs")

HOOKS = [
    ("problem", "боль: начни с проблемы зрителя («Тратишь час на…?»)"),
    ("result", "результат: начни с итога или «до/после» («Через неделю кожа стала…»)"),
    ("review", "отзыв от первого лица: «Я не верил, пока не попробовал…»"),
    ("number", "цифра или список: «3 причины, почему…»"),
    ("myth", "разрушение мифа: «Все думают, что…, но…»"),
]


def ugc_block(n: int, brand: dict | None, lang: str) -> str:
    """Добавка к промту умного монтажа: n вариантов с разными хуками, финал с призывом, ответ - {"variants": [...]}."""
    hooks = "\n".join(f"- вариант {i + 1}: {HOOKS[i % len(HOOKS)][1]}" for i in range(n))
    b = brand or {}
    name = f" Бренд: {b['name']}." if b.get("name") else ""
    cta = f" Последняя сцена - призыв: «{b['cta']}» или близко по смыслу." if b.get("cta") else \
        " Последняя сцена - чёткий призыв к действию (купить, перейти по ссылке, написать)."
    en = lang == "en"
    return f"""

РЕЖИМ UGC-РЕКЛАМЫ (важнее общих правил выше, где они расходятся):
Это рекламный ролик в стиле UGC - как будто обычный человек рассказывает о товаре от себя, живо и честно, без канцелярита и «лучший на рынке».{name}
Сделай {n} РАЗНЫХ варианта одного ролика: отличаются хук (первая фраза) и подача, факты о товаре те же.
{hooks}
Правила для всех вариантов: длина 20-35 секунд, 5-8 сцен; в первые 2 секунды - хук; дальше - 1-2 выгоды на конкретных бытовых примерах; без выдуманных цифр, отзывов и гарантий - только то, что есть в описании товара.{cta}
Кадры (visual.query) - {"по-английски, " if not en else ""}простые и снимаемые: руки с товаром, человек пользуется, результат, бытовая сцена.

ФОРМАТ ОТВЕТА ДЛЯ ЭТОГО РЕЖИМА (заменяет формат выше): верни ТОЛЬКО JSON вида
{{"variants": [ {{...сценарий 1 в формате выше...}}, {{...сценарий 2...}} ]}}
- каждый элемент variants - полный сценарий с полями title, description, tags, scenes и т.д., как описано выше."""


def ugc_brief(product: str, brand: dict | None) -> str:
    b = brand or {}
    return (f"Рекламный UGC-ролик. Товар или услуга: {product.strip()}"
            + (f". Бренд: {b['name']}" if b.get("name") else ""))


# ---------- разбор референса ----------

def _frames(video: Path, times: list[float], out: Path, cols: int = 4) -> Path | None:
    from core import fontpack
    fontpack.ensure()
    font = Path.home() / ".fonts" / "FiraSans-SemiBold.ttf"
    tmp = out.parent / "frames"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    n = 0
    for t in times[:12]:
        f = tmp / f"f_{n:03d}.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2:color=0x0E1525,"
                        f"drawtext=fontfile={font}:text='{int(t // 60)}\\:{int(t % 60):02d}':x=10:y=10:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6",
                        str(f)], capture_output=True, timeout=60)
        if f.exists():
            n += 1
    if not n:
        return None
    rows = (n + cols - 1) // cols
    res = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "1", "-i", str(tmp / "f_%03d.jpg"),
                          "-vf", f"pad=iw+8:ih+8:4:4:color=0x0E1525,tile={cols}x{rows}:color=0x0E1525", "-frames:v", "1", "-q:v", "3", str(out)],
                         capture_output=True, timeout=60)
    shutil.rmtree(tmp, ignore_errors=True)
    return out if res.returncode == 0 and out.exists() else None


def hook_kind(text: str) -> str:
    t = (text or "").lower()
    if "?" in t:
        return "вопрос"
    if re.search(r"\d", t):
        return "цифра"
    if re.search(r"\b(ты|вы|тебе|вам|твой|ваш|you|your)\b", t):
        return "обращение к зрителю"
    if re.search(r"\b(никогда|все думают|на самом деле|секрет|ошибк|never|secret|actually|mistake)", t):
        return "интрига или разрушение мифа"
    return "утверждение"


def analyze(video: Path, work: Path) -> dict:
    """Склейки, длины планов, темп речи, хук и раскадровка."""
    from common.media import probe
    from core import cutter, subtitles
    work.mkdir(parents=True, exist_ok=True)
    info = probe(video)
    dur = float(info["format"].get("duration") or 0)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    cuts = cutter.effective_cuts(cutter.detect_cuts(video, 0.30), dur)
    shots = [0.0] + cuts
    lens = [b - a for a, b in zip(shots, shots[1:] + [dur])]
    words, lang = ([], "")
    if has_audio:
        try:
            words, lang = subtitles.transcribe_words_lang(video, None)
        except Exception as e:  # noqa: BLE001
            log.warning("речь референса: %s", e)
    text = " ".join(w for _, _, w in words)
    hook = " ".join(w for s, _, w in words if s < 3.0)
    speech = sum(e - s for s, e, _ in words)
    tail = " ".join(w for s, _, w in words if s >= dur - 5).lower()
    sheet = _frames(video, [s + 0.25 for s in shots], work / "storyboard.jpg")
    return {
        "duration": dur, "w": v.get("width"), "h": v.get("height"), "shots": len(shots),
        "avg_shot": (sum(lens) / len(lens)) if lens else dur, "cuts_per_min": len(cuts) / dur * 60 if dur else 0,
        "first_cut": cuts[0] if cuts else dur, "words": len(words), "wps": len(words) / speech if speech > 1 else 0,
        "speech_share": speech / dur if dur else 0, "hook": hook, "hook_kind": hook_kind(hook) if hook else "без речи",
        "cta": bool(re.search(r"подпис|подпиш|ссылк|жми|переход|subscribe|link|follow|comment|коммент", tail)),
        "text": text, "lang": lang or "ru", "storyboard": str(sheet) if sheet else "",
    }


def report(a: dict) -> str:
    mm = lambda t: f"{int(t) // 60}:{int(t) % 60:02}"  # noqa: E731
    pace = "очень быстрый" if a["avg_shot"] < 1.5 else "быстрый" if a["avg_shot"] < 2.5 else "средний" if a["avg_shot"] < 4 else "спокойный"
    lines = [f"🔍 Разбор референса ({mm(a['duration'])}, {a.get('w')}x{a.get('h')})",
             f"\n🎬 Монтаж: {a['shots']} планов, средний план {a['avg_shot']:.1f} с ({pace}), {a['cuts_per_min']:.0f} склеек в минуту; "
             f"первая смена кадра на {a['first_cut']:.1f} с"]
    if a["words"]:
        talk = "быстро" if a["wps"] > 3 else "живо" if a["wps"] > 2.3 else "спокойно"
        lines.append(f"🗣 Речь: {a['words']} слов, {a['wps']:.1f} слова в секунду ({talk}), речь занимает {a['speech_share'] * 100:.0f}% ролика")
        lines.append(f"🪝 Хук (первые 3 с): «{a['hook'][:150]}» - {a['hook_kind']}")
    else:
        lines.append("🗣 Речи нет - ролик держится на картинке, тексте на экране и музыке")
    lines.append("📣 В конце есть призыв" if a["cta"] else "📣 Призыва в конце нет")
    tips = []
    if a["first_cut"] > 2.5:
        tips.append("первая смена кадра позже 2,5 с - в своём ролике меняйте кадр раньше, чтобы удержать первые секунды")
    if a["avg_shot"] < 2.5:
        tips.append("частые склейки: держите темп - сцены по 1,5-2,5 с")
    if a["words"] and a["hook_kind"] in ("вопрос", "цифра", "обращение к зрителю"):
        tips.append(f"хук-«{a['hook_kind']}» - хорошо работает, повторите приём на своей теме")
    if tips:
        lines.append("\n💡 " + "\n💡 ".join(tips))
    return "\n".join(lines)


def ref_block(a: dict) -> str:
    """Добавка к промту умного монтажа: повторить структуру референса на своей теме."""
    n = max(4, min(15, a["shots"]))
    secs = max(15, min(75, int(a["duration"])))
    return f"""

РЕФЕРЕНС (важнее общих правил о длине и числе сцен): повтори СТРУКТУРУ и ТЕМП этого ролика, но на теме выше и своими словами - текст не копируй.
- длина около {secs} с, примерно {n} сцен (в референсе средний план {a['avg_shot']:.1f} с, {a['cuts_per_min']:.0f} склеек в минуту);
- хук в первые 3 секунды того же типа: {a['hook_kind']}{f" (в референсе: «{a['hook'][:120]}»)" if a['hook'] else ''};
- темп речи около {a['wps']:.1f} слова в секунду;
- {'в конце - призыв, как в референсе' if a['cta'] else 'финал - неожиданный вывод, без призыва'}.
Структура референса по тексту (для понимания, не для копирования): {a['text'][:900]}"""
