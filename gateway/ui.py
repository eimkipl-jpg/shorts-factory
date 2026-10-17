"""Общие помощники для экранов на inline-кнопках."""
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import CallbackQuery, Message

log = logging.getLogger("ui")


async def safe_answer(cb: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """Отвечает на нажатие, не падая, если оно устарело («query is too old»)."""
    try:
        await cb.answer(text, show_alert=show_alert)
    except TelegramAPIError:
        pass


async def safe_edit(message: Message, text: str, kb=None) -> None:
    """Обновляет экран на месте. Экран не изменился - молча ок.
    Сообщение нельзя отредактировать (фото, слишком старое, удалено) - присылает экран новым сообщением."""
    try:
        await message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        log.info("edit_text не удался (%s), шлю новым сообщением", str(e)[:80])
        await message.answer(text, reply_markup=kb)
