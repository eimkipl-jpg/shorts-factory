"""Telegram-шлюз. Принимает аудио (или текст для озвучки), проверяет формат,
создаёт папку задачи с исходником и metadata.json, ставит задачу в очередь.

Формат подписи (caption) к аудио / первая строка текстового сообщения:
    Заголовок | Описание | #тег1 #тег2
Для текстового сообщения после первой строки идёт текст озвучки.
"""
import asyncio
import json
import time
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from common import config, db, library, profile
from common.captions import parse_caption
from common.media import audio_duration
from gateway import wizard
from gateway.ui import safe_answer, safe_edit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gateway")

ALLOWED_EXT = {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".flac", ".opus"}
TELEGRAM_LIMIT_MB = 20

dp = Dispatcher()
dp.include_router(wizard.router)


@dp.update.outer_middleware()
async def _plan_quick_guard(handler, event, data):
    """Режим «текст = задача» живёт, пока человек в задачнике: ушёл в другой раздел (кнопка не pl:, команда) — выключаем."""
    try:
        from gateway import plannerui
        cb, msg = getattr(event, "callback_query", None), getattr(event, "message", None)
        if cb is not None and not (cb.data or "").startswith("pl:"):
            plannerui.leave(cb.from_user.id)
        elif msg is not None and (msg.text or "").startswith("/") and msg.from_user:
            plannerui.leave(msg.from_user.id)
    except Exception:
        log.exception("plan guard")
    return await handler(event, data)


@dp.errors()
async def _ignore_not_modified(event) -> bool:
    """«message is not modified» — экран не изменился, это не ошибка."""
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc):
        cq = event.update.callback_query
        if cq:
            try:
                await cq.answer("Без изменений")
            except Exception:  # noqa: BLE001
                pass
        return True
    return False


HELP = (
    "Пришлите аудио с подписью в формате:\n"
    "Заголовок | Описание | #тег1 #тег2\n\n"
    "Или текстовое сообщение: первая строка - такая же подпись, дальше текст для озвучки.\n\n"
    "Нарезка из ваших клипов (папка _clips на Google Drive):\n"
    "/mix [клипов] [секунд] [тема] [dub=en] [music=auto] [subs=off] [| Заголовок | Описание | #теги]\n"
    "Примеры: /mix 6 30 abstract\n/mix 4 40 dub=en music=auto (перевод речи из ваших видео в _own; с темой - из клипов темы: /mix 4 40 memes dub=en)\n/clips - список тем\n\n"
    "💡 Банк идей по каналам: /ideas (идеи txt на Диске в _ideas, кнопка «Взять в работу», проект с видео, промт, JSON), /idea текст - добавить идею\n"
    "🎞 Умный монтаж (сценарий по сценам, кадры под смысл каждой фразы): /smart\n"
    "📖 Истории (карточка комментария + залипательное видео, текст появляется под озвучку): /story\n"
    "📜 Сценарий от нейросети: /prompt (промт с вашими темами), затем пришлите JSON текстом или файлом\n"
    "🗣 Словарь произношения (числа, сокращения, бренды, свои слова): /say, /saylist, /saytest\n"
    "📋 Задачник по каналам (задачи, видео на загрузку, ежедневные повторы, утренний отчёт): /plan, /task текст, /vid номер, /report\n"
    "✂️ Нарезка длинного видео на фрагменты (по переходам или интересные моменты): /cut\n"
    "🎬 Конструктор ролика на кнопках: /new (видеоряд, речь, музыка, субтитры, стиль, канал)\n"
    "Библиотека: /lib - фоны, клипы, музыка, звуки, поиск в Pexels/Pixabay; /find тег [тип]; /tag <id> теги; /src <id> источник | лицензия; /names - названия для видео; /title <id> название\n\n"
    "Команды:\n/channel - выбрать канал\n/status - сколько задач в очереди"
)


def allowed(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in config.ALLOWED_USER_IDS


@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    if not allowed(message):
        return await message.answer("Нет доступа.")
    ch = db.get_user_channel(message.from_user.id)
    await message.answer(f"{HELP}\n\nТекущий канал: {ch}", reply_markup=_main_menu())


@dp.message(Command("channel"))
async def cmd_channel(message: Message):
    if not allowed(message):
        return
    arg = (message.text or "").split(maxsplit=1)[1:] or [""]
    name = arg[0].strip()
    if name in config.CHANNELS:
        db.set_user_channel(message.from_user.id, name)
        return await message.answer(f"Канал: {name}")
    current = db.get_user_channel(message.from_user.id)
    await message.answer(
        f"Текущий канал: {current}\nДоступные: {', '.join(config.CHANNELS)}\n"
        f"Смена: /channel <имя>"
    )


@dp.message(Command("status"))
async def cmd_status(message: Message):
    if allowed(message):
        await message.answer(f"В очереди: {db.queue_size()}")


@dp.message(Command("clips"))
async def cmd_clips(message: Message):
    if not allowed(message):
        return
    from core import mix
    try:
        await asyncio.to_thread(mix.sync_clips)
    except Exception as e:  # noqa: BLE001
        return await message.answer(f"Не удалось обновить клипы: {e}")
    lines = []
    for t in mix.list_themes():
        n = len([f for f in (config.CLIPS_DIR / t).iterdir() if f.suffix.lower() in mix.VIDEO_EXT])
        lines.append(f"{t}: {n}")
    await message.answer("Темы клипов:\n" + ("\n".join(lines) or "пусто - добавьте видео в _clips/<тема>/ на Диске"))


@dp.message(Command("mix"))
async def cmd_mix(message: Message):
    if not allowed(message):
        return
    body = (message.text or "").partition(" ")[2]
    args, _, tail = body.partition("|")
    clips, seconds, theme = config.MIX_DEFAULT_CLIPS, config.MIX_DEFAULT_SECONDS, None
    nums, opts = [], {}
    for tok in args.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            opts[k.lower()] = v
        elif tok.isdigit():
            nums.append(int(tok))
        else:
            theme = tok.lstrip("#")
    if nums:
        clips = nums[0]
    if len(nums) > 1:
        seconds = nums[1]
    if not (2 <= clips <= 20) or not (5 <= seconds <= 180):
        return await message.answer("Клипов 2-20, длительность 5-180 с. Пример: /mix 4 40 dub=en music=auto")
    bad = set(opts) - {"dub", "music", "subs", "voice"}
    if bad:
        return await message.answer(f"Неизвестные параметры: {', '.join(sorted(bad))}. Допустимо: dub= music= subs= voice=")
    channel = db.get_user_channel(message.from_user.id)
    meta = {"title": f"Нарезка {theme or 'mix'}", "description": "", "tags": [f"#{theme}"] if theme else []}
    if tail.strip():
        parsed = parse_caption(tail.strip())
        if parsed is None:
            return await message.answer("После | нужно: Заголовок | Описание | #теги")
        meta = parsed
    meta.update({"clips": clips, "seconds": seconds, "theme": theme})
    if "dub" in opts:
        meta["dub"] = opts["dub"].lower()
        meta["theme"] = None  # для дубляжа клипы берутся из _own
    if "music" in opts:
        meta["music"] = opts["music"]
    if "subs" in opts:
        meta["subs"] = opts["subs"].lower() not in ("off", "0", "no", "нет")
    if "voice" in opts:
        meta["voice"] = opts["voice"]
    task_id = db.create_task(channel, "mix", message.chat.id)
    task_dir = _task_dir(task_id)
    _write_meta(task_dir, task_id, channel, meta, "mix")
    db.mark_ready(task_id)
    extra = f", перевод на {meta['dub']}" if meta.get("dub") else f", тема {theme or 'любая'}"
    await message.answer(f"Принято (нарезка): task_{task_id}, {clips} клипов, ~{seconds} с{extra}, музыка: {meta.get('music') or profile.get(channel)['music']}. В очереди: {db.queue_size()}")


def _main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎬 Создать", callback_data="m:create")
    kb.button(text="📚 Библиотека", callback_data="m:lib")
    kb.button(text="📥 Очередь", callback_data="m:queue")
    kb.button(text="⚙️ Профиль", callback_data="pf:show")
    kb.button(text="📜 Сценарий (JSON)", callback_data="sc:help")
    kb.button(text="💡 Банк идей", callback_data="id:home")
    kb.button(text="📖 Истории", callback_data="sy:home")
    kb.button(text="🎞 Умный монтаж", callback_data="sm:home")
    kb.button(text="📋 Задачник", callback_data="pl:home")
    kb.adjust(2)
    return kb.as_markup()


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    if allowed(message):
        await message.answer("Главное меню:", reply_markup=_main_menu())


STATUS_ICON = {"new": "⏳", "processing": "⚙️", "uploading": "☁️", "done": "✅", "failed": "❌", "rejected": "🚫"}

PF_LABELS = {"voice": "🎙 Голос", "rate": "⚡ Скорость речи", "bg": "🖼 Фон", "music": "🎵 Музыка", "vol": "🔉 Громкость музыки",
             "subs": "🔤 Субтитры", "lang": "🌐 Язык аудио"}


def _pf_options(key: str) -> list[str]:
    if key == "voice":
        return config.VOICES
    if key == "bg":
        themes = sorted(d.name for d in config.BACKGROUNDS_DIR.iterdir() if d.is_dir()) if config.BACKGROUNDS_DIR.exists() else []
        return ["auto"] + themes
    if key == "music":
        return ["off", "auto"] + [t for t, _ in library.tag_counts("music") if t != "без-темы"]
    if key == "vol":
        return ["5", "10", "15", "25", "40"]
    if key == "subs":
        return ["1", "0"]
    if key == "lang":
        return ["ru", "en", "auto"]
    if key == "rate":
        return ["0", "5", "10", "15", "20", "25"]
    return []


def _pf_show(value: str, key: str) -> str:
    if key == "subs":
        return "вкл" if value == "1" else "выкл"
    if key == "vol":
        return f"{value}%"
    if key == "rate":
        return "обычная" if value in ("0", "") else f"+{value}%"
    return value


def _pf_screen(user_id: int):
    ch = db.get_user_channel(user_id)
    p = profile.get(ch)
    text = (f"⚙️ Профиль канала «{ch}»\nНажатие на кнопку переключает значение. Настройки применяются к новым роликам.\n"
            "⚡ Скорость речи ускоряет озвучку во всех режимах (сценарии, истории, текст), если у ролика своя скорость не выбрана.")
    kb = InlineKeyboardBuilder()
    for key, label in PF_LABELS.items():
        kb.button(text=f"{label}: {_pf_show(p[key], key)}", callback_data=f"pf:{key}")
    kb.button(text=f"📺 Канал: {ch}  (выбрать или создать)", callback_data="pf:chlist")
    kb.button(text="⬅ Меню", callback_data="m:back")
    kb.adjust(1)
    return text, kb.as_markup()


@dp.callback_query(F.data.startswith("pf:"))
async def cb_profile(cb: CallbackQuery):
    if cb.from_user.id not in config.ALLOWED_USER_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    key = cb.data.split(":")[1]
    ch = db.get_user_channel(cb.from_user.id)
    if key == "chlist":
        kb = InlineKeyboardBuilder()
        for i, name in enumerate(config.CHANNELS):
            kb.button(text=("✅ " if name == ch else "📺 ") + name, callback_data=f"pf:ch:{i}")
        kb.button(text="➕ Новый канал", callback_data="pf:newch")
        kb.button(text="⬅ Профиль", callback_data="pf:show")
        kb.adjust(2)
        await safe_answer(cb)
        return await safe_edit(cb.message, "📺 Каналы\nВыбранный канал используется везде: конструктор, профиль, идеи, истории, задачник.\n"
                              "«Новый канал» сразу создаёт его папки на сервере и на Диске.", kb.as_markup())
    if key == "newch":
        st = wizard.load(cb.from_user.id)
        st["awaiting"] = "pf_chan_new"
        wizard.save(cb.from_user.id, st)
        kb = InlineKeyboardBuilder()
        kb.button(text="✖ Отмена", callback_data="pf:chlist")
        await safe_answer(cb)
        return await safe_edit(cb.message, "➕ Пришлите название нового канала: 2-30 символов, буквы, цифры, «_» и «-» (пробелы станут «_»).", kb.as_markup())
    if key == "ch":
        idx = int(cb.data.split(":")[2])
        if 0 <= idx < len(config.CHANNELS):
            db.set_user_channel(cb.from_user.id, config.CHANNELS[idx])
    elif key == "channel":
        i = config.CHANNELS.index(ch)
        db.set_user_channel(cb.from_user.id, config.CHANNELS[(i + 1) % len(config.CHANNELS)])
    elif key != "show":
        opts = _pf_options(key)
        cur = profile.get(ch)[key]
        if opts:
            nxt = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else opts[0]
            profile.set_value(ch, key, nxt)
    text, kb = _pf_screen(cb.from_user.id)
    await safe_answer(cb)
    await safe_edit(cb.message, text, kb)


def _queue_screen():
    rows = db.recent_tasks(8)
    lines = [f"{STATUS_ICON.get(r['status'], '•')} task_{r['id']} · {r['channel']} · {r['input_type']}"
             + (f" · {(r['error'] or '')[:60]}" if r["status"] == "failed" else "") for r in rows]
    kb = InlineKeyboardBuilder()
    for r in rows:
        if r["status"] == "failed":
            kb.button(text=f"🔁 Повторить task_{r['id']}", callback_data=f"q:retry:{r['id']}")
    kb.button(text="🔄 Обновить", callback_data="m:queue")
    kb.button(text="⬅ Меню", callback_data="m:back")
    kb.adjust(1)
    stamp = time.strftime("%H:%M:%S")
    return f"📥 Очередь (обновлено {stamp}):\n" + ("\n".join(lines) or "пусто"), kb.as_markup()


@dp.callback_query(F.data.startswith("q:retry:"))
async def cb_retry(cb: CallbackQuery):
    if cb.from_user.id not in config.ALLOWED_USER_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    ok = db.retry_task(int(cb.data.split(":")[2]))
    await cb.answer("Возвращена в очередь" if ok else "Нельзя повторить (папка уже удалена)", show_alert=not ok)
    text, kb = _queue_screen()
    await safe_edit(cb.message, text, kb)


@dp.callback_query(F.data.startswith("m:"))
async def cb_menu(cb: CallbackQuery):
    if cb.from_user.id not in config.ALLOWED_USER_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    what = cb.data.split(":")[1]
    await safe_answer(cb)  # сразу, чтобы кнопка не «висела» и нажатие не устарело
    if what == "back":
        await safe_edit(cb.message, "Главное меню:", _main_menu())
    elif what == "lib":
        await asyncio.to_thread(library.scan)
        await safe_edit(cb.message, "Библиотека материалов:", _lib_menu())
    elif what == "queue":
        text, kb = _queue_screen()
        await safe_edit(cb.message, text, kb)
    elif what == "create":
        await wizard.show_main(cb.message, cb.from_user.id, edit=True)


ICONS = {"bg": "🖼", "clip": "🎞", "music": "🎵", "own": "🎥", "sfx": "🔔", "sat": "🧘"}


def _lib_menu():
    st = library.stats()
    kb = InlineKeyboardBuilder()
    for t, name in library.TYPES.items():
        kb.button(text=f"{ICONS[t]} {name} ({st.get(t, 0)})", callback_data=f"lib:{t}")
    kb.button(text="🔎 Найти в интернете", callback_data="src:ask")
    kb.button(text="➕ Видео по ссылке", callback_data="src:url")
    kb.button(text="🏷 Названия моих видео", callback_data="lib:names")
    kb.button(text="✂️ Нарезка видео", callback_data="cu:start")
    kb.button(text="🔄 Обновить", callback_data="lib:refresh")
    kb.button(text="⬅ Меню", callback_data="m:back")
    kb.adjust(2)
    return kb.as_markup()


def _fmt(a: dict) -> str:
    dur = f"{a['duration']:.0f} с" if a["duration"] else "?"
    lic = f" · {a['license']}" if a["license"] else ""
    src = f"\n   источник: {a['source']}" if a["source"] else ""
    name = f"«{a['title']}» ({Path(a['path']).name})" if a.get("title") else Path(a["path"]).name
    return f"#{a['id']} {name} · {dur} · тег: {a['tags'] or '-'} · исп.: {a['uses']}{lic}{src}"


@dp.message(Command("lib"))
async def cmd_lib(message: Message):
    if not allowed(message):
        return
    await asyncio.to_thread(library.scan)
    await message.answer("Библиотека материалов:", reply_markup=_lib_menu())


@dp.callback_query(F.data.startswith("lib:"))
async def cb_lib(cb: CallbackQuery):
    if cb.from_user.id not in config.ALLOWED_USER_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    parts = cb.data.split(":")
    if parts[1] == "names":
        from core import sources
        await safe_answer(cb, "Ищу названия…")
        res = await asyncio.to_thread(sources.fill_titles)
        return await cb.message.answer(f"Названий найдено: {res['found']} из {res['total']}."
                                       + (("\nНе нашёл: " + ", ".join(res["failed"][:15])) if res["failed"] else ""))
    await safe_answer(cb)  # отвечаем сразу: синхронизация с Диском может идти дольше времени жизни нажатия
    if parts[1] == "refresh":
        from core import mix
        await safe_edit(cb.message, "⏳ Обновляю с Диска…", None)
        note = ""
        try:
            await asyncio.wait_for(asyncio.to_thread(mix.sync_clips), timeout=600)
        except asyncio.TimeoutError:
            note = "\n⚠️ Диск отвечает слишком долго, показаны локальные файлы."
        except Exception as e:  # noqa: BLE001
            note = f"\n⚠️ Диск не обновился ({str(e)[:150]}), показаны локальные файлы."
        r = await asyncio.to_thread(library.scan)
        await safe_edit(
            cb.message,
            f"Обновлено в {time.strftime('%H:%M:%S')}: +{r['added']}, -{r['removed']}, всего {r['total']}{note}",
            _lib_menu(),
        )
    elif len(parts) == 2 and parts[1] in library.TYPES:  # выбран тип -> темы
        kb = InlineKeyboardBuilder()
        for tag, n in library.tag_counts(parts[1])[:30]:
            kb.button(text=f"{tag} ({n})", callback_data=f"lib:{parts[1]}:{tag}")
        kb.button(text="⬅ Назад", callback_data="lib:back")
        kb.adjust(2)
        await safe_edit(cb.message, f"{ICONS[parts[1]]} {library.TYPES[parts[1]]}: выберите тему", kb.as_markup())
    elif parts[1] == "back":
        await safe_edit(cb.message, "Библиотека материалов:", _lib_menu())
    elif len(parts) >= 3 and parts[1] in library.TYPES:  # тип + тема -> список файлов
        tag = ":".join(parts[2:])
        items = library.search(parts[1], [tag], limit=15)
        text = "\n".join(_fmt(a) for a in items) or "пусто"
        await cb.message.answer(f"{library.TYPES[parts[1]]} / {tag}:\n{text}\n\nТеги: /tag <id> тег1 тег2")


@dp.message(Command("find"))
async def cmd_find(message: Message):
    if not allowed(message):
        return
    args = (message.text or "").split()[1:]
    type_ = next((a for a in args if a in library.TYPES), None)
    tags = [a for a in args if a not in library.TYPES]
    if not tags and not type_:
        return await message.answer("Пример: /find космос music  или  /find neon bg")
    items = library.search(type_, tags, limit=15)
    await message.answer("\n".join(_fmt(a) for a in items) or "Ничего не найдено")


@dp.message(Command("names"))
async def cmd_names(message: Message):
    """Подтягивает названия для «моих видео» с именами-ID YouTube."""
    if not allowed(message):
        return
    from core import sources
    await message.answer("⏳ Ищу названия по ID видео, это займёт около минуты…")
    res = await asyncio.to_thread(sources.fill_titles)
    text = f"Названий найдено: {res['found']} из {res['total']}."
    if res["failed"]:
        text += "\nНе нашёл: " + ", ".join(res["failed"][:20]) + "\nЗадать вручную: /title <id> название"
    await message.answer(text)


@dp.message(Command("title"))
async def cmd_title(message: Message):
    """/title <id> <название> - название материала (id из /lib или /find)."""
    if not allowed(message):
        return
    body = (message.text or "").partition(" ")[2].strip()
    head, _, title = body.partition(" ")
    if not head.lstrip("#").isdigit() or not title.strip():
        return await message.answer("Пример: /title 12 Кот падает с дивана")
    ok = library.set_title(int(head.lstrip("#")), title)
    await message.answer("Название записано" if ok else "Материал с таким id не найден")


@dp.message(Command("src"))
async def cmd_src(message: Message):
    """/src <id> <источник> | <лицензия> - записать, откуда материал и на каких условиях."""
    if not allowed(message):
        return
    body = (message.text or "").partition(" ")[2].strip()
    head, _, rest = body.partition(" ")
    if not head.lstrip("#").isdigit() or not rest.strip():
        return await message.answer("Пример: /src 12 https://pixabay.com/music/... | Pixabay Content License")
    source, _, lic = rest.partition("|")
    ok = library.set_info(int(head.lstrip("#")), source=source.strip(), license_=lic.strip() if lic.strip() else None)
    await message.answer("Записано" if ok else "Материал с таким id не найден")


@dp.message(Command("tag"))
async def cmd_tag(message: Message):
    if not allowed(message):
        return
    args = (message.text or "").split()[1:]
    if len(args) < 2 or not args[0].lstrip("#").isdigit():
        return await message.answer("Пример: /tag 12 спокойный космос")
    ok = library.add_tags(int(args[0].lstrip("#")), args[1:])
    await message.answer("Теги добавлены" if ok else "Материал с таким id не найден")


def _task_dir(task_id: int) -> Path:
    d = config.TASKS_DIR / f"task_{task_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_meta(task_dir: Path, task_id: int, channel: str, meta: dict, input_type: str) -> None:
    data = {"task_id": task_id, "channel": channel, "input_type": input_type, **profile.for_task(channel), **meta}
    (task_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


async def _reject(message: Message, reason: str):
    await message.answer(f"Отклонено: {reason}\n\n{HELP}")


@dp.message(F.audio | F.voice | F.document | F.photo | F.video | F.video_note)
async def on_audio(message: Message, bot: Bot):
    if not allowed(message):
        return await message.answer("Нет доступа.")
    if await wizard.handle_audio(message, bot):  # аудио или логотип для мастера «Создать»
        return
    if message.photo:
        return await message.answer("Фото принимается только как логотип: 🎬 Создать → 5️⃣ Стиль → Логотип.")
    if message.video or message.video_note:
        return await message.answer("Видео принимается в проект банка идей: 💡 Банк идей → идея в работе → «📤 Прислать файлы».")

    meta = parse_caption(message.caption)
    if meta is None:
        return await _reject(message, "нужна подпись в формате «Заголовок | Описание | #теги».")

    obj = message.audio or message.voice or message.document
    name = getattr(obj, "file_name", None) or "voice.ogg"
    ext = Path(name).suffix.lower() or ".ogg"
    if message.document and ext not in ALLOWED_EXT:
        return await _reject(message, f"формат {ext or '?'} не поддерживается ({', '.join(sorted(ALLOWED_EXT))}).")
    if message.document and message.document.mime_type and not message.document.mime_type.startswith(("audio/", "application/ogg")):
        return await _reject(message, f"это не аудио ({message.document.mime_type}).")

    channel = db.get_user_channel(message.from_user.id)
    task_id = db.create_task(channel, "audio", message.chat.id)
    task_dir = _task_dir(task_id)
    dest = task_dir / f"input_audio{ext}"

    try:
        await bot.download(obj, destination=dest)
    except TelegramBadRequest as e:
        _cleanup_failed(task_id, task_dir)
        if "too big" in str(e).lower():
            return await _reject(message, f"файл больше {TELEGRAM_LIMIT_MB} МБ (лимит Telegram).")
        raise

    # Валидация содержимого: реально ли это аудио и подходит ли длительность.
    try:
        dur = await asyncio.to_thread(audio_duration, dest)
    except ValueError as e:
        _cleanup_failed(task_id, task_dir)
        return await _reject(message, str(e))
    if not (config.MIN_AUDIO_SECONDS <= dur <= config.MAX_AUDIO_SECONDS):
        _cleanup_failed(task_id, task_dir)
        return await _reject(
            message,
            f"длительность {dur:.0f} с, допустимо {config.MIN_AUDIO_SECONDS:.0f}-{config.MAX_AUDIO_SECONDS:.0f} с.",
        )

    _write_meta(task_dir, task_id, channel, meta, "audio")
    db.mark_ready(task_id)
    await message.answer(f"Принято: task_{task_id}, канал {channel}, {dur:.0f} с. В очереди: {db.queue_size()}")


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message):
    if not allowed(message):
        return await message.answer("Нет доступа.")
    if await wizard.handle_text(message):  # текст/фрагмент для мастера «Создать»
        return
    first, _, script = (message.text or "").partition("\n")
    meta = parse_caption(first)
    script = script.strip()
    if meta is None or len(script) < 20:
        return await _reject(message, "первая строка - «Заголовок | Описание | #теги», дальше текст озвучки (от 20 символов).")

    channel = db.get_user_channel(message.from_user.id)
    task_id = db.create_task(channel, "text", message.chat.id)
    task_dir = _task_dir(task_id)
    (task_dir / "script.txt").write_text(script, "utf-8")
    _write_meta(task_dir, task_id, channel, meta, "text")
    db.mark_ready(task_id)
    await message.answer(f"Принято (озвучка): task_{task_id}, канал {channel}. В очереди: {db.queue_size()}")


def _cleanup_failed(task_id: int, task_dir: Path) -> None:
    import shutil
    shutil.rmtree(task_dir, ignore_errors=True)
    db.set_status(task_id, "rejected")


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")
    db.init()
    library.init()
    profile.init()
    session = None
    if config.TELEGRAM_API_URL:
        session = AiohttpSession(api=TelegramAPIServer.from_base(config.TELEGRAM_API_URL, is_local=True))
    bot = Bot(config.BOT_TOKEN, session=session)
    log.info("Gateway started")
    from gateway import plannerui
    planner_task = asyncio.create_task(plannerui.report_loop(bot))   # повторы задачника и утренний отчёт
    try:
        await dp.start_polling(bot)
    finally:
        planner_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
