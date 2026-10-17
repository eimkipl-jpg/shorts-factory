"""Материалы: поиск клипов в Pexels/Pixabay, добавление по ссылке и загрузка логотипа канала.

Кнопки: src:ask (поиск), src:more (ещё результаты), src:add:<i> (добавить), src:url (по ссылке).
Состояние ожидания («жду запрос», «жду ссылку», «жду логотип») хранится в состоянии мастера (gateway.state).
"""
import asyncio
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config
from core import sources
from gateway import state

log = logging.getLogger("materials")
router = Router()

PER_PAGE = 6


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _ok(uid: int) -> bool:
    return uid in config.ALLOWED_USER_IDS


ASK_TEXT = (
    "🔎 Что ищем? Пришлите запрос (стоки лучше понимают английский): например neon city night.\n"
    "Чтобы сразу назвать тему, в которую сохранятся клипы: «neon city night | city»."
)
URL_TEXT = (
    "➕ Пришлите ссылку на видео: оно скачается в «Мои видео» (_own) со звуком и попадёт на Диск.\n"
    "Используйте только свои ролики или материалы, на которые у вас есть права."
)


@router.callback_query(F.data.startswith("src:"))
async def on_src(cb: CallbackQuery):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    parts = cb.data.split(":")
    act = parts[1]
    st = state.load(uid)
    if act == "ask":
        st["awaiting"] = "search"
        state.save(uid, st)
        await cb.message.answer(ASK_TEXT)
    elif act == "url":
        st["awaiting"] = "url"
        state.save(uid, st)
        await cb.message.answer(URL_TEXT)
    elif act == "more":
        q = st["lists"].get("found_q")
        if not q:
            return await cb.answer("Сначала задайте запрос", show_alert=True)
        await cb.answer("Ищу…")
        return await _search(cb.message, uid, st, q, st["lists"].get("found_theme"), st["lists"].get("found_page", 1) + 1)
    elif act == "add":
        found = st["lists"].get("found", [])
        i = int(parts[2])
        if not (0 <= i < len(found)):
            return await cb.answer("Результаты устарели, повторите поиск", show_alert=True)
        await cb.answer("Скачиваю…")
        theme = st["lists"].get("found_theme") or sources.slug(st["lists"].get("found_q", ""))
        try:
            res = await asyncio.to_thread(sources.add_item, found[i], theme)
        except Exception as e:  # noqa: BLE001
            return await cb.message.answer(f"Не удалось скачать: {e}")
        a = res["asset"]
        drive = "✅ загружено на Диск" if res["drive_ok"] else f"⚠️ на Диск не загрузилось ({res['drive_msg'] or 'ошибка rclone'}) - при синхронизации файл может пропасть"
        head = "Уже было в библиотеке" if res["existed"] else "Добавлено"
        await cb.message.answer(
            f"{head}: {Path(res['path']).name} → тема «{theme}»" + (f" · #{a['id']}" if a else "") + f"\n{drive}\n"
            f"Источник: {found[i].get('page') or '-'} · {sources.LICENSES.get(found[i]['provider'], '')}"
        )
        return
    await cb.answer()


async def _search(message: Message, uid: int, st: dict, query: str, theme: str | None, page: int = 1) -> None:
    try:
        items, errors = await asyncio.to_thread(sources.search, query, page, PER_PAGE)
    except sources.NoKeys as e:
        st["awaiting"] = None
        state.save(uid, st)
        return await message.answer(f"Поиск недоступен: {e}")
    except Exception as e:  # noqa: BLE001
        return await message.answer(f"Поиск не удался: {e}")
    st["awaiting"] = None
    st["lists"].update({"found": items, "found_q": query, "found_theme": theme, "found_page": page})
    state.save(uid, st)
    if not items:
        note = ("\n" + "; ".join(errors)) if errors else ""
        return await message.answer(f"По запросу «{query}» ничего не нашлось.{note}")
    target = theme or sources.slug(query)
    await message.answer(f"Найдено по «{query}» (страница {page}). Клипы сохранятся в тему «{target}».")
    for i, it in enumerate(items):
        dur = f"{it['duration']} с" if it.get("duration") else "?"
        cap = f"{it['provider'].capitalize()} · {dur} · {it.get('w')}×{it.get('h')} · {it.get('author') or '-'}"
        kb = _kb([[_btn("➕ Добавить в библиотеку", f"src:add:{i}")]])
        try:
            if not it.get("thumb"):
                raise ValueError("нет превью")
            await message.answer_photo(it["thumb"], caption=cap, reply_markup=kb)
        except (TelegramBadRequest, ValueError):
            await message.answer(f"🎞 {cap}\n{it.get('page') or ''}", reply_markup=kb)
    if errors:
        await message.answer("Часть источников не ответила: " + "; ".join(errors))
    await message.answer("Дальше:", reply_markup=_kb([[_btn("🔎 Ещё результаты", "src:more"), _btn("⬅ Меню", "m:back")]]))


async def handle_text(message: Message, uid: int, st: dict) -> bool:
    aw = st.get("awaiting")
    text = (message.text or "").strip()
    if aw == "search":
        query, _, theme = text.partition("|")
        query = query.strip()
        if len(query) < 2:
            await message.answer("Запрос слишком короткий.")
            return True
        await message.answer(f"Ищу «{query}»…")
        await _search(message, uid, st, query, sources.slug(theme) if theme.strip() else None)
        return True
    if aw == "url":
        st["awaiting"] = None
        state.save(uid, st)
        await message.answer("⏳ Скачиваю, это может занять несколько минут…")
        try:
            res = await asyncio.to_thread(sources.add_url, text)
        except Exception as e:  # noqa: BLE001
            await message.answer(f"Не удалось скачать: {e}")
            return True
        a = res["asset"]
        drive = "✅ загружено на Диск" if res["drive_ok"] else f"⚠️ на Диск не загрузилось ({res['drive_msg'] or 'ошибка rclone'})"
        await message.answer(f"Готово: {Path(res['path']).name}" + (f" · #{a['id']}" if a else "") + f"\n{drive}\nТеперь доступно в 1️⃣ Видеоряд → «Моё видео».")
        return True
    return False


async def handle_logo(message: Message, bot: Bot, uid: int, st: dict) -> bool:
    """Сохраняет присланную картинку как логотип текущего канала."""
    if st.get("awaiting") != "logo":
        return False
    if message.photo:
        obj, ext = message.photo[-1], ".jpg"
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        obj = message.document
        ext = Path(message.document.file_name or "logo.png").suffix.lower() or ".png"
    else:
        await message.answer("Пришлите картинку: лучше PNG с прозрачным фоном, отправленный как файл.")
        return True
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    channel = st["channel"]
    for old in config.ASSETS_DIR.glob(f"logo_{channel}.*"):
        old.unlink(missing_ok=True)
    dest = config.ASSETS_DIR / f"logo_{channel}{ext}"
    await bot.download(obj, destination=dest)
    st["awaiting"] = None
    if st["look"]["logo"] == "off":
        st["look"]["logo"] = "tr"
    state.save(uid, st)
    await message.answer(f"Логотип канала «{channel}» сохранён. Положение: справа сверху (меняется в 5️⃣ Стиль → Логотип).")
    return True
