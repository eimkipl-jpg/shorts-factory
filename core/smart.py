"""Режим «🎞 Умный монтаж»: видеоряд собирается по сценарию, а не случайно.

JSON (его пишет нейросеть по промту make_prompt): список сцен, у каждой фраза для озвучки и описание кадра.
{
  "title": "...", "description": "...", "tags": ["#финансы"], "channel": "MemesOfRakhim",
  "voice": "el:VOICE_ID" | "ru-RU-DmitryNeural",            # необязательно
  "music": {"mood": "calm", "volume": 10} | "off" | "auto",
  "scenes": [
    {"text": "Фраза для озвучки",
     "visual": {"query": "empty wallet close up", "alt": ["coins falling", "price tag"]},   # что показать (по-английски)
     "theme": "финансы",        # необязательно: папка своей библиотеки как запасной вариант
     "own": "my_clip.mp4"}      # необязательно: конкретное своё видео
  ]
}
Как собирается ролик:
1. вся озвучка одним проходом (интонация не рвётся), время каждого слова - по Whisper;
2. граница сцены - момент, когда диктор начинает её первую фразу, значит длина кадра = длина мысли;
3. под каждую сцену ищется клип: своя библиотека (теги и названия) → Pexels/Pixabay по запросу сцены (найденное сохраняется
   в библиотеку и на Диск, в следующий раз берётся оттуда) → запасные запросы; клип берётся, только если слова запроса
   совпали с его описанием (MIN_COV), иначе сцена получает тёмный фон;
   необязательное поле сцены stat рисует поверх цифру, столбцы или график (core.statfx);
4. длинная сцена режется на куски до 6 с из разных клипов, чтобы кадр менялся.
"""
import logging
import math
import re
import shutil
import subprocess
from pathlib import Path

from common import config, library
from common.looks import merged_look, merged_sfx
from common.media import audio_duration, run, video_duration
from core import compose, histmap, imagesrc, mix, sources, statfx, story, subtitles, tts, vision

log = logging.getLogger("smart")

SCENE_MAX = 24
TEXT_MAX = 220
MAX_SECONDS = 170
CHARS_PER_SECOND = 14.5
CPS_BY_LANG = {"ru": 14.5, "en": 17.0}   # знаков озвучки в секунду (английская речь короче по знакам)
PIECE_MAX = 6.0          # самый длинный кусок одного клипа, с
PIECE_MIN = 0.9
TAIL = 0.9
LEAD = 0.12              # картинка меняется чуть раньше слов - так монтаж выглядит естественнее
STOCK_THEME = "smart"    # папка клипов, найденных на стоках
# такты сюжета «вопрос -> ответ» (новые) и прежние блоки (старые сценарии тоже принимаются)
BLOCKS = {"hook": "❓", "context": "🧩", "reveal": "💡", "escalate": "📈", "reason": "🔑", "punch": "🥊", "other": "🔄",
          "flip": "🏁", "setup": "🧩", "main": "📌", "climax": "🏁", "cta": "📣"}
BEAT_NAMES = {"hook": "вопрос", "context": "контекст", "reveal": "раскрытие", "escalate": "усиление", "reason": "причина",
              "punch": "панчлайн", "other": "другая сторона", "flip": "финал-поворот"}
# связки, которыми фраза цепляется за предыдущую (как в хороших роликах: «хотя», «то есть», «причём», «поэтому», «но»)
CONNECTORS = ("но", "а", "и", "хотя", "то есть", "причём", "причем", "поэтому", "потому", "зато", "однако", "даже", "так что",
              "вот", "именно", "только", "ведь", "тогда", "и вот", "и тут", "в итоге", "сегодня", "теперь", "раньше", "ещё", "еще",
              "дело", "получается", "значит", "иначе", "при этом", "кстати", "however", "but", "so", "and", "because", "that's",
              "which", "still", "yet", "even", "today", "now", "then", "here", "turns")
EMPH_MAX = 3            # сцен с крупным текстом на ролик
CARDS_MIN_MAX = 3       # карточек при графике «минимум»
BEAT_PAUSE = 0.35       # тишина после вопроса и перед финалом, с
VTYPES = ("video", "photo", "illustration", "archive")
VLABEL = {"video": "видео: ", "photo": "фото: ", "illustration": "иллюстрация: ", "archive": "архив: "}   # visual.type: живое видео, фото, иллюстрация, архивное фото
UPSCALE_MAX = 1.35      # во сколько раз можно растянуть горизонтальную картинку на весь кадр, дальше - карточкой
UPSCALE_MIN_W = 900     # вертикальная картинка уже этой ширины идёт карточкой, а не на весь кадр
VISION_GOOD = 6.0       # оценка Claude (0-10), с которой кадр считается подходящим
VISION_WEAK = 4.0
MIN_COV = 0.5           # доля слов запроса, которая должна совпасть с описанием ролика
# доля рисованных карточек на весь кадр: (от %, до %, подпись в настройках)
GRAPHICS = {"min": (10, 25, "минимум: 2-3 карточки"), "off": (0, 0, "нет"), "low": (20, 30, "мало 20-30%"), "mid": (40, 50, "средне 40-50%"), "high": (60, 80, "много 60-80%")}
# темп речи: (скорость Edge в %, сколько оставлять тишины между фразами, подпись)
PACE = {"calm": (0, 0.32, "спокойный"), "normal": (6, 0.2, "живой"), "fast": (12, 0.13, "быстрый")}
# настройки режима: ключ -> (раздел, подпись, [(значение, как показать)]); первое значение - по умолчанию
OPTIONS = {
    "music_vol": ("voice", "🔉 Громкость музыки", [(10, "10%"), (15, "15%"), (20, "20%"), (30, "30%"), (5, "5%")]),
    "pace": ("voice", "🗣 Темп речи", [("normal", PACE["normal"][2]), ("fast", PACE["fast"][2]), ("calm", PACE["calm"][2])]),
    "img_mode": ("visual", "🖼 Картинки", [("fill", "на весь кадр, с панорамой"), ("card", "карточкой на размытом фоне")]),
    "strict": ("visual", "🎯 Подбор", [(True, "строгий: лучше иконка, чем мимо"), (False, "обычный")]),
    "src_video": ("visual", "🎬 Стоковое видео", [(True, "да"), (False, "нет")]),
    "src_photo": ("visual", "📷 Стоковые фото", [(True, "да"), (False, "нет")]),
    "src_illustration": ("visual", "🖌 Иллюстрации", [(True, "да"), (False, "нет")]),
    "src_archive": ("visual", "🏛 Архив Wikimedia", [(True, "да"), (False, "нет")]),
    "graphics": ("look", "📊 Графика на весь кадр", [(k, v[2]) for k, v in GRAPHICS.items()]),
    "theme": ("look", "🎨 Оформление", [("dark", "тёмное цветное"), ("light", "светлое"), ("black", "чёрное, минимализм")]),
    "sub_style": ("look", "🔤 Субтитры", [("calm", "спокойные, 2 строки"), ("hl", "подсветка слова"), ("yellow", "жёлтые"), ("plain", "белые"), ("off", "выключены")]),
    "sub_size": ("look", "🔠 Размер субтитров", [("m", "обычный"), ("l", "крупный"), ("s", "мелкий")]),
    "sub_pos": ("look", "↕ Место субтитров", [("low", "ниже центра"), ("bottom", "внизу")]),
    "sub_case": ("look", "Aa Регистр", [("upper", "ЗАГЛАВНЫЕ"), ("normal", "обычный")]),
    "maps": ("look", "🗺 Карты", [("smart", "по сценарию"), ("off", "выключены")]),
    "length": ("script", "⏱ Длина ролика", [("long", "45-60 с"), ("xl", "60-75 с"), ("xxl", "90-120 с"), ("mid", "30-45 с"),
                                             ("short", "20-30 с")]),
    "tone": ("script", "🎭 Подача", [("talk", "как блогер, разговорно"), ("pop", "научпоп"), ("story", "история другу"),
                                      ("fun", "с юмором"), ("drama", "драматично"), ("expert", "экспертно")]),
    "style": ("edit", "✨ Переходы", [("none", "резкие склейки"), ("clean", "чистые"), ("smooth", "плавные"), ("dynamic", "динамичные"),
                                      ("doc", "документальные"), ("cinema", "кино"), ("hype", "хайп")]),
}
SECTIONS = {"voice": "🎙 Голос и звук", "visual": "🖼 Картинки и источники", "look": "🎨 Графика и субтитры",
            "script": "📝 Сценарий", "edit": "✨ Монтаж"}
# длина: (слов от, до, секунд, сцен от, до). Темп хороших роликов - около 2,4 слова в секунду, не быстрее
LENGTHS = {"short": (50, 70, "20-30", 5, 8), "mid": (75, 108, "30-45", 7, 10), "long": (108, 145, "45-60", 8, 13),
           "xl": (145, 180, "60-75", 10, 15), "xxl": (215, 285, "90-120", 14, 22)}
WPS_MAX = 2.6
TONES = {"talk": "живой разговор блогера: от первого лица, простыми словами, с личной интонацией и бытовыми примерами, как в сторис другу; "
                 "без лекторского тона и без готовых фраз",
         "pop": "научно-популярная: сложное простыми словами, через удивительные факты",
         "story": "рассказ другу: тепло, лично, с «представь» и «смотри», как будто объясняешь за чаем",
         "fun": "с юмором: ироничные сравнения и неожиданные бытовые аналогии, но факты точные; шутка не заменяет смысл",
         "drama": "драматичная: напряжение, ставки, короткие рубленые фразы, паузы перед развязкой",
         "expert": "экспертная: уверенно и точно, термины только с объяснением, цифры и механизмы"}
SUB_SIZES = {"s": 0.82, "m": 1.0, "l": 1.18}
SUB_POS = {"low": 1330, "bottom": 1480}
CFG_V = 47              # версия настроек: при обновлении бота старые сохранённые настройки получают новые значения по умолчанию
DEFAULT_CFG = {"voice": "auto", "music": "off", "stock": True, "subs": True,
               **{k: v[2][0][0] for k, v in OPTIONS.items()}, "v": CFG_V}


def upgrade_cfg(cfg: dict) -> dict:
    """Сохранённые настройки старых версий получают новые рекомендуемые значения по шагам, остальное - как выбрал пользователь.
    v36: графика «минимум», спокойные субтитры, 45-60 с, живой темп. v47: подача «как блогер» вместо научпопа по умолчанию."""
    cfg = dict(cfg or {})
    v = int(cfg.get("v") or 0)
    if v < 36:
        cfg.update({"graphics": "min", "sub_style": "calm", "length": "long"})
        if cfg.get("pace") == "fast":
            cfg["pace"] = "normal"
        if cfg.get("subs") is False:
            cfg["sub_style"] = "off"
    if v < 47 and cfg.get("tone", "pop") == "pop":
        cfg["tone"] = "talk"
    cfg["v"] = CFG_V
    return cfg


# переходы умного монтажа: только аккуратные варианты (без пикселизации и «шторок»), камера не дёргается
SMART_STYLES = {
    "none": {"transition": None, "d": 0.0},
    "clean": {"transition": ["fade"], "d": 0.22},
    "smooth": {"transition": ["smoothleft", "smoothright", "smoothup", "fade"], "d": 0.35},
    "dynamic": {"transition": ["slideleft", "slideright", "slideup", "smoothleft"], "d": 0.25},
    "doc": {"transition": ["fade", "dissolve"], "d": 0.4, "grade": "faded", "fx": ["grain"]},
    "cinema": {"transition": ["fadeblack", "fade"], "d": 0.45, "grade": "teal", "fx": ["vignette"]},
    "hype": {"transition": ["zoomin", "slideup", "fadewhite"], "d": 0.2, "grade": "pop"},
}


def smart_style(name: str) -> dict:
    """Параметры склейки для compose.assemble: переходы из SMART_STYLES, без наезда камеры поверх всего ролика."""
    import random as _r
    base = dict(compose.resolve_style("none"))
    spec = SMART_STYLES.get(name, SMART_STYLES["none"])
    pool = list(spec.get("transition") or [])
    base.update({"name": f"smart_{name}", "zoom": "off", "grade": spec.get("grade"), "fx": list(spec.get("fx") or []),
                 "transition": _r.choice(pool) if pool else None, "transition_pool": pool if len(pool) > 1 else None,
                 "d": spec["d"] if pool else 0.0})
    return base


def opt(cfg: dict, key: str):
    """Значение настройки с проверкой: неизвестное значение -> по умолчанию."""
    vals = [v for v, _ in OPTIONS[key][2]]
    val = (cfg or {}).get(key, DEFAULT_CFG[key])
    return val if val in vals else DEFAULT_CFG[key]


def opt_label(cfg: dict, key: str) -> str:
    return dict(OPTIONS[key][2])[opt(cfg, key)]
STOP = {"the", "and", "with", "for", "from", "into", "over", "close", "up", "view", "shot", "of", "a", "an", "in", "on", "at"}


def is_smart(data) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        return False
    return any(isinstance(s, dict) and (isinstance(s.get("visual"), (dict, str)) or s.get("query") or isinstance(s.get("map"), dict))
               for s in data["scenes"])


def _q(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:80]


def el_voices() -> dict[str, str]:
    """Голоса ElevenLabs из .env: {название: 'el:ID'}."""
    out = {}
    for item in config.ELEVENLABS_VOICES:
        name, _, vid = item.partition(":")
        if vid:
            out[name.strip()] = f"el:{vid.strip()}"
    return out


def parse(data: dict, ctx: dict) -> dict:
    errors: list[str] = []
    warn: list[str] = []
    scenes_in = data.get("scenes")
    scenes: list[dict] = []
    if not isinstance(scenes_in, list) or not scenes_in:
        errors.append("scenes: нужен непустой список сцен")
        scenes_in = []
    if len(scenes_in) > SCENE_MAX:
        errors.append(f"scenes: {len(scenes_in)} сцен, максимум {SCENE_MAX}")
        scenes_in = scenes_in[:SCENE_MAX]
    seconds = 0.0
    noq: list[int] = []
    for i, sc in enumerate(scenes_in, 1):
        where = f"сцена {i}"
        if not isinstance(sc, dict):
            errors.append(f"{where}: нужен объект с полями text и visual")
            continue
        text = story.clean_text(sc.get("text") or "", keep_paras=False)
        if not text:
            errors.append(f"{where}: нет text (фраза для озвучки)")
            continue
        if len(text) > TEXT_MAX:
            errors.append(f"{where}: текст {len(text)} символов, максимум {TEXT_MAX} (разбейте на сцены)")
        vis = sc.get("visual")
        if isinstance(vis, str):
            vis = {"query": vis}
        vis = vis if isinstance(vis, dict) else {}
        query = _q(vis.get("query") or sc.get("query"))
        alt = [_q(a) for a in (vis.get("alt") or []) if _q(a)][:3] if isinstance(vis.get("alt"), list) else []
        item = {"text": text, "query": query, "alt": alt}
        vtype = _q(vis.get("type")).lower()
        if vtype in VTYPES and vtype != "video":
            item["vtype"] = vtype
        theme = _q(sc.get("theme"))
        if theme:
            if theme in (ctx.get("themes") or []):
                item["theme"] = theme
            else:
                warn.append(f"{where}: темы «{theme}» нет в библиотеке ({', '.join(ctx.get('themes') or []) or 'пусто'}); пропущена")
        own = _q(sc.get("own"))
        if own:
            if own in (ctx.get("own") or []):
                item["own"] = own
                if sc.get("start") is not None:
                    try:
                        item["start"] = max(0.0, float(sc["start"]))
                    except (TypeError, ValueError):
                        pass
            else:
                warn.append(f"{where}: своего видео «{own}» нет в _own; подберу по запросу")
        if sc.get("stat"):
            st = statfx.normalize(sc["stat"], where, warn)
            if st:
                item["stat"] = st
        if sc.get("map"):                 # историческая карта вместо картинки (core.histmap)
            if opt(ctx.get("cfg") or {}, "maps") == "off":
                warn.append(f"{where}: карты выключены в настройках, вместо карты - картинка по visual")
            else:
                mp = histmap.normalize(sc["map"], where, warn)
                if mp:
                    item["map"] = mp
        if not query and not item.get("own") and not item.get("theme") and not (item.get("stat") or {}).get("full") \
                and not sc.get("emphasis") and not item.get("map"):
            noq.append(i)
        block = _q(sc.get("block")).lower()
        if block in BLOCKS:
            item["block"] = block
        emph = sc.get("emphasis") if not item.get("map") else None
        if emph:                        # крупный текст вместо ведущего: ключевые слова (список, строка или true)
            if emph is True:
                item["emphasis"] = []
            else:
                words = emph if isinstance(emph, list) else re.split(r"[,;]\s*", str(emph))
                item["emphasis"] = [_q(w)[:30] for w in words if _q(w)][:4]
        scenes.append(item)
    frame = {k: story.clean_text(str(data.get(k) or ""), keep_paras=False)[:200] for k in ("question", "answer", "punchline", "flip")}
    frame = {k: v for k, v in frame.items() if v}
    if scenes:
        warn += story_checks(scenes, frame)
    emph = [i for i, x in enumerate(scenes) if "emphasis" in x]
    for i in emph[EMPH_MAX:]:                     # больше трёх крупных текстов - уже не акцент
        scenes[i].pop("emphasis", None)
    if len(emph) > EMPH_MAX:
        warn.append(f"крупный текст (emphasis) в {len(emph)} сценах, оставлены первые {EMPH_MAX}: акцент работает, когда он редкий")
    hero = data.get("hero")
    if isinstance(hero, str):
        hero = {"query": hero}
    hero_out = None
    if isinstance(hero, dict) and _q(hero.get("query")):
        htype = _q(hero.get("type")).lower()
        hero_out = {"query": _q(hero["query"]), "vtype": htype if htype in ("photo", "illustration", "archive") else "archive",
                    "alt": [_q(a) for a in (hero.get("alt") or []) if _q(a)][:3] if isinstance(hero.get("alt"), list) else []}
    if scenes:
        long_ = [i for i, x in enumerate(scenes, 1) if len(x["text"]) > 130]
        if long_:
            warn.append("длинные сцены (динамика просядет, разбейте): " + ", ".join(map(str, long_[:6])))
    if scenes:
        script = story.clean_text(str(data.get("script") or ""), keep_paras=False)
        if script:
            import difflib
            a_ = re.findall(r"\w+", script.lower())
            b_ = re.findall(r"\w+", " ".join(x["text"] for x in scenes).lower())
            if difflib.SequenceMatcher(None, a_, b_, autojunk=False).ratio() < 0.9:
                warn.append("тексты сцен расходятся с полем script: озвучка пойдёт по сценам, проверьте, что ничего не потерялось")
        sym = [i for i, x in enumerate(scenes, 1) if re.search(r"[%$€₽()/\\]|\d", x["text"])]
        if sym:
            warn.append("цифры и знаки в тексте сцен " + ", ".join(map(str, sym[:8]))
                        + ": Edge может прочитать их с неверным окончанием, лучше словами («двадцать один процент»)")
        kinds = [(x.get("stat") or {}).get("kind") if statfx.is_full(x.get("stat")) else None for x in scenes]
        many = [k for k in set(kinds) if k and kinds.count(k) > 2]
        if many:
            warn.append("однообразно: карточек " + ", ".join(f"{k} ×{kinds.count(k)}" for k in many)
                        + " - чередуйте с фото, иллюстрациями, архивом и другими видами карточек")
        run_ = best = 0
        for k in kinds:
            run_ = run_ + 1 if k else 0
            best = max(best, run_)
        if best > 3:
            warn.append(f"{best} карточки подряд без картинки: ролик похож на презентацию, вставьте фото, архив или видео")
        n_cards = sum(1 for k in kinds if k)
        if graphics_level(ctx.get("cfg")) == "min" and n_cards > CARDS_MIN_MAX:
            warn.append(f"карточек {n_cards}, при графике «минимум» лучше до {CARDS_MIN_MAX}: оставьте их для цифр и сравнений, "
                        "остальное - точные картинки и крупный текст")
        n_words = sum(len(x["text"].split()) for x in scenes)
        lo_w, hi_w, secs = LENGTHS[opt(ctx.get("cfg") or {}, "length")][:3]
        if n_words > hi_w * 1.1:
            warn.append(f"{n_words} слов на {secs} с: диктору придётся спешить (лучше до {hi_w}). Уберите фразы, без которых ответ понятен, "
                        "или выберите длину побольше в настройках")
    lang = story.detect_lang(" ".join(s["text"] for s in scenes))
    cps = CPS_BY_LANG.get(lang, CHARS_PER_SECOND)
    seconds = sum(len(s["text"]) / cps + 0.15 for s in scenes) + TAIL   # длинные паузы сжимаются при сборке
    limit = min(MAX_SECONDS, config.CREATOR_MAX_SECONDS) if config.CREATOR else MAX_SECONDS
    if seconds > limit:
        errors.append(f"длина ≈ {seconds:.0f} с, максимум {limit} с: сократите сценарий")
    elif seconds > 62:
        warn.append(f"длина ≈ {seconds:.0f} с: дольше минуты")

    meta: dict = {"title": story.clean_text(data.get("title") or "Ролик", keep_paras=False)[:100],
                  "description": story.clean_text(data.get("description") or "", keep_paras=False)[:1000],
                  "tags": [str(t).strip()[:40] for t in (data.get("tags") or []) if str(t).strip()][:15]}
    v = data.get("voice")
    name = v.get("name") if isinstance(v, dict) else v
    if name:
        name = str(name)
        if name in config.VOICES or name.startswith("el:"):
            meta["voice"] = name
        elif name in el_voices():
            meta["voice"] = el_voices()[name]
        else:
            warn.append(f"voice: голоса «{name}» нет в списке; возьму голос из настроек")
    if isinstance(v, dict) and v.get("rate") is not None:
        try:
            meta["rate"] = max(-50, min(100, int(v["rate"])))
        except (TypeError, ValueError):
            pass
    m = data.get("music")
    mood = m.get("mood") if isinstance(m, dict) else m
    if mood:
        mood = str(mood)
        if mood in ("off", "auto") or mood in (ctx.get("music") or []):
            meta["music"] = mood
        else:
            warn.append(f"music: настроения «{mood}» нет ({', '.join(ctx.get('music') or []) or 'музыки нет'}); как в настройках")
    if isinstance(m, dict) and m.get("volume") is not None:
        try:
            meta["music_vol"] = max(0, min(100, int(m["volume"])))
        except (TypeError, ValueError):
            pass
    channel = data.get("channel") or ctx.get("channel")
    if not config.has_channel(channel or ""):
        if data.get("channel"):
            warn.append(f"channel: канала «{data.get('channel')}» нет; взят «{ctx.get('channel')}»")
        channel = ctx.get("channel")
    idea = story.clean_text(str(data.get("idea") or ""), keep_paras=False)[:200]
    if hero_out is None and scenes:              # главный кадр темы не задан: первый архивный или фото-запрос сценария
        first = next((x for x in scenes if x.get("query") and x.get("vtype") in ("archive", "photo")), None)
        if first:
            hero_out = {"query": first["query"], "vtype": first["vtype"], "alt": first.get("alt", [])}
    if noq and not hero_out:
        warn.append("сцены " + ", ".join(map(str, noq[:8])) + ": нет visual.query и нет главного кадра (hero) - возьму слова из текста, "
                    "попадание хуже. Добавьте hero или запрос картинки")
    return {"errors": errors, "warnings": warn, "scenes": scenes, "meta": meta, "channel": channel, "seconds": seconds, "lang": lang,
            "idea": idea or frame.get("answer", ""), "frame": frame, "hero": hero_out}


# шаблоны, по которым текст узнаётся как написанный нейросетью (проверка сценария до монтажа)
AI_CLICHES = [
    r"причина (довольно |очень |на самом деле )?прост", r"вот в ч[её]м дело", r"но есть нюанс", r"а теперь самое интересное",
    r"секрет в том", r"это не просто", r"не просто \w+, а", r"так что да\b", r"\bне факт\b", r"давайте разбер[её]мся",
    r"в современном мире", r"не секрет, что", r"удивительно, но", r"представьте себе", r"и это ещё не всё", r"\bиграет (важную|ключевую) роль",
    r"\band the reason is simple", r"\bhere'?s the thing", r"\bhere'?s why\b", r"\bbut here'?s the catch", r"\bplot twist",
    r"\blet that sink in", r"\bit'?s not just\b", r"\bgame[- ]changer", r"\bin today'?s world", r"\bso yes\b", r"\blet'?s dive",
]


def cliche_hits(scenes: list[dict]) -> list[tuple[int, str]]:
    """(номер сцены, найденная фраза) - шаблонные обороты в тексте сцен."""
    hits = []
    for i, sc in enumerate(scenes, 1):
        low = (sc.get("text") or "").lower()
        for pat in AI_CLICHES:
            m = re.search(pat, low)
            if m:
                hits.append((i, m.group(0)))
                break
    return hits


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё]+", (text or "").lower())


def _opens_with_link(text: str) -> bool:
    """Фраза цепляется за предыдущую: начинается со связки или содержит её в первых трёх словах («Сегодня всё наоборот», «И тут…»)."""
    low = " ".join(_words(text)[:4])
    return any(low == c or low.startswith(c + " ") or f" {c} " in f" {' '.join(low.split()[:3])} " for c in CONNECTORS)


def story_checks(scenes: list[dict], frame: dict) -> list[str]:
    """Проверка сюжета до монтажа: вопрос в начале, связки между фразами, финал возвращается к вопросу, нет «подпишись»."""
    warn = []
    first, last = scenes[0]["text"], scenes[-1]["text"]
    if len(first.split()) > 16:
        warn.append(f"сюжет: первая фраза {len(first.split())} слов, лучше до 14 - интрига должна прозвучать за 3 секунды")
    seen_q: dict[str, int] = {}
    dup = []
    for i, x in enumerate(scenes, 1):
        q = (x.get("query") or "").strip().lower()
        if q:
            if q in seen_q:
                dup.append(f"{seen_q[q]} и {i}")
            seen_q.setdefault(q, i)
    if dup:
        warn.append("визуал: одинаковый запрос картинки в сценах " + ", ".join(dup[:4]) + " - картинка повторится; дайте разные запросы")
    hits = cliche_hits(scenes)
    if hits:
        warn.append("текст звучит шаблонно (как нейросеть): " + "; ".join(f"сцена {i} - «{h}»" for i, h in hits[:5])
                    + ". Нажмите «🗣 Сделать текст живее»")
    if re.search(r"подпиш|лайк|колокольч|subscribe|like the video", last.lower()):
        warn.append("сюжет: финал - это ответ на вопрос и поворот, а не «подпишись»; зритель досматривает ради ответа")
    q = set(w[:5] for w in _words(frame.get("question") or first) if len(w) > 3)
    tail = set(w[:5] for x in scenes[-2:] for w in _words(x["text"]) if len(w) > 3)
    if q and not q & tail:
        warn.append("сюжет: финал не возвращается к вопросу из начала - зритель не получит ответа (повторите ключевое слово вопроса в финале)")
    return warn


def voice_for(cfg: dict, meta: dict, lang: str) -> str:
    if meta.get("voice"):
        return meta["voice"]
    if cfg.get("voice") and cfg["voice"] != "auto":
        return cfg["voice"]
    return story.VOICE_BY_LANG.get(lang, config.TTS_VOICE)


def voice_label(voice: str) -> str:
    for name, v in el_voices().items():
        if v == voice:
            return f"ElevenLabs · {name}"
    return voice


def _stat_line(st: dict) -> str:
    k = st["kind"]
    if k == "number":
        return st["value"]
    if k == "bars":
        return "столбцы " + ", ".join(i["label"] for i in st["items"])
    if k == "line":
        return f"график {len(st['values'])} точек"
    if k == "list":
        return f"список из {len(st['items'])}"
    if k == "compare":
        return f"{st['left']['value']} vs {st['right']['value']}"
    if k == "flow":
        return "цепочка: " + " → ".join(x["text"] for x in st["steps"])
    if k == "icons":
        return "иконки: " + ", ".join(x.get("label") or x["icon"] for x in st["items"])
    return "фраза: " + st["text"][:30]


def summary(res: dict, cfg: dict) -> str:
    m = res["meta"]
    voice = voice_label(voice_for(cfg, m, res["lang"]))
    lines = [f"🎞 Умный монтаж: «{m['title']}»",
             f"Канал: {res['channel']} · сцен: {len(res['scenes'])} · ≈ {res['seconds']:.0f} с · голос: {voice}",
             f"Клипы: своя библиотека → {'стоки (Pexels/Pixabay)' if cfg.get('stock', True) else 'только своя библиотека'}",
             *([f"❓ Вопрос: {res['frame']['question']}"] if (res.get("frame") or {}).get("question") else []),
             *([f"💡 Ответ: {res['idea']}"] if res.get("idea") else []),
             *([f"🖼 Главный кадр: {res['hero']['query']}"] if res.get("hero") else []),
             ""]
    for i, s in enumerate(res["scenes"], 1):
        emph = ("🔠 крупный текст" + (f" ({', '.join(s['emphasis'])})" if s["emphasis"] else "")) if "emphasis" in s else ""
        if s.get("map"):
            emph = "🗺 " + histmap.describe(s["map"], res.get("lang", "ru"))
        vis = "" if s.get("map") else ('графика на весь кадр' if (s.get('stat') or {}).get('full') else ((VLABEL.get(s.get('vtype', 'video'), '') + (s['query'] or '')) if s.get('query') else (s.get('own') or s.get('theme') or ('главный кадр' if res.get('hero') else '(по тексту)'))))
        lines.append(f"{i}. {BLOCKS.get(s.get('block'), '')}«{s['text'][:120]}{'…' if len(s['text']) > 120 else ''}»\n   🎬 {emph or ''}{' · ' if emph and vis else ''}{vis}"
                     + (f" · 📊 {_stat_line(s['stat'])}" if s.get("stat") else ""))
    if res["warnings"]:
        lines.append("\n⚠️ Замечания:\n" + "\n".join(f"• {w}" for w in res["warnings"][:8]))
    n_card = sum(1 for x in res["scenes"] if (x.get("stat") or {}).get("full") or (x.get("stat") or {}).get("kind") in statfx.FULL_ONLY)
    share = round(100 * n_card / max(len(res["scenes"]), 1))
    lo, hi, label = GRAPHICS[graphics_level(cfg)]
    lines.append(f"\n🎨 Графика на весь кадр: {n_card} из {len(res['scenes'])} сцен ({share}%), в настройках: {label}")
    if cfg.get("graphics") != "off" and not lo - 15 <= share <= hi + 15:
        lines.append(f"⚠️ Доля графики {share}% далеко от выбранных {lo}-{hi}%: можно собрать так или попросить нейросеть переделать сценарий.")
    out = "\n".join(lines)
    return out if len(out) <= 3900 else out[:3890] + "…"


# ---------- подбор клипов ----------

def keywords(text: str) -> list[str]:
    toks = re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower())
    return [t for t in dict.fromkeys(toks) if t not in STOP][:8]


def _stem(w: str) -> str:
    for suf in ("ing", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def coverage(query: str, text: str) -> float:
    """Доля слов запроса, которые есть в описании/тегах ролика (с учётом окончаний): 0..1."""
    kw = [_stem(k) for k in keywords(query)]
    if not kw:
        return 0.0
    have = {_stem(t) for t in re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower())}
    return min(1.0, sum(1 for k in kw if k in have) / min(len(kw), 3))


def _abs(rel: str) -> Path:
    return config.ROOT / rel


def library_candidates(queries: list[str], theme: str | None, used: set[Path]) -> list[tuple[Path, float]]:
    """Клипы своей библиотеки с долей совпавших слов запроса в тегах и названии (лучшая по всем запросам)."""
    best: dict[str, float] = {}
    for qi, q in enumerate(queries):
        kws = keywords(q)
        if not kws:
            continue
        hits: dict[str, int] = {}
        for t in kws:
            for a in library.search("clip", [t], 300):
                hits[a["path"]] = hits.get(a["path"], 0) + 1
        for path, n in hits.items():
            cov = min(1.0, n / min(len(kws), 3)) * (1.0 if qi == 0 else 0.9)
            best[path] = max(best.get(path, 0.0), cov)
    out = [(_abs(p), c) for p, c in best.items() if _abs(p).exists()]
    out.sort(key=lambda x: (-x[1], x[0] in used, x[0].name))
    return out[:8]


def _sharpness(it: dict) -> float:
    """Насколько чётким ролик будет в вертикальном кадре 1080x1920: горизонтальное 1920x1080 обрезается до 607 px по ширине
    и растягивается почти вдвое (0,56), 4K или вертикальное видео - без растяжения (1,0)."""
    w, h = it.get("w") or 0, it.get("h") or 0
    if not w or not h:
        return 0.5
    crop_w = w if h >= w else h * 9 / 16
    return min(1.0, crop_w / 1080)


def stock_candidates(query: str, used_ids: set, report: list, context: str = "", scores: dict | None = None) -> list[tuple[Path, float]]:
    """Ищет на стоках и скачивает лучшие ролики (до 3). Порядок: если задан ANTHROPIC_API_KEY - Claude смотрит на превью и ставит
    оценку по смыслу, иначе - совпадение слов запроса с описанием ролика. Теги запроса получают только подошедшие ролики."""
    if not query:
        return []
    try:
        items, errs = sources.search(query, per_page=10)
    except sources.NoKeys as e:
        if "no_keys" not in used_ids:
            used_ids.add("no_keys")
            report.append(str(e))
        return []
    except Exception as e:  # noqa: BLE001
        report.append(f"стоки: {str(e)[:120]}")
        return []
    ranked: list[tuple[float, dict]] = []     # (оценка 0..1, ролик); 0.5 и больше - подходит
    vis = None
    if vision.available() and items:
        items = items[: vision.MAX_IMAGES]
        vis = vision.score(vision.fetch_many([it.get("thumb") for it in items]), query, context or query)
        if vis is None and "vision" not in used_ids:
            used_ids.add("vision")
            report.append("проверка кадров через Claude недоступна (ключ или сеть): подбор по тегам")
    if vis is not None:
        for sc, it in zip(vis, items):
            if sc >= VISION_WEAK:
                ranked.append((sc / (2 * VISION_GOOD), it))
                if scores is not None:
                    scores[(it["provider"], it["id"])] = sc
    else:
        for it in items:
            cov = coverage(query, it.get("tags", ""))
            if cov > 0:
                ranked.append((cov, it))
    ranked.sort(key=lambda x: (-round(x[0], 2), -_sharpness(x[1])))     # при равном смысле - чётче в вертикальном кадре
    out: list[tuple[Path, float]] = []
    for cov, it in ranked:
        try:
            res = sources.add_item(it, STOCK_THEME)
        except Exception as e:  # noqa: BLE001
            report.append(f"не скачался {it['provider']} {it['id']}: {str(e)[:100]}")
            continue
        if res.get("asset"):
            tags = keywords(it.get("tags", ""))
            if cov >= MIN_COV:
                tags += keywords(query)
            library.add_tags(res["asset"]["id"], list(dict.fromkeys(tags)))
        if scores is not None and (it["provider"], it["id"]) in scores:
            scores[res["path"]] = scores[(it["provider"], it["id"])]
        out.append((res["path"], cov))
        if len(out) >= 3 or (cov < MIN_COV and out):
            break
    return out


def plan_pieces(need: float) -> list[float]:
    """Длины кусков для сцены: до PIECE_MAX секунд, примерно поровну, не короче PIECE_MIN."""
    if need <= PIECE_MAX * 1.15:
        return [need]
    k = math.ceil(need / PIECE_MAX)
    return [need / k] * k


def pick_for_scene(scene: dict, need: float, state: dict, cfg: dict, ctx: dict) -> tuple[list[tuple[Path, float | None]], str]:
    """-> ([(клип, начало)], откуда: own|library|stock|weak|card). Пустой список + «card» - кадра по смыслу нет, нужна графика.
    state: общие для ролика used и отчёт."""
    pieces = plan_pieces(need)
    used: set[Path] = state["used"]
    report: list = state["report"]
    if scene.get("own"):
        f = next((p for p in mix.own_clips() if p.name == scene["own"]), None)
        if f is not None and f.exists():
            return [(f, scene.get("start"))], "own"
    queries = [q for q in [scene.get("query"), *scene.get("alt", [])] if q] or [" ".join(keywords(scene["text"])[:3])]
    lib = library_candidates(queries, scene.get("theme") if not scene.get("query") else None, used)
    scores: dict = state.setdefault("scores", {})
    good = [c for c, cov in lib if cov >= MIN_COV and c not in used]
    weak = [c for c, cov in lib if 0 < cov < MIN_COV and c not in used]
    if vision.available() and (good or weak):          # теги в библиотеке врут чаще, чем картинка: смотрим сами кадры
        cand = (good + weak)[:4]
        vs = vision.score([vision.frame_of(c, max(video_duration(c) / 2 - 0.1, 0.0)) for c in cand], queries[0], scene["text"])
        if vs is not None:
            good = [c for c, v in zip(cand, vs) if v >= VISION_GOOD]
            weak = [c for c, v in zip(cand, vs) if VISION_WEAK <= v < VISION_GOOD]
            scores.update({c: v for c, v in zip(cand, vs)})
    source = "library" if good else ""
    if len(good) < len(pieces) and cfg.get("stock", True):
        for q in queries:
            for c, cov in stock_candidates(q, state["stock_ids"], report, scene["text"], scores):
                if c in used or c in good or c in weak:
                    continue
                (good if cov >= MIN_COV else weak).append(c)
            if good:
                source = source or "stock"
            if len(good) >= len(pieces):
                break
    cands = good or weak
    if not cands:
        return [], "card"
    source = source if good else "weak"
    out = []
    for i in range(len(pieces)):
        clip = cands[i % len(cands)]
        used.add(clip)
        out.append((clip, None))
    return out, source


# картинки с крупными надписями, логотипами и баннерами (часто на чужом языке) в ролик не берём
NEG_TAGS = {"text", "word", "words", "letter", "letters", "lettering", "font", "typography", "quote", "quotes", "sign", "logo",
            "banner", "poster", "sale", "discount", "label", "alphabet", "calligraphy", "slogan", "headline", "caption"}


def _entities(query: str) -> list[str]:
    """Имена собственные и годы из запроса (Venezuela, Weimar, 1923): у картинки они обязаны быть в описании,
    иначе придёт похожий снимок другой страны или эпохи."""
    out = []
    for i, w in enumerate(re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", query or "")):
        if re.fullmatch(r"1[5-9]\d\d|20\d\d", w) or (w[:1].isupper() and len(w) > 2 and not (i == 0 and w.lower() in STOP)):
            out.append(w.lower())
    return out


def pick_image(scene: dict, vtype: str, state: dict, cfg: dict) -> tuple[Path, str] | None:
    """Картинка под сцену (фото/иллюстрация Pixabay, архив Wikimedia Commons). -> (файл, подпись автора) или None."""
    if not cfg.get("stock", True):
        return None
    used = state.setdefault("used_img", set())
    queries = [q for q in [scene.get("query"), *scene.get("alt", [])] if q][:3]
    for q in queries:
        try:
            items = [it for it in imagesrc.search(q, vtype) if (it["provider"], it["id"]) not in used]
        except Exception as e:  # noqa: BLE001
            state["report"].append(f"картинки ({vtype}): {str(e)[:120]}")
            continue
        if not items:
            continue
        ranked = None
        if vision.available():
            vs = vision.score(vision.fetch_many([it["thumb"] for it in items[: vision.MAX_IMAGES]]), q, scene["text"])
            if vs is not None:
                ranked = sorted(((v, it) for v, it in zip(vs, items) if v >= VISION_GOOD), key=lambda x: -x[0])
        if ranked is None:
            strict = opt(cfg, "strict")
            if strict:      # строго: совпали 2 из 3 слов запроса и все имена собственные и годы
                need = 0.67 if len(keywords(q)) >= 2 else 1.0
            else:           # у архивных снимков описание подробное, достаточно совпадения части слов; у стоков - половины
                need = 0.34 if vtype == "archive" else MIN_COV
            ents = _entities(q) if (strict or vtype == "archive") else []
            ranked = sorted(((coverage(q, it["tags"]), it) for it in items), key=lambda x: -x[0])
            ranked = [x for x in ranked if x[0] >= need and all(e in x[1]["tags"].lower() for e in ents)
                      and not (strict and vtype != "archive" and NEG_TAGS & set(re.findall(r"[a-z]+", x[1]["tags"].lower())))]
        for _, it in ranked[:2]:
            try:
                path = imagesrc.download(it)
            except Exception as e:  # noqa: BLE001
                state["report"].append(f"картинка не скачалась: {str(e)[:100]}")
                continue
            used.add((it["provider"], it["id"]))
            return path, it.get("credit", "")
    return None


def _image_part(work: Path, i: int, img: Path, length: float, variant: int = 0, mode: str = "fill") -> dict:
    """Картинка оживает: медленный наезд/отъезд или панорама. Вертикальная - на весь кадр. Горизонтальная: mode=fill - на весь
    кадр с панорамой вдоль снимка, mode=card - карточкой в верхней части кадра на размытой подложке из неё же."""
    part = work / f"p{i:02}.mp4"
    try:
        info = run_probe(img)
        w, h = info
    except Exception:  # noqa: BLE001
        w, h = 1600, 900
    frames = max(2, int(round(length * 30)) + 2)
    motion = variant % 3                    # 0 - наезд, 1 - отъезд, 2 - панорама
    if motion == 0:
        z, x, y = f"1+0.10*on/{frames}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion == 1:
        z, x, y = f"1.10-0.10*on/{frames}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    else:
        z, x, y = "1.10", f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"
    enc = ["-t", f"{length:.2f}", "-an", "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(compose.PART_CRF),
           "-pix_fmt", "yuv420p", str(part)]
    vertical = h / max(w, 1) >= 1.25
    # маленькую картинку не растягиваем на весь кадр (Pixabay отдаёт до 1280 px, растяжение в 2 раза - это «мыло»):
    # она идёт карточкой на размытой подложке из неё же, почти в своём размере
    if (vertical and w < UPSCALE_MIN_W) or (not vertical and mode == "fill" and 2040 / max(h, 1) > UPSCALE_MAX):
        mode, vertical = "card", False
    if vertical:                             # вертикальная: на весь кадр
        graph = (f"[0:v]scale=2160:3840:force_original_aspect_ratio=increase:flags=lanczos,crop=2160:3840,"
                 f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s=1080x1920:fps=30,setsar=1[v]")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(img), "-filter_complex", graph, "-map", "[v]"] + enc
    elif mode == "fill":                     # горизонтальная на весь кадр: камера плывёт вдоль снимка
        span = f"(iw-1080)*min(t/{max(length, 0.5):.2f},1)"
        x = span if variant % 2 == 0 else f"(iw-1080)-{span}"
        graph = (f"[0:v]scale=-2:2040:flags=lanczos,crop=1080:1920:x='{x}':y='(ih-1920)/2',eq=contrast=1.04:saturation=1.06,"
                 f"unsharp=5:5:0.5,setsar=1,fps=30[v]")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{length:.2f}", "-i", str(img),
               "-filter_complex", graph, "-map", "[v]"] + enc
    else:
        fh = min(1400, int(round(1080 * h / max(w, 1) / 2)) * 2)
        fw = 1080 if fh < 1400 else int(round(1400 * w / max(h, 1) / 2)) * 2      # горизонтальная - во всю ширину кадра
        top = max(140, int(720 - fh / 2))                # над строкой субтитров
        graph = (f"[0:v]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=14,"
                 f"scale=1080:1920,eq=brightness=-0.3:saturation=0.75,setsar=1[bg];"
                 f"[1:v]scale={fw * 2}:-2:flags=lanczos,zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={fw}x{fh}:fps=30,"
                 f"unsharp=5:5:0.4,setsar=1[fg];"
                 f"[bg][fg]overlay=(W-w)/2:{top}:shortest=1,fps=30[v]")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{length:.2f}", "-i", str(img), "-i", str(img),
               "-filter_complex", graph, "-map", "[v]"] + enc
    run(cmd)
    return {"part": part, "A": video_duration(part), "speed": 1.0}


def run_probe(img: Path) -> tuple[int, int]:
    import subprocess
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0:s=x", str(img)], capture_output=True, text=True, timeout=30)
    w, h = res.stdout.strip().split("x")[:2]
    return int(w), int(h)


def _relaxed_pick(sc: dict, need: float, state: dict, cfg: dict, allowed: set, credits: list) -> tuple[list, Path | None, str]:
    """Точного кадра нет: пробуем 1-2 самых «предметных» слова из запроса (или из английского текста сцены) - фото, видео,
    иллюстрация, без строгого фильтра, но с проверкой совпадения слов. -> (клипы, картинка, источник) или ([], None, "")."""
    from core.reels import en_queries
    src_text = " ".join([sc.get("query") or "", *sc.get("alt", [])]).strip()
    if not src_text and re.fullmatch(r"[\x00-\x7f]*", sc.get("text") or ""):
        src_text = sc.get("text") or ""
    words = en_queries(src_text)[:2]
    soft = {**cfg, "strict": False}
    for word in words:
        rsc = {**sc, "query": word, "alt": []}
        for kind in [k for k in ("photo", "video", "illustration") if k in allowed]:
            if kind == "video":
                picks, src = pick_for_scene(rsc, need, state, soft, {})
                if picks and src not in ("weak", "card"):
                    return picks, None, "relaxed"
                continue
            found = pick_image(rsc, kind, state, soft)
            if found:
                img, credit = found
                if credit:
                    credits.append(credit)
                return [], img, "relaxed"
    return [], None, ""


def _auto_icon_card(scene: dict) -> dict | None:
    """Карточка-иконка из слов запроса (oil -> капля, bread -> хлеб), когда честной картинки не нашлось."""
    from core import iconpack
    for q in [scene.get("query"), *scene.get("alt", [])]:
        for w in keywords(q or ""):
            if iconpack.resolve(w):
                return {"kind": "icons", "items": [{"icon": w, "label": ""}], "label": "", "source": "", "unit": "", "full": True}
    return None


def _hero_bg(work: Path, hero: Path, name: str = "bg_hero.png", blur: int = 28, bright: float = -0.32) -> Path | None:
    """Затемнённый и размытый главный кадр темы - фон карточек и крупного текста вместо цветного градиента."""
    out = work / name
    if out.exists():
        return out
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(hero), "-vf",
             f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur={blur}:3,"
             f"eq=brightness={bright}:saturation=0.75,setsar=1", "-frames:v", "1", str(out)])
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("фон из главного кадра не получился: %s", e)
        return None


def _bg_image(work: Path, variant: int, hero: Path | None = None) -> Path:
    """Фон карточки картинкой, один раз на ролик: затемнённый главный кадр темы (тёмное оформление) или градиент с мягкими
    кругами. Дальше фон только «оживает» наездом камеры - рисовать его каждый кадр заново в разы медленнее."""
    if hero is not None and statfx.THEME != "light":
        got = _hero_bg(work, hero)
        if got is not None:
            return got
    c0, c1 = statfx.PALETTES[variant % len(statfx.PALETTES)]
    out = work / f"bg_{statfx.THEME}_{variant % len(statfx.PALETTES)}.png"
    if out.exists():
        return out
    ass = out.with_suffix(".ass")
    subtitles.write_ass([], ass, None, None, statfx.decor_static(variant))
    src = f"gradients=s=1080x1920:c0={c0}:c1={c1}:x0=0:y0=0:x1=700:y1=1920"
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, "-vf", f"subtitles={ass.name}", "-frames:v", "1", out.name],
            cwd=work)
    except Exception:  # noqa: BLE001 - старый ffmpeg без gradients
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={c1}:s=1080x1920", "-frames:v", "1", out.name], cwd=work)
    return out


def _card_part(work: Path, i: int, length: float, variant: int = 0, stat: dict | None = None, lang: str = "ru",
               hero: Path | None = None, extra: list[str] | None = None, bg: Path | None = None) -> dict:
    """Сцена-карточка целиком в видео: фон + графика + медленный наезд камеры. Графика «запекается» в кусок, поэтому
    переходы между сценами идут вместе с ней, а не поверх. extra - готовые строки ASS (крупный текст), bg - свой фон."""
    part = work / f"p{i:02}.mp4"
    bg = bg or _bg_image(work, variant, hero)
    vf = "fps=30"
    ev = (statfx.events(stat, 0.0, length, lang, variant, decor=False) if stat else []) + list(extra or [])
    if ev:
        ass = work / f"card{i:02}.ass"
        subtitles.write_ass([], ass, None, None, ev)
        vf += f",subtitles={ass.name}"
    zoom = f"(1+0.05*t/{max(length, 0.5):.2f})"
    vf += (f",scale=w='trunc(1080*{zoom}/2)*2':h='trunc(1920*{zoom}/2)*2':eval=frame:flags=bicubic,"
           "crop=1080:1920:'(iw-1080)/2':'(ih-1920)/2',setsar=1")
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{length:.2f}", "-i", bg.name, "-vf", vf, "-t", f"{length:.2f}",
         "-an", "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(compose.PART_CRF), "-pix_fmt", "yuv420p", part.name],
        cwd=work)
    return {"part": part, "A": video_duration(part), "speed": 1.0}


def _bg_part(work: Path, i: int, length: float, variant: int = 0, hero: Path | None = None) -> dict:
    """Пустой фон (сцена без картинки и без графики)."""
    return _card_part(work, i, length, variant, hero=hero)


def _emph_part(work: Path, i: int, length: float, events: list[str], img: Path | None, variant: int, hero: Path | None) -> dict:
    """Крупный текст вместо ведущего: слова появляются по одному поверх затемнённой картинки сцены или главного кадра."""
    bg = _hero_bg(work, img, f"bg_emph{i:02}.png", blur=6, bright=-0.38) if img is not None else None
    if bg is None and hero is not None:
        bg = _hero_bg(work, hero, "bg_emph_hero.png", blur=6, bright=-0.38)
    return _card_part(work, i, length, variant, extra=events, bg=bg, hero=hero)


def insert_silence(src: Path, dst: Path, at: list[float], dur: float) -> Path:
    """Вставляет тишину dur секунд в моменты at (секунды исходной озвучки): пауза после вопроса и перед финалом,
    которую сжатие пауз убрало бы."""
    import wave

    import numpy as np
    with wave.open(str(src)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if sw != 2 or not at:
        return src
    x = x.reshape(-1, ch) if ch > 1 else x.reshape(-1, 1)
    gap = np.zeros((int(sr * dur), x.shape[1]), dtype=np.int16)
    parts, pos = [], 0
    for t in sorted(at):
        k = min(max(int(t * sr), pos), len(x))
        parts += [x[pos:k], gap]
        pos = k
    parts.append(x[pos:])
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.concatenate(parts).tobytes())
    return dst


# ---------- сборка ----------

def _clean_word(tok: str) -> str:
    """Слово для субтитра: без кавычек и хвостовых запятых/точек (вопрос и восклицание остаются)."""
    w = tok.strip("«»\"'“”„()[]")
    w = re.sub(r"[,.;:…—–-]+$", "", w)
    return w


def sub_groups(tokens: list[str], times: list[float], speech_end: float, max_words: int = 3,
               max_chars: int = 22, comma: bool = True) -> list[dict]:
    """Субтитры из текста сценария (Whisper путает имена, числа и окончания), время слов - из распознавания.
    Группа: до 3 слов и ~22 знаков, обрывается на конце фразы и на запятой. -> [{"a", "b", "words": [(время, слово)]}]"""
    words = [(times[i], _clean_word(t), t) for i, t in enumerate(tokens) if re.search(r"\w", t)]
    words = [w for w in words if w[1]]
    groups, cur = [], []
    for k, (t, w, raw) in enumerate(words):
        if cur and (len(cur) >= max_words or len(" ".join(x[1] for x in cur + [(t, w)])) > max_chars):
            groups.append(cur)
            cur = []
        cur.append((t, w))
        if raw[-1] in (".!?…,;:—" if comma else ".!?…:") or (k + 1 < len(words) and words[k + 1][0] - t > 0.9):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    out = []
    for gi, g in enumerate(groups):
        a = g[0][0]
        nxt = groups[gi + 1][0][0] if gi + 1 < len(groups) else speech_end + 0.4
        b = max(min(nxt, g[-1][0] + 1.0), a + 0.3)
        out.append({"a": a, "b": b, "words": g})
    return out


def tighten_pauses(src: Path, dst: Path, keep: float) -> tuple[Path, float]:
    """Сжимает паузы внутри озвучки до keep секунд (Edge-TTS делает 0,5-1 с тишины после каждого предложения,
    из-за этого голос «обрывается»). Склейка с короткой перекрёстной сменой, щелчков нет. -> (файл, сколько секунд убрано)."""
    try:
        import wave

        import numpy as np
    except ImportError:
        return src, 0.0
    try:
        with wave.open(str(src)) as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            return src, 0.0
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        hop = max(1, int(sr * 0.01))
        n = len(x) // hop
        if n < 20:
            return src, 0.0
        rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-9)
        db = 20 * np.log10(rms / 32768.0)
        thr = max(float(np.percentile(db, 95)) - 38.0, -58.0)
        silent = db < thr
        keep_f = max(4, int(round(keep / 0.01)))
        segs, pos, removed, i = [], 0, 0.0, 0
        while i < n:
            if not silent[i]:
                i += 1
                continue
            j = i
            while j < n and silent[j]:
                j += 1
            if i > 0 and j < n and (j - i) > keep_f + 6:        # только паузы внутри речи, края уже обрезаны
                cut_a = (i + keep_f // 2) * hop
                cut_b = (j - (keep_f - keep_f // 2)) * hop
                segs.append((pos, cut_a))
                removed += (cut_b - cut_a) / sr
                pos = cut_b
            i = j
        if removed < 0.05:
            return src, 0.0
        segs.append((pos, len(x)))
        fade = int(sr * 0.012)
        y = x[segs[0][0]:segs[0][1]].copy()
        for s0, s1 in segs[1:]:
            seg = x[s0:s1]
            f = min(fade, len(y), len(seg))
            if f > 0:
                ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
                y[-f:] = y[-f:] * (1 - ramp) + seg[:f] * ramp
                y = np.concatenate([y, seg[f:]])
            else:
                y = np.concatenate([y, seg])
        with wave.open(str(dst), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(np.clip(y, -32768, 32767).astype(np.int16).tobytes())
        return dst, removed
    except Exception as e:  # noqa: BLE001
        log.warning("паузы в озвучке не сжаты: %s", e)
        return src, 0.0


def _speakable_join(scenes: list[dict]) -> str:
    """Весь текст одним абзацем: перенос строки Edge читает как длинную паузу. Без точки в конце сцены - ставим её."""
    parts = []
    for sc in scenes:
        t = sc["text"].strip()
        parts.append(t if t[-1:] in ".!?…:;," else t + ".")
    return " ".join(parts)


def reset_stock_tags() -> None:
    """Один раз: прежняя версия ставила скачанным роликам слова запроса, даже если кадр не подходил. Теги сбрасываются,
    правильные вернутся при следующем поиске на стоках (по описанию самого ролика)."""
    marker = config.ROOT / ".smart_tags_v2"
    if marker.exists():
        return
    try:
        with library.connect() as c:
            c.execute("UPDATE assets SET tags=? WHERE type='clip' AND path LIKE ?", (STOCK_THEME, f"%/{STOCK_THEME}/%"))
        marker.write_text("1", "utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("сброс тегов стоковых клипов не удался: %s", e)


def build(task_dir: Path, meta: dict) -> Path:
    sm = meta["smart"]
    cfg = {**DEFAULT_CFG, **(meta.get("smart_cfg") or {})}
    lang = sm.get("lang", "ru")
    voice = meta.get("voice") or voice_for(cfg, {}, lang)
    pace = PACE.get(cfg.get("pace"), PACE["normal"])
    rate = meta.get("rate") or pace[0] or None
    mix.sync_clips()
    library.scan()
    reset_stock_tags()
    work = task_dir / "parts"
    work.mkdir(exist_ok=True)
    scenes = sm["scenes"]

    narration = _speakable_join(scenes)
    raw = work / "smart.mp3"
    tts.synthesize(narration, raw, voice, rate)
    voice_file = compose._trim_speech(raw, work / "smart.wav")
    voice_file, cut = tighten_pauses(voice_file, work / "smart_tight.wav", pace[1])
    if cut:
        log.info("паузы в озвучке сжаты на %.1f с", cut)
    speech_len = audio_duration(voice_file)
    total = speech_len + TAIL

    tokens: list[str] = []
    first_tok: list[int] = []
    for s in scenes:
        first_tok.append(len(tokens))
        tokens += s["text"].split()
    try:
        words = subtitles.transcribe_words(voice_file, lang)
    except Exception as e:  # noqa: BLE001 - без Whisper границы считаются по длине слов
        log.warning("Whisper не сработал (%s): границы сцен по длине слов", e)
        words = []
        meta.setdefault("warnings", []).append("Whisper не сработал: смена кадров и субтитры рассчитаны по длине слов, возможны неточности")
    times = story.align(tokens, words, speech_len - 0.05)
    # пауза после вопроса и перед финалом (панчлайном): зрителю нужна секунда, чтобы вопрос «повис», а ответ прозвучал
    beat_at = []
    if len(scenes) > 2:
        beat_at.append(1)
        fin = next((k for k in range(len(scenes) - 1, 1, -1) if scenes[k].get("block") in ("flip", "climax")), len(scenes) - 1)
        beat_at.append(fin)
        punch = next((k for k in range(2, fin) if scenes[k].get("block") == "punch"), None)
        if punch:
            beat_at.append(punch)
    beat_at = sorted(set(k for k in beat_at if 0 < k < len(scenes)))
    if beat_at:
        dur = BEAT_PAUSE + (0.1 if cfg.get("pace") == "calm" else 0.0)
        cuts = [max(0.0, times[first_tok[k]] - 0.04) for k in beat_at]
        try:
            voice_file = insert_silence(voice_file, work / "smart_beats.wav", cuts, dur)
            times = [t + dur * sum(1 for c in cuts if t >= c) for t in times]
            speech_len = audio_duration(voice_file)
            total = speech_len + TAIL
        except Exception as e:  # noqa: BLE001
            log.warning("паузы между тактами не вставлены: %s", e)
    starts = [0.0] + [max(0.0, times[i] - LEAD) for i in first_tok[1:]]
    for i in range(1, len(starts)):                       # границы идут строго вперёд
        starts[i] = max(starts[i], starts[i - 1] + 0.6)
    ends = starts[1:] + [total]
    log.info("умный монтаж: %d сцен, речь %.1f с, Whisper %d слов", len(scenes), speech_len, len(words))

    look = merged_look({"layout": "fill", "crop_bars": "auto"})
    statfx.set_theme(opt(cfg, "theme"))
    allowed = {k for k in ("video", "photo", "illustration", "archive") if opt(cfg, f"src_{k}")}
    state = {"used": set(), "stock_ids": set(), "report": []}
    items, report_scenes, stat_events, credits = [], [], [], []
    pal = sum(map(ord, meta.get("title") or "")) % len(statfx.PALETTES)     # одна палитра на весь ролик
    hero_img = None
    hero = sm.get("hero")
    if hero and hero.get("query"):        # главный кадр темы: фон крупного текста и карточек, замена «кадра мимо»
        hsc = {"query": hero["query"], "alt": hero.get("alt", []), "text": ""}
        for kind in [k for k in dict.fromkeys([hero.get("vtype", "archive"), "archive", "photo", "illustration"]) if k in allowed]:
            found = pick_image(hsc, kind, state, cfg)
            if found:
                hero_img, credit = found
                if credit:
                    credits.append(credit)
                break
    mute: list[tuple[float, float]] = []      # где субтитры не нужны: там крупный текст
    last_src = ""
    n = 0
    maps_used = False
    for i, sc in enumerate(scenes):
        need = max(ends[i] - starts[i], 0.8)
        if sc.get("map") and opt(cfg, "maps") != "off":
            k0, k1 = first_tok[i], (first_tok[i + 1] if i + 1 < len(scenes) else len(tokens))
            wrel = [(max(0.0, times[k] - starts[i]), tokens[k]) for k in range(k0, k1)]
            try:
                part = histmap.render(work, f"p{n:02}", need, sc["map"], lang, wrel, statfx.ACCENTS[pal % len(statfx.ACCENTS)])
                items.append({"part": part, "A": video_duration(part), "speed": 1.0})
                n += 1
                maps_used = True
                report_scenes.append({"scene": i + 1, "query": sc.get("query"), "source": "map", "clips": [],
                                      "from": round(starts[i], 2), "to": round(ends[i], 2), "map": histmap.describe(sc["map"], lang)})
                continue
            except Exception as e:  # noqa: BLE001 - карта не получилась: сцена собирается как обычно, по visual
                log.warning("карта сцены %d не получилась: %s", i + 1, e)
                meta.setdefault("warnings", []).append(f"сцена {i + 1}: карта не получилась ({str(e)[:120]}), показана картинка")
        hero_bg = hero_img if i == 0 else None    # размытый главный кадр - фон только первой сцены, иначе он мелькает весь ролик
        if "emphasis" in sc:
            img = None
            ekind = sc.get("vtype") if sc.get("vtype") in ("archive", "photo", "illustration") else "photo"
            if sc.get("query") and ekind in allowed:
                found = pick_image(sc, ekind, state, cfg)
                if found:
                    img, credit = found
                    if credit:
                        credits.append(credit)
            k0, k1 = first_tok[i], (first_tok[i + 1] if i + 1 < len(scenes) else len(tokens))
            kin = statfx.kinetic_events([(max(0.0, times[k] - starts[i]), tokens[k]) for k in range(k0, k1)], need,
                                        sc["emphasis"], accent=statfx.ACCENTS[pal % len(statfx.ACCENTS)])
            try:
                got = [_emph_part(work, n, need, kin, img, pal, hero_bg)]
                source = "emphasis"
                mute.append((starts[i] + 0.05, ends[i] - 0.05))
            except Exception as e:  # noqa: BLE001
                log.warning("крупный текст сцены %d не получился: %s", i + 1, e)
                got, source = [_bg_part(work, n, need, pal, hero_bg)], "card"
            n += len(got)
            items += got
            last_src = source
            report_scenes.append({"scene": i + 1, "query": sc.get("query"), "source": source, "clips": [],
                                  "from": round(starts[i], 2), "to": round(ends[i], 2), "emphasis": sc["emphasis"],
                                  **({"image": img.name} if img is not None else {})})
            continue
        stat = sc.get("stat")
        if stat and cfg.get("graphics") == "off" and (stat.get("full") or stat["kind"] in statfx.FULL_ONLY):
            stat = None                                      # в настройках выключена графика на весь кадр
            meta.setdefault("warnings", []).append(f"сцена {i + 1}: карточка на весь кадр пропущена (графика выключена в настройках)")
        img = None
        if stat and (stat.get("full") or stat["kind"] in statfx.FULL_ONLY):
            picks, source = [], "card"
        else:
            picks, source = [], ""
            vtype = sc.get("vtype", "video")
            order = {"archive": ["archive", "photo", "illustration"], "illustration": ["illustration", "photo"],
                     "photo": ["photo", "video", "illustration"], "video": ["video", "photo"]}[vtype]
            if not sc.get("query") and not sc.get("own") and not sc.get("theme") and hero_img is not None:
                order = []                       # абстрактная мысль без картинки: главный кадр темы
            for kind in [k for k in order if k in allowed]:
                if kind == "video":
                    picks, source = pick_for_scene(sc, need, state, cfg, {})
                    if picks and source == "weak" and opt(cfg, "strict"):
                        picks = []                   # строгий подбор: «похожее» видео не берём
                    if picks:
                        break
                    source = ""
                else:
                    found = pick_image(sc, kind, state, cfg)
                    if found:
                        img, credit = found
                        source = kind
                        if credit:
                            credits.append(credit)
                        break
            if not source and not stat and (sc.get("query") or sc.get("text")):
                picks, img, source = _relaxed_pick(sc, need, state, cfg, allowed, credits)
            source = source or "card"
            if source == "card" and not stat and hero_img is not None and state.get("hero_uses", 0) < 2 and last_src != "hero":
                img, source = hero_img, "hero"   # главный кадр темы - не больше двух раз за ролик и не в соседних сценах
                state["hero_uses"] = state.get("hero_uses", 0) + 1
            elif source == "card" and not stat:
                stat = _auto_icon_card(sc)       # ничего не подошло: крупная иконка по смыслу вместо повтора или случайного кадра
        if picks:
            lens = plan_pieces(need)
            got = compose._fixed_parts(work, picks, lens, False, look, None, "60:white", n0=n)
        elif img is not None:
            try:
                got = [_image_part(work, n, img, need, i, opt(cfg, "img_mode"))]
            except Exception as e:  # noqa: BLE001
                log.warning("картинка %s не оживилась: %s", img.name, e)
                got, source = [_bg_part(work, n, need, pal, hero_bg)], "card"
        elif stat and statfx.is_full(stat):
            try:
                got = [_card_part(work, n, need, pal, stat, lang, hero_bg)]
            except Exception as e:  # noqa: BLE001 - карточка не запеклась: фон + графика поверх всего ролика
                log.warning("карточка сцены %d не запеклась: %s", i + 1, e)
                got = [_bg_part(work, n, need, pal, hero_bg)]
                stat_events += statfx.events(stat, starts[i], ends[i] - 0.05, lang, pal)
        else:
            got = [_bg_part(work, n, need, pal, hero_bg)]
        n += len(got)
        items += got
        if stat and not statfx.is_full(stat):         # плашка с цифрой поверх видео или картинки
            stat_events += statfx.events(stat, starts[i], ends[i] - 0.05, lang, pal)
        last_src = source
        report_scenes.append({"scene": i + 1, "query": sc.get("query"), "source": source, "clips": [p.name for p, _ in picks],
                              "from": round(starts[i], 2), "to": round(ends[i], 2), **({"stat": stat["kind"]} if stat else {}),
                              **({"image": img.name} if img is not None else {}),
                              **({"score": state["scores"][picks[0][0]]} if picks and picks[0][0] in state.get("scores", {}) else {})})
    subs_on = cfg.get("subs", True) and opt(cfg, "sub_style") != "off"
    if subs_on:     # свои субтитры: текст сценария, шрифт как у графики; там, где крупный текст, субтитров нет
        calm = opt(cfg, "sub_style") == "calm"
        grp = sub_groups(tokens, times, speech_len, *((8, 44, False) if calm else (3, 22, True)))
        grp = [g for g in grp if not any(m0 <= (g["a"] + g["b"]) / 2 <= m1 for m0, m1 in mute)]
        for g in grp:                 # субтитр не залезает на крупный текст следующей сцены
            for m0, _ in mute:
                if g["a"] < m0 < g["b"]:
                    g["b"] = m0
        if calm:
            stat_events += statfx.calm_subtitle_events(grp, y=SUB_POS[opt(cfg, "sub_pos")], scale=SUB_SIZES[opt(cfg, "sub_size")])
        else:
            stat_events += statfx.subtitle_events(grp, upper=opt(cfg, "sub_case") == "upper",
                                                  y=SUB_POS[opt(cfg, "sub_pos")], scale=SUB_SIZES[opt(cfg, "sub_size")],
                                                  style=opt(cfg, "sub_style"), accent=cfg.get("sub_accent") or statfx.SUB_ACCENT)
    groups: list = []
    meta["subs"] = bool(subs_on)
    meta["smart_report"] = report_scenes
    if state["report"]:
        meta.setdefault("warnings", []).extend(dict.fromkeys(state["report"]))
    weak = [r["scene"] for r in report_scenes if r["source"] == "weak"]
    cards = [r["scene"] for r in report_scenes if r["source"] == "card" and not r.get("stat")]
    if weak:
        meta.setdefault("warnings", []).append("слабое совпадение кадра со смыслом в сценах " + ", ".join(map(str, weak))
                                               + ": поменяйте visual.query на более простой или добавьте свой клип (own)")
    if cards:
        meta.setdefault("warnings", []).append("в сценах " + ", ".join(map(str, cards))
                                               + " не нашлось подходящей картинки, показан пустой фон: упростите visual.query или сделайте карточку (stat)")
    if stat_events:
        meta["_ass_extra"] = stat_events
    if maps_used:   # CC BY 4.0: автор данных карт - в описании
        meta["description"] = (meta.get("description") or "").rstrip() + "\n\n" + histmap.CREDIT["en" if lang == "en" else "ru"]
    if credits:     # свободные лицензии требуют указать автора
        meta["credits"] = list(dict.fromkeys(credits))
        label = "Images" if lang == "en" else "Изображения"
        meta["description"] = (meta.get("description") or "").rstrip() + f"\n\n{label}: " + "; ".join(meta["credits"])
    meta["look_used"] = {"smart": cfg}
    style = smart_style(opt(cfg, "style"))
    out = compose.assemble(task_dir, items, voice_file, groups, meta, style, look, merged_sfx(None))
    shutil.rmtree(work, ignore_errors=True)
    for junk in ("subs.ass", "speech_all_list.txt", "speech_all.wav", "music_base.wav", "music_loop.wav", "music_list.txt"):
        (task_dir / junk).unlink(missing_ok=True)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg не создал final.mp4")
    if meta.get("watermark"):
        _watermark(out, str(meta["watermark"]))
    return out


def _watermark(video: Path, text: str) -> None:
    """Демо-ролик бесплатного старта: полупрозрачная подпись сверху и снизу (перекодирование видео, звук копируется)."""
    from core import fontpack
    fontpack.ensure()
    font = Path.home() / ".fonts" / "FiraSans-SemiBold.ttf"
    safe = text.replace("\\", "").replace("'", "").replace(":", "\\:").replace("%", "")
    common = f"fontfile={font}:text='{safe}':fontsize=54:fontcolor=white@0.55:borderw=3:bordercolor=black@0.45:x=(w-text_w)/2"
    vf = f"drawtext={common}:y=60,drawtext={common}:y=h-330"
    tmp = video.with_name("wm.mp4")
    res = subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                          "-c:a", "copy", "-movflags", "+faststart", str(tmp)], capture_output=True, text=True)
    if res.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(video)
    else:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("не удалось наложить водяной знак: " + res.stderr[-200:])


# ---------- промт для нейросети ----------

_ICON_HINT = ("money, coins, bank, wallet, piggy-bank, credit-card, hand-coins, currency-dollar, currency-rub, bread, shopping-cart, "
              "tag, house, factory, buildings, car, airplane, ship, globe, flag, map-trifold, sword, shield, crown, gavel, scales, "
              "printer, newspaper, file-text, user, users-three, baby, briefcase, graduation-cap, brain, lightbulb, heart, smiley, "
              "skull, fire, lightning, drop, leaf, tree, clock, calendar, hourglass, trend-up, trend-down, chart-line-up, warning, "
              "lock, key, robot, cpu, device-mobile, desktop, share-network, chat-circle, envelope, thumbs-up, eye, rocket, atom, "
              "dna, flask, pill, virus, trophy, target, puzzle-piece, handshake, question, check-circle, x-circle")

_KINDS_TEXT = """ВИДЫ КАРТОЧЕК (поле stat; full - на весь кадр на цветном фоне):
- "flow" - цепочка причин и следствий со стрелками, шаги появляются по очереди. Лучший способ показать «почему»: {"kind": "flow", "label": "Как рухнула марка", "steps": [{"text": "Война", "icon": "sword"}, {"text": "Огромные долги", "icon": "hand-coins"}, {"text": "Печатный станок", "icon": "printer"}]} (2-4 шага, шаг до 30 знаков).
- "icons" - 1-3 крупные иконки с подписями: герои, предметы, «что на кону»: {"kind": "icons", "label": "Буханка хлеба, ноябрь 1923", "items": [{"icon": "bread", "label": "Цена в марках", "value": "200 млрд"}], "source": "..."}.
- "number" - одна крупная цифра, набегает от нуля (иконка необязательна): {"kind": "number", "full": true, "value": "21%", "label": "Ключевая ставка, 2024", "icon": "bank", "source": "Банк России, 2024"}.
- "compare" - было и стало, процент изменения посчитается сам: {"kind": "compare", "label": "Цена хлеба", "left": {"label": "1918", "value": "0,6 марки"}, "right": {"label": "1923", "value": "200 млрд"}, "source": "..."}.
- "bars" - сравнение 2-5 значений: {"kind": "bars", "full": true, "label": "...", "items": [{"label": "Россия", "value": 4.1, "text": "4,1%"}, {"label": "Еврозона", "value": 0.9, "text": "0,9%"}], "source": "..."}.
- "line" - динамика, 3-14 точек: {"kind": "line", "full": true, "label": "...", "values": [4.9, 8.4, 11.9, 7.4], "labels": ["2020", "2021", "2022", "2023"], "unit": "%", "source": "..."}.
- "list" - 2-4 параллельных пункта с иконками: {"kind": "list", "label": "Что потеряли люди", "items": [{"text": "Сбережения", "icon": "piggy-bank"}, {"text": "Пенсии", "icon": "user"}]}.
- "fact" - крупный короткий вывод с иконкой: {"kind": "fact", "text": "Деньги держатся на доверии", "accent": "доверии", "icon": "handshake"} (2-6 слов).
Подписи у иконок и шагов - до 20 знаков, без повтора фразы диктора; value - короткое число («500+», «200 млрд»), не слова.
Иконки (поле icon) - простое английское слово; надёжно работают: """ + _ICON_HINT + """."""


def graphics_level(cfg: dict | None) -> str:
    lvl = (cfg or {}).get("graphics", DEFAULT_CFG["graphics"])
    return lvl if lvl in GRAPHICS else DEFAULT_CFG["graphics"]


VTYPE_TEXT = {
    "archive": "- \"archive\" - настоящие исторические фото и документы: люди, места, события (Wikimedia Commons). Пиши в запросе "
               "имя собственное с большой буквы и год: «Venezuela hyperinflation 2018», «Weimar 1923 banknotes», «Richard Dawkins». "
               "Картинка берётся, только если эти слова есть в её описании. Не уверен, что такой снимок существует, - сделай карточку.\n",
    "illustration": "- \"illustration\" - рисованная иллюстрация (Pixabay) для понятий, эмоций и метафор: «money printing», "
                    "«brain idea», «falling chart».\n",
    "photo": "- \"photo\" - стоковое фото (Pixabay): конкретный предмет или сцена: «bread loaf», «empty supermarket shelves», "
             "«oil pump».\n",
    "video": "- \"video\" - живое стоковое видео: действие, движение. Без стран и городов, абстракций и редких сюжетов - "
             "их на стоках нет.\n",
}


_EXAMPLE = {
    "ru": """ПЛОХО (так нельзя): «Инфляция - это рост цен. В Германии была сильная инфляция. Деньги обесценились. Подпишись.» - нет интриги, фразы не связаны, финал ни на что не отвечает.
ПЛОХО: список фактов по теме («В Риме были инсулы. Красс скупал дома. Был пожар.») - зритель не понимает, зачем смотреть дальше.
ПЛОХО (звучит как нейросеть): «А причина довольно простая. То есть не старые машины, а гордость Британии. Острый угол собирает напряжение. Круглому краю собрать его негде. Так что да, это мелочь. Но мелочь ли?» - шаблонные связки, зеркальные афоризмы, финал-лозунг.

ХОРОШО (уровень, к которому стремимся; тему и факты бери свои):
question: «Почему в самолёте все окна круглые?»
[hook, emphasis: «квадратное»] Ты когда-нибудь видел в самолёте квадратное окно?
[context] В поезде или в автобусе - сколько угодно, а в самолёте ни одного.
[reveal] Всё началось с двух британских «Комет», которые в пятьдесят четвёртом развалились прямо в воздухе.
[escalate] Это были новейшие лайнеры, и сначала все искали бомбу или ошибку пилотов.
[reason, emphasis: «угол»] Виноватым оказался обычный угол окна.
[main] На каждом взлёте корпус чуть раздувается, и вся нагрузка собирается в этом углу. Там и пошла трещина.
[other] С тех пор у самолётов скруглено всё: окна, двери, даже грузовые люки.
[flip, emphasis: «Комет»] В следующий раз, когда будешь смотреть в иллюминатор, вспомни про те две «Кометы».
Почему хорошо: одна интрига с первой фразы; рассказ, как другу, полными предложениями; конкретные детали (год, название самолёта, что именно треснуло); фразы разной длины; связки только там, где они нужны; финал - живая деталь, а не афоризм.""",
    "en": """BAD: «Inflation means rising prices. Germany had high inflation. Money lost value. Subscribe.» - no hook, no links, no payoff.
BAD (sounds like AI): «And the reason is simple. Not old planes - Britain's pride. A sharp corner gathers stress. A round edge has nowhere to gather it. So yes, it's a detail. But is it?» - template connectors, mirrored one-liners, slogan ending.

GOOD (the level we want; use your own topic and facts):
question: «Why are all plane windows round?»
[hook, emphasis: «square»] Have you ever seen a square window on a plane?
[context] Trains and buses have them everywhere, but planes never do.
[reveal] It goes back to two British Comets that broke apart in mid-air in 1954.
[escalate] They were brand-new jets, and at first everyone blamed a bomb or the pilots.
[reason, emphasis: «corner»] The real culprit turned out to be the corner of a window.
[main] Every takeoff stretches the body a little, and all that stress piles up right in the corner. That's where the crack started.
[other] Since then engineers round off everything: windows, doors, even cargo hatches.
[flip, emphasis: «Comets»] Next time you look out of a plane window, think of those two Comets.
Why it works: one hook from the first line; told like to a friend, in full sentences; concrete details (year, plane name, what cracked); varied sentence length; connectors only where needed; the ending is a vivid detail, not a slogan.""",
}

_BANNED = {
    "ru": "«давайте разберёмся», «в этом видео», «как известно», «в современном мире», «не секрет, что», «это не просто X, это Y», "
          "«не X, а Y» как приём, «и причина (довольно) проста(я)», «вот в чём дело», «но есть нюанс», «а теперь самое интересное», "
          "«секрет в том», «спойлер», «и это ещё не всё», «так что да», «не факт», «и вот тут», «по сути», «буквально» как усилитель, "
          "«играет важную роль», «является», «осуществляет», «в рамках», «итак», «таким образом», «удивительно, но», «представьте себе», "
          "«ключевой», «уникальный», «невероятный», «настоящий» как усилитель, «подписывайся», «ставь лайк»",
    "en": "«let's dive in», «in this video», «in today's world», «it's no secret», «it's not just X, it's Y», «not X, but Y» as a device, "
          "«and the reason is simple», «here's the thing», «here's why», «but here's the catch», «plot twist», «let that sink in», "
          "«so yes», «spoiler», «plays a crucial role», «game-changer», «delve», «unlock», «journey», «imagine this», «incredible», "
          "«ultimately», «in conclusion», «subscribe»",
}

_FRAME_TEXT = """СЮЖЕТ: ОДНА ИНТРИГА И ЖИВОЙ РАССКАЗ (главное правило).
Ролик держится на одной интриге из первой фразы: вопрос, неожиданный факт или маленькая сцена, после которой хочется узнать продолжение. Дальше - рассказ, который к ней ведёт, а не обзор темы и не список фактов.
Поля для JSON:
- "question": интрига ролика одной фразой, с конкретикой (имя, место, предмет). Придумай 5 вариантов и возьми тот, мимо которого нельзя пролистать.
- "answer": к чему приходит ролик - мысль, которую зритель унесёт, одной фразой.
- "punchline": необязательно - одна фраза, которую хочется пересказать другу; своими словами, без зеркальных пар «раньше X - теперь Y».
- "flip": как заканчивается ролик - живая деталь, вывод или возврат к началу; не лозунг и не риторический вопрос.

ТАКТЫ (поле block у сцены) - это порядок мыслей, а не обязательные фразы: hook (интрига) -> context (что все думают или почему странно) -> reveal (главный факт) -> escalate (подробности, ставки) -> reason и main (объяснение простыми словами, на примере) -> other (другая сторона или как сейчас) -> flip (финал). Такты можно объединять и пропускать, если так звучит естественнее; блок punch - только если есть сильная фраза.

ГОЛОС - КАК ЖИВОЙ ЧЕЛОВЕК (это проверяют в первую очередь):
- Пиши так, как рассказывают другу вслух или в сторис, а не как пишут статью: простые слова, разговорный порядок слов, можно «я», «мы», «ты».
- Каждое предложение полное и грамматически правильное - без обрывков вроде «Фото, подпись сверху, шутка».
- Конкретика вместо общих слов: не «технологии всё изменили», а «в Vine на ролик было шесть секунд».
- Фразы разной длины: короткая, потом подлиннее. Риторических вопросов - не больше одного, кроме хука.
- Связки («а», «но», «поэтому», «и тогда») - только там, где без них непонятно; не в каждой фразе и не одна и та же подряд.
- Юмор - из ситуации и деталей, а не из каламбуров. Без пафоса и восклицаний.
- Без афоризмов и зеркальных пар («Старым мемам нужны были глаза. Новым - память»), без триад «быстро, просто, удобно», без шаблонов из списка «Запрещено».
- Проверка вслух: если так не сказал бы живой человек в разговоре - перепиши.

КРУПНЫЙ ТЕКСТ (поле "emphasis" у сцены): в двух-трёх сценах (обычно хук, главная причина и финал) слова диктора появляются крупно по одному в центре кадра, вместо субтитров. В emphasis - 1-2 ключевых слова из этой же фразы, они выделятся цветом: "emphasis": ["квадратное"]. В других сценах emphasis не ставь.

ГЛАВНЫЙ КАДР (поле "hero"): одна картинка, которая лучше всего показывает тему ролика (место, предмет, человек): "hero": {"type": "archive", "query": "Ostia Antica insula", "alt": ["Roman insula ruins"]}. Это запасной кадр - всё равно дай КАЖДОЙ сцене свой visual (или stat, или map), иначе одна и та же картинка будет мелькать весь ролик."""

def _graphics_block(cfg: dict | None) -> str:
    lvl = graphics_level(cfg)
    lo, hi, _ = GRAPHICS[lvl]
    if lvl == "off":
        share = ("Карточки на весь кадр НЕ используй: весь ролик - точные картинки и крупный текст. К сцене с реальным фактом можно "
                 "добавить небольшую плашку с цифрой поверх картинки (stat kind number, bars или line, без full).")
    elif lvl == "min":
        share = (f"Карточки (stat) - только для цифр и сравнений, {CARDS_MIN_MAX - 1}-{CARDS_MIN_MAX} на ролик: number, compare, bars, line; "
                 "list, flow или icons - не больше одной. Всё остальное - точные картинки (visual) и крупный текст (emphasis).")
    else:
        share = (f"Примерно {lo}-{hi}% сцен - нарисованные карточки (stat), остальные - картинка (visual). Не больше двух карточек "
                 "одного вида на ролик и не больше двух карточек подряд: чередуй их с фото, иллюстрациями, архивом и видео.")
    return ("ВИЗУАЛ. Сцена - либо карточка (поле stat), либо картинка (поле visual), либо крупный текст (emphasis). " + share + "\n"
            "ТОЧНОСТЬ ВАЖНЕЕ КРАСОТЫ: картинка показывает ровно то, о чём фраза - тот самый город, человека, предмет, событие. Когда говорят "
            "«Samsung», на экране Samsung, а не «какой-то телефон». Для абстрактной мысли дай простой снимаемый кадр, который её показывает "
            "(человек листает ленту, руки пересчитывают деньги, пустой офис ночью) - или карточку stat. Похожая, но чужая картинка "
            "(другая страна, эпоха, предмет) хуже, чем никакой. Не повторяй один и тот же запрос в разных сценах.\n"
            "Картинки (visual.type):\n" + "".join(VTYPE_TEXT[k] for k in VTYPES if opt(cfg, f"src_{k}")) +
            "visual.query - 2-5 слов ПО-АНГЛИЙСКИ, существительное первым; visual.alt - 2 запроса попроще и пошире.\n"
            "Про реальное событие, человека, место - archive; про предмет - photo; про действие - video; про цифру - number, compare, bars, line.\n\n"
            + ("" if lvl == "off" else _KINDS_TEXT + "\n")
            + "Правила карточек: карточка иллюстрирует фразу, а не повторяет её - субтитры уже показывают слова диктора. Цифра на карточке = "
            "цифра в этой же сцене. Цифры только реальные, с источником и годом в source; не уверен - не ставь цифру.")


_MAPS_TEXT = """КАРТЫ (поле "map" у сцены вместо visual): бот рисует историческую карту с границами государств на нужный год (от 3400 до н. э. до сегодня), подсвечивает государства и прямо в кадре меняет границы от года к году.
Ставь карту, когда во фразе важно «где»: территория, границы, завоевания, колонии, распад империи, торговый путь, переселение. 1-3 карты на ролик, не больше двух сцен-карт подряд. В остальных сценах карту не ставь.
"map": {"years": [1850, 1890, 1910], "title": "Индокитай", "highlight": [{"at": "Бангкок", "label": "Сиам"}, {"at": "Ханой", "label": {"1850": "Вьетнам", "1890": "Французский Индокитай"}}], "pins": ["Бангкок"], "arrows": [{"from": "Париж", "to": "Ханой"}], "cues": ["девяностому"]}
- years: 1-4 года по возрастанию, до нашей эры - отрицательные (-44). Один год - "year": 117. Карта сегодняшнего мира - без year и years.
- highlight (до 4): "at" - город или страна, которые В ТОТ ГОД были внутри нужного государства, современное название по-русски («Бангкок», «Стамбул», «Рим», «Таиланд»); бот сам находит, кому принадлежала эта точка в каждый год. "label" - короткое историческое название по-русски (до 24 знаков); меняется по годам - словарь {год: название}. "whole": true - вся империя вместе с колониями, иначе - только та часть, где точка.
- pins - до 4 городов-меток; arrows - до 2 стрелок «откуда - куда» (поход, путь, переселение).
- cues - если лет несколько: по одному слову из текста этой сцены на каждую смену года, на этом слове карта переключается.
- Год на карте = год в тексте сцены (словами). Не уверен в истории границ - не ставь карту, сделай archive или карточку."""


def _maps_block(cfg: dict | None) -> str:
    return ("\n" + _MAPS_TEXT + "\n") if opt(cfg, "maps") != "off" and histmap.available() else ""


def make_prompt(topic: str, lang: str, ctx: dict, cfg: dict | None = None) -> str:
    lang_name = story.LANG_NAMES.get(lang, "русский")
    themes = ", ".join(ctx.get("themes") or []) or "нет"
    music = ", ".join(ctx.get("music") or []) or "нет"
    channels = ", ".join(ctx.get("channels") or [])
    key = "en" if lang == "en" else "ru"
    ln = LENGTHS[opt(cfg, "length")]
    return f"""Ты - автор-сценарист сильного канала коротких видео (YouTube Shorts). Твои ролики досматривают, потому что в первую секунду звучит вопрос, на который хочется узнать ответ, каждая фраза приближает к нему, а финал отвечает неожиданно. Напиши сценарий на тему ниже. Верни ТОЛЬКО JSON, без пояснений и без markdown.

ТЕМА И ПОЖЕЛАНИЯ: {topic or '(придумай сам)'}
Язык озвучки: {lang_name}. Озвучка {ln[2]} секунд: {ln[0]}-{ln[1]} слов, {ln[3]}-{ln[4]} сцен. Это спокойный темп (около 2,4 слова в секунду): больше слов - диктор начнёт спешить.
Подача: {TONES[opt(cfg, "tone")]}.
Если в пожеланиях названа тема (например, «сравнение римской и современной недвижимости») - ролик ровно про неё, вопрос строится из неё, без ухода в соседние истории.
ДЕРЖИСЬ ТЕМЫ АВТОРА: вопрос-хук - это вход В ТЕМУ, а не замена темы. Нельзя подменять угол соседним (тема «история съёмок фильма» - рассказываешь историю съёмок, а не «почему снимали здесь, а не там»; тема «как работает X» - объясняешь X). Если указаны главная мысль и обязательные факты - ролик ведёт к этой мысли и упоминает все эти факты; форма рассказа - как указано. Самопроверка: прочитай тему автора и свой вопрос - если зритель, кликнувший на тему, получит другой ролик, перепиши.

РАБОТА ПО ШАГАМ (делай про себя, в ответ - только JSON):
1. Исследование: 6-8 КОНКРЕТНЫХ фактов по теме (имена, даты, места, числа, предметы). Только то, в чём уверен.
2. Вопрос, ответ, панчлайн, финал (поля question, answer, punchline, flip) - см. ниже.
3. Текст по тактам (поле script), потом нарезка на сцены (тексты сцен подряд = script слово в слово), потом визуал.
4. Самопроверка по 10-балльной шкале: интрига цепляет? звучит как живой человек, а не как текст нейросети (нет шаблонов из списка «Запрещено», нет зеркальных афоризмов и обрывков)? каждая фраза вытекает из предыдущей? есть конкретика? финал отвечает на интригу? темп спокойный? Всё ниже 9 - перепиши.

{_FRAME_TEXT}

ТЕКСТ:
- Конкретика в каждой сцене: имя, число, место, предмет или действие, которое можно увидеть. Показывай, а не называй.
- Через зрителя, когда уместно: «ты платишь», «представь, ты снимаешь квартиру».
- Фразы 4-14 слов; чередуй короткие и средние. Глаголы вместо существительных.
- Вслух не больше трёх чисел на ролик, каждое переведено в понятную картинку.
- Пиши для уха: числа словами так, как их читают; без %, $, скобок, кавычек-ёлочек, слэшей и эмодзи в тексте - это только в графике.
- Запрещено (звучит как нейросеть): {_BANNED[key]}.
- Деньги и финансы: без гарантий дохода и советов купить или продать актив; в description добавь «Не является инвестиционной рекомендацией».

{_EXAMPLE[key]}

{_graphics_block(cfg)}
{_maps_block(cfg)}
ФОРМАТ ОТВЕТА:
{{
  "title": "Название до 70 знаков: тот же вопрос или парадокс",
  "description": "1-2 предложения для YouTube",
  "tags": ["#тег1", "#тег2"],
  "channel": "одно из: {channels}",
  "music": {{"mood": "одно из: {music} или off", "volume": 10}},
  "question": "Интрига ролика одной фразой",
  "answer": "К чему приходит ролик, одной фразой",
  "punchline": "Необязательно: фраза, которую хочется пересказать",
  "flip": "Как заканчивается ролик",
  "hero": {{"type": "archive", "query": "главный кадр темы по-английски", "alt": ["попроще"]}},
  "script": "Весь текст озвучки целиком",
  "scenes": [
    {{"block": "hook", "text": "Вопрос?", "emphasis": ["ключевое"], "visual": {{"type": "archive", "query": "Ostia Antica insula", "alt": ["Roman insula ruins"]}}}},
    {{"block": "context", "text": "Фраза…", "visual": {{"type": "photo", "query": "penthouse view city", "alt": ["skyscraper apartment"]}}}},
    {{"block": "reveal", "text": "Фраза…", "stat": {{"kind": "compare", "label": "...", "left": {{"label": "...", "value": "..."}}, "right": {{"label": "...", "value": "..."}}, "source": "..."}}}},
    {{"block": "reason", "text": "Фраза…", "emphasis": ["ключевое"], "visual": {{"type": "photo", "query": "...", "alt": ["..."]}}}},
    {{"block": "flip", "text": "Финальная фраза…", "emphasis": ["ключевое"], "visual": {{"type": "photo", "query": "...", "alt": ["..."]}}, "theme": "необязательно, одна из: {themes}"}}
  ]
}}
"""


def make_humanize_prompt(data: dict, lang: str) -> str:
    """Промт-редактор живой речи: переписать только тексты сцен, чтобы они звучали как человек, а не как нейросеть."""
    import json
    body = json.dumps(data, ensure_ascii=False, indent=1)
    key = "en" if lang == "en" else "ru"
    lang_name = story.LANG_NAMES.get(lang, "русский")
    return f"""Ты редактор живой устной речи для коротких видео (Shorts, Reels). Ниже сценарий в JSON. Текст сейчас звучит как написанный нейросетью. Перепиши ТОЛЬКО тексты сцен (поле text у каждой сцены) и поле script (= тексты сцен подряд, слово в слово), чтобы ролик звучал так, будто его рассказывает живой человек другу. Верни ТОЛЬКО исправленный JSON целиком, без пояснений.

Что сохранить без изменений: тему, все факты, имена, даты и числа (ничего не выдумывай и не добавляй); количество и порядок сцен; поля visual, stat, map, hero, block, title, tags, music. Если в сцене есть emphasis - эти слова должны остаться в её тексте (или замени emphasis на 1-2 слова из нового текста). Длина каждой сцены - примерно как была (плюс-минус 3 слова), язык - {lang_name}.

Как переписать:
- Разговорно и просто: как говорят вслух, а не как пишут; можно «я», «мы», «ты»; бытовые слова вместо книжных.
- Каждое предложение полное и грамматически правильное. Обрывки вроде «Фото, подпись сверху, шутка» - переделай в нормальную фразу.
- Убери зеркальные пары-афоризмы («раньше X - теперь Y», «старым нужны глаза, новым - память»), конструкции «не X, а Y», триады, риторические вопросы подряд, пафос.
- Убери шаблонные связки; оставь связку только там, где без неё непонятна логика. Не начинай две фразы подряд одинаково.
- Фразы разной длины: короткая - подлиннее - короткая.
- Финал - живая деталь или вывод своими словами, а не лозунг и не вопрос в пустоту.
- Запрещено: {_BANNED[key]}.
Проверка: прочитай каждую сцену вслух. Если живой человек так бы не сказал - перепиши ещё раз.

СЦЕНАРИЙ:
{body}
"""


def make_edit_prompt(data: dict, lang: str, cfg: dict | None = None) -> str:
    """Промт для доработки готового сценария: нейросеть-редактор проверяет его по рубрике и переписывает слабые места."""
    import json
    body = json.dumps(data, ensure_ascii=False, indent=1)
    lang_name = story.LANG_NAMES.get(lang, "русский")
    key = "en" if lang == "en" else "ru"
    ln = LENGTHS[opt(cfg, "length")]
    return f"""Ты главный редактор сильного канала коротких видео. Ниже сценарий в JSON. Сделай его заметно сильнее и верни ТОЛЬКО исправленный JSON в том же формате, без пояснений.

Сначала про себя оцени по 10-балльной шкале и найди слабые места:
1. Интрига: ролик держится на ОДНОЙ интриге из первой фразы (вопрос, неожиданный факт, маленькая сцена) с конкретикой, до 14 слов? Весь ролик к ней ведёт, а не перечисляет факты по теме? Если нет - придумай 5 вариантов и возьми лучший.
2. Живой голос: звучит как человек рассказывает другу, а не как текст нейросети? Убери шаблонные связки, зеркальные пары-афоризмы («раньше X - теперь Y»), конструкции «не X, а Y», риторические вопросы подряд, финал-лозунг. Каждое предложение полное и грамматически правильное.
3. Связность: каждая фраза вытекает из предыдущей по смыслу; связки - только там, где без них непонятно. Фразы, которые можно выкинуть, - выкинь.
4. Темп: {ln[0]}-{ln[1]} слов на {ln[2]} секунд, не больше - иначе диктор спешит.
5. Конкретика и язык ({lang_name}): в каждой сцене то, что можно увидеть; разговорно; числа словами; без %, $, скобок; без штампов: {_BANNED[key]}.
6. Крупный текст: emphasis (1-2 ключевых слова из фразы) только в сценах hook, reason и flip. Есть поле hero - главный кадр темы.
7. Визуал: картинка показывает ровно то, о чём фраза; где важно «где» (границы, завоевания, колонии) - сцена с картой (поле map, его формат не меняй); для абстрактной мысли - простой снимаемый кадр по смыслу (не повторяй запросы между сценами); карточки только для цифр и сравнений, без повтора фразы; цифры только реальные, с источником.
Перепиши всё, что ниже 9 баллов. Факты не выдумывай: если не уверен - убери. script = тексты сцен подряд. Тему ролика не меняй.

СЦЕНАРИЙ:
{body}
"""
