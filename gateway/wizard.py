"""Мастер «Создать»: конструктор ролика из 6 блоков на кнопках Telegram.

1 Видеоряд · 2 Речь · 3 Музыка (+ звуковые эффекты) · 4 Субтитры · 5 Стиль · 6 Канал -> «Собрать» -> рецепт в очередь.
Стиль (блок 5) делится на экраны: монтаж, компоновка, скорость и эффекты, текст на экране, логотип.
Состояние хранится в SQLite по пользователю (gateway.state), поэтому переживает перезапуск бота.
Пресеты сохраняют все настройки, «Сюрприз» собирает случайный вид ролика.
"""
import asyncio
import json
import logging
import random
import re
import shutil
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config, db, library, phrases, profile
from common.captions import parse_caption
from common.looks import (CAPTION_MODES, CAPTION_SIZES, CLIP_LENS, CROP_BARS, CTA_PRESETS, FRAME_COLORS, FRAME_SIZES, FX, HOOK_DURS, HOOK_POS,
                          HOOK_PRESETS, HOOK_SIZES, HOOK_STARTS, LAYOUTS, LOGO_POS, MIN_HEIGHTS, ORIG_VOLS, POS, PROGRESS_COLORS,
                          PROGRESS_DESIGNS, PROGRESS_POS, SPEEDS, TEXT_STYLES, CTA_WHEN)
from common.media import audio_duration
from gateway import cutui, materials, sayui, scenario_ui, state
from gateway.state import load, save, waiting  # noqa: F401  (waiting используется bot.py)

log = logging.getLogger("wizard")
from gateway.ui import safe_edit  # noqa: E402

router = Router()
router.include_router(materials.router)
router.include_router(scenario_ui.router)
router.include_router(cutui.router)
from gateway import styleui  # noqa: E402
router.include_router(styleui.router)
from gateway import ideasui  # noqa: E402
router.include_router(ideasui.router)
router.include_router(sayui.router)
from gateway import storyui  # noqa: E402
router.include_router(storyui.router)
from gateway import smartui  # noqa: E402
router.include_router(smartui.router)
from gateway import plannerui  # noqa: E402
router.include_router(plannerui.router)

ALLOWED_EXT = {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".flac", ".opus"}

VIDEO_KINDS = {"bg": "🖼 Фон", "clips": "🎞 Нарезка из клипов", "own": "🎥 Моё видео"}
SPEECH_KINDS = {"none": "Нет", "audio": "🎙 Моё аудио", "text": "⌨️ Текст → озвучка", "translate": "🌐 Перевод речи из видео"}
LANGS = {"en": "English", "ru": "Русский", "de": "Deutsch", "es": "Español", "fr": "Français"}
RATES = (-20, -10, 0, 10, 20, 35)
PHRASES = {
    "ru": "Привет! Так звучит этот голос.", "en": "Hi! This is how this voice sounds.",
    "de": "Hallo! So klingt diese Stimme.", "es": "¡Hola! Así suena esta voz.", "fr": "Salut ! Voici le son de cette voix.",
}
MODES = {"off": "нет", "title": "из заголовка", "text": "свой текст", "random": "случайный из списка"}
OWN_PAGE = 8
PICK_PAGE = 8


def _styles():
    from core import montage
    return montage.labels()


def _sub_looks():
    from core.subtitles import SUB_LOOKS
    return SUB_LOOKS


def _default(user_id: int) -> dict:
    return state.default(user_id)


# ---------- клавиатуры ----------

def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


class Rows:
    def __init__(self):
        self.rows: list[list[InlineKeyboardButton]] = []

    def row(self, *btns: InlineKeyboardButton) -> None:
        self.rows.append(list(btns))

    def grid(self, btns: list[InlineKeyboardButton], width: int) -> None:
        for i in range(0, len(btns), width):
            self.rows.append(btns[i:i + width])

    def markup(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=self.rows)


def _mark(cond: bool) -> str:
    return "✅ " if cond else ""


# ---------- тексты ----------

def _fmt_time(t):
    return "-" if t is None else f"{int(t // 60)}:{int(t % 60):02}"


def _th_label(v: dict) -> str:
    from core import mix
    items = mix.norm_themes(v.get("theme"))
    if len(items) > 1:
        return f"темы {' + '.join(items)}" + (" (поровну)" if v.get("balance") else "")
    return f"тема {items[0] if items else 'любая'}"


def _video_text(st: dict) -> str:
    v = st["video"]
    ranged = v.get("len") not in (None, "eq")
    ln = f" · клипы {CLIP_LENS.get(v.get('len'), '')}" if ranged else ""
    if v.get("picks") and v["kind"] in ("clips", "own") and not v.get("file"):
        return f"выбранные ролики · {len(v['picks'])} шт · {v['seconds']} с{ln}"
    if v["kind"] == "bg":
        return f"фон · тема {_th_label(v)}"
    if v["kind"] == "clips":
        if ranged:
            return f"нарезка из клипов · {_th_label(v)} · {v['seconds']} с{ln}"
        return f"нарезка из клипов · {_th_label(v)} · {v['clips']} шт · {v['seconds']} с"
    if v["file"]:
        frag = f" · фрагмент {_fmt_time(v['start'])}–{_fmt_time(v['end'])}" if v["start"] is not None else " · целиком"
        return f"моё видео · {v['file']}{frag}"
    return f"моё видео · случайные {v['clips']} шт · {v['seconds']} с"


def _voice_label(v: str) -> str:
    parts = v.split("-")
    if len(parts) < 3:
        return v
    return f"{parts[-1].removesuffix('Neural')} {parts[0]}-{parts[1]}"


def _rate_text(rate: int) -> str:
    return "норм." if not rate else f"{rate:+d}%"


def _effective_voice(sp: dict) -> str:
    """Голос, которым реально будет озвучено: при переводе он зависит от языка перевода."""
    from core import dub
    if sp["kind"] == "translate":
        try:
            return dub.pick_voice(sp["target"], sp.get("voice"))
        except RuntimeError:
            return sp.get("voice") or config.TTS_VOICE
    return sp.get("voice") or config.TTS_VOICE


def _speech_text(st: dict) -> str:
    sp = st["speech"]
    if sp["kind"] == "translate":
        return f"перевод речи → {sp['target']} · {_voice_label(_effective_voice(sp))} · {_rate_text(sp['rate'])}"
    if sp["kind"] == "text":
        return f"текст → озвучка · {_voice_label(_effective_voice(sp))} · {_rate_text(sp['rate'])}"
    return SPEECH_KINDS[sp["kind"]].replace("🎙 ", "")


def _orig_text(st: dict) -> str:
    o = st["video"].get("orig_sound")
    if o is None:
        return "авто"
    vol = st["video"].get("orig_vol")
    return ("да" + (f" · {vol}%" if vol else "")) if o else "нет"


def _hook_line(h: dict) -> str:
    if h["mode"] == "off":
        return "нет"
    what = {"title": "из заголовка", "random": "случайный из списка"}.get(h["mode"], f"«{h['text'] or 'не задан'}»")
    dur = "авто" if h["dur"] == "auto" else f"{h['dur']} с"
    return f"{what} · {HOOK_POS[h['pos']]} · {h['size'].upper()} · {TEXT_STYLES[h['style']]} · с {h['start']} с, показ {dur}"


def _cta_line(c: dict) -> str:
    if c["mode"] == "off":
        return "нет"
    dur = "авто" if c["dur"] == "auto" else f"{c['dur']} с"
    what = "случайный из списка" if c["mode"] == "random" else f"«{c['text']}»"
    return f"{what} · {HOOK_POS[c['pos']]} · {c['size'].upper()} · {TEXT_STYLES[c['style']]} · {CTA_WHEN[c['when']]}, показ {dur}"


def _caption_line(cp: dict) -> str:
    if cp["mode"] == "off":
        return "нет"
    what = "заголовок ролика" if cp["mode"] == "title" else f"«{cp['text'] or 'не задан'}»"
    return f"{what} · {HOOK_POS[cp['pos']]} · {CAPTION_SIZES[cp['size']]} · {TEXT_STYLES[cp['style']]} · весь ролик"


def _progress_line(pg: dict) -> str:
    if pg["design"] == "off":
        return "нет"
    label = f" · текст «{pg['label']}»" if pg.get("label") else ""
    return (f"{PROGRESS_DESIGNS[pg['design']].lower()} · {PROGRESS_POS[pg['pos']]} · цвет {PROGRESS_COLORS[pg['color']][0]}"
            f" · кнопки лайка и подписки в конце: {'да' if pg['end'] else 'нет'}{label}")


def _sfx_text(st: dict) -> str:
    s = st["sfx"]
    parts = [n for k, n in (("cuts", "на стыках"), ("start", "в начале")) if s[k]]
    if not parts:
        return "нет"
    return f"{' + '.join(parts)} · {s['tag'] or 'любые'} · {s['vol']}%"


def _look_lines(st: dict) -> str:
    L = st["look"]
    line1 = f"{_styles().get(st['style'], st['style'])} · {LAYOUTS[L['layout']].lower()} · скорость {SPEEDS[L['speed']]}"
    extra = []
    if L["fx"]:
        extra.append("эффекты: " + ", ".join(FX[f] for f in L["fx"]))
    if L["layout"] == "frame":
        extra.append(f"рамка: {FRAME_SIZES[L['frame']['size']]}, {FRAME_COLORS.get(L['frame']['color'], L['frame']['color'])}")
    if L["hook"]["mode"] != "off":
        extra.append(f"хук: {MODES[L['hook']['mode']]}, {HOOK_POS[L['hook']['pos']]}")
    if L["cta"]["mode"] != "off":
        extra.append("призыв: " + ("случайный" if L["cta"]["mode"] == "random" else f"«{L['cta']['text']}»"))
    if L["caption"]["mode"] != "off":
        extra.append("подпись: " + ("заголовок" if L["caption"]["mode"] == "title" else "свой текст"))
    if L["progress"]["design"] != "off":
        extra.append(f"полоска: {PROGRESS_DESIGNS[L['progress']['design']].lower()}")
    if L.get("crop_bars", "off") != "off":
        extra.append(f"обрезка полос: {CROP_BARS[L['crop_bars']]}")
    if L["logo"] != "off":
        extra.append(f"лого: {LOGO_POS[L['logo']]}")
    return line1 + ("\n      " + " · ".join(extra) if extra else "")


def main_text(st: dict) -> str:
    music = "нет" if st["music"] == "off" else f"{st['music']} · {st['vol']}%"
    sub = "нет" if not st["subs"] else (
        f"{_sub_looks()[st['look']['sub']]['label']} · {POS[st['look']['subpos']]}"
        + (" · по звуку видео" if st["subs_src"] == "orig" and st["speech"]["kind"] == "none" else "")
        + (f" · перевод → {st['subs_lang']}" if st.get("subs_lang") else ""))
    return (
        f"🎬 Создать ролик\n\n"
        f"1️⃣ Видеоряд: {_video_text(st)}\n"
        f"2️⃣ Речь: {_speech_text(st)}\n"
        f"3️⃣ Музыка: {music} · звук оригинала: {_orig_text(st)}\n"
        f"      звуковые эффекты: {_sfx_text(st)}\n"
        f"4️⃣ Субтитры: {sub}\n"
        f"5️⃣ Стиль: {_look_lines(st)}\n"
        f"6️⃣ Канал: {st['channel']}"
    )


# ---------- экраны ----------

def main_kb():
    r = Rows()
    r.grid([_b(n, f"w:b:{i}") for i, n in enumerate(
        ["1️⃣ Видеоряд", "2️⃣ Речь", "3️⃣ Музыка", "4️⃣ Субтитры", "5️⃣ Стиль", "6️⃣ Канал"], 1)], 2)
    r.row(_b("🚀 Собрать", "w:go"))
    r.row(_b("💾 Пресеты", "w:b:p"), _b("🎲 Сюрприз", "w:p:rnd"))
    r.row(_b("♻️ Сброс", "w:reset"), _b("⬅ Меню", "m:back"))
    return r.markup()


def _themes(st: dict, kind: str) -> list[str]:
    if kind == "bg":
        return sorted(d.name for d in config.BACKGROUNDS_DIR.iterdir() if d.is_dir()) if config.BACKGROUNDS_DIR.exists() else []
    from core import mix
    return mix.list_themes()


def _own_files() -> list[Path]:
    from core import mix
    return mix.own_clips()


def _voices_for(sp: dict) -> list[str]:
    from core import dub
    if sp["kind"] == "translate":
        voices = [v for v in config.VOICES if v[:2] == sp["target"]]
        return voices or ([dub.DEFAULT_VOICES[sp["target"]]] if sp["target"] in dub.DEFAULT_VOICES else [])
    return list(config.VOICES)


def _logo_file(channel: str) -> Path | None:
    found = sorted(config.ASSETS_DIR.glob(f"logo_{channel}.*"))
    return found[0] if found else None


def block_screen(st: dict, block, uid: int | None = None):
    r = Rows()
    back = "w:main"
    L, S = st["look"], st["sfx"]
    if block == 1:
        v = st["video"]
        text = "1️⃣ Видеоряд: откуда берём картинку"
        r.row(*[_b(_mark(v["kind"] == k) + name, f"w:v:kind:{k}") for k, name in VIDEO_KINDS.items()])
        picks = v.get("picks") or []
        if v["kind"] in ("bg", "clips") and not picks:
            themes = _themes(st, v["kind"])
            st["lists"]["themes"] = themes
            from core import mix
            chosen = [t.lower() for t in mix.norm_themes(v["theme"])]
            r.row(_b(_mark(not chosen) + "Любая тема", "w:v:theme:-1"))
            r.grid([_b(_mark(t.lower() in chosen) + t, f"w:v:theme:{i}") for i, t in enumerate(themes[:24])], 2)
            text += "\nТемы можно отмечать несколько: клипы возьмутся из всех отмеченных."
            if len(chosen) > 1 and v["kind"] == "clips":
                r.row(_b(_mark(not v.get("balance")) + "🎲 Вперемешку", "w:v:bal:0"),
                      _b(_mark(bool(v.get("balance"))) + "⚖️ Поровну из тем", "w:v:bal:1"))
                text += "\n«Вперемешку»: клипы из любых тем случайно. «Поровну»: темы чередуются, у каждой примерно равная доля."
        if v["kind"] == "clips" or (picks and v["kind"] == "own"):
            r.row(*[_b(_mark(v["len"] == k) + "⏱ " + name, f"w:v:len:{k}") for k, name in CLIP_LENS.items()])
            text += ("\nДлина клипа: у каждого клипа своя длина из диапазона, клипы не повторяются, пока есть другие. "
                     "«Равные»: длительность делится поровну между N клипами.")
        if v["kind"] == "clips" and not picks:
            r.row(*[_b(_mark(v.get("min_h", 0) == h_) + f"🔍 {name}", f"w:v:minh:{h_}") for h_, name in MIN_HEIGHTS.items()])
            text += "\nКачество клипов: отсеивает клипы с низким разрешением (по высоте кадра)."
        if (v["kind"] == "clips" and v["len"] == "eq" or (v["kind"] == "own" and not v["file"])) and not picks:
            r.grid([_b(_mark(v["clips"] == n) + f"{n} клипа", f"w:v:n:{n}") for n in (2, 3, 4, 6, 8, 10)], 3)
        if v["kind"] in ("clips", "own") and not v["file"]:
            r.row(_b("🎯 Выбрать ролики" + (f" ({len(picks)})" if picks else ""), "w:b:pk"))
            if picks:
                text += f"\nВыбрано роликов: {len(picks)}. Они склеятся в том порядке, в каком вы их выбрали."
                r.row(_b("🧹 Сбросить выбор", "w:k:clr1"))
        text += "\nДлительность нужна, если нет речи (при озвучке она берётся из аудио)."
        r.row(*[_b(_mark(v["seconds"] == s) + f"{s} с", f"w:v:sec:{s}") for s in (15, 30, 45)])
        r.row(*[_b(_mark(v["seconds"] == s) + f"{s} с", f"w:v:sec:{s}") for s in (60, 90)])
        if v["kind"] == "own":
            titles = library.own_titles()
            own = sorted((f.name for f in _own_files()), key=lambda n: (titles.get(n) or n).lower())
            st["lists"]["own"] = own
            pages = max((len(own) - 1) // OWN_PAGE + 1, 1)
            page = min(st["lists"].get("own_page", 0), pages - 1)
            r.row(_b(_mark(not v["file"]) + "🎲 Случайное", "w:v:file:-1"))
            r.grid([_b(_mark(v["file"] == name) + (titles.get(name) or name)[:30], f"w:v:file:{page * OWN_PAGE + i}")
                    for i, name in enumerate(own[page * OWN_PAGE:(page + 1) * OWN_PAGE])], 1)
            if pages > 1:
                r.row(_b("◀", f"w:v:page:{(page - 1) % pages}"), _b(f"{page + 1}/{pages}", "w:v:page:%d" % page),
                      _b("▶", f"w:v:page:{(page + 1) % pages}"))
            if any(n not in titles for n in own):
                r.row(_b("🏷 Подтянуть названия", "w:v:names"))
            if v["file"]:
                r.row(_b("✂️ Фрагмент", "w:v:frag"), _b("Целиком", "w:v:whole"))
            r.row(_b("🔄 Обновить с Диска", "w:v:sync"))
        r.row(_b("🔎 Найти материалы", "src:ask"), _b("➕ По ссылке", "src:url"))
    elif block == "pk":
        v = st["video"]
        cfg = st["lists"].setdefault("pk", {"src": "clip", "theme": None, "page": 0})
        picks = v.get("picks") or []
        text = ("🎯 Выбор роликов для сборки\nНажимайте на ролики: порядок выбора = порядок в видео. "
                f"Выбрано: {len(picks)}.")
        r.row(_b(_mark(cfg["src"] == "clip") + "🎞 Клипы", "w:k:src:clip"), _b(_mark(cfg["src"] == "own") + "🎥 Мои видео", "w:k:src:own"))
        if cfg["src"] == "clip":
            themes = [t for t, _ in library.tag_counts("clip") if t != "без-темы"]
            st["lists"]["pk_themes"] = themes
            r.row(_b(_mark(cfg["theme"] is None) + "Все темы", "w:k:theme:-1"))
            r.grid([_b(_mark(cfg["theme"] == t) + t, f"w:k:theme:{i}") for i, t in enumerate(themes[:24])], 3)
        rows = library.search(cfg["src"], [], limit=100000)
        if cfg["src"] == "clip" and cfg["theme"]:
            rows = [a for a in rows if cfg["theme"] in (a["tags"] or "").split()]
        rows = [a for a in rows if (config.ROOT / a["path"]).exists()]
        rows.sort(key=lambda a: (a["title"] or Path(a["path"]).name).lower())
        rows = rows[:400]
        st["lists"]["pk_items"] = [a["path"] for a in rows]
        pages = max((len(rows) - 1) // PICK_PAGE + 1, 1)
        page = min(cfg.get("page", 0), pages - 1)
        cfg["page"] = page
        if not rows:
            text += "\nВ этом разделе пока нет видео."
        for i, a in enumerate(rows[page * PICK_PAGE:(page + 1) * PICK_PAGE]):
            idx = page * PICK_PAGE + i
            pos = picks.index(a["path"]) + 1 if a["path"] in picks else 0
            dur = f" · {a['duration']:.0f}с" if a.get("duration") else ""
            name = ((a["title"] or Path(a["path"]).name)[:28]) + dur
            r.row(_b((f"{pos}✅ " if pos else "▫️ ") + name, f"w:k:t:{idx}"))
        if pages > 1:
            r.row(_b("◀", f"w:k:pg:{(page - 1) % pages}"), _b(f"{page + 1}/{pages}", f"w:k:pg:{page}"), _b("▶", f"w:k:pg:{(page + 1) % pages}"))
        r.row(_b("✅ Вся страница", "w:k:all"), _b("🧹 Очистить", "w:k:clr"))
        back = "w:b:1"
    elif block == 2:
        sp = st["speech"]
        text = "2️⃣ Речь: чей голос будет в ролике"
        r.grid([_b(_mark(sp["kind"] == k) + name, f"w:s:kind:{k}") for k, name in SPEECH_KINDS.items()], 2)
        if sp["kind"] == "translate":
            text += ("\nРечь берётся из видеоряда («Моё видео» или клипы нарезок), переводится и озвучивается. "
                     "В ролик идут только клипы, где есть речь на другом языке.")
            r.grid([_b(_mark(sp["target"] == k) + name, f"w:s:lang:{k}") for k, name in LANGS.items()], 3)
        if sp["kind"] in ("text", "translate"):
            voices = _voices_for(sp)
            st["lists"]["voices"] = voices
            eff = _effective_voice(sp)
            text += f"\n\n🎙 Голос: {_voice_label(eff)} · скорость речи: {_rate_text(sp['rate'])}"
            prof_rate = int(profile.get(st["channel"]).get("rate") or 0)
            if prof_rate and not sp["rate"]:
                text += f" (из профиля канала: {_rate_text(prof_rate)})"
            r.grid([_b(_mark(v == eff) + _voice_label(v), f"w:s:voice:{i}") for i, v in enumerate(voices)], 2)
            r.grid([_b(_mark(sp["rate"] == x) + ("🐢 " if x < 0 else "🐇 " if x > 0 else "") + _rate_text(x), f"w:s:rate:{x}")
                    for x in RATES], 3)
            r.row(_b("🎧 Прослушать", "w:s:prev"), _b("💾 Голос в профиль канала", "w:s:save"))
    elif block == 3:
        text = "3️⃣ Музыка"
        tags = [t for t, _ in library.tag_counts("music") if t != "без-темы"]
        st["lists"]["tags"] = tags
        r.row(_b(_mark(st["music"] == "off") + "Нет", "w:m:set:-2"), _b(_mark(st["music"] == "auto") + "Авто", "w:m:set:-1"))
        r.grid([_b(_mark(st["music"] == t) + t, f"w:m:set:{i}") for i, t in enumerate(tags[:12])], 2)
        r.row(*[_b(_mark(st["vol"] == vol) + f"🔉 {vol}%", f"w:m:vol:{vol}") for vol in (5, 12, 25, 40)])
        ov = st["video"]
        r.row(*[_b(_mark(ov.get("orig_sound") == val) + f"🔊 Звук клипов: {name}", f"w:m:orig:{key}")
                for key, name, val in (("auto", "авто", None), ("on", "да", True), ("off", "нет", False))])
        if ov.get("orig_sound") is True:
            r.row(*[_b(_mark(ov.get("orig_vol") == n) + f"громкость {n}%", f"w:m:ovol:{n}") for n in ORIG_VOLS])
            r.row(_b(f"🎚 Автоприглушение: {'да' if ov.get('duck', True) else 'нет'}", "w:m:duck"))
        r.row(_b(f"🔔 Звуковые эффекты: {_sfx_text(st)}", "w:b:31"))
        text += ("\nЗвук клипов (лай, голоса, шум): «да» оставляет его в ролике, «нет» выключает, «авто» включает, если нет озвучки "
                 "(при озвучке или переводе речь глушит звук клипа: пока говорит голос, оригинал почти не слышен, в паузах возвращается "
                 "на 50%). Громкость каждого клипа выравнивается автоматически. «Автоприглушение» - звук клипа тише под речью "
                 "и музыка тише под звуком клипа; выключите, чтобы оригинал всё время звучал ровно (10% под речью).")
    elif block == 31:
        text = ("🔔 Звуковые эффекты\nКороткие звуки поверх ролика: свист на стыках клипов, удар или «поп» в начале. "
                "Встроенные звуки уже есть; свои кладите на Диск в ShortsFactory/_clips/_sfx/<тег>/ (потом /lib → Обновить).")
        if not library.tag_counts("sfx"):
            library.scan()
        tags = [t for t, _ in library.tag_counts("sfx") if t != "без-темы"]
        st["lists"]["sfxtags"] = tags
        r.row(_b(f"✂️ На стыках клипов: {'да' if S['cuts'] else 'нет'}", "w:x:cuts"))
        r.row(_b(f"▶️ В начале ролика: {'да' if S['start'] else 'нет'}", "w:x:start"))
        r.grid([_b(_mark(S["tag"] is None) + "Любые", "w:x:tag:-1")]
               + [_b(_mark(S["tag"] == t) + t, f"w:x:tag:{i}") for i, t in enumerate(tags[:10])], 3)
        r.row(*[_b(_mark(S["vol"] == x) + f"🔉 {x}%", f"w:x:vol:{x}") for x in (30, 60, 100)])
        back = "w:b:3"
    elif block == 4:
        text = "4️⃣ Субтитры"
        r.row(_b(_mark(not st["subs"]) + "Нет", "w:c:subs:0"), _b(_mark(st["subs"]) + "Да", "w:c:subs:1"))
        r.row(_b(_mark(st["subs_src"] == "speech") + "🗣 По речи ролика", "w:c:subsrc:speech"),
              _b(_mark(st["subs_src"] == "orig") + "🎬 По звуку видео", "w:c:subsrc:orig"))
        text += ("\n«По речи ролика»: озвучка, ваше аудио или перевод. «По звуку видео»: бот распознаёт речь из самих клипов "
                 "(работает, когда в блоке «Речь» выбрано «Нет»; язык определяется автоматически, рендер дольше).")
        sl = st.get("subs_lang")
        r.row(_b(_mark(not sl) + "🌐 Как в речи", "w:c:sublang:same"))
        r.grid([_b(_mark(sl == k) + name, f"w:c:sublang:{k}") for k, name in LANGS.items()], 3)
        text += ("\n🌐 Язык субтитров: «как в речи» или перевод на другой язык - речь при этом остаётся как есть "
                 "(например, оригинальный звук клипа и английские титры).")
        r.grid([_b(_mark(L["sub"] == k) + d["label"], f"w:l:sub:{k}") for k, d in _sub_looks().items()], 2)
        r.row(*[_b(_mark(L["subpos"] == k) + name, f"w:l:subpos:{k}") for k, name in POS.items()])
        text += "\nВид: цвет и оформление. Положение: «низ» и «верх» подняты, чтобы не попасть под интерфейс Shorts."
    elif block == 5:
        text = "5️⃣ Стиль: что увидит зритель"
        r.row(_b(f"🎞 Монтаж: {_styles().get(st['style'], st['style'])}", "w:b:51"))
        r.row(_b(f"📐 Компоновка: {LAYOUTS[L['layout']]}", "w:b:52"))
        r.row(_b(f"⏩ Скорость и эффекты: {SPEEDS[L['speed']]}" + (f" + {len(L['fx'])}" if L["fx"] else ""), "w:b:53"))
        r.row(_b("📝 Текст на экране: хук и призыв", "w:b:54"))
        r.row(_b(f"📶 Полоска прогресса: {PROGRESS_DESIGNS[L['progress']['design']]}", "w:b:56"))
        r.row(_b(f"🏷 Логотип: {LOGO_POS[L['logo']]}", "w:b:55"))
    elif block == 51:
        text = "🎞 Монтаж: переходы, зум, цвет\n★ - ваши стили. Свой стиль: «Конструктор» (переходы, зум, цвет, эффекты)."
        r.row(_b("🛠 Конструктор: собрать свой стиль", "ms:hub"))
        r.grid([_b(_mark(st["style"] == k) + name[:1].upper() + name[1:], f"w:c:style:{k}") for k, name in _styles().items()], 2)
        back = "w:b:5"
    elif block == 52:
        text = ("📐 Компоновка кадра\nЗаполнить: клип растягивается на весь кадр (края обрезаются). Размытый фон: клип целиком по центру, "
                "фон из него же. Сплит: два клипа друг над другом. Рамка: клип в цветной рамке.")
        r.grid([_b(_mark(L["layout"] == k) + name, f"w:l:layout:{k}") for k, name in LAYOUTS.items()], 2)
        if L["layout"] == "frame":
            fr = L["frame"]
            custom = fr["color"] not in FRAME_COLORS
            text += (f"\n\n🖼 Рамка: {FRAME_SIZES[fr['size']]}, цвет {fr['color'] if custom else FRAME_COLORS[fr['color']]}. "
                     "Чем тоньше рамка, тем крупнее клип. «Размытый фон»: вместо цвета размытая копия самого клипа.")
            r.row(*[_b(_mark(fr["size"] == k) + name, f"w:l:fsize:{k}") for k, name in FRAME_SIZES.items()])
            r.grid([_b(_mark(fr["color"] == k) + name, f"w:l:fcol:{k}") for k, name in FRAME_COLORS.items()], 2)
            r.row(_b(_mark(custom) + "✏️ Свой цвет (#RRGGBB)", "w:l:fcustom"))
        text += ("\n\n✂️ Чёрные полосы по краям клипа: автоматически находятся (по трём фрагментам клипа) и обрезаются до растяжки. "
                 "Обрезаются только чёрные поля, серые и цветные остаются; слишком тёмное видео не трогается.")
        r.row(*[_b(_mark(L.get("crop_bars", "off") == k) + name, f"w:l:crop:{k}") for k, name in CROP_BARS.items()])
        back = "w:b:5"
    elif block == 53:
        text = ("⏩ Скорость видео и эффекты\nСкорость меняет картинку (оригинальный звук подстраивается). "
                "«Микс» — у каждого клипа своя. При переводе речи скорость всегда 1×, чтобы не разойтись с озвучкой.")
        r.grid([_b(_mark(L["speed"] == k) + name, f"w:l:speed:{k}") for k, name in SPEEDS.items()], 3)
        r.row(*[_b(("✅ " if k in L["fx"] else "") + name, f"w:l:fx:{k}") for k, name in FX.items()])
        back = "w:b:5"
    elif block == 54:
        h, c = L["hook"], L["cta"]
        text = ("📝 Текст на экране\nХук — крупный текст в первые секунды (цепляет внимание). Призыв — плашка: «подпишись», «лайк». "
                "Подпись — текст на весь ролик, например название снизу.\n"
                "Положение, размер, оформление и время показа настраиваются отдельно для каждого. "
                "Хук и призыв могут быть случайными из списка канала: каждый ролик берёт новую фразу.\n\n"
                f"Хук: {_hook_line(h)}\nПризыв: {_cta_line(c)}\nПодпись: {_caption_line(L['caption'])}")
        r.row(_b("🪝 Настроить хук", "w:b:541"))
        r.row(_b("📣 Настроить призыв", "w:b:542"))
        r.row(_b(f"🔤 Подпись на всё видео: {CAPTION_MODES[L['caption']['mode']]}", "w:b:543"))
        back = "w:b:5"
    elif block == 541:
        h = L["hook"]
        text = (f"🪝 Хук: {_hook_line(h)}\n«Из заголовка» берёт заголовок из вашей подписи «Заголовок | Описание | #теги».")
        r.grid([_b(_mark(h["mode"] == k) + name, f"w:l:hook:{k}") for k, name in MODES.items()], 2)
        n_h = len(phrases.custom(st["channel"], "hook"))
        r.row(_b(f"🗂 Мои фразы хука: {n_h if n_h else 'стандартные'}", "w:b:548"))
        if h["mode"] == "random":
            text += "\n«Случайный из списка»: каждый ролик берёт новую фразу из списка канала, подряд одна и та же не повторяется."
        if h["mode"] == "text":
            r.row(_b("✏️ Свой текст хука", "w:l:hooktext"))
            r.grid([_b(_mark(h["text"] == p_) + p_, f"w:l:hookp:{i}") for i, p_ in enumerate(HOOK_PRESETS)], 2)
        if h["mode"] != "off":
            r.grid([_b(_mark(h["pos"] == k) + name, f"w:l:hpos:{k}") for k, name in HOOK_POS.items()], 3)
            r.row(*[_b(_mark(h["size"] == k) + f"Размер {name}", f"w:l:hsize:{k}") for k, name in HOOK_SIZES.items()])
            r.grid([_b(_mark(h["style"] == k) + name, f"w:l:hstyle:{k}") for k, name in TEXT_STYLES.items()], 2)
            r.row(*[_b(_mark(float(h["start"]) == float(k)) + (f"с {k} с" if k else "сразу"), f"w:l:hstart:{k}") for k in HOOK_STARTS])
            r.row(*[_b(_mark(h["dur"] == k) + ("показ: авто" if k == "auto" else f"{k} с"), f"w:l:hdur:{k}") for k in HOOK_DURS])
        back = "w:b:54"
    elif block == 542:
        c = L["cta"]
        text = (f"📣 Призыв: {_cta_line(c)}\n"
                "Совет: если в клипах есть вшитые подписи (обычно внизу), поставьте призыв вверх или выше центра.")
        r.row(_b(_mark(c["mode"] == "off") + "Призыв: нет", "w:l:cta:off"))
        r.grid([_b(_mark(c["mode"] == "text" and c["text"] == p) + p, f"w:l:cta:{i}") for i, p in enumerate(CTA_PRESETS)], 2)
        n_c = len(phrases.custom(st["channel"], "cta"))
        r.row(_b(_mark(c["mode"] == "random") + "🎲 Случайный из списка", "w:l:cta:rnd"),
              _b(f"🗂 Мои фразы: {n_c if n_c else 'стандартные'}", "w:b:549"))
        r.row(_b("✏️ Свой призыв", "w:l:ctatext"))
        if c["mode"] != "off":
            r.grid([_b(_mark(c["pos"] == k) + name, f"w:l:cpos:{k}") for k, name in HOOK_POS.items()], 3)
            r.row(*[_b(_mark(c["size"] == k) + f"Размер {name}", f"w:l:csize:{k}") for k, name in HOOK_SIZES.items()])
            r.grid([_b(_mark(c["style"] == k) + name, f"w:l:cstyle:{k}") for k, name in TEXT_STYLES.items()], 2)
            r.row(*[_b(_mark(c["when"] == k) + name, f"w:l:cwhen:{k}") for k, name in CTA_WHEN.items()])
            r.row(*[_b(_mark(c["dur"] == k) + ("показ: авто" if k == "auto" else f"{k} с"), f"w:l:cdur:{k}") for k in HOOK_DURS])
        back = "w:b:54"
    elif block == 543:
        cp = L["caption"]
        text = ("🔤 Подпись на всё видео\nТекст, который виден весь ролик: например, название снизу. «Из заголовка» берёт заголовок "
                "из вашей подписи «Заголовок | Описание | #теги». Положение «низ» оставляет место под кнопки лайка и подписки.\n\n"
                f"Сейчас: {_caption_line(cp)}")
        r.grid([_b(_mark(cp["mode"] == k) + name, f"w:l:capmode:{k}") for k, name in CAPTION_MODES.items()], 3)
        if cp["mode"] == "text":
            r.row(_b("✏️ Свой текст подписи", "w:l:captext"))
        if cp["mode"] != "off":
            r.grid([_b(_mark(cp["pos"] == k) + name, f"w:l:cappos:{k}") for k, name in HOOK_POS.items()], 3)
            r.row(*[_b(_mark(cp["size"] == k) + f"Размер {name}", f"w:l:capsize:{k}") for k, name in CAPTION_SIZES.items()])
            r.grid([_b(_mark(cp["style"] == k) + name, f"w:l:capstyle:{k}") for k, name in TEXT_STYLES.items()], 2)
        back = "w:b:54"
    elif block in (548, 549):
        kind = "hook" if block == 548 else "cta"
        what = "хука" if kind == "hook" else "призыва"
        mine = phrases.custom(st["channel"], kind)
        lang = _effective_voice(st["speech"])[:2]
        if mine:
            body = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(mine[:40]))
            text = (f"🗂 Фразы {what} канала «{st['channel']}»: {len(mine)}\n{body}\n\n"
                    "Режим «случайный» берёт фразу из этого списка. Чтобы убрать фразу, нажмите её номер.")
            r.grid([_b(f"🗑 {i + 1}", f"w:ph:del:{kind}:{i}") for i in range(min(len(mine), 40))], 6)
        else:
            std = phrases.defaults(kind, lang)
            text = (f"🗂 Фразы {what} канала «{st['channel']}»: свой список пуст, берутся стандартные ({'RU' if lang == 'ru' else 'EN'} "
                    f"по языку озвучки):\n" + "\n".join(f"• {t}" for t in std[:10]) + ("\n…" if len(std) > 10 else "")
                    + "\n\nДобавьте свои фразы, и режим «случайный» будет брать только их.")
        r.row(_b("➕ Добавить фразы", f"w:ph:add:{kind}"))
        if mine:
            r.row(_b("🧹 Очистить (вернуть стандартные)", f"w:ph:clr:{kind}"))
        back = "w:b:541" if kind == "hook" else "w:b:542"
    elif block == 56:
        pg = L["progress"]
        text = ("📶 Полоска прогресса\nЗаполняется ровно на длину ролика. В конце «достаёт» кнопки лайка и подписки: они нажимаются, "
                "а надпись меняется на «подписан». Дизайны: линия (тонкая, в стиле YouTube), капсула (белая с золотом), "
                "неон (светящиеся сегменты).\n\n"
                f"Сейчас: {_progress_line(pg)}")
        r.grid([_b(_mark(pg["design"] == k) + name, f"w:l:pdesign:{k}") for k, name in PROGRESS_DESIGNS.items()], 2)
        if pg["design"] != "off":
            r.row(*[_b(_mark(pg["pos"] == k) + name, f"w:l:ppos:{k}") for k, name in PROGRESS_POS.items()])
            r.grid([_b(_mark(pg["color"] == k) + name_[0], f"w:l:pcol:{k}") for k, name_ in PROGRESS_COLORS.items()], 4)
            r.row(_b(_mark(pg["end"]) + "Кнопки лайка и подписки в конце", "w:l:pend:1"))
            if pg["end"]:
                r.row(_b("✏️ Текст кнопки подписки" + (f": {pg['label']}" if pg.get("label") else " (по языку)"), "w:l:plabel"))
        back = "w:b:5"
    elif block == 55:
        has = _logo_file(st["channel"])
        text = (f"🏷 Логотип канала «{st['channel']}»\n" + ("Загружен ✅" if has else "Не загружен: нажмите «Загрузить» и пришлите PNG (лучше как файл, с прозрачностью).")
                + "\nЛоготип хранится отдельно для каждого канала.")
        r.row(_b("📤 Загрузить логотип", "w:l:logoup"))
        r.grid([_b(_mark(L["logo"] == k) + name, f"w:l:logo:{k}") for k, name in LOGO_POS.items()], 2)
        back = "w:b:5"
    elif block == "p":
        presets = state.list_presets(uid) if uid else []
        text = ("💾 Пресеты — сохранённые наборы настроек (видеоряд, речь, музыка, звуки, субтитры, стиль).\n"
                + ("Нажмите на пресет, чтобы применить." if presets else "Пока пусто: настройте ролик и нажмите «Сохранить текущие»."))
        r.row(_b("💾 Сохранить текущие", "w:p:save"), _b("🎲 Сюрприз", "w:p:rnd"))
        for p in presets[:12]:
            r.row(_b(f"▶ {p['name']}", f"w:p:load:{p['id']}"), _b("🗑", f"w:p:del:{p['id']}"))
    else:  # 6
        text = "6️⃣ Канал: куда выгрузить"
        r.grid([_b(_mark(st["channel"] == ch) + ch, f"w:c:chan:{ch}") for ch in config.CHANNELS], 2)
    r.row(_b("⬅ Назад" if back != "w:main" else "⬅ Назад к сборке", back))
    return text, r.markup()


async def _edit(cb: CallbackQuery, text: str, kb) -> None:
    await safe_edit(cb.message, text, kb)


async def show_main(message: Message, user_id: int, edit: bool = False) -> None:
    st = load(user_id)
    st["awaiting"] = None
    save(user_id, st)
    if edit:
        await safe_edit(message, main_text(st), main_kb())
    else:
        await message.answer(main_text(st), reply_markup=main_kb())


def _ok(user_id: int) -> bool:
    return user_id in config.ALLOWED_USER_IDS


@router.message(Command("new"))
async def cmd_new(message: Message):
    if message.from_user and _ok(message.from_user.id):
        await show_main(message, message.from_user.id)


def parse_time(s: str) -> float:
    s = s.strip()
    if ":" in s:
        parts = [float(x) for x in s.split(":")]
        return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    return float(s)


def parse_fragment(text: str) -> tuple[float, float] | None:
    m = re.match(r"^\s*([\d:.]+)\s*[-–—]\s*([\d:.]+)\s*$", text)
    if not m:
        return None
    try:
        a, b = parse_time(m.group(1)), parse_time(m.group(2))
    except ValueError:
        return None
    return (a, b) if b > a else None


def randomize(st: dict) -> None:
    """«Сюрприз»: случайный вид ролика. Видеоряд, речь и канал не трогаем."""
    L, S = st["look"], st["sfx"]
    from core import montage
    st["style"] = random.choice([*montage.BUILTIN_ORDER, *montage.user_styles(), "random"])
    L["layout"] = random.choices(list(LAYOUTS), [40, 30, 15, 15])[0]
    L["speed"] = random.choices(list(SPEEDS), [60, 8, 14, 8, 10])[0]
    L["fx"] = random.sample(list(FX), random.choice([0, 0, 1, 1, 2]))
    L["sub"] = random.choice(list(_sub_looks()))
    L["subpos"] = random.choice(["center", "center", "bottom"])
    tags = [t for t, _ in library.tag_counts("music") if t != "без-темы"]
    if library.stats().get("music"):
        st["music"] = random.choice(["auto", *tags])
    S["cuts"], S["start"] = random.random() < 0.6, random.random() < 0.3


# ---------- обработчики кнопок ----------

@router.callback_query(F.data.startswith("w:"))
async def on_button(cb: CallbackQuery):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    st = load(uid)
    parts = cb.data.split(":")
    act = parts[1]
    v, sp, L, S = st["video"], st["speech"], st["look"], st["sfx"]
    st["awaiting"] = None
    block = None
    note = None

    if act == "main":
        pass
    elif act == "reset":
        st = _default(uid)
    elif act == "b":
        block = int(parts[2]) if parts[2].isdigit() else parts[2]
    elif act == "v":
        block, key, val = 1, parts[2], parts[3] if len(parts) > 3 else None
        if key == "kind":
            v["kind"], v["theme"], v["file"], v["start"], v["end"], v["picks"] = val, None, None, None, None, []
            if val == "own":
                v["clips"] = 1
            elif v["clips"] < 2:
                v["clips"] = 4
        elif key == "theme":
            from core import mix
            i = int(val)
            themes = st["lists"].get("themes", [])
            if 0 <= i < len(themes):   # переключатель: отметить / снять отметку
                cur = mix.norm_themes(v["theme"])
                low = [t.lower() for t in cur]
                if themes[i].lower() in low:
                    cur = [t for t in cur if t.lower() != themes[i].lower()]
                else:
                    cur.append(themes[i])
                v["theme"] = mix.pack_themes(cur)
            else:
                v["theme"] = None
            if len(mix.norm_themes(v["theme"])) < 2:
                v["balance"] = None
        elif key == "bal":
            v["balance"] = True if val == "1" else None
        elif key == "n":
            v["clips"] = int(val)
        elif key == "len":
            v["len"] = val if val in CLIP_LENS else "full"
        elif key == "minh":
            v["min_h"] = int(val) if int(val) in MIN_HEIGHTS else 0
        elif key == "sec":
            v["seconds"] = int(val)
        elif key == "file":
            i = int(val)
            own = st["lists"].get("own", [])
            v["file"] = own[i] if 0 <= i < len(own) else None
            v["start"] = v["end"] = None
            if v["file"]:
                v["clips"] = 1
                v["picks"] = []
        elif key == "page":
            st["lists"]["own_page"] = int(val)
        elif key == "names":
            from core import sources
            await cb.answer("Ищу названия на YouTube…")
            res = await asyncio.to_thread(sources.fill_titles)
            note = f"Названий найдено: {res['found']} из {res['total']}"
            if res["failed"]:
                await cb.message.answer("Не нашёл названия для: " + ", ".join(res["failed"][:15]) + "\nЗадать вручную: /title <id> название (id смотрите в /lib)")
        elif key == "whole":
            v["start"] = v["end"] = None
        elif key == "frag":
            st["awaiting"] = "fragment"
            save(uid, st)
            await cb.message.answer("Пришлите фрагмент в формате 0:12-0:47 (минуты:секунды или просто секунды: 12-47).")
            return await cb.answer()
        elif key == "sync":
            from core import mix
            await cb.answer("Синхронизирую с Диском…")
            try:
                await asyncio.to_thread(mix.sync_clips)
            except Exception as e:  # noqa: BLE001
                await cb.message.answer(f"Диск не ответил: {e}")
            await asyncio.to_thread(library.scan)
    elif act == "k":
        block, key = "pk", parts[2]
        val = parts[3] if len(parts) > 3 else None
        cfg = st["lists"].setdefault("pk", {"src": "clip", "theme": None, "page": 0})
        picks = v.setdefault("picks", [])
        items = st["lists"].get("pk_items", [])
        if key == "src":
            cfg.update(src=val, theme=None, page=0)
        elif key == "theme":
            themes, i = st["lists"].get("pk_themes", []), int(val)
            cfg.update(theme=themes[i] if 0 <= i < len(themes) else None, page=0)
        elif key == "pg":
            cfg["page"] = int(val)
        elif key == "t":
            i = int(val)
            if 0 <= i < len(items):
                if items[i] in picks:
                    picks.remove(items[i])
                else:
                    picks.append(items[i])
                v["file"] = None
        elif key == "all":
            page = cfg.get("page", 0)
            for rel in items[page * PICK_PAGE:(page + 1) * PICK_PAGE]:
                if rel not in picks:
                    picks.append(rel)
            v["file"] = None
        elif key == "clr":
            picks.clear()
        elif key == "clr1":
            picks.clear()
            block = 1
    elif act == "s":
        block, key, val = 2, parts[2], parts[3] if len(parts) > 3 else None
        if key == "kind":
            sp["kind"] = val
        elif key == "lang":
            sp["target"] = val
        elif key == "voice":
            voices = st["lists"].get("voices", [])
            i = int(val)
            if 0 <= i < len(voices):
                sp["voice"] = voices[i]
        elif key == "rate":
            sp["rate"] = int(val)
        elif key == "save":
            voice = _effective_voice(sp)
            profile.set_value(st["channel"], "voice", voice)
            note = f"Голос {_voice_label(voice)} сохранён в профиле канала"
        elif key == "prev":
            from core import tts
            voice = _effective_voice(sp)
            await cb.answer("Озвучиваю образец…")
            path = config.TMP_DIR / f"voice_prev_{uid}.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                prof_rate = int(profile.get(st["channel"]).get("rate") or 0)
                await asyncio.to_thread(tts.synthesize, PHRASES.get(voice[:2], PHRASES["en"]), path, voice, sp.get("rate") or prof_rate or None)
                await cb.message.answer_audio(FSInputFile(path), title=_voice_label(voice), performer="образец голоса")
            except Exception as e:  # noqa: BLE001
                await cb.message.answer(f"Не удалось озвучить образец: {e}")
            finally:
                path.unlink(missing_ok=True)
            save(uid, st)
            return
    elif act == "m":
        block = 3
        if parts[2] == "set":
            i = int(parts[3])
            tags = st["lists"].get("tags", [])
            if i == -2:
                st["music"] = "off"
            elif i == -1 or i >= len(tags):
                st["music"] = "auto"
            else:
                st["music"] = tags[i]
        elif parts[2] == "vol":
            st["vol"] = int(parts[3])
        elif parts[2] == "orig":
            st["video"]["orig_sound"] = {"auto": None, "on": True, "off": False}.get(parts[3] if len(parts) > 3 else "auto")
        elif parts[2] == "ovol":
            st["video"]["orig_sound"], st["video"]["orig_vol"] = True, int(parts[3])
        elif parts[2] == "duck":
            st["video"]["duck"] = not st["video"].get("duck", True)
    elif act == "x":
        block, key, val = 31, parts[2], parts[3] if len(parts) > 3 else None
        if key in ("cuts", "start"):
            S[key] = not S[key]
        elif key == "tag":
            tags = st["lists"].get("sfxtags", [])
            i = int(val)
            S["tag"] = tags[i] if 0 <= i < len(tags) else None
        elif key == "vol":
            S["vol"] = int(val)
    elif act == "l":
        key, val = parts[2], parts[3] if len(parts) > 3 else None
        if key == "layout":
            L["layout"], block = val, 52
        elif key == "crop" and val in CROP_BARS:
            L["crop_bars"], block = val, 52
        elif key == "fsize":
            L["frame"]["size"], block = val, 52
        elif key == "fcol":
            L["frame"]["color"], block = val, 52
        elif key == "fcustom":
            st["awaiting"] = "framecolor"
            save(uid, st)
            await cb.message.answer("Пришлите цвет рамки в формате #RRGGBB, например #ff8800 (оранжевый) или #101010 (почти чёрный).")
            return await cb.answer()
        elif key == "hookp" and val.isdigit() and int(val) < len(HOOK_PRESETS):
            L["hook"].update(mode="text", text=HOOK_PRESETS[int(val)])
            block = 541
        elif key == "hpos" and val in HOOK_POS:
            L["hook"]["pos"], block = val, 541
        elif key == "hsize" and val in HOOK_SIZES:
            L["hook"]["size"], block = val, 541
        elif key == "hstyle" and val in TEXT_STYLES:
            L["hook"]["style"], block = val, 541
        elif key == "hstart":
            L["hook"]["start"], block = float(val), 541
        elif key == "hdur":
            L["hook"]["dur"], block = (val if val == "auto" else float(val)), 541
        elif key == "cpos" and val in HOOK_POS:
            L["cta"]["pos"], block = val, 542
        elif key == "csize" and val in HOOK_SIZES:
            L["cta"]["size"], block = val, 542
        elif key == "cstyle" and val in TEXT_STYLES:
            L["cta"]["style"], block = val, 542
        elif key == "cwhen" and val in CTA_WHEN:
            L["cta"]["when"], block = val, 542
        elif key == "cdur":
            L["cta"]["dur"], block = (val if val == "auto" else float(val)), 542
        elif key == "speed":
            L["speed"], block = val, 53
        elif key == "fx":
            L["fx"] = [f for f in L["fx"] if f != val] if val in L["fx"] else [*L["fx"], val]
            block = 53
        elif key == "sub":
            L["sub"], block = val, 4
        elif key == "subpos":
            L["subpos"], block = val, 4
        elif key == "hook":
            L["hook"]["mode"], block = val, 541
            if val == "text" and not L["hook"]["text"]:
                st["awaiting"] = "hook"
                save(uid, st)
                await cb.message.answer("Пришлите текст хука (до 80 символов), например: «Ты этого не знал».")
                return await cb.answer()
        elif key == "hooktext":
            st["awaiting"] = "hook"
            save(uid, st)
            await cb.message.answer("Пришлите текст хука (до 80 символов). «-» — убрать хук.")
            return await cb.answer()
        elif key == "cta":
            block = 542
            if val == "off":
                L["cta"].update(mode="off", text="")
            elif val == "rnd":
                L["cta"].update(mode="random", text="")
            elif val.isdigit() and int(val) < len(CTA_PRESETS):
                L["cta"].update(mode="text", text=CTA_PRESETS[int(val)])
        elif key == "ctatext":
            st["awaiting"] = "cta"
            save(uid, st)
            await cb.message.answer("Пришлите текст призыва (до 80 символов). «-» — убрать призыв.")
            return await cb.answer()
        elif key == "capmode" and val in CAPTION_MODES:
            L["caption"]["mode"], block = val, 543
            if val == "text" and not L["caption"]["text"]:
                st["awaiting"] = "caption"
                save(uid, st)
                await cb.message.answer("Пришлите текст подписи (до 90 символов), например название ролика.")
                return await cb.answer()
        elif key == "captext":
            st["awaiting"] = "caption"
            save(uid, st)
            await cb.message.answer("Пришлите текст подписи (до 90 символов). «-» — убрать подпись.")
            return await cb.answer()
        elif key == "cappos" and val in HOOK_POS:
            L["caption"]["pos"], block = val, 543
        elif key == "capsize" and val in CAPTION_SIZES:
            L["caption"]["size"], block = val, 543
        elif key == "capstyle" and val in TEXT_STYLES:
            L["caption"]["style"], block = val, 543
        elif key == "pdesign" and val in PROGRESS_DESIGNS:
            L["progress"]["design"], block = val, 56
        elif key == "ppos" and val in PROGRESS_POS:
            L["progress"]["pos"], block = val, 56
        elif key == "pcol" and val in PROGRESS_COLORS:
            L["progress"]["color"], block = val, 56
        elif key == "pend":
            L["progress"]["end"], block = not L["progress"]["end"], 56
        elif key == "plabel":
            st["awaiting"] = "plabel"
            save(uid, st)
            await cb.message.answer("Пришлите текст кнопки подписки (до 20 символов), например «СМОТРИ ЕЩЁ». «-» — вернуть текст по языку ролика.")
            return await cb.answer()
        elif key == "logo":
            L["logo"], block = val, 55
        elif key == "logoup":
            st["awaiting"] = "logo"
            save(uid, st)
            await cb.message.answer(f"Пришлите логотип для канала «{st['channel']}»: PNG с прозрачным фоном (лучше как файл) или обычное фото.")
            return await cb.answer()
    elif act == "ph":
        sub, kind = parts[2], parts[3]
        if kind not in phrases.KINDS:
            return await cb.answer("Неизвестный список", show_alert=True)
        block = 548 if kind == "hook" else 549
        if sub == "add":
            st["awaiting"] = f"phrase_{kind}"
            save(uid, st)
            await cb.message.answer("Пришлите фразы: по одной в строке, каждая до 80 символов. Можно сразу несколько. "
                                    "Повторы пропускаются.")
            return await cb.answer()
        if sub == "del" and len(parts) > 4 and parts[4].isdigit():
            phrases.remove(st["channel"], kind, int(parts[4]))
            note = "Удалено"
        elif sub == "clr":
            phrases.clear(st["channel"], kind)
            note = "Список очищен, будут стандартные фразы"
    elif act == "p":
        sub = parts[2]
        if sub == "save":
            st["awaiting"] = "preset_name"
            save(uid, st)
            await cb.message.answer("Как назвать пресет? Пришлите название (до 30 символов). Если такое имя уже есть, оно обновится.")
            return await cb.answer()
        if sub == "load":
            p = state.get_preset(uid, int(parts[3]))
            if p:
                state.apply(st, p["data"])
                note = f"Применён пресет «{p['name']}»"
            else:
                note = "Пресет не найден"
        elif sub == "del":
            state.delete_preset(uid, int(parts[3]))
            block = "p"
            note = "Удалено"
        elif sub == "rnd":
            randomize(st)
            note = "Сюрприз! Вид ролика собран случайно. Проверьте и жмите «Собрать»"
    elif act == "c":
        key, val = parts[2], parts[3]
        block = {"subs": 4, "subsrc": 4, "sublang": 4, "style": 51, "chan": 6}[key]
        if key == "subs":
            st["subs"] = val == "1"
        elif key == "sublang":
            st["subs_lang"] = val if val in LANGS else None
            if st["subs_lang"]:
                st["subs"] = True
        elif key == "subsrc":
            st["subs_src"] = val
            if val == "orig":
                st["subs"] = True
        elif key == "style":
            st["style"] = val
        elif key == "chan" and val in config.CHANNELS:
            st["channel"] = val
            db.set_user_channel(uid, val)
    elif act == "go":
        return await _go(cb, st, uid)

    save(uid, st)
    if block:
        text, kb = block_screen(st, block, uid)
        save(uid, st)  # block_screen обновляет st["lists"]
        await _edit(cb, text, kb)
    else:
        await _edit(cb, main_text(st), main_kb())
    await cb.answer(note)


def validate(st: dict) -> str | None:
    v, sp, L, S = st["video"], st["speech"], st["look"], st["sfx"]
    if sp["kind"] == "translate" and v["kind"] not in ("own", "clips"):
        return "Перевод речи возможен для видеоряда «Моё видео» или «Клипы» (в фонах обычно нет речи). Выберите его в блоке 1."
    if v.get("picks") and v["kind"] in ("clips", "own") and not v.get("file"):
        miss = [p for p in v["picks"] if not (config.ROOT / p).exists()]
        if miss:
            return f"Выбранных роликов не найдено на сервере: {len(miss)} (возможно, удалены). Откройте «🎯 Выбрать ролики» и выберите заново."
    elif v["kind"] == "own" and not _own_files():
        return "В папке _own нет видео. Положите ролики в ShortsFactory/_clips/_own/ на Диске и нажмите «Обновить с Диска»."
    if st["music"] != "off" and not library.search("music", [] if st["music"] == "auto" else [st["music"]], limit=1):
        library.scan()
        if not library.search("music", [] if st["music"] == "auto" else [st["music"]], limit=1):
            return ("Музыка выбрана, но в библиотеке нет подходящих треков. Положите mp3 в ShortsFactory/_clips/_music/ "
                    "на Диске и нажмите /lib → «Обновить», либо выберите «Нет» в блоке «Музыка».")
    if (S["cuts"] or S["start"]) and not library.search("sfx", [S["tag"]] if S["tag"] else [], limit=1):
        library.scan()
        if not library.search("sfx", [S["tag"]] if S["tag"] else [], limit=1):
            return "Звуковые эффекты включены, но подходящих звуков нет. Выберите тег «Любые» или положите звуки в _clips/_sfx/."
    if L["hook"]["mode"] == "text" and not L["hook"]["text"].strip():
        return "Хук «свой текст» выбран, но текст не задан (5️⃣ Стиль → Текст на экране)."
    if L["caption"]["mode"] == "text" and not L["caption"]["text"].strip():
        return "Подпись «свой текст» выбрана, но текст не задан (5️⃣ Стиль → Текст на экране → Подпись)."
    if L["logo"] != "off" and not _logo_file(st["channel"]):
        return f"Логотип включён, но для канала «{st['channel']}» он не загружен (5️⃣ Стиль → Логотип)."
    if st["subs"] and st["subs_src"] == "orig" and sp["kind"] == "none" and v["kind"] == "bg":
        return "Субтитры «по звуку видео» не сработают: у фона нет звука. Выберите нарезку или своё видео либо другой источник субтитров."
    if v["start"] is not None and v["end"] is not None and v["end"] <= v["start"]:
        return "Конец фрагмента раньше начала."
    return None


async def _go(cb: CallbackQuery, st: dict, uid: int):
    err = validate(st)
    if err:
        return await cb.answer(err, show_alert=True)
    kind = st["speech"]["kind"]
    if kind in ("audio", "text"):
        st["awaiting"] = kind
        save(uid, st)
        if kind == "audio":
            await cb.message.answer("Пришлите аудио: файл или голосовое, 3–90 с. В подписи можно указать «Заголовок | Описание | #теги».")
        else:
            await cb.message.answer("Пришлите текст озвучки. Первой строкой можно указать «Заголовок | Описание | #теги», остальное будет озвучено.")
        return await cb.answer()
    await cb.answer()
    await create_task(cb.message, uid, extra=None)


# ---------- создание задачи ----------

def recipe_of(st: dict) -> dict:
    v = st["video"]
    return {
        "video": {k: v.get(k) for k in ("kind", "theme", "clips", "seconds", "file", "start", "end", "orig_sound", "picks", "len", "orig_vol", "duck", "min_h", "balance")},
        "speech": dict(st["speech"]),
        "style": st["style"],
        "subs_src": st["subs_src"],
        "subs_lang": st.get("subs_lang"),
        "look": st["look"],
        "sfx": st["sfx"],
    }


async def create_task(message: Message, uid: int, extra: dict | None) -> None:
    """extra: {'meta': {...}, 'script': str} или {'meta': {...}, 'audio': (Path-getter)} - см. обработчики ввода."""
    st = load(uid)
    st["awaiting"] = None
    save(uid, st)
    channel = st["channel"]
    meta = (extra or {}).get("meta") or {"title": "Ролик", "description": "", "tags": []}
    task_id = db.create_task(channel, "recipe", message.chat.id)
    task_dir = config.TASKS_DIR / f"task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)
    try:
        if extra and extra.get("script"):
            (task_dir / "script.txt").write_text(extra["script"], "utf-8")
        if extra and extra.get("audio_saver"):
            await extra["audio_saver"](task_dir)
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        db.set_status(task_id, "rejected")
        raise
    data = {
        "task_id": task_id, "channel": channel, "input_type": "recipe",
        **profile.for_task(channel),
        "music": st["music"], "music_vol": st["vol"], "subs": st["subs"],
        **meta, "recipe": recipe_of(st),
    }
    (task_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    db.mark_ready(task_id)
    await message.answer(f"Принято: task_{task_id}, канал {channel}. В очереди: {db.queue_size()}\n\n{main_text(st)}")


# ---------- ввод после кнопок ----------

async def handle_text(message: Message) -> bool:
    """True, если сообщение забрал мастер."""
    uid = message.from_user.id
    st = load(uid)
    aw = st.get("awaiting")
    text = (message.text or "").strip()
    if await plannerui.handle_text(message, uid, st):   # задачник: новая задача или повтор, название нового канала
        return True
    if await smartui.handle_text(message, uid, st):   # умный монтаж: тема для промта и JSON сценария со сценами и visual
        return True
    if await storyui.handle_text(message, uid, st):   # истории: JSON истории, ссылка или папка залипательных видео
        return True
    if await ideasui.handle_text(message, uid, st):   # банк идей: название канала/идеи, ссылка, JSON проекта
        return True
    if (aw not in ("text", "hook", "cta", "preset_name", "framecolor", "mstyle_name", "caption", "plabel", "phrase_hook", "phrase_cta")
            and await scenario_ui.handle_text(message, uid)):
        return True  # сообщение - сценарий JSON
    if aw == "fragment":
        frag = parse_fragment(text)
        if frag is None:
            await message.answer("Не понял. Формат: 0:12-0:47 или 12-47")
            return True
        st["video"]["start"], st["video"]["end"] = frag
        st["awaiting"] = None
        save(uid, st)
        await message.answer(main_text(st), reply_markup=main_kb())
        return True
    if aw in ("hook", "cta"):
        clear = text in ("-", "—", "")
        if aw == "hook":
            st["look"]["hook"].update(mode="off", text="") if clear else st["look"]["hook"].update(mode="text", text=text[:80])
        else:
            st["look"]["cta"].update(mode="off", text="") if clear else st["look"]["cta"].update(mode="text", text=text[:80])
        st["awaiting"] = None
        save(uid, st)
        screen, kb = block_screen(st, 541 if aw == "hook" else 542, uid)
        await message.answer(screen, reply_markup=kb)
        return True
    if aw in ("phrase_hook", "phrase_cta"):
        kind = aw.split("_", 1)[1]
        n = phrases.add(st["channel"], kind, text)
        st["awaiting"] = None
        save(uid, st)
        screen, kb = block_screen(st, 548 if kind == "hook" else 549, uid)
        await message.answer((f"Добавлено фраз: {n}\n\n" if n else "Новых фраз нет: пусто или такие уже есть.\n\n") + screen, reply_markup=kb)
        return True
    if aw == "caption":
        clear = text in ("-", "—", "")
        st["look"]["caption"].update(mode="off", text="") if clear else st["look"]["caption"].update(mode="text", text=text[:90])
        st["awaiting"] = None
        save(uid, st)
        screen, kb = block_screen(st, 543, uid)
        await message.answer(screen, reply_markup=kb)
        return True
    if aw == "plabel":
        st["look"]["progress"]["label"] = "" if text in ("-", "—", "") else text[:20]
        st["awaiting"] = None
        save(uid, st)
        screen, kb = block_screen(st, 56, uid)
        await message.answer(screen, reply_markup=kb)
        return True
    if aw == "framecolor":
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})", text)
        if not m:
            await message.answer("Не понял цвет. Формат: #RRGGBB, например #ff8800.")
            return True
        st["look"]["frame"]["color"] = "#" + m.group(1).lower()
        st["awaiting"] = None
        save(uid, st)
        screen, kb = block_screen(st, 52, uid)
        await message.answer(screen, reply_markup=kb)
        return True
    if aw == "mstyle_name":
        return await styleui.handle_text(message, uid, st)
    if aw == "preset_name":
        if not text:
            await message.answer("Название не может быть пустым.")
            return True
        name = state.save_preset(uid, text, st)
        st["awaiting"] = None
        save(uid, st)
        screen, kb = block_screen(st, "p", uid)
        await message.answer(f"Сохранено: «{name}»\n\n{screen}", reply_markup=kb)
        return True
    if aw == "text":
        first, _, rest = (message.text or "").partition("\n")
        meta = parse_caption(first)
        script = rest.strip() if meta else (message.text or "").strip()
        if len(script) < 20:
            await message.answer("Текст слишком короткий (от 20 символов).")
            return True
        if not meta:
            meta = {"title": script[:60], "description": "", "tags": []}
        await create_task(message, uid, {"meta": meta, "script": script})
        return True
    if await cutui.handle_text(message, uid, st):
        return True
    return await materials.handle_text(message, uid, st)


async def handle_audio(message: Message, bot: Bot) -> bool:
    uid = message.from_user.id
    st = load(uid)
    if await smartui.handle_media(message, bot, uid, st):  # умный монтаж: JSON сценария файлом
        return True
    if await storyui.handle_media(message, bot, uid, st):  # истории: залипательные видео и JSON истории файлом
        return True
    if await ideasui.handle_media(message, bot, uid, st):  # банк идей: видео и JSON в проект
        return True
    if await scenario_ui.handle_document(message, bot, uid):  # сценарий JSON файлом
        return True
    if await materials.handle_logo(message, bot, uid, st):  # логотип канала (фото или картинка-файл)
        return True
    if st.get("awaiting") != "audio":
        return False
    obj = message.audio or message.voice or message.document
    if obj is None:
        return False
    name = getattr(obj, "file_name", None) or "voice.ogg"
    ext = Path(name).suffix.lower() or ".ogg"
    if message.document and ext not in ALLOWED_EXT:
        await message.answer(f"Формат {ext} не поддерживается: пришлите аудио ({', '.join(sorted(ALLOWED_EXT))}).")
        return True
    meta = parse_caption(message.caption) or {"title": "Ролик", "description": "", "tags": []}
    holder = {}

    async def saver(task_dir: Path):
        dest = task_dir / f"input_audio{ext}"
        await bot.download(obj, destination=dest)
        dur = await asyncio.to_thread(audio_duration, dest)
        if not (config.MIN_AUDIO_SECONDS <= dur <= config.MAX_AUDIO_SECONDS):
            raise ValueError(f"длительность {dur:.0f} с, допустимо {config.MIN_AUDIO_SECONDS:.0f}-{config.MAX_AUDIO_SECONDS:.0f} с")
        holder["dur"] = dur

    try:
        await create_task(message, uid, {"meta": meta, "audio_saver": saver})
    except (ValueError, TelegramBadRequest) as e:
        await message.answer(f"Отклонено: {e}. Пришлите другое аудио.")
    return True
