"""Сценарий JSON в боте: приём (текст или файл), проверка, сводка с подтверждением, /prompt для нейросети.

Поток: нейросеть пишет JSON -> вы присылаете его боту -> бот показывает сводку и замечания -> «🚀 Собрать» -> задача в очереди.
Длинный JSON (больше 4096 символов) Telegram режет на части, поэтому присылайте его файлом .json.
"""
import asyncio
import json
import logging
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config, db, profile
from core import scenario, voicestyle
from gateway import state

log = logging.getLogger("scenario_ui")
router = Router()

HELP = (
    "📜 Сценарий из JSON\n\n"
    "1. Нажмите «📋 Промт для нейросети» — бот пришлёт готовый промт с вашими темами, музыкой и голосами.\n"
    "2. Вставьте промт в любую нейросеть, допишите тему ролика.\n"
    "3. Ответ (JSON) пришлите сюда текстом или файлом .json. Бот проверит его, покажет сводку и замечания.\n"
    "4. Нажмите «🚀 Собрать».\n\n"
    "Текст звучит «как нейросеть»? Выберите тон рассказчика в промте или нажмите «🪄 Очеловечить черновик»: "
    "бот пришлёт второй промт, который перепишет только фразы."
)

MENU = [("📋 Промт для нейросети", "sc:prompt")], [("🪄 Очеловечить черновик", "sc:polish")]


def _tone_kb(prefix: str) -> InlineKeyboardMarkup:
    rows = [[(name, f"{prefix}:{key}")] for key, (name, _) in voicestyle.TONES.items()]
    rows.append([("Без тона (только правила)", f"{prefix}:none")])
    return _kb(*rows)


def _kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])


def _ok(uid: int) -> bool:
    return uid in config.ALLOWED_USER_IDS


async def _send_long(message: Message, text: str, filename: str) -> None:
    if len(text) <= 3900:
        await message.answer(text)
    else:
        await message.answer_document(BufferedInputFile(text.encode("utf-8"), filename=filename),
                                      caption="Промт длинный, поэтому файлом: скопируйте текст целиком.")


async def send_prompt(message: Message, uid: int, tone: str | None = voicestyle.DEFAULT_TONE) -> None:
    ctx = await asyncio.to_thread(scenario.build_ctx, db.get_user_channel(uid))
    await _send_long(message, scenario.make_prompt(ctx, tone=tone), "prompt_for_ai.txt")
    await message.answer("Допишите в промте тему ролика и отправьте нейросети. Готовый JSON пришлите мне.")


async def send_polish(message: Message, tone: str | None) -> None:
    await _send_long(message, voicestyle.polish_prompt(tone), "polish_prompt.txt")
    await message.answer("Вставьте вместо <вставьте сюда JSON> черновик из нейросети, отправьте ей и пришлите мне результат.")


@router.message(Command("prompt", "scenario"))
async def cmd_prompt(message: Message):
    if message.from_user and _ok(message.from_user.id):
        await message.answer(HELP, reply_markup=_kb(*MENU))


@router.callback_query(F.data.startswith("sc:"))
async def on_sc(cb: CallbackQuery):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    act = cb.data.split(":")[1]
    if act == "help":
        await cb.message.answer(HELP, reply_markup=_kb(*MENU))
    elif act == "prompt":
        await cb.message.answer("Какой тон у рассказчика?", reply_markup=_tone_kb("sc:p"))
    elif act == "polish":
        await cb.message.answer("В каком тоне переписать?", reply_markup=_tone_kb("sc:q"))
    elif act in ("p", "q"):
        key = cb.data.split(":")[2]
        tone = key if key in voicestyle.TONES else None
        await cb.answer("Собираю промт…")
        if act == "p":
            return await send_prompt(cb.message, uid, tone)
        return await send_polish(cb.message, tone)
    elif act == "cancel":
        st = state.load(uid)
        st["lists"].pop("scenario", None)
        state.save(uid, st)
        await cb.message.answer("Сценарий отменён.")
    elif act == "go":
        st = state.load(uid)
        pending = st["lists"].pop("scenario", None)
        if not pending:
            return await cb.answer("Сценарий не найден, пришлите JSON заново", show_alert=True)
        state.save(uid, st)
        await cb.answer()
        await create_task(cb.message, pending)
        if pending.get("project"):   # сценарий из проекта банка идей
            from gateway import ideasui
            await ideasui.after_go(cb.message, uid, pending)
        return
    await cb.answer()


async def create_task(message: Message, res: dict) -> None:
    channel = res["channel"]
    task_id = db.create_task(channel, "recipe", message.chat.id)
    task_dir = config.TASKS_DIR / f"task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)
    data = {"task_id": task_id, "channel": channel, "input_type": "recipe", **profile.for_task(channel),
            **res["meta"], "recipe": res["recipe"], "source": "scenario_json"}
    if res["meta"].get("voice") is None:
        data["voice"] = profile.get(channel)["voice"]
    (task_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    db.mark_ready(task_id)
    await message.answer(f"Принято: task_{task_id}, канал {channel}, сцен: {res['scenes']}. В очереди: {db.queue_size()}")


async def _review(message: Message, uid: int, raw: str) -> None:
    try:
        data = scenario.extract_json(raw)
    except ValueError as e:
        return await message.answer(f"❌ {e}\nПришлите исправленный JSON (или файл .json).")
    ctx = await asyncio.to_thread(scenario.build_ctx, db.get_user_channel(uid))
    res = scenario.parse(data, ctx)
    if res["errors"]:
        lines = "\n".join(f"• {e}" for e in res["errors"][:12])
        extra = "\n\n⚠️ Заодно:\n" + "\n".join(f"• {w}" for w in res["warnings"][:5]) if res["warnings"] else ""
        return await message.answer(
            f"❌ В сценарии есть ошибки, ролик не собран:\n{lines}{extra}\n\n"
            "Отправьте этот список нейросети с просьбой исправить JSON и пришлите результат снова.")
    st = state.load(uid)
    st["lists"]["scenario"] = res
    state.save(uid, st)
    await message.answer(scenario.summary(res), reply_markup=_kb([("🚀 Собрать", "sc:go"), ("✖ Отмена", "sc:cancel")]))


def looks_like_json(text: str) -> bool:
    t = text.lstrip()
    return t.startswith("{") or t.startswith("```")


async def handle_text(message: Message, uid: int) -> bool:
    if not looks_like_json(message.text or ""):
        return False
    await _review(message, uid, message.text)
    return True


async def handle_document(message: Message, bot: Bot, uid: int) -> bool:
    doc = message.document
    if not doc or not ((doc.file_name or "").lower().endswith(".json") or doc.mime_type == "application/json"):
        return False
    if (doc.file_size or 0) > 512 * 1024:
        await message.answer("Файл слишком большой для сценария (больше 512 КБ).")
        return True
    buf = BytesIO()
    await bot.download(doc, destination=buf)
    try:
        raw = buf.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        await message.answer("Файл не в кодировке UTF-8.")
        return True
    await _review(message, uid, raw)
    return True
