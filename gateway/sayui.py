"""Словарь произношения в боте.

/say слово = как читать   - добавить своё правило («слово*» - с любым окончанием: рахим* = рахим, рахима, рахиму)
/unsay слово               - удалить правило
/saylist                   - свои правила и сколько слов во встроенном словаре
/saytest текст             - показать, как текст будет прочитан, и прислать пробную озвучку
Подмена действует только на голос; субтитры остаются как в сценарии.
"""
import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile, Message

from common import config
from core import pronounce, tts
from core import pronounce_data as D

log = logging.getLogger("sayui")
router = Router()

HELP = (
    "🗣 Словарь произношения (русский и английский)\n"
    "/say слово = как читать - добавить правило. Пример: /say Рахим* = Рахи́м\n"
    "Правило только для английской озвучки: /say en: gif = jif  (только для русской: ru:)\n"
    "«*» в конце слова значит любое окончание. Ударение можно поставить значком ́ после гласной.\n"
    "/unsay слово - удалить правило\n/saylist - мои правила\n"
    "/saytest текст - как прочитается + пробная озвучка"
)


def _ok(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in config.ALLOWED_USER_IDS


@router.message(Command("say"))
async def cmd_say(message: Message, command: CommandObject):
    if not _ok(message):
        return
    arg = (command.args or "").strip()
    sep = next((s for s in ("=", "→", "->") if s in arg), None)
    if not arg or sep is None:
        return await message.answer(HELP)
    word, reading = (x.strip() for x in arg.split(sep, 1))
    if not word or not reading:
        return await message.answer(HELP)
    key = pronounce.add_user(word, reading)
    await message.answer(f"✅ «{key}» → «{reading}»\nПроверить: /saytest {pronounce._split_lang(word)[1]}")


@router.message(Command("unsay"))
async def cmd_unsay(message: Message, command: CommandObject):
    if not _ok(message):
        return
    word = (command.args or "").strip()
    if not word:
        return await message.answer("Формат: /unsay слово")
    await message.answer("🗑 Удалено." if pronounce.remove_user(word) else "Такого правила нет. Список: /saylist")


@router.message(Command("saylist"))
async def cmd_saylist(message: Message):
    if not _ok(message):
        return
    user = pronounce.load_user()
    from core import pronounce_en_data as E
    base = len(D.WORDS) + len(D.LATIN_ABBR) + len(D.ABBR) + len(D.PHRASES)
    base_en = len(E.WORDS) + len(E.SPELLED) + len(E.PHRASES)
    lines = [f"{k} → {v}" for k, v in sorted(user.items())]
    body = "\n".join(lines) if lines else "Своих правил пока нет."
    await message.answer(f"🗣 Мои правила ({len(user)}):\n{body}\n\nВстроено: русский ~{base}, английский ~{base_en} слов, сокращений и брендов, "
                         "плюс числа, даты, проценты, деньги, единицы, римские цифры.\n\n" + HELP)


@router.message(Command("saytest"))
async def cmd_saytest(message: Message, command: CommandObject):
    if not _ok(message):
        return
    text = (command.args or "").strip()
    if not text:
        return await message.answer("Формат: /saytest текст, например: /saytest В 2025 году iPhone 15 стоил 79 990 ₽, т.е. около 5% зарплаты в США")
    lang = pronounce.lang_of(text, None)
    voice = next((v for v in config.VOICES if v[:2].lower() == "en"), None) if lang == "en" else None
    spoken = pronounce.prepare(text, voice or config.TTS_VOICE)
    await message.answer(f"Так прочитается ({'английский' if lang == 'en' else 'русский'}):\n{spoken}")
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "say.mp3"
            await asyncio.to_thread(tts.synthesize, text, out, voice, None)
            await message.answer_audio(FSInputFile(out, filename="saytest.mp3"), caption="Пробная озвучка")
    except Exception as e:  # noqa: BLE001
        log.warning("saytest: %s", e)
        await message.answer(f"Текст подготовлен, но озвучить не вышло: {e}")
