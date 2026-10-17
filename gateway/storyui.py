"""Режим «📖 Истории» в боте: промт для нейросети, приём JSON истории, оформление карточки, залипательные видео.

Кнопки (sy:...):
  sy:home                         экран режима
  sy:p, sy:pl:<язык>, sy:pg:<язык>:<жанр>   промт: язык -> жанр -> готовый промт
  sy:set, sy:s:<ключ>:<значение>  оформление (theme, layout, hl, progress, sat, voice, music)
  sy:sat                          залипательные видео: папки и сколько в них
  sy:up:<папка>, sy:url:<папка>   ждать видео / ссылку в папку («-» - корень)
  sy:nf                           новая папка; sy:sync - обновить с Диска
  sy:go, sy:cancel                собрать / отменить присланную историю
"""
import asyncio
import json
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config, db, library, profile
from core import ideas, mix, scenario, sources, story
from gateway import state
from gateway.ui import safe_answer, safe_edit

log = logging.getLogger("storyui")
router = Router()

HELP = ("📖 Истории\n"
        "Сначала карточка с вопросом, потом история в карточке комментария: слова появляются, пока диктор читает. "
        "Фон - залипательное видео (паркур, кинетический песок и т. п.).\n\n"
        "1. «📋 Промт для истории» → язык и жанр → бот пришлёт готовый промт.\n"
        "2. Вставьте промт в нейросеть (впишите свою идею или оставьте как есть).\n"
        "3. Ответ-JSON пришлите сюда текстом или файлом .json.\n"
        "4. Проверьте сводку и нажмите «🚀 Собрать».")
VIDEO_EXT = story.VIDEO_EXT


def _kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows if row])


def _ok(uid: int) -> bool:
    return uid in config.ALLOWED_USER_IDS


def _mark(on: bool) -> str:
    return "✅ " if on else ""


def cfg_of(st: dict) -> dict:
    cfg = {**story.DEFAULT_CFG, **(st.get("story") or {})}
    return cfg


def _sat_label(cfg: dict) -> tuple[str, int]:
    folders = story.sat_folders()
    if cfg.get("sat", "all") == "all":
        return "все", sum(folders.values())
    return cfg["sat"], folders.get(cfg["sat"], 0)


def home_screen(st: dict):
    cfg = cfg_of(st)
    sat, n = _sat_label(cfg)
    total = sum(story.sat_folders().values())
    text = (f"{HELP}\n\nСейчас: карточка {story.THEMES[cfg['theme']]}, {story.LAYOUTS[cfg['layout']]}; "
            f"залипательные: {sat} ({n}); голос: {'по языку истории' if cfg['voice'] == 'auto' else cfg['voice']}; "
            f"музыка: {cfg['music']}")
    if total == 0:
        text += "\n\n❗ Залипательных видео пока нет: добавьте хотя бы одно, иначе ролик не соберётся."
    return text, _kb([("📋 Промт для истории", "sy:p")], [("🎨 Оформление", "sy:set")],
                     [(f"🧘 Залипательные видео ({total})", "sy:sat")], [("⬅ Меню", "m:back")])


def settings_screen(st: dict):
    cfg = cfg_of(st)
    folders = story.sat_folders()
    text = ("🎨 Оформление историй (для всех следующих роликов)\n"
            "Компоновка «поверх видео» - карточка в верхней части кадра на фоне видео; «сверху/снизу» - карточка на размытом фоне "
            "в верхней половине, видео в нижней.\nПодсветка - текущее слово выделено цветом, пока диктор его читает.")
    rows = [
        [(_mark(cfg["theme"] == k) + "Карточка " + v, f"sy:s:theme:{k}") for k, v in story.THEMES.items()],
        [(_mark(cfg["layout"] == k) + v, f"sy:s:layout:{k}") for k, v in story.LAYOUTS.items()],
        [(_mark(cfg["hl"]) + "Подсветка слова", "sy:s:hl:1" if not cfg["hl"] else "sy:s:hl:0")],
        [(_mark(cfg["progress"] == k) + ("Полоска: " + v if k != "off" else "Без полоски"), f"sy:s:progress:{k}")
         for k, v in story.PROGRESS.items()],
        [(_mark(cfg["sat"] == "all") + "Видео: все", "sy:s:sat:all")]
        + [(_mark(cfg["sat"] == k) + f"{k} ({n})", f"sy:s:sat:{k}") for k, n in folders.items() if k][:3],
        [("🎙 Голос: " + ("по языку" if cfg["voice"] == "auto" else cfg["voice"]), "sy:s:voice:next"),
         ("🎵 Музыка: " + cfg["music"], "sy:s:music:next")],
        [("⬅ Назад", "sy:home")],
    ]
    return text, _kb(*rows)


def sat_screen(st: dict):
    folders = story.sat_folders()
    lines = [f"• {'(корень)' if not k else k}: {n}" for k, n in folders.items()] or ["пока пусто"]
    text = ("🧘 Залипательные видео (паркур, кинетический песок, слайм, мыло, маркетплейс-распаковки - всё, что «залипательно»).\n"
            f"Папка на Диске: _clips/{story.SAT_NAME}/ - подпапки становятся видами (например parkour, sand).\n"
            "Берите свои записи или видео с лицензией, разрешающей использование.\n\n" + "\n".join(lines))
    rows = [[("📤 Прислать видео в корень", "sy:up:-"), ("🔗 По ссылке", "sy:url:-")]]
    for k in [k for k in folders if k][:6]:
        rows.append([(f"📤 В «{k}»", f"sy:up:{k}"), (f"🔗 В «{k}»", f"sy:url:{k}")])
    rows += [[("➕ Новая папка", "sy:nf"), ("🔄 С Диска", "sy:sync")], [("⬅ Назад", "sy:home")]]
    return text, _kb(*rows)


async def _show(cb: CallbackQuery, screen) -> None:
    await safe_edit(cb.message, *screen)


async def _send_long(message: Message, text: str, filename: str) -> None:
    if len(text) <= 3900:
        await message.answer(text)
    else:
        await message.answer_document(BufferedInputFile(text.encode("utf-8"), filename=filename),
                                      caption="Промт длинный, поэтому файлом: скопируйте текст целиком.")


@router.message(Command("story", "stories"))
async def cmd_story(message: Message):
    if message.from_user and _ok(message.from_user.id):
        st = state.load(message.from_user.id)
        text, kb = home_screen(st)
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("sy:"))
async def on_sy(cb: CallbackQuery):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    parts = cb.data.split(":")
    act = parts[1]
    st = state.load(uid)
    await safe_answer(cb)
    if act == "home":
        if st.get("awaiting") in ("sat_upload", "sat_url", "sat_folder"):
            st["awaiting"] = None
            state.save(uid, st)
        return await _show(cb, home_screen(st))
    if act == "p":
        return await _show(cb, ("📋 На каком языке история?", _kb([(story.LANG_NAMES["ru"].capitalize(), "sy:pl:ru"),
                                                                   ("English", "sy:pl:en")], [("⬅ Назад", "sy:home")])))
    if act == "pl":
        lang = parts[2] if len(parts) > 2 and parts[2] in story.LANG_NAMES else "ru"
        rows = [[(name, f"sy:pg:{lang}:{key}")] for key, (name, _) in story.GENRES.items()]
        rows.append([("🎲 Любой жанр (на выбор нейросети)", f"sy:pg:{lang}:any")])
        rows.append([("⬅ Назад", "sy:p")])
        return await _show(cb, ("Какой жанр?", _kb(*rows)))
    if act == "pg":
        lang = parts[2] if len(parts) > 2 and parts[2] in story.LANG_NAMES else "ru"
        genre = parts[3] if len(parts) > 3 and parts[3] in story.GENRES else None
        ctx = await asyncio.to_thread(scenario.build_ctx, db.get_user_channel(uid))
        await _send_long(cb.message, story.make_prompt(lang, genre, ctx), "story_prompt.txt")
        return await cb.message.answer("Впишите в строку «ТЕМА И ПОЖЕЛАНИЯ» свою идею (или оставьте как есть) и отправьте промт нейросети. "
                                       "Готовый JSON пришлите мне.", reply_markup=_kb([("⬅ К историям", "sy:home")]))
    if act == "set":
        return await _show(cb, settings_screen(st))
    if act == "s" and len(parts) >= 4:
        key, val = parts[2], ":".join(parts[3:])
        cfg = cfg_of(st)
        if key == "theme" and val in story.THEMES:
            cfg["theme"] = val
        elif key == "layout" and val in story.LAYOUTS:
            cfg["layout"] = val
        elif key == "hl":
            cfg["hl"] = val == "1"
        elif key == "progress" and val in story.PROGRESS:
            cfg["progress"] = val
        elif key == "sat" and (val == "all" or val in story.sat_folders()):
            cfg["sat"] = val
        elif key == "voice":
            opts = ["auto"] + list(config.VOICES)
            cfg["voice"] = opts[(opts.index(cfg["voice"]) + 1) % len(opts)] if cfg["voice"] in opts else "auto"
        elif key == "music":
            opts = ["off", "auto"] + [t for t, _ in library.tag_counts("music") if t != "без-темы"]
            cfg["music"] = opts[(opts.index(cfg["music"]) + 1) % len(opts)] if cfg["music"] in opts else "off"
        st["story"] = cfg
        state.save(uid, st)
        return await _show(cb, settings_screen(st))
    if act == "sat":
        if st.get("awaiting") in ("sat_upload", "sat_url", "sat_folder"):
            st["awaiting"] = None
            state.save(uid, st)
        await asyncio.to_thread(library.scan)
        return await _show(cb, sat_screen(st))
    if act in ("up", "url"):
        folder = "" if len(parts) < 3 or parts[2] == "-" else story.safe_folder(parts[2])
        st["awaiting"] = "sat_upload" if act == "up" else "sat_url"
        st["lists"]["sat_target"] = folder
        state.save(uid, st)
        where = f"папку «{folder}»" if folder else "корень папки залипательных"
        if act == "up":
            return await cb.message.answer(f"📤 Присылайте видео (можно несколько подряд) - они лягут в {where} и на Диск.\n"
                                           "Telegram отдаёт боту файлы до 20 МБ; крупнее - положите в папку на Диске "
                                           f"(_clips/{story.SAT_NAME}/{folder}) и нажмите «🔄 С Диска».",
                                           reply_markup=_kb([("✅ Готово", "sy:sat")]))
        return await cb.message.answer(f"🔗 Пришлите ссылку на видео - оно скачается в {where} и загрузится на Диск.\n"
                                       "Используйте свои ролики или материалы, на которые у вас есть права.",
                                       reply_markup=_kb([("✖ Отмена", "sy:sat")]))
    if act == "nf":
        st["awaiting"] = "sat_folder"
        state.save(uid, st)
        return await cb.message.answer("➕ Пришлите название папки латиницей, например parkour или sand.",
                                       reply_markup=_kb([("✖ Отмена", "sy:sat")]))
    if act == "sync":
        await safe_edit(cb.message, "⏳ Обновляю залипательные видео с Диска…", None)
        note = ""
        try:
            await asyncio.wait_for(asyncio.to_thread(mix.sync_clips), timeout=600)
        except asyncio.TimeoutError:
            note = "⚠️ Диск отвечает слишком долго, показаны локальные файлы.\n\n"
        except Exception as e:  # noqa: BLE001
            note = f"⚠️ Диск не обновился ({str(e)[:150]}), показаны локальные файлы.\n\n"
        await asyncio.to_thread(library.scan)
        text, kb = sat_screen(st)
        return await safe_edit(cb.message, note + text, kb)
    if act == "cancel":
        st["lists"].pop("story_pending", None)
        state.save(uid, st)
        return await cb.message.answer("История отменена.", reply_markup=_kb([("⬅ К историям", "sy:home")]))
    if act == "go":
        pending = st["lists"].pop("story_pending", None)
        if not pending:
            return await cb.message.answer("История не найдена, пришлите JSON заново.")
        state.save(uid, st)
        return await create_task(cb.message, pending, cfg_of(st))


async def create_task(message: Message, res: dict, cfg: dict) -> None:
    channel = res["channel"]
    m, s = res["meta"], res["story"]
    if not story.sat_pool(cfg.get("sat")):
        return await message.answer("❗ Нет залипательных видео: добавьте их (🧘 Залипательные видео), потом пришлите JSON снова.",
                                    reply_markup=_kb([("🧘 Залипательные видео", "sy:sat")]))
    task_id = db.create_task(channel, "story", message.chat.id)
    task_dir = config.TASKS_DIR / f"task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_cfg = {**cfg, "progress": m.get("progress", cfg.get("progress", "off"))}
    data = {"task_id": task_id, "channel": channel, "input_type": "story", **profile.for_task(channel),
            "title": m["title"], "description": m["description"], "tags": m["tags"],
            "voice": story.voice_for(cfg, m, s["lang"]), "rate": m.get("rate", 0),
            "music": m.get("music", cfg.get("music", "off")), "music_vol": m.get("music_vol", cfg.get("music_vol", 8)),
            "subs": False, "lang": s["lang"], "story": s, "story_cfg": task_cfg, "source": "story_json"}
    (task_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    db.mark_ready(task_id)
    await message.answer(f"Принято: task_{task_id}, история «{m['title'][:60]}», канал {channel}. В очереди: {db.queue_size()}",
                         reply_markup=_kb([("📖 Ещё история", "sy:home")]))


async def review(message: Message, uid: int, st: dict, data: dict) -> None:
    ctx = await asyncio.to_thread(scenario.build_ctx, db.get_user_channel(uid))
    ctx["channel"] = db.get_user_channel(uid)
    res = story.parse(data, ctx)
    if res["errors"]:
        lines = "\n".join(f"• {e}" for e in res["errors"][:10])
        return await message.answer(f"❌ В истории есть ошибки, ролик не собран:\n{lines}\n\n"
                                    "Отправьте этот список нейросети с просьбой исправить JSON и пришлите результат снова.")
    st["lists"]["story_pending"] = res
    state.save(uid, st)
    await message.answer(story.summary(res, cfg_of(st)),
                         reply_markup=_kb([("🚀 Собрать", "sy:go"), ("✖ Отмена", "sy:cancel")], [("🎨 Оформление", "sy:set")]))


def _story_json(raw: str) -> dict | None:
    if not scenario_looks_like_json(raw):
        return None
    try:
        data = scenario.extract_json(raw)
    except ValueError:
        return None
    return data if story.is_story(data) else None


def scenario_looks_like_json(text: str) -> bool:
    t = (text or "").lstrip()
    return t.startswith("{") or t.startswith("```")


async def handle_text(message: Message, uid: int, st: dict) -> bool:
    """True, если сообщение забрал режим историй: JSON истории, ссылка или название папки залипательных."""
    text = (message.text or "").strip()
    aw = st.get("awaiting")
    if aw == "sat_url":
        folder = st["lists"].get("sat_target") or ""
        dest = story.sat_dir() / folder if folder else story.sat_dir()
        st["awaiting"] = None
        state.save(uid, st)
        await message.answer("⏳ Скачиваю, это может занять несколько минут…")
        try:
            res = await asyncio.to_thread(sources.add_url, text, dest, story.sat_remote(folder))
        except Exception as e:  # noqa: BLE001
            await message.answer(f"Не удалось скачать: {e}", reply_markup=_kb([("🧘 Залипательные видео", "sy:sat")]))
            return True
        drive = "✅ загружено на Диск" if res["drive_ok"] else (f"⚠️ на Диск не загрузилось ({res['drive_msg'] or 'ошибка rclone'}): "
                                                               "при синхронизации файл может пропасть")
        await message.answer(f"Готово: {Path(res['path']).name}\n{drive}", reply_markup=_kb([("🧘 Залипательные видео", "sy:sat")]))
        return True
    if aw == "sat_folder":
        name = story.safe_folder(text)
        if not name:
            await message.answer("Название пустое. Пришлите, например, parkour.")
            return True
        st["awaiting"] = None
        state.save(uid, st)
        (story.sat_dir() / name).mkdir(parents=True, exist_ok=True)
        ok, msg = await asyncio.to_thread(ideas._rc, "mkdir", ideas._remote(story.sat_remote(name)), timeout=120)
        note = "" if ok else f"\n⚠️ На Диске папка не создалась ({msg.strip()[-120:]}): создайте её вручную, иначе синхронизация её уберёт."
        await message.answer(f"Папка «{name}» создана.{note}")
        text, kb = sat_screen(st)
        await message.answer(text, reply_markup=kb)
        return True
    data = _story_json(text)
    if data is None:
        return False
    await review(message, uid, st, data)
    return True


async def handle_media(message: Message, bot: Bot, uid: int, st: dict) -> bool:
    """Видео в папку залипательных (когда бот его ждёт) и JSON истории файлом."""
    doc = message.document
    if doc and ((doc.file_name or "").lower().endswith(".json") or doc.mime_type == "application/json") and (doc.file_size or 0) <= 512 * 1024:
        from io import BytesIO
        buf = BytesIO()
        await bot.download(doc, destination=buf)
        try:
            data = _story_json(buf.getvalue().decode("utf-8-sig"))
        except UnicodeDecodeError:
            data = None
        if data is None:
            return False   # обычный сценарий: пусть разбирают другие режимы (файл скачается ещё раз, он маленький)
        await review(message, uid, st, data)
        return True
    if st.get("awaiting") != "sat_upload":
        return False
    obj = message.video or message.animation or doc
    if obj is None:
        return False
    name = getattr(obj, "file_name", None) or f"sat_{message.message_id}.mp4"
    if doc and Path(name).suffix.lower() not in VIDEO_EXT and not (doc.mime_type or "").startswith("video/"):
        await message.answer("Это не видео. Пришлите mp4/mov/mkv/webm.")
        return True
    if not Path(name).suffix:
        name += ".mp4"
    folder = st["lists"].get("sat_target") or ""
    dest_dir = story.sat_dir() / folder if folder else story.sat_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = ideas.unique_file(dest_dir, name)
    try:
        await bot.download(obj, destination=dest)
    except TelegramBadRequest as e:
        dest.unlink(missing_ok=True)
        if "too big" in str(e).lower():
            await message.answer("Файл больше 20 МБ, Telegram не отдаёт его боту. Положите его на Диск в папку "
                                 f"_clips/{story.SAT_NAME}/{folder} и нажмите «🔄 С Диска».",
                                 reply_markup=_kb([("🧘 Залипательные видео", "sy:sat")]))
            return True
        raise
    ok, msg = await asyncio.to_thread(sources.push_to_drive, dest, story.sat_remote(folder))
    n = story.sat_folders().get(folder, 0)
    drive = "" if ok else f"\n⚠️ На Диск не загрузилось ({msg.strip()[-120:] or 'ошибка rclone'}): при синхронизации файл может пропасть."
    await message.answer(f"Сохранено: {dest.name} → {folder or 'корень'} (всего там {n}).{drive}",
                         reply_markup=_kb([("✅ Готово", "sy:sat")]))
    return True
