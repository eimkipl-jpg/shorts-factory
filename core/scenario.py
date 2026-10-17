"""Сценарий в формате JSON: проверка, превращение в рецепт, сводка и промт для нейросети.

Идея: сценарий пишет нейросеть (отдельно от бота), вы присылаете JSON боту, бот проверяет его, показывает сводку
и после подтверждения собирает ролик: каждая сцена = фраза (озвучка) + свой клип, длина сцены = длина озвучки.

Схема (все поля кроме title и scenes необязательны):
{
  "title": "Заголовок для YouTube",  "description": "Описание", "tags": ["#космос", "#факты"],
  "channel": "channel1",  "theme": "космос",                 # тема клипов по умолчанию
  "voice": {"name": "ru-RU-DmitryNeural", "rate": 0},         # или просто строка с именем голоса
  "music": {"mood": "calm", "volume": 12},                    # или "off" / "auto" / "calm"
  "subtitles": true,  "original_sound": false,
  "style": {"montage": "dynamic", "layout": "blur", "speed": "1", "fx": ["grain"], "sub": "white", "subpos": "bottom", "logo": "tr"},
  "sfx": {"cuts": true, "start": false, "tag": null, "volume": 60},   # или true/false
  "hook": "Крупный текст в начале" | "title" | "random" | ["вариант 1", "вариант 2"],   # список или "random": каждый раз случайный
  "cta": "Подпишись!" | "random" | ["вариант 1", "вариант 2"],
  "caption": "title" | "Постоянная подпись на всё видео",
  "progress": "line" | "capsule" | "neon" | {"design": "neon", "pos": "bottom", "color": "auto", "end": true},   # полоска прогресса
  "scenes": [ {"text": "Фраза для озвучки", "theme": "космос", "sfx": "whoosh"}, ... ]
}
Сцена: text (озвучка), theme (тема клипов или список тем ["a","b"]: клип берётся из любой), own + start (свой ролик из _own вместо клипа темы),
seconds (только для сцены без text: её точная длина; сцена с озвучкой длится ровно по фразе), pause (необязательная пауза после фразы, 0-3 с), sfx (тег звука на начале сцены или false).
"""
import difflib
import json
import re
from pathlib import Path

from common import config, library
from common.looks import CROP_BARS, FX, LAYOUTS, LOGO_POS, POS, SPEEDS

MAX_SCENES = 20
MAX_TEXT = 400
MAX_SECONDS = 180
CHARS_PER_SECOND = 14.0  # грубая оценка скорости речи для сводки
RATE_RANGE = (-50, 100)
VOICE_RE = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,4}-\w+$")
from core import montage as _montage  # noqa: E402

MONTAGES = ("none", "random", *_montage.BUILTIN_ORDER)
SUBS = ("classic", "white", "neon", "box")


# ---------- контекст (что есть в библиотеке) ----------

def build_ctx(channel: str | None = None) -> dict:
    """Реальное содержимое библиотеки: нейросети нужно знать, какие темы и теги существуют."""
    from core import mix
    library.scan()
    themes = mix.list_themes()
    music = [t for t, _ in library.tag_counts("music") if t != "без-темы"]
    sfx = [t for t, _ in library.tag_counts("sfx") if t != "без-темы"]
    own = sorted(f.name for f in mix.own_clips())
    titles = library.own_titles()
    tags = {Path(a["path"]).name: a["tags"] for a in library.search("own", limit=100000)}
    durs = {Path(a["path"]).name: a["duration"] for a in library.search("own", limit=100000)}
    return {
        "themes": themes, "music": music, "sfx": sfx, "own": own, "own_titles": titles, "own_tags": tags, "own_durations": durs,
        "voices": list(config.VOICES), "channels": list(config.CHANNELS),
        "channel": channel or config.CHANNELS[0],
    }


# ---------- разбор ----------

def extract_json(text: str) -> dict:
    """Достаёт JSON из сообщения: допускает ```-ограждения и лишний текст вокруг."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("не нашёл JSON (нужен объект в фигурных скобках)")
    try:
        data = json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON не читается: {e.msg} (строка {e.lineno}, позиция {e.colno})") from None
    if not isinstance(data, dict):
        raise ValueError("JSON должен быть объектом, а не списком")
    return data


def _close(value: str, options: list[str]) -> str | None:
    """Точное совпадение без учёта регистра или близкое (опечатка)."""
    low = {o.lower(): o for o in options}
    if value.lower() in low:
        return low[value.lower()]
    m = difflib.get_close_matches(value.lower(), list(low), n=1, cutoff=0.75)
    return low[m[0]] if m else None


def _find_own(value: str, ctx: dict) -> str | None:
    """Имя файла из _own по имени (с опечаткой) или по названию ролика (целиком или по части)."""
    hit = _close(value, ctx["own"]) if value else None
    if hit:
        return hit
    titles = ctx.get("own_titles") or {}
    low = value.lower().strip()
    if not low:
        return None
    exact = [n for n, t in titles.items() if t.lower() == low]
    if len(exact) == 1:
        return exact[0]
    part = [n for n, t in titles.items() if low in t.lower()]
    if len(part) == 1:
        return part[0]
    by_title = difflib.get_close_matches(low, [t.lower() for t in titles.values()], n=1, cutoff=0.8)
    if by_title:
        names = [n for n, t in titles.items() if t.lower() == by_title[0]]
        return names[0] if len(names) == 1 else None
    return None


def _num(v, name: str, lo: float, hi: float, errors: list) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        errors.append(f"{name}: нужно число, а пришло «{v}»")
        return None
    if not lo <= x <= hi:
        errors.append(f"{name}: {x:g} вне допустимого диапазона {lo:g}..{hi:g}")
        return None
    return x


def _tags(v) -> list[str]:
    items = re.findall(r"[\w-]+", v) if isinstance(v, str) else [str(x) for x in (v or [])]
    out = []
    for t in items:
        t = "#" + t.strip().lstrip("#").replace(" ", "_")
        if len(t) > 1 and t not in out:
            out.append(t)
    return out[:15]


def parse(data: dict, ctx: dict) -> dict:
    """{'errors': [...], 'warnings': [...], 'recipe': {...}, 'meta': {...}, 'channel': str, 'scenes': n, 'seconds': float}.
    При errors ролик собирать нельзя; warnings - то, что исправлено автоматически."""
    from core import mix
    errors: list[str] = []
    warn: list[str] = []

    title = str(data.get("title") or "").strip()
    if not title:
        errors.append("title: нужен заголовок")
    scenes_in = data.get("scenes")
    if not isinstance(scenes_in, list) or not scenes_in:
        errors.append("scenes: нужен непустой список сцен")
        scenes_in = []
    if len(scenes_in) > MAX_SCENES:
        errors.append(f"scenes: {len(scenes_in)} сцен, максимум {MAX_SCENES}")
        scenes_in = scenes_in[:MAX_SCENES]

    # канал
    channel = str(data.get("channel") or ctx["channel"])
    if channel not in ctx["channels"]:
        warn.append(f"channel: «{channel}» нет в списке каналов ({', '.join(ctx['channels'])}); взят {ctx['channel']}")
        channel = ctx["channel"]

    # тема по умолчанию
    def theme_of(value, where: str):
        """Одна тема, список или строка «a, b»: сцена возьмёт клип из любой из них."""
        names = mix.norm_themes(value)
        if not names:
            return None
        got = []
        for name in names:
            found = _close(name, ctx["themes"])
            if found is None:
                warn.append(f"{where}: темы «{name}» нет (есть: {', '.join(ctx['themes']) or 'пусто'}); пропущена")
            else:
                if found.lower() != name.lower():
                    warn.append(f"{where}: тема «{name}» исправлена на «{found}»")
                got.append(found)
        return mix.pack_themes(got)

    default_theme = theme_of(data.get("theme"), "theme")

    # голос
    voice_in = data.get("voice")
    rate = 0
    voice = None
    if isinstance(voice_in, dict):
        voice, rate_in = voice_in.get("name") or voice_in.get("voice"), voice_in.get("rate", 0)
    else:
        voice, rate_in = voice_in, 0
    if voice:
        voice = str(voice)
        if not VOICE_RE.match(voice):
            errors.append(f"voice: «{voice}» не похоже на имя голоса (пример: ru-RU-DmitryNeural)")
        elif voice not in ctx["voices"]:
            warn.append(f"voice: «{voice}» нет в списке голосов меню; попробуем его как есть")
    if rate_in not in (0, None, ""):
        r = _num(rate_in, "voice.rate", *RATE_RANGE, errors)
        rate = int(r) if r is not None else 0

    # музыка
    music_in = data.get("music", "off")
    vol = None
    if isinstance(music_in, dict):
        vol_in = music_in.get("volume")
        music_in = music_in.get("mood") or "off"
        if vol_in is not None:
            v_ = _num(vol_in, "music.volume", 0, 100, errors)
            vol = int(v_) if v_ is not None else None
    music = str(music_in or "off")
    if music not in ("off", "auto"):
        found = _close(music, ctx["music"])
        if found:
            if found != music:
                warn.append(f"music: «{music}» исправлено на «{found}»")
            music = found
        else:
            fallback = "auto" if ctx["music"] else "off"
            warn.append(f"music: тега «{music}» нет (есть: {', '.join(ctx['music']) or 'музыки нет'}); будет «{fallback}»")
            music = fallback
    if music == "auto" and not ctx["music"]:
        warn.append("music: музыки в библиотеке нет, ролик будет без музыки")
        music = "off"

    # стиль
    st_in = data.get("style") if isinstance(data.get("style"), dict) else {}
    montage = str(st_in.get("montage", "none"))
    if montage not in MONTAGES and montage not in _montage.user_styles():
        warn.append(f"style.montage: «{montage}» неизвестно ({', '.join(MONTAGES)}); взято «none»")
        montage = "none"
    look: dict = {}

    def pick(key: str, allowed, default, label: str):
        val = str(st_in.get(key, default))
        if val not in allowed:
            warn.append(f"style.{key}: «{val}» неизвестно ({', '.join(allowed)}); взято «{default}»")
            val = default
        look[label] = val
        return val

    pick("layout", list(LAYOUTS), "fill", "layout")
    pick("speed", list(SPEEDS), "1", "speed")
    pick("sub", list(SUBS), "classic", "sub")
    pick("subpos", list(POS), "center", "subpos")
    pick("logo", list(LOGO_POS), "off", "logo")
    cb = st_in.get("crop_bars", "off")
    cb = {True: "auto", False: "off", None: "off"}.get(cb, cb) if isinstance(cb, bool) or cb is None else str(cb)
    if cb not in CROP_BARS:
        warn.append(f"style.crop_bars: «{cb}» неизвестно ({', '.join(CROP_BARS)}); взято «off»")
        cb = "off"
    look["crop_bars"] = cb
    fx_in = st_in.get("fx") or []
    fx = []
    for f in (fx_in if isinstance(fx_in, list) else [fx_in]):
        if f in FX:
            fx.append(f)
        else:
            warn.append(f"style.fx: эффекта «{f}» нет ({', '.join(FX)}); пропущен")
    look["fx"] = fx

    look["hook"] = _text_block(data.get("hook"), "hook", warn)
    look["cta"] = _text_block(data.get("cta"), "cta", warn)
    look["caption"] = _caption_block(data.get("caption"))
    look["progress"] = _progress_block(data.get("progress"), warn)

    # звуковые эффекты
    sfx_in = data.get("sfx", False)
    sfx = {"cuts": False, "start": False, "tag": None, "vol": 60}
    if isinstance(sfx_in, dict):
        sfx["cuts"], sfx["start"] = bool(sfx_in.get("cuts")), bool(sfx_in.get("start"))
        tag = sfx_in.get("tag")
        if tag:
            found = _close(str(tag), ctx["sfx"])
            if found:
                sfx["tag"] = found
            else:
                warn.append(f"sfx.tag: звука «{tag}» нет (есть: {', '.join(ctx['sfx']) or 'нет'}); будут любые")
        if sfx_in.get("volume") is not None:
            v_ = _num(sfx_in["volume"], "sfx.volume", 0, 100, errors)
            sfx["vol"] = int(v_) if v_ is not None else 60
    elif sfx_in:
        sfx["cuts"] = True

    # сцены
    scenes, total = [], 0.0
    for i, sc in enumerate(scenes_in, 1):
        where = f"сцена {i}"
        if not isinstance(sc, dict):
            errors.append(f"{where}: нужен объект с полем text")
            continue
        text = str(sc.get("text") or "").strip()
        secs = sc.get("seconds")
        if not text and secs is None:
            errors.append(f"{where}: нужен text (озвучка) или seconds (длина без озвучки)")
            continue
        if len(text) > MAX_TEXT:
            errors.append(f"{where}: текст {len(text)} символов, максимум {MAX_TEXT} (разбейте на сцены)")
        item: dict = {"text": text}
        if secs is not None:
            s_ = _num(secs, f"{where}: seconds", 1, 60, errors)
            if s_ is not None:
                item["seconds"] = s_
        if sc.get("pause") is not None:
            p_ = _num(sc["pause"], f"{where}: pause", 0, 3, errors)
            if p_:
                item["pause"] = p_
        if sc.get("own"):
            name = _find_own(str(sc["own"]), ctx)
            if name is None:
                errors.append(f"{where}: своего видео «{sc['own']}» нет в _own или название неоднозначно "
                              f"(укажите имя файла как в списке промта)")
            else:
                if name != str(sc["own"]):
                    warn.append(f"{where}: «{sc['own']}» → файл {name}")
                item["own"] = name
                if sc.get("start") is not None:
                    st_ = _num(sc["start"], f"{where}: start", 0, 36000, errors)
                    if st_ is not None:
                        item["start"] = st_
        else:
            t = theme_of(sc.get("theme"), where)
            item["theme"] = t or default_theme
        per = sc.get("sfx")
        if per is False:
            item["sfx"] = False
        elif per:
            found = _close(str(per), ctx["sfx"])
            if found:
                item["sfx"] = found
            else:
                warn.append(f"{where}: звука «{per}» нет (есть: {', '.join(ctx['sfx']) or 'нет'}); пропущен")
        est = (len(text) / CHARS_PER_SECOND + 0.3 + float(item.get("pause") or 0)) if text else float(item.get("seconds") or 3)
        total += est
        scenes.append(item)
    if total > MAX_SECONDS:
        errors.append(f"общая длина ≈ {total:.0f} с, максимум {MAX_SECONDS} с: сократите текст или число сцен")
    elif total > 60:
        warn.append(f"длина ≈ {total:.0f} с: дольше 60 секунд")

    subs = data.get("subtitles")
    subs_lang = str(data.get("subtitles_lang") or "").strip().lower()[:2] or None
    if subs_lang and subs_lang not in ("en", "ru", "de", "es", "fr"):
        warn.append(f"subtitles_lang: «{data.get('subtitles_lang')}» не поддерживается (en, ru, de, es, fr); субтитры как в речи")
        subs_lang = None
    meta = {
        "title": title[:100], "description": str(data.get("description") or "")[:4000], "tags": _tags(data.get("tags")),
        "music": music, "music_vol": vol if vol is not None else 12,
        "subs": True if subs is None else bool(subs),
    }
    if voice:
        meta["voice"] = voice
    recipe = {
        "video": {"kind": "scenes", "theme": default_theme, "clips": len(scenes), "seconds": int(total) or 30,
                  "file": None, "start": None, "end": None, "orig_sound": bool(data.get("original_sound", False))},
        "speech": {"kind": "scenes", "target": "en", "voice": voice, "rate": rate},
        "style": montage, "subs_src": "speech", "subs_lang": subs_lang, "look": {**look}, "sfx": sfx, "scenes": scenes,
    }
    return {"errors": errors, "warnings": warn, "recipe": recipe, "meta": meta, "channel": channel,
            "scenes": len(scenes), "seconds": total}


# ---------- сводка ----------

def _voice_label(v: str | None) -> str:
    if not v:
        return "из профиля канала"
    parts = v.split("-")
    return f"{parts[-1].removesuffix('Neural')} {parts[0]}-{parts[1]}" if len(parts) >= 3 else v


def summary(res: dict) -> str:
    from core import mix
    STYLE_LABELS = _montage.labels()
    r, m = res["recipe"], res["meta"]
    L, sp = r["look"], r["speech"]
    lines = [
        f"📜 Сценарий «{m['title']}»",
        f"Канал: {res['channel']} · сцен: {res['scenes']} · ≈ {res['seconds']:.0f} с",
        f"Голос: {_voice_label(sp['voice'])} · скорость {sp['rate']:+d}%" if sp["rate"] else f"Голос: {_voice_label(sp['voice'])}",
        f"Музыка: {m['music']}" + (f" · {m['music_vol']}%" if m["music"] != "off" else "") + f" · субтитры: {'да' if m['subs'] else 'нет'}"
        + (f" (перевод → {res['recipe'].get('subs_lang')})" if m["subs"] and res["recipe"].get("subs_lang") else ""),
        f"Стиль: {STYLE_LABELS.get(r['style'], r['style'])} · {LAYOUTS[L['layout']].lower()} · скорость {SPEEDS[L['speed']]}"
        + (f" · эффекты: {', '.join(FX[f] for f in L['fx'])}" if L["fx"] else ""),
    ]
    def _txt(b: dict) -> str:
        return ("из заголовка" if b["mode"] == "title" else
                (f"случайный из {len(b['pool'])} вариантов" if b.get("pool") else "случайный из списка канала") if b["mode"] == "random"
                else f"«{b['text']}»")
    if L["hook"]["mode"] != "off":
        lines.append("Хук: " + _txt(L["hook"]))
    if L["cta"]["mode"] != "off":
        lines.append(f"Призыв: {_txt(L['cta'])}")
    if L.get("caption", {}).get("mode", "off") != "off":
        lines.append("Подпись на видео: " + ("заголовок ролика" if L["caption"]["mode"] == "title" else f"«{L['caption']['text']}»"))
    if L.get("progress", {}).get("design", "off") != "off":
        from common.looks import PROGRESS_DESIGNS
        lines.append(f"Полоска прогресса: {PROGRESS_DESIGNS[L['progress']['design']].lower()}"
                     + ("" if L["progress"].get("end", True) else " (без кнопок в конце)"))
    lines.append("")
    for i, sc in enumerate(r["scenes"], 1):
        src = f"своё: {sc['own']}" if sc.get("own") else (mix.themes_label(sc.get("theme"), "любая тема"))
        txt = sc["text"] if len(sc["text"]) <= 70 else sc["text"][:67] + "..."
        lines.append(f"{i}. {src} · «{txt}»" if txt else f"{i}. {src} · без озвучки {sc.get('seconds', 3):g} с")
    if res["warnings"]:
        lines += ["", "⚠️ Исправлено автоматически:"] + [f"• {w}" for w in res["warnings"][:8]]
    text = "\n".join(lines)
    return text if len(text) <= 3800 else text[:3790] + "\n…"


# ---------- промт для нейросети ----------

EXAMPLE = {
    "title": "Венера, где год короче дня",
    "description": "Короткий факт о Венере.",
    "tags": ["#космос", "#венера", "#факты"],
    "theme": "космос",
    "voice": {"name": "ru-RU-DmitryNeural", "rate": 0},
    "music": {"mood": "calm", "volume": 12},
    "subtitles": True,
    "style": {"montage": "dynamic", "layout": "blur", "sub": "white", "subpos": "bottom"},
    "sfx": {"cuts": True, "start": True},
    "hook": "Год короче дня",
    "cta": "Дальше будет страннее",
    "caption": "title",
    "progress": "line",
    "scenes": [
        {"text": "На Венере вы бы не дожили до первого рассвета. Просто потому, что он там не наступает почти никогда.", "theme": "космос"},
        {"text": "Один оборот вокруг себя планета делает за двести сорок три земных дня, и это дольше, чем её собственный год.", "theme": "космос"},
        {"text": "Вокруг Солнца Венера успевает за двести двадцать пять. Выходит, год заканчивается раньше, чем начинается утро.", "theme": "космос"},
        {"text": "А чтобы всё было совсем странно, крутится она в обратную сторону, и Солнце там встаёт на западе.", "theme": "космос"},
    ],
}


def _text_block(value, kind: str, warn: list) -> dict:
    """hook/cta в сценарии: строка (свой текст), "title" (только хук), "random" или список вариантов (выбирается случайный при каждом рендере)."""
    if isinstance(value, list):
        pool = [" ".join(str(x).split())[:80] for x in value if str(x).strip()][:12]
        if pool:
            return {"mode": "random", "text": "", "pool": pool}
        return {"mode": "off", "text": ""}
    text = str(value or "").strip()
    low = text.lower()
    if not text:
        return {"mode": "off", "text": ""}
    if low == "random":
        return {"mode": "random", "text": ""}
    if low == "title":
        if kind == "hook":
            return {"mode": "title", "text": ""}
        warn.append("cta: «title» не поддерживается для призыва; призыв пропущен")
        return {"mode": "off", "text": ""}
    return {"mode": "text", "text": text[:80]}


def _caption_block(value) -> dict:
    """caption: "title" - заголовок ролика на всё время, любой другой текст - как есть, пусто/false - нет."""
    if not value or value is True:
        return {"mode": "off", "text": ""}
    text = str(value).strip()
    if text.lower() == "title":
        return {"mode": "title", "text": ""}
    return {"mode": "text", "text": text[:90]} if text else {"mode": "off", "text": ""}


def _progress_block(value, warn: list) -> dict:
    """progress: имя дизайна или объект {design, pos, color, end, label}; пусто/false - нет."""
    from common.looks import PROGRESS_COLORS, PROGRESS_DESIGNS, PROGRESS_POS
    out = {"design": "off"}
    if not value:
        return out
    cfg = value if isinstance(value, dict) else {"design": value}
    design = str(cfg.get("design") or "").lower()
    if design not in PROGRESS_DESIGNS or design == "off":
        warn.append(f"progress.design: «{design}» неизвестно ({', '.join(k for k in PROGRESS_DESIGNS if k != 'off')}); полоска не добавлена")
        return out
    out["design"] = design
    pos, color = str(cfg.get("pos") or "bottom"), str(cfg.get("color") or "auto")
    out["pos"] = pos if pos in PROGRESS_POS else "bottom"
    out["color"] = color if color in PROGRESS_COLORS else "auto"
    if pos not in PROGRESS_POS or color not in PROGRESS_COLORS:
        warn.append("progress: неизвестное pos или color заменено значением по умолчанию")
    if cfg.get("end") is not None:
        out["end"] = bool(cfg["end"])
    if cfg.get("label"):
        out["label"] = str(cfg["label"])[:20]
    return out


def make_prompt(ctx: dict, brief: str = "", tone: str | None = "friend") -> str:
    def lst(items):
        return ", ".join(items) if items else "(пока нет: поле пропусти)"

    def own_lines() -> str:
        titles, tags, durs = ctx.get("own_titles") or {}, ctx.get("own_tags") or {}, ctx.get("own_durations") or {}
        if not ctx["own"]:
            return "  (пока нет: поле own пропусти)"
        rows = []
        for name in ctx["own"]:
            extra = []
            if name in (ctx.get("project_files") or []):
                extra.insert(0, "файл проекта")
            if durs.get(name):
                extra.append(f"{durs[name]:.0f} с")
            if tags.get(name):
                extra.append("теги: " + tags[name])
            rows.append(f"  {name} — {titles.get(name) or 'без названия'}" + (f" ({', '.join(extra)})" if extra else ""))
        return "\n".join(rows)
    from core import voicestyle
    project_rule = ("- Это проект со своими видео. Строй сценарий на файлах из списка own, помеченных «файл проекта»: "
                    "по возможности у каждой сцены свой own с осмысленным start (seconds не пиши), выбирай по названию и длительности; "
                    "темы клипов нужны только если файлов не хватает.\n" if ctx.get("project_files") else "")
    return f"""Ты автор коротких вертикальных видео (YouTube Shorts) с живым авторским голосом. Напиши сценарий и верни ТОЛЬКО JSON, без пояснений и без ```.

ТЕМА И ЗАДАЧА: {brief or '<впиши тему ролика, язык, тон, целевую длину>'}

ПРАВИЛА
- Это ОДИН связный рассказ, а не подборка фактов. Сначала придумай сквозную линию (кто, что произошло, к чему идём) и напиши весь текст целиком, как будто читаешь его вслух одним дыханием; потом режь на сцены по границам предложений.
- Каждая сцена вытекает из предыдущей: опирается на сказанное (ссылка назад: «он», «тот самый», «и тут», «а теперь», «но») или отвечает на вопрос, который только что возник. Если сцену можно поставить на любое другое место, её надо переписать.
- Связки живой речи между сценами обязательны: «и», «но», «а», «поэтому», «потому что», «пока», «только», «зато», «в итоге». Не начинай подряд несколько сцен с подлежащего и точки.
- Каждая сцена - 1-3 предложения, 12-30 слов (≈ 120-300 символов, 5-12 секунд речи), длина сцен разная. Не дроби мысль на обрубки по 4-6 слов и не пиши по одному факту на сцену.
- Форма: хук (первые 1-2 сцены, сразу в действие или парадокс) - развитие с нарастающим напряжением - поворот - точка. Каждый факт нужен для следующего шага истории; лишнее выбрасывай.
- Общая длина до 60 секунд (≈ 14 символов текста на секунду), сцен не больше {MAX_SCENES}, обычно 5-8. Поле seconds у сцен с text НЕ пиши: длину задаёт озвучка. seconds нужен только для сцен без text.
- Перед ответом перечитай весь text подряд, склеив сцены: если слышны паузы между несвязанными фактами, перепиши связки.
- Используй ТОЛЬКО значения из списков ниже; если подходящего нет, пропусти поле.
{project_rule}
{voicestyle.style_rules(tone)}

ДОСТУПНО СЕЙЧАС
- theme (тема клипов): {lst(ctx['themes'])}
- music.mood: {lst(ctx['music'])}
- sfx.tag и scenes[].sfx: {lst(ctx['sfx'])}
- voice.name: {lst(ctx['voices'])}
- own (свои видео вместо клипа темы; пиши имя файла ровно как в списке, выбирай по названию):
{own_lines()}
- style.montage: {', '.join(MONTAGES)}; layout: {', '.join(LAYOUTS)}; speed: {', '.join(SPEEDS)}; fx: {', '.join(FX)};
  sub: {', '.join(SUBS)}; subpos: {', '.join(POS)}; crop_bars: {', '.join(CROP_BARS)} (обрезка чёрных полос клипа; по умолчанию off)
- channel: {', '.join(ctx['channels'])}

ПОЛЯ
title (обязательно), description, tags, channel, theme (тема по умолчанию), voice {{name, rate от -50 до 100}}, music {{mood, volume 0-100}},
subtitles (true/false), subtitles_lang (необязательно: en, ru, de, es, fr - субтитры переводятся на этот язык, озвучка остаётся), original_sound (true/false), style {{...}}, sfx {{cuts, start, tag, volume}}, hook (текст на экране в начале, "title"
или список из 3-5 вариантов: каждый рендер берёт случайный), cta (призыв в конце; тоже строка, "random" или список вариантов),
caption (постоянная подпись на всё видео: "title" или свой текст), progress (полоска прогресса: line, capsule или neon; в конце появляются
кнопки лайка и подписки), scenes (обязательно): список объектов {{text, theme, own, start, seconds, sfx}}.

ПРИМЕР ФОРМАТА (только структура JSON: тему, фразы и интонацию из примера не копируй)
{json.dumps(EXAMPLE, ensure_ascii=False, indent=2)}
"""
