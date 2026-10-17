"""Режим «История»: пост-вопрос и история-комментарий в карточке своего дизайна, слова появляются вместе с озвучкой,
фоном (или снизу) идёт залипательное видео из папки clips/_satisfying (на Диске: _clips/_satisfying).

JSON истории (его пишет нейросеть по промту make_prompt):
{
  "title": "Название ролика", "description": "...", "tags": ["#история"], "channel": "MemesOfRakhim",
  "voice": {"name": "en-GB-RyanNeural", "rate": 0},          # или строка; можно не указывать - голос по языку
  "music": {"mood": "calm", "volume": 8},                      # или "off" / "auto"
  "post":    {"author": "quiet_harbor", "time": "5 ч", "text": "Вопрос к людям", "likes": "12,4 тыс", "comments": "1,8 тыс", "place": ""},
  "comment": {"author": "mila_night", "time": "3 ч", "text": "История от первого лица.\\nАбзацы через перевод строки.", "likes": "8,1 тыс"},
  "progress": "line"                                           # необязательно: полоска прогресса внизу
}
Озвучка - одним проходом (интонация не рвётся), время каждого слова - по Whisper с выравниванием к тексту
(если распознать не вышло - по длине слов). Карточка листается страницами по предложениям.
"""
import difflib
import logging
import random
import re
import shutil
import zlib
from pathlib import Path

from common import config, library
from common.looks import merged_look, merged_sfx
from common.media import audio_duration, video_duration, video_only
from core import compose, mix, subtitles, textfit, tts
from core.progress import _bgr, _circle, _rrect, _svg_to_ass, _t

log = logging.getLogger("story")

SAT_NAME = "_satisfying"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
MAX_POST = 220
MAX_COMMENT = 1600
MAX_SECONDS = 170
CHARS_PER_SECOND = 14.5
TAIL = 0.9            # хвост после последнего слова, с
SEG_MAX = 18.0        # самый длинный кусок одного залипательного видео, с

THEMES = {"light": "светлая", "dark": "тёмная"}
LAYOUTS = {"full": "поверх видео", "split": "сверху карточка, снизу видео"}
PROGRESS = {"off": "нет", "line": "линия", "capsule": "капсула", "neon": "неон"}
DEFAULT_CFG = {"theme": "light", "layout": "full", "sat": "all", "hl": True, "progress": "off", "voice": "auto",
               "music": "off", "music_vol": 8}
VOICE_BY_LANG = {"ru": "ru-RU-DmitryNeural", "en": "en-GB-RyanNeural"}

# ---------- оформление карточки (пиксели кадра 1080x1920) ----------
CARD_X, CARD_W, RADIUS = 60, 960, 34
PAD = 44
AVATAR_R = 42
NAME_FS, META_FS, TITLE_FS, BODY_FS, FOOT_FS = 38, 30, 54, 46, 32
TITLE_PITCH, BODY_PITCH, PARA_GAP = 70, 62, 18
BODY_TOP = 40 + 2 * AVATAR_R + 30      # от верха карточки до первой строки текста
FOOT_GAP, FOOT_H, PAD_B = 22, 44, 36
TOP_FULL = 250                         # верх карточки в режиме «поверх видео» (ниже верхних кнопок Shorts)
PAGE_PX = {"full": 10 * BODY_PITCH, "split": 9 * BODY_PITCH}
TEXT_W = CARD_W - 2 * PAD
MOVE, ANIM_MS, FADE_MS, REVEAL_MS = 18, 200, 160, 110
COLORS = {
    "light": {"card": "#FFFFFF", "text": "#1A1A1B", "meta": "#7A7C80", "icon": "#8A8D91", "shadow_a": "&H90&", "hl": "#2F6BFF"},
    "dark": {"card": "#1E1F23", "text": "#ECECEE", "meta": "#9A9CA2", "icon": "#9A9CA2", "shadow_a": "&H50&", "hl": "#5B8CFF"},
}
AVATAR_COLORS = ["#FF6B6B", "#4D96FF", "#2BB673", "#FF9F1C", "#9B5DE5", "#00A6D6", "#E84A9A", "#14B8A6"]
# значки Material Icons (Apache 2.0): сердце, облачко комментария, стрелка «ответить»
ICON_HEART = ("M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 "
              "19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z")
ICON_BUBBLE = "M21.99 4c0-1.1-.89-2-1.99-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14l4 4-.01-18z"
ICON_REPLY = "M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z"
WORDS = {"ru": {"reply": "Ответить", "post_time": "5 ч", "com_time": "3 ч"},
         "en": {"reply": "Reply", "post_time": "5h", "com_time": "3h"}}


# ---------- папка залипательных видео ----------

def sat_dir() -> Path:
    return config.CLIPS_DIR / SAT_NAME


def sat_remote(sub: str | None = None) -> str:
    base = f"{config.CLIPS_REMOTE_PATH}/{SAT_NAME}"
    return f"{base}/{sub}" if sub and sub != "all" else base


def safe_folder(name: str) -> str:
    s = re.sub(r"[^\w-]+", "-", (name or "").strip().lower(), flags=re.UNICODE).strip("-_")
    return s[:30]


def sat_folders() -> dict[str, int]:
    """Подпапки и сколько в них видео: {"": в корне, "parkour": 12, ...}."""
    base = sat_dir()
    out: dict[str, int] = {}
    if not base.exists():
        return out
    for f in base.rglob("*"):
        if f.is_file() and f.suffix.lower() in VIDEO_EXT:
            rel = f.relative_to(base).parts
            key = rel[0] if len(rel) > 1 else ""
            out[key] = out.get(key, 0) + 1
    for d in base.iterdir():
        if d.is_dir():
            out.setdefault(d.name, 0)
    return dict(sorted(out.items()))


def sat_pool(sub: str | None) -> list[Path]:
    base = sat_dir()
    root = base / sub if sub and sub != "all" else base
    files = [f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXT] if root.exists() else []
    files = video_only(files)
    if not files and root != base:
        log.warning("в папке залипательных «%s» нет видео, беру из всех", sub)
        files = video_only([f for f in base.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXT]) if base.exists() else []
    return sorted(files)


# ---------- текст ----------

_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️​-‏⁠﻿]")


def clean_text(text: str, keep_paras: bool = True) -> str:
    t = _EMOJI.sub("", str(text or "")).replace("\r", "")
    t = re.sub(r"[*_`#>]+(?=\S)|(?<=\S)[*_`]+", "", t)          # остатки markdown
    t = t.replace("\\n", "\n")
    paras = [" ".join(p.split()) for p in t.split("\n")]
    paras = [p for p in paras if p]
    return ("\n" if keep_paras else " ").join(paras)


def _nick(value, fallback: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", str(value or "").strip().lstrip("@/").replace(" ", "_"), flags=re.UNICODE).strip("_.-")
    return s[:24] or fallback


def detect_lang(text: str) -> str:
    letters = re.findall(r"[^\W\d_]", text or "", re.UNICODE)
    if not letters:
        return "ru"
    cyr = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    return "ru" if cyr / len(letters) >= 0.3 else "en"


def _fake_count(seed: str, lang: str, lo: int, hi: int) -> str:
    rnd = random.Random(zlib.crc32(seed.encode("utf-8")))
    n = rnd.randint(lo, hi)
    if n < 1000:
        return str(n)
    k = n / 1000
    if lang == "ru":
        return f"{k:.1f}".replace(".", ",").replace(",0", "") + " тыс"
    return f"{k:.1f}".replace(".0", "") + "k"


def is_story(data) -> bool:
    return isinstance(data, dict) and ("comment" in data or "post" in data) and "scenes" not in data


def parse(data: dict, ctx: dict) -> dict:
    """Проверка JSON истории: {'errors', 'warnings', 'story', 'meta', 'channel', 'seconds'}."""
    errors: list[str] = []
    warn: list[str] = []
    post_in = data.get("post") if isinstance(data.get("post"), dict) else {"text": data.get("post") or ""}
    com_in = data.get("comment") if isinstance(data.get("comment"), dict) else {"text": data.get("comment") or ""}
    post_text = clean_text(post_in.get("text") or post_in.get("title") or "", keep_paras=False)
    com_text = clean_text(com_in.get("text") or "")
    if not com_text:
        errors.append("comment.text: нет текста истории")
    if not post_text:
        warn.append("post.text пустой: карточка-вопрос будет без текста, ролик начнётся сразу с истории")
    if len(post_text) > MAX_POST:
        errors.append(f"post.text: {len(post_text)} символов, максимум {MAX_POST} (это короткий вопрос)")
    if len(com_text) > MAX_COMMENT:
        errors.append(f"comment.text: {len(com_text)} символов, максимум {MAX_COMMENT}")
    lang = detect_lang(com_text or post_text)
    w = WORDS[lang]
    seconds = (len(post_text) + len(com_text)) / CHARS_PER_SECOND + 1.5
    if seconds > MAX_SECONDS:
        errors.append(f"длина ≈ {seconds:.0f} с, максимум {MAX_SECONDS} с: сократите историю")
    elif seconds > 62:
        warn.append(f"длина ≈ {seconds:.0f} с: дольше минуты (Shorts до 3 минут, но удержание обычно лучше до 60 с)")
    elif seconds < 15:
        warn.append(f"длина ≈ {seconds:.0f} с: очень коротко для истории")

    post_author = _nick(post_in.get("author"), "curious_one")
    com_author = _nick(com_in.get("author"), "storyteller")
    story = {
        "lang": lang,
        "post": {"author": post_author, "time": str(post_in.get("time") or w["post_time"])[:12], "text": post_text,
                 "likes": str(post_in.get("likes") or _fake_count(post_text, lang, 2000, 40000))[:12],
                 "comments": str(post_in.get("comments") or _fake_count(post_text + "c", lang, 300, 6000))[:12],
                 "place": clean_text(post_in.get("place") or "", keep_paras=False)[:28]},
        "comment": {"author": com_author, "time": str(com_in.get("time") or w["com_time"])[:12], "text": com_text,
                    "likes": str(com_in.get("likes") or _fake_count(com_text, lang, 800, 25000))[:12]},
    }

    meta: dict = {"title": clean_text(data.get("title") or post_text or "История", keep_paras=False)[:100],
                  "description": clean_text(data.get("description") or "", keep_paras=False)[:500],
                  "tags": [str(t).strip()[:40] for t in (data.get("tags") or []) if str(t).strip()][:15]}
    v = data.get("voice")
    name = v.get("name") if isinstance(v, dict) else v
    if name:
        if name in config.VOICES:
            meta["voice"] = name
        else:
            warn.append(f"voice: голоса «{name}» нет в списке; возьму голос по языку")
    if isinstance(v, dict) and v.get("rate") is not None:
        try:
            meta["rate"] = max(-50, min(100, int(v["rate"])))
        except (TypeError, ValueError):
            warn.append("voice.rate: не число, пропущено")
    m = data.get("music")
    mood = m.get("mood") if isinstance(m, dict) else m
    if mood:
        mood = str(mood)
        if mood in ("off", "auto") or mood in (ctx.get("music") or []):
            meta["music"] = mood
        else:
            warn.append(f"music: настроения «{mood}» нет ({', '.join(ctx.get('music') or []) or 'музыки нет'}); без музыки")
            meta["music"] = "off"
    if isinstance(m, dict) and m.get("volume") is not None:
        try:
            meta["music_vol"] = max(0, min(100, int(m["volume"])))
        except (TypeError, ValueError):
            pass
    prog = data.get("progress")
    if prog:
        design = prog.get("design") if isinstance(prog, dict) else prog
        if design in PROGRESS:
            meta["progress"] = design
        else:
            warn.append(f"progress: «{design}» неизвестно ({', '.join(PROGRESS)}); пропущено")
    channel = data.get("channel") or ctx.get("channel")
    if not config.has_channel(channel or ""):
        if data.get("channel"):
            warn.append(f"channel: канала «{data.get('channel')}» нет; взят «{ctx.get('channel')}»")
        channel = ctx.get("channel")
    return {"errors": errors, "warnings": warn, "story": story, "meta": meta, "channel": channel, "seconds": seconds}


def summary(res: dict, cfg: dict) -> str:
    s, m = res["story"], res["meta"]
    words = len(s["comment"]["text"].split())
    folders = sat_folders()
    n_sat = sum(folders.values()) if cfg.get("sat", "all") == "all" else folders.get(cfg["sat"], 0)
    sat_name = "все" if cfg.get("sat", "all") == "all" else cfg["sat"]
    voice = voice_for(cfg, m, s["lang"])
    lines = [
        f"📖 История: «{m['title']}»",
        f"Канал: {res['channel']} · язык: {s['lang']} · голос: {voice} · ≈ {res['seconds']:.0f} с",
        f"Пост: @{s['post']['author']}: «{s['post']['text'][:120]}»",
        f"Комментарий: @{s['comment']['author']}, {words} слов",
        f"Оформление: карточка {THEMES[cfg['theme']]}, {LAYOUTS[cfg['layout']]}, подсветка слова: {'да' if cfg.get('hl', True) else 'нет'}",
        f"Залипательные видео: {sat_name} ({n_sat} шт.)",
    ]
    if n_sat == 0:
        lines.append("❗ Залипательных видео нет: добавьте их (📖 Истории → 🧘 Залипательные видео), иначе ролик не соберётся.")
    if res["warnings"]:
        lines.append("\n⚠️ Замечания:\n" + "\n".join(f"• {w}" for w in res["warnings"][:8]))
    return "\n".join(lines)


def voice_for(cfg: dict, meta: dict, lang: str) -> str:
    """Голос: выбранный в оформлении (если он того же языка, что история), иначе из JSON, иначе стандартный для языка."""
    chosen = cfg.get("voice")
    if chosen and chosen != "auto" and chosen[:2].lower() == lang:
        return chosen
    if meta.get("voice") and meta["voice"][:2].lower() == lang:
        return meta["voice"]
    return VOICE_BY_LANG.get(lang, config.TTS_VOICE)


# ---------- время слов ----------

def _norm(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower().replace("ё", "е"), flags=re.UNICODE)


def _weight(tok: str) -> float:
    w = len(_norm(tok)) + 1.5
    if re.search(r"[.!?…]$", tok):
        w += 7
    elif re.search(r"[,;:—–-]$", tok):
        w += 3
    return w


def align(tokens: list[str], words: list[tuple[float, float, str]], speech_end: float) -> list[float]:
    """Время начала каждого слова текста. words - слова Whisper (начало, конец, слово).
    Совпавшие слова берут время Whisper, остальные распределяются между соседями по длине."""
    n = len(tokens)
    times: list[float | None] = [None] * n
    if words:
        a = [_norm(t) for t in tokens]
        b = [_norm(w[2]) for w in words]
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    times[i1 + k] = words[j1 + k][0]
            elif tag == "replace" and (i2 - i1) and (j2 - j1) and abs((i2 - i1) - (j2 - j1)) <= 2:
                for k in range(i2 - i1):   # слово записано иначе (число, дефис): время по порядку внутри куска
                    times[i1 + k] = words[j1 + min(int(k * (j2 - j1) / (i2 - i1)), j2 - j1 - 1)][0]
    # опоры: начало и конец речи
    start = min([t for t in times if t is not None], default=0.05)
    start = min(start, 0.3)
    anchors = [(-1, start)] + [(i, t) for i, t in enumerate(times) if t is not None] + [(n, max(speech_end, start + 0.5))]
    weights = [_weight(t) for t in tokens]
    for (ia, ta), (ib, tb) in zip(anchors, anchors[1:]):
        gap = list(range(ia + 1, ib))
        if not gap:
            continue
        tb = max(tb, ta)
        # слово ia уже стоит в ta, раздаём время между ia и ib пропорционально «весу» слов
        span = [weights[ia]] if ia >= 0 else []
        total_w = sum(span) + sum(weights[i] for i in gap)
        acc = sum(span)
        for i in gap:
            times[i] = ta + (tb - ta) * (acc / total_w if total_w else 0)
            acc += weights[i]
    out, prev = [], 0.0
    for t in times:
        t = max(float(t if t is not None else prev), prev)
        out.append(t)
        prev = t
    return out


# ---------- страницы и карточка ----------

def _esc(w: str) -> str:
    return w.replace("\\", "/").replace("{", "(").replace("}", ")")


def _wrap_idx(idxs: list[int], tokens: list[str], size: float, bold: bool = False) -> list[list[int]]:
    out, cur = [], []
    for i in idxs:
        cand = " ".join(tokens[j] for j in cur + [i])
        if cur and textfit.width(cand, size, bold) > TEXT_W:
            out.append(cur)
            cur = [i]
        else:
            cur.append(i)
    if cur:
        out.append(cur)
    return out


def _layout(units: list[tuple[int, list[int]]], tokens: list[str]) -> tuple[list[tuple[bool, list[int]]], int]:
    """Строки страницы: [(отступ абзаца перед строкой, индексы слов)] и высота текста в пикселях."""
    groups: list[tuple[int, list[int]]] = []
    for para, idxs in units:
        if groups and groups[-1][0] == para:
            groups[-1][1].extend(idxs)
        else:
            groups.append((para, list(idxs)))
    lines = []
    for g, (_, idxs) in enumerate(groups):
        for k, ln in enumerate(_wrap_idx(idxs, tokens, BODY_FS)):
            lines.append((g > 0 and k == 0, ln))
    height = sum(BODY_PITCH + (PARA_GAP if gap else 0) for gap, _ in lines)
    return lines, height


def _units(paras: list[list[int]], tokens: list[str]) -> list[tuple[int, list[int]]]:
    """Предложения: [(номер абзаца, индексы слов)]."""
    out = []
    for p, idxs in enumerate(paras):
        cur = []
        for i in idxs:
            cur.append(i)
            if re.search(r"[.!?…]+[»\"')]*$", tokens[i]):
                out.append((p, cur))
                cur = []
        if cur:
            out.append((p, cur))
    return out


def paginate(paras: list[list[int]], tokens: list[str], budget: int) -> list[list[tuple[int, list[int]]]]:
    """Страницы карточки по предложениям; длинное предложение режется по словам."""
    pages, cur = [], []
    for unit in _units(paras, tokens):
        if _layout(cur + [unit], tokens)[1] <= budget:
            cur = cur + [unit]
            continue
        if cur:
            pages.append(cur)
            cur = []
        if _layout([unit], tokens)[1] <= budget:
            cur = [unit]
            continue
        para, chunk = unit[0], []
        for i in unit[1]:
            if not chunk or _layout([(para, chunk + [i])], tokens)[1] <= budget:
                chunk.append(i)
            else:
                pages.append([(para, chunk)])
                chunk = [i]
        cur = [(para, chunk)]
    if cur:
        pages.append(cur)
    return pages


def _anim(x: float, y: float, an: int = 7) -> str:
    return f"\\an{an}\\move({x:.0f},{y + MOVE:.0f},{x:.0f},{y:.0f},0,{ANIM_MS})\\fad({FADE_MS},0)"


def _ev(a: float, b: float, layer: int, tags: str, body: str) -> str:
    return f"Dialogue: {layer},{_t(a)},{_t(b)},Prog,,0,0,0,,{{{tags}}}{body}"


def _card(a: float, b: float, y0: float, h: float, pal: dict, author: str, meta_line: str,
          foot: list[tuple[str, str]]) -> list[str]:
    """Фон карточки, тень, аватар, ник, строка времени и подвал со значками. Текст рисуется отдельно."""
    x0 = CARD_X
    ev = []
    shape = _rrect(CARD_W, h, RADIUS)
    ev.append(_ev(a, b, 20, f"{_anim(x0, y0 + 10)}\\bord0\\shad0\\blur14\\c&H000000&\\alpha{pal['shadow_a']}\\p4", shape))
    ev.append(_ev(a, b, 21, f"{_anim(x0, y0)}\\bord0\\shad0\\c{_bgr(pal['card'])}\\p4", shape))
    av = AVATAR_COLORS[zlib.crc32(author.encode("utf-8")) % len(AVATAR_COLORS)]
    ax, ay = x0 + PAD, y0 + 40
    ev.append(_ev(a, b, 22, f"{_anim(ax, ay)}\\bord0\\shad0\\c{_bgr(av)}\\p4", _circle(AVATAR_R)))
    letter = next((ch for ch in author if ch.isalnum()), "?").upper()
    ev.append(_ev(a, b, 23, f"{_anim(ax + AVATAR_R, ay + AVATAR_R, 5)}\\fs40\\b1\\bord0\\shad0\\c&HFFFFFF&", _esc(letter)))
    nx = ax + 2 * AVATAR_R + 22
    ev.append(_ev(a, b, 23, f"{_anim(nx, y0 + 46)}\\fs{NAME_FS}\\b1\\bord0\\shad0\\c{_bgr(pal['text'])}", _esc(author)))
    ev.append(_ev(a, b, 23, f"{_anim(nx, y0 + 46 + NAME_FS + 8)}\\fs{META_FS}\\b0\\bord0\\shad0\\c{_bgr(pal['meta'])}",
                  _esc(meta_line)))
    fy = y0 + h - PAD_B - FOOT_H
    fx = x0 + PAD
    for icon, label in foot:
        path = _svg_to_ass(icon, 38)
        ev.append(_ev(a, b, 23, f"{_anim(fx, fy + 3)}\\bord0\\shad0\\c{_bgr(pal['icon'])}\\p4", path))
        tx = fx + 50
        ev.append(_ev(a, b, 23, f"{_anim(tx, fy + 5)}\\fs{FOOT_FS}\\b1\\bord0\\shad0\\c{_bgr(pal['meta'])}", _esc(label)))
        fx = tx + textfit.width(label, FOOT_FS, True) + 46
    return ev


def _text_line(a: float, b: float, x: float, y: float, idxs: list[int], tokens: list[str], times: list[float],
               size: int, bold: bool, pal: dict, hl: bool) -> str:
    """Строка текста: каждое слово проявляется в момент, когда его произносит диктор; текущее слово подсвечено."""
    col, acc = _bgr(pal["text"]), _bgr(pal["hl"])
    parts = []
    for j in idxs:
        ms = int(round((times[j] - a) * 1000))
        nxt = int(round((times[j + 1] - a) * 1000)) if j + 1 < len(times) else ms + 700
        w = _esc(tokens[j])
        if ms <= 30:
            tag = "\\alpha&H00&" + (f"\\c{acc}" if hl and nxt and nxt > 60 else f"\\c{col}")
        else:
            tag = f"\\alpha&HFF&\\t({ms},{ms + REVEAL_MS},\\alpha&H00&)" + (f"\\c{acc}" if hl else f"\\c{col}")
        if hl and nxt is not None and (ms > 30 or nxt > 60):
            tag += f"\\t({max(nxt, 0)},{max(nxt, 0) + 1},\\c{col})"
        parts.append(f"{{{tag}}}{w} ")
    base = f"{_anim(x, y)}\\fs{size}\\b{1 if bold else 0}\\bord0\\shad0\\c{col}\\q2"
    return _ev(a, b, 24, base, "".join(parts).rstrip())


def card_events(story: dict, cfg: dict, tokens: list[str], times: list[float], n_post: int,
                paras: list[list[int]], total: float) -> list[str]:
    pal = COLORS.get(cfg.get("theme"), COLORS["light"])
    layout = cfg.get("layout") if cfg.get("layout") in LAYOUTS else "full"
    hl = bool(cfg.get("hl", True))
    lang = story.get("lang", "ru")
    budget = PAGE_PX[layout]
    pages = paginate(paras, tokens, budget)
    lead = 0.12
    starts = []
    for pg in pages:
        t0 = times[pg[0][1][0]] - lead
        starts.append(max(t0, (starts[-1] + 0.3) if starts else 0.0))
    events: list[str] = []
    post = story["post"]

    def y_of(h: float) -> float:
        return TOP_FULL if layout == "full" else max(60, (960 - h) / 2)

    # карточка поста (вопрос)
    if n_post:
        end = starts[0] if starts else total
        size = TITLE_FS
        lines = _wrap_idx(list(range(n_post)), tokens, size, bold=True)
        while len(lines) * TITLE_PITCH > budget and size > 40:
            size -= 4
            lines = _wrap_idx(list(range(n_post)), tokens, size, bold=True)
        pitch = int(size * 1.3)
        h = BODY_TOP + len(lines) * pitch + FOOT_GAP + FOOT_H + PAD_B
        y0 = y_of(h)
        meta_line = f"{post['place']} · {post['time']}" if post.get("place") else post["time"]
        events += _card(0.0, end, y0, h, pal, post["author"], meta_line,
                        [(ICON_HEART, post["likes"]), (ICON_BUBBLE, post["comments"])])
        for k, ln in enumerate(lines):
            events.append(_text_line(0.0, end, CARD_X + PAD, y0 + BODY_TOP + k * pitch, ln, tokens, times, size, True, pal, hl))
    com = story["comment"]
    reply = WORDS.get(lang, WORDS["ru"])["reply"]
    for p, pg in enumerate(pages):
        a = starts[p]
        b = starts[p + 1] if p + 1 < len(pages) else total
        lines, body_h = _layout(pg, tokens)
        h = BODY_TOP + body_h + FOOT_GAP + FOOT_H + PAD_B
        y0 = y_of(h)
        events += _card(a, b, y0, h, pal, com["author"], com["time"], [(ICON_HEART, com["likes"]), (ICON_REPLY, reply)])
        y = y0 + BODY_TOP
        for gap, ln in lines:
            y += PARA_GAP if gap else 0
            events.append(_text_line(a, b, CARD_X + PAD, y, ln, tokens, times, BODY_FS, False, pal, hl))
            y += BODY_PITCH
    return events


# ---------- видеоряд и сборка ----------

def _sat_items(work: Path, total: float, look: dict, sub: str | None) -> list[dict]:
    pool = sat_pool(sub)
    if not pool:
        raise RuntimeError("нет залипательных видео: добавьте их в 📖 Истории → 🧘 Залипательные видео "
                           f"(или положите в папку {SAT_NAME} внутри _clips на Диске)")
    items, remain, used, i = [], total, set(), 0
    while remain > 0.05:
        cands = [f for f in pool if f not in used] or pool
        clip = library.choose(cands)
        used.add(clip)
        if len(used) >= len(pool):
            used.clear()
        dur = video_duration(clip) or 0.0
        seg = min(remain, SEG_MAX, max(dur - 0.3, 2.0)) if dur > 2.5 else min(remain, SEG_MAX)
        if 0 < remain - seg < 2.5:   # без крошечного последнего куска
            seg = remain
        start = random.uniform(0, dur - seg - 0.2) if dur > seg + 0.4 else 0.0
        items += compose._fixed_parts(work, [(clip, start)], seg, False, look, None, "60:white", n0=i)
        remain -= items[-1]["A"]
        i += 1
    return items


def build(task_dir: Path, meta: dict) -> Path:
    story = meta["story"]
    cfg = {**DEFAULT_CFG, **(meta.get("story_cfg") or {})}
    lang = story.get("lang", "ru")
    voice = meta.get("voice") or VOICE_BY_LANG.get(lang, config.TTS_VOICE)
    rate = meta.get("rate") or None
    mix.sync_clips()
    library.scan()
    work = task_dir / "parts"
    work.mkdir(exist_ok=True)
    post_text = story["post"]["text"]
    com_paras_text = [p for p in story["comment"]["text"].split("\n") if p.strip()]
    narration = (post_text + "\n\n" if post_text else "") + "\n".join(com_paras_text)
    raw = work / "story.mp3"
    tts.synthesize(narration, raw, voice, rate)
    voice_file = compose._trim_speech(raw, work / "story.wav")
    speech_len = audio_duration(voice_file)
    total = speech_len + TAIL

    post_tokens = post_text.split()
    tokens = list(post_tokens)
    paras: list[list[int]] = []
    for ptxt in com_paras_text:
        words = ptxt.split()
        paras.append(list(range(len(tokens), len(tokens) + len(words))))
        tokens += words
    try:
        whisper_words = subtitles.transcribe_words(voice_file, lang)
    except Exception as e:  # noqa: BLE001  - без Whisper слова появляются по длине
        log.warning("Whisper не сработал (%s): время слов по их длине", e)
        whisper_words = []
        meta.setdefault("warnings", []).append("Whisper не сработал: слова появляются по расчёту, а не по озвучке")
    times = align(tokens, whisper_words, speech_len - 0.05)
    matched = sum(1 for w in whisper_words) if whisper_words else 0
    log.info("история: %d слов текста, Whisper %d слов, речь %.1f с", len(tokens), matched, speech_len)

    meta["_ass_extra"] = card_events(story, cfg, tokens, times, len(post_tokens), paras, total)
    look = merged_look({"layout": "half" if cfg.get("layout") == "split" else "fill", "crop_bars": "auto",
                        "progress": {"design": cfg.get("progress") if cfg.get("progress") in PROGRESS else "off"}})
    items = _sat_items(work, total, look, cfg.get("sat"))
    meta["subs"] = False
    meta["look_used"] = {"story": cfg}
    style = compose.resolve_style("none")
    out = compose.assemble(task_dir, items, voice_file, [], meta, style, look, merged_sfx(None))
    shutil.rmtree(work, ignore_errors=True)
    for junk in ("subs.ass", "speech_all_list.txt", "speech_all.wav", "music_base.wav", "music_loop.wav", "music_list.txt"):
        (task_dir / junk).unlink(missing_ok=True)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg не создал final.mp4")
    return out


# ---------- промт для нейросети ----------

GENRES = {
    "creepy": ("Жуткое", "необъяснимое или тревожное событие из обычной жизни; напряжение растёт с каждой фразой; финал оставляет "
                         "мурашки, без прямого объяснения и без сверхъестественного в лоб"),
    "revenge": ("Тихая месть", "рассказчика несправедливо задели, и он изобретательно и законно ставит обидчика на место; "
                               "финал приносит удовлетворение, без насилия"),
    "who": ("Кто прав?", "моральная дилемма: рассказчик сделал спорный поступок и сомневается; честно показаны обе стороны; "
                         "последняя фраза оставляет зрителя решать самому"),
    "work": ("Работа и клиенты", "абсурдный клиент, начальник или коллега; конкретные детали профессии; ироничный финал"),
    "neighbors": ("Соседи и семья", "бытовой конфликт или странность с соседями, роднёй, арендодателем; узнаваемые мелочи; "
                                    "неожиданная развязка"),
    "funny": ("Смешное", "нелепая ситуация, которая шаг за шагом разгоняется до абсурда; комичный финал одной фразой"),
    "twist": ("С поворотом", "история кажется обычной, а последняя фраза переворачивает весь смысл"),
}
LANG_NAMES = {"ru": "русский", "en": "английский (English)"}

EXAMPLE = {
    "title": "Сосед каждую ночь стучал в одно и то же время",
    "description": "История о соседе, который стучал в стену ровно в три ночи.",
    "tags": ["#история", "#соседи", "#жуткое"],
    "voice": {"name": "ru-RU-DmitryNeural", "rate": 0},
    "post": {"author": "quiet_harbor", "time": "6 ч", "text": "Какую привычку соседа вы до сих пор не можете объяснить?",
             "likes": "14,2 тыс", "comments": "2,3 тыс"},
    "comment": {"author": "mila_on_night_shift", "time": "4 ч", "likes": "9,8 тыс",
                "text": "Первая фраза сразу в действие.\nВторой абзац продолжает и добавляет деталь.\nПоследний абзац - сильная точка."},
}


def make_prompt(lang: str = "ru", genre: str | None = None, ctx: dict | None = None, brief: str = "") -> str:
    import json
    ctx = ctx or {}
    gname, gdesc = GENRES.get(genre or "", ("Любой", "выбери сам один из жанров: жуткое, тихая месть, кто прав, работа, соседи, "
                                                    "смешное, с поворотом"))
    voices = [v for v in config.VOICES if v[:2].lower() == lang] or config.VOICES
    ex = dict(EXAMPLE)
    if lang == "en":
        ex = {**ex, "title": "My neighbor knocked at exactly 3 AM every night",
              "description": "A story about a neighbor who knocked on the wall at 3 AM.",
              "tags": ["#story", "#neighbors", "#creepy"], "voice": {"name": "en-GB-RyanNeural", "rate": 0},
              "post": {**ex["post"], "time": "6h", "likes": "14.2k", "comments": "2.3k",
                       "text": "What habit of your neighbor can you still not explain?"},
              "comment": {**ex["comment"], "time": "4h", "likes": "9.8k",
                          "text": "First sentence jumps straight into action.\nSecond paragraph adds a detail.\nLast paragraph lands the ending."}}
    music = ", ".join(ctx.get("music") or []) or "(музыки пока нет: поле music пропусти)"
    time_ex = "«5h», «2d»" if lang == "en" else "«5 ч», «2 дн»"
    num_ex = "«12.4k», «864»" if lang == "en" else "«12,4 тыс», «864»"
    return f"""Ты автор коротких историй для вертикальных видео. Формат: один человек задаёт вопрос, другой отвечает историей в комментарии. Историю придумываешь с нуля: это художественный вымысел, а не пересказ или перевод чужих постов. Верни ТОЛЬКО JSON, без пояснений и без ```.

ЯЗЫК ВСЕХ ТЕКСТОВ: {LANG_NAMES.get(lang, lang)} (ники - латиницей)
ЖАНР: {gname} - {gdesc}
ТЕМА И ПОЖЕЛАНИЯ: {brief or '<впиши свою идею или оставь как есть - тогда придумай сам>'}

КАК УСТРОЕН РОЛИК
- Сначала на экране карточка поста с вопросом (post.text), диктор читает его.
- Затем карточка комментария (comment.text): история от первого лица, диктор читает её целиком, слова появляются на экране по ходу чтения.

post.text - ВОПРОС
- Один вопрос к людям, 6-16 слов, разговорно, вызывает любопытство. Пример: «Какой поступок незнакомца вы до сих пор не можете объяснить?»
- Без капса, без эмодзи, без кликбейтных слов вроде «шок».

comment.text - ИСТОРИЯ
- 130-190 слов (50-70 секунд речи). Первое предложение - сразу в действие или в интригу, без «Это было давно» и без представления себя.
- От первого лица, как будто человек пишет комментарий: простые слова, короткие фразы вперемешку с длинными, живые конкретные детали (время, место, предмет, звук, реплика).
- Это один связный рассказ: каждая фраза продолжает предыдущую («и», «но», «а потом», «поэтому», «только»), напряжение растёт, ближе к концу поворот.
- Последняя фраза - сильная точка: неожиданная деталь или реплика. Без морали, без вопроса к зрителю, без «вот такая история».
- 3-6 абзацев, абзацы разделяй символом \\n внутри строки.
- Для озвучки: числа словами, без аббревиатур, без эмодзи, без скобок и без markdown.
- Персонажи вымышленные: обычные имена или «сосед», «начальница». Никаких реальных людей, адресов, телефонов, названий реальных компаний в плохом свете.
- Не пиши, что это пост с Reddit или другой площадки, не упоминай лайки, подписку и комментарии.

НИКИ И ЦИФРЫ НА КАРТОЧКАХ
- post.author и comment.author - разные придуманные ники латиницей в нижнем регистре, 6-20 символов, можно с _ и цифрами (например quiet_harbor, mila_on_night_shift). Не используй ники реальных людей и брендов.
- time - сколько времени назад: {time_ex}. likes и comments - правдоподобные числа строкой: {num_ex}. Комментарий моложе поста.

ПРОЧИЕ ПОЛЯ
- title: интригующее название ролика до 60 символов (не повторяй вопрос дословно). description: 1-2 предложения. tags: 3-5 хештегов.
- voice.name: один из {', '.join(voices)}; rate от -20 до 20 (0 - обычная скорость).
- music (необязательно): {{"mood": "<одно из: {music}>", "volume": 8}} - громкость 5-12, чтобы не мешать голосу.

ПЕРЕД ОТВЕТОМ проверь: история читается вслух на одном дыхании, каждая фраза тянет к следующей, в ней 130-190 слов, а финал бьёт.

ПРИМЕР ФОРМАТА (только структура: сюжет, ники и фразы не копируй)
{json.dumps(ex, ensure_ascii=False, indent=2)}
"""
