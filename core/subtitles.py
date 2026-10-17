"""Субтитры: Whisper -> слова с таймингами -> .srt и стилизованный .ass для Shorts."""
import subprocess
from pathlib import Path

from common import config

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def load_audio(path: Path):
    """Декодируем аудио через ffmpeg в массив 16 кГц mono, чтобы не зависеть от библиотеки av."""
    import numpy as np

    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg не смог декодировать аудио: {res.stderr.decode(errors='ignore')[-500:]}")
    return np.frombuffer(res.stdout, dtype=np.float32)


def _whisper_lang(lang: str | None) -> str | None:
    """None -> язык из .env; 'auto' -> Whisper определяет сам; иначе код языка."""
    if lang is None:
        return config.WHISPER_LANGUAGE
    return None if lang in ("", "auto") else lang


def transcribe_words(audio: Path, lang: str | None = None) -> list[tuple[float, float, str]]:
    return transcribe_words_lang(audio, lang)[0]


def transcribe_words_lang(audio: Path, lang: str | None = None, prompt: str | None = None,
                          vad: bool = True) -> tuple[list[tuple[float, float, str]], str]:
    """Слова с таймингами и язык речи (заданный или определённый Whisper'ом).
    prompt - подсказка Whisper'у (например, образец с «э-э», чтобы он не выбрасывал слова-паразиты)."""
    kw = {"initial_prompt": prompt} if prompt else {}
    segments, info = _get_model().transcribe(
        load_audio(audio), language=_whisper_lang(lang), word_timestamps=True, vad_filter=vad, **kw
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            token = w.word.strip()
            if token:
                words.append((w.start, w.end, token))
    return words, (getattr(info, "language", None) or _whisper_lang(lang) or "")


def translate_segments(audio: Path, lang: str | None = None) -> list[tuple[float, float, str]]:
    """Речь, переведённая Whisper'ом на английский, по фразам: [(начало, конец, текст)].
    Нужен ReelsEditor, чтобы искать вставки на стоках английскими запросами, как умный монтаж."""
    segments, _ = _get_model().transcribe(load_audio(audio), language=_whisper_lang(lang), task="translate", vad_filter=True)
    return [(float(s.start), float(s.end), (s.text or "").strip()) for s in segments if (s.text or "").strip()]


def group_words(words, max_words: int = 3, max_gap: float = 0.6):
    """Группы по 2-3 слова: динамичные короткие титры, как в Shorts."""
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w[0] - cur[-1][1] > max_gap):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return [(g[0][0], g[-1][1], " ".join(x[2] for x in g)) for g in groups]


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def write_srt(groups, path: Path) -> None:
    lines = []
    for i, (a, b, text) in enumerate(groups, 1):
        lines += [str(i), f"{_srt_time(a)} --> {_srt_time(b)}", text, ""]
    path.write_text("\n".join(lines), "utf-8")


# Виды субтитров: цвета в формате ASS (&HAABBGGRR), BorderStyle 1 - контур, 3 - плашка.
SUB_LOOKS = {
    "classic": {"label": "жёлтые", "primary": "&H0000FFFF", "outline": "&H00000000", "size": 96, "bstyle": 1, "out": 7, "shadow": 2, "upper": True},
    "white": {"label": "белые", "primary": "&H00FFFFFF", "outline": "&H00000000", "size": 92, "bstyle": 1, "out": 6, "shadow": 2, "upper": False},
    "neon": {"label": "неон", "primary": "&H00FFFF00", "outline": "&H00FF00FF", "size": 96, "bstyle": 1, "out": 6, "shadow": 0, "upper": True},
    "box": {"label": "плашка", "primary": "&H00FFFFFF", "outline": "&H99000000", "size": 84, "bstyle": 3, "out": 14, "shadow": 0, "upper": False},
}
# позиция: (выравнивание ASS, отступ снизу/сверху); низ и верх подняты, чтобы не попасть под интерфейс Shorts
SUB_POS = {"center": (5, 0), "bottom": (2, 430), "top": (8, 430)}
HOOK_SIZE = {"m": 72, "l": 88, "xl": 110}
CTA_SIZE = {"m": 58, "l": 74, "xl": 92}
CAPTION_SIZE = {"s": 42, "m": 52, "l": 66}
# постоянная подпись: «низ» оставляет место под кнопки лайка и подписки в конце ролика
CAPTION_POS = {"top": (8, 150), "upper": (8, 420), "center": (5, 0), "lower": (2, 560), "bottom": (2, 330)}
# позиции текста на экране: (выравнивание ASS, отступ); «низ» выше интерфейса Shorts
TEXT_POS = {"top": (8, 230), "upper": (8, 560), "center": (5, 0), "lower": (2, 600), "bottom": (2, 300)}
# оформление: (цвет текста, цвет контура/плашки, BorderStyle, контур, тень); цвета ASS &HAABBGGRR
TEXT_STYLE = {
    "outline": ("&H00FFFFFF", "&H00000000", 1, 8, 2),
    "gold": ("&H00000000", "&H0000D7FF", 3, 16, 0),
    "white": ("&H00000000", "&H00FFFFFF", 3, 16, 0),
    "black": ("&H00FFFFFF", "&H99000000", 3, 16, 0),
    "red": ("&H00FFFFFF", "&H004639E6", 3, 16, 0),
}
FONT = "DejaVu Sans"


def _style(name, size, primary, outline, bstyle, out, shadow, align, margin_v, back="&H00000000"):
    return (f"Style: {name},{FONT},{size},{primary},&H000000FF,{outline},{back},-1,0,0,0,100,100,0,0,"
            f"{bstyle},{out},{shadow},{align},70,70,{margin_v},1")


def _header(look: dict | None) -> tuple[str, dict]:
    look = look or {}
    sl = SUB_LOOKS.get(look.get("sub", "classic"), SUB_LOOKS["classic"])
    align, margin = SUB_POS.get(look.get("subpos", "center"), SUB_POS["center"])
    hk, ct = look.get("hook") or {}, look.get("cta") or {}
    h_al, h_mv = TEXT_POS.get(hk.get("pos", "top"), TEXT_POS["top"])
    c_al, c_mv = TEXT_POS.get(ct.get("pos", "bottom"), TEXT_POS["bottom"])
    hs = TEXT_STYLE.get(hk.get("style", "outline"), TEXT_STYLE["outline"])
    cs = TEXT_STYLE.get(ct.get("style", "gold"), TEXT_STYLE["gold"])
    cp = look.get("caption") or {}
    p_al, p_mv = CAPTION_POS.get(cp.get("pos", "bottom"), CAPTION_POS["bottom"])
    ps = TEXT_STYLE.get(cp.get("style", "outline"), TEXT_STYLE["outline"])
    pg = look.get("progress") or {}
    if p_al == 2 and pg.get("design", "off") != "off" and pg.get("pos") == "high":
        p_mv = max(p_mv, 520)   # полоска «выше интерфейса» стоит выше: подпись поднимается над ней и над кнопками
    styles = [
        _style("Shorts", sl["size"], sl["primary"], sl["outline"], sl["bstyle"], sl["out"], sl["shadow"], align, margin),
        # хук: крупный белый текст сверху; призыв: чёрный текст на золотой плашке внизу
        _style("Hook", HOOK_SIZE.get(hk.get("size", "l"), 88), hs[0], hs[1], hs[2], hs[3], hs[4], h_al, h_mv),
        _style("CTA", CTA_SIZE.get(ct.get("size", "l"), 74), cs[0], cs[1], cs[2], cs[3], cs[4], c_al, c_mv),
        # постоянная подпись (название ролика и т. п.) и текст кнопок полоски прогресса
        _style("Caption", CAPTION_SIZE.get(cp.get("size", "m"), 52), ps[0], ps[1], ps[2], max(ps[3] - 2, 1) if ps[2] == 1 else ps[3], ps[4],
               p_al, p_mv),
        _style("Prog", 40, "&H00FFFFFF", "&H00000000", 1, 0, 0, 5, 0),
        _style("ProgText", 40, "&H00FFFFFF", "&H00000000", 1, 0, 0, 5, 0),
    ]
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n" + "\n".join(styles) + "\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return head, sl


def _clean(text: str) -> str:
    """Спецсимволы ASS не должны попадать в текст титра."""
    return text.replace("{", "(").replace("}", ")").replace("\\", " ").replace("\n", "\\N").strip()


def write_ass(groups, path: Path, look: dict | None = None, overlays: list | None = None, extra: list | None = None) -> None:
    """groups - титры речи [(start, end, text)]; overlays - [(start, end, text, 'hook'|'cta'|'caption')] поверх видео;
    extra - готовые строки Dialogue (полоска прогресса, core.progress)."""
    head, sl = _header(look)
    body = []
    for a, b, text in groups:
        t = _clean(text)
        body.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Shorts,,0,0,0,,{t.upper() if sl['upper'] else t}")
    for a, b, text, kind in overlays or []:
        style = {"hook": "Hook", "caption": "Caption"}.get(kind, "CTA")
        t = _clean(text)
        t = t.upper() if kind == "hook" else t
        fade = "\\fad(400,400)" if kind == "caption" else "\\fad(250,250)"
        body.append(f"Dialogue: {0 if kind == 'caption' else 1},{_ass_time(a)},{_ass_time(b)},{style},,0,0,0,,{{{fade}}}{t}")
    body += extra or []
    path.write_text(head + "\n".join(body) + "\n", "utf-8")


def build_subtitles(audio: Path, out_dir: Path, lang: str | None = None) -> Path:
    words = transcribe_words(audio, lang)
    if not words:
        raise RuntimeError("Whisper не распознал речь в аудио")
    groups = group_words(words)
    write_srt(groups, out_dir / "subs.srt")
    ass = out_dir / "subs.ass"
    write_ass(groups, ass)
    return ass
