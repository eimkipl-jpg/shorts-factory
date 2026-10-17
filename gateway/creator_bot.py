"""Бот для креаторов (патч 39): отдельный токен, свой каталог данных, кредиты и бесплатный старт.
Запуск: ENV_FILE=.env.creator python -m gateway.creator_bot (юнит creator-gateway создаёт creator_setup.py).

Сценарий человека: /start → «Создать ролик» (умный монтаж) → промт → нейросеть → JSON → «Собрать» → видео прилетает в чат.
Платёж: Telegram Stars (XTR) - единственный способ Telegram для цифровых товаров."""
import asyncio
import logging
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message,
                           PreCheckoutQuery)

from common import adminstats, config, creator, db, library, profile
from gateway import adminui, creatorui, smartui, state
from gateway.ui import safe_answer, safe_edit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("creator_bot")

router = Router()
dp = Dispatcher()

def _kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows if r])


def menu_screen(uid: int):
    return creatorui.menu_screen(uid)


_HITS: dict[int, list[float]] = {}


def _flood(uid: int) -> bool:
    """Больше 15 действий за 10 секунд - игнорируем (защита от спама, бот не должен подвисать для остальных)."""
    now = time.time()
    hits = [t for t in _HITS.get(uid, []) if now - t < 10]
    hits.append(now)
    _HITS[uid] = hits
    if len(_HITS) > 5000:
        _HITS.clear()
    return len(hits) > 15


@dp.update.outer_middleware()
async def _guard(handler, event, data):
    """Только личные чаты; заблокированным - отказ; от спама - молчаливая защита."""
    msg = getattr(event, "message", None) or getattr(getattr(event, "callback_query", None), "message", None)
    user = data.get("event_from_user")
    if user is not None and not creator.is_admin(user.id) and _flood(user.id):
        return None
    if msg is not None and msg.chat.type != "private":
        return None
    if user is not None:
        ev, detail = _event_of(event)
        try:
            u, new = await asyncio.to_thread(adminstats.touch, user.id, user.full_name or "", user.username or "", ev, detail)
        except Exception:  # noqa: BLE001 - статистика не должна ломать бота
            log.exception("touch")
            u, new = creator.get(user.id, user.full_name or ""), False
        if new:
            if ev == "start" and detail:
                adminstats.set_src(user.id, detail)
                u["src"] = detail
            adminstats.on_new_user(u)
        if u["banned"]:
            return None
    return await handler(event, data)


def _event_of(event) -> tuple[str, str]:
    """Что сделал человек - для админ-статистики (без содержимого сообщений)."""
    cq = getattr(event, "callback_query", None)
    if cq is not None:
        d = cq.data or ""
        return ("", "") if d.startswith("adm:") else ("cb", d[:40])
    m = getattr(event, "message", None)
    if m is None:
        return "", ""
    if m.successful_payment:
        return "pay", str(m.successful_payment.total_amount)
    t = (m.text or "").strip()
    if t.startswith("/"):
        cmd, _, arg = t.partition(" ")
        cmd = cmd.split("@")[0].lower()
        return ("start", arg.strip()[:32]) if cmd == "/start" else ("cmd", cmd[:20])
    if m.document or m.video or m.photo:
        return "file", "video" if m.video else "photo" if m.photo else (m.document.file_name or "")[-20:]
    return ("text", "") if t else ("", "")


@router.message(Command("start", "menu"))
async def cmd_start(message: Message):
    text, kb = menu_screen(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "m:back")
async def cb_back(cb: CallbackQuery):
    await safe_answer(cb)
    await safe_edit(cb.message, *menu_screen(cb.from_user.id))


@router.message(Command("help"))
async def cmd_help(message: Message):
    adm = ("\n\n👑 Админ: /admin - статистика и люди, /user <id> - карточка человека, /grant, /ban, /unban, /reply, /refund"
           if creator.is_admin(message.from_user.id) else "")
    await message.answer("Команды:\n/start - главное меню\n/smart - умный монтаж\n/map 1850 Бангкок - проверить историческую карту\n"
                         + ("" if config.FREE_MODE else "/balance - баланс, /buy - купить ролики\n")
                         + "/terms - условия\n/support текст - написать администратору\n/delete_me - удалить мои данные\n\n"
                         "Схемы по шагам - в меню «📖 Инструкция»." + adm)


TERMS = ("Условия использования\n"
         "• Вы отвечаете за содержание сценариев и за права на материалы, которые публикуете.\n"
         "• Стоковые кадры и изображения подбираются из свободных источников; обязательные ссылки на авторов бот добавляет в описание - "
         "не удаляйте их.\n"
         "• Кредит списывается при постановке ролика в очередь и возвращается, если ролик не удалось собрать.\n"
         "• Оплаченные кредиты не сгорают. Возврат платежа Stars - по обращению в /support.\n"
         "• Ролики хранятся на сервере только до отправки вам; сценарии и настройки - пока вы пользуетесь ботом.\n"
         "• Скачивание и нарезка - только для материалов, на которые у вас есть права; вы отвечаете за их дальнейшее использование.")


@router.message(Command("terms"))
async def cmd_terms(message: Message):
    try:
        text = (config.ROOT / "terms.txt").read_text("utf-8")
    except OSError:
        text = TERMS
    await message.answer(text[:4000])


@router.message(Command("balance"))
async def cmd_balance(message: Message):
    text, kb = buy_screen(message.from_user.id)
    await message.answer(text, reply_markup=kb)


def buy_screen(uid: int):
    if config.FREE_MODE:
        return ("🎁 Сейчас всё бесплатно: " + creatorui.limits_text() + ". Платные тарифы появятся позже.",
                _kb([("⬅ Меню", "m:back")]))
    u = creator.get(uid)
    lines = [f"💰 Баланс: бесплатных {creator.free_left(u)}, кредитов {u['credits']}", "1 кредит = 1 ролик без водяного знака.", ""]
    rows = []
    for n, stars in config.CREATOR_PACKS:
        lines.append(f"• {n} роликов - {stars} ⭐")
        rows.append([(f"{n} роликов · {stars} ⭐", f"buy:{n}")])
    rows.append([("⬅ Меню", "m:back")])
    lines.append("\n⭐ Stars покупаются в Telegram (Настройки → Мои звёзды) картой или через App Store / Google Play.")
    return "\n".join(lines), _kb(*rows)


@router.message(Command("buy"))
async def cmd_buy(message: Message):
    text, kb = buy_screen(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(cb: CallbackQuery, bot: Bot):
    await safe_answer(cb)
    arg = cb.data.split(":", 1)[1]
    if arg == "menu" or config.FREE_MODE:
        return await safe_edit(cb.message, *buy_screen(cb.from_user.id))
    try:
        n = int(arg)
    except ValueError:
        return
    stars = creator.pack_price(n)
    if not stars:
        return
    await bot.send_invoice(chat_id=cb.message.chat.id, title=f"{n} роликов", description=f"Пакет из {n} роликов без водяного знака",
                           payload=f"pack:{n}", currency="XTR", prices=[LabeledPrice(label=f"{n} роликов", amount=stars)],
                           provider_token="")


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    ok = False
    try:
        n = int(q.invoice_payload.split(":", 1)[1])
        ok = q.currency == "XTR" and creator.pack_price(n) == q.total_amount
    except (ValueError, IndexError):
        pass
    await q.answer(ok=ok, error_message=None if ok else "Пакет недоступен, откройте /buy заново")


@router.message(F.successful_payment)
async def paid(message: Message):
    sp = message.successful_payment
    try:
        n = int(sp.invoice_payload.split(":", 1)[1])
    except (ValueError, IndexError):
        return log.error("платёж с неизвестным payload: %s", sp.invoice_payload)
    first = creator.add_credits(message.from_user.id, n, "buy", sp.telegram_payment_charge_id)
    log.info("оплата: user=%s пакет=%s сумма=%s %s charge=%s новый=%s", message.from_user.id, n, sp.total_amount, sp.currency,
             sp.telegram_payment_charge_id, first)
    u = creator.get(message.from_user.id)
    await message.answer(f"✅ Оплата получена: +{n} роликов. Баланс: {u['credits']}.", reply_markup=_kb([("🎞 Создать ролик", "sm:home")]))


# ---------- администратор ----------

def _admin(message: Message) -> bool:
    return bool(message.from_user) and creator.is_admin(message.from_user.id)


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject):
    if not _admin(message):
        return
    try:
        uid, n = [int(x) for x in (command.args or "").split()[:2]]
    except ValueError:
        return await message.answer("Формат: /grant <user_id> <сколько>")
    creator.add_credits(uid, n, "grant")
    await message.answer(f"Начислено {n} → {uid}. Баланс: {creator.get(uid)['credits']}")


@router.message(Command("ban", "unban"))
async def cmd_ban(message: Message, command: CommandObject):
    if not _admin(message):
        return
    try:
        uid = int((command.args or "").split()[0])
    except (ValueError, IndexError):
        return await message.answer("Формат: /ban <user_id>  или  /unban <user_id>")
    creator.set_ban(uid, command.command == "ban")
    await message.answer(("Заблокирован " if command.command == "ban" else "Разблокирован ") + str(uid))


@router.message(Command("refund"))
async def cmd_refund(message: Message, bot: Bot, command: CommandObject):
    """Возврат Stars по платежу: /refund <id платежа из логов или из чека>. Деньги возвращаются человеку, кредиты за этот платёж снимаются."""
    if not _admin(message):
        return
    charge = (command.args or "").strip()
    if not charge:
        return await message.answer("Формат: /refund <telegram_payment_charge_id>")
    if not creator.payment_known(charge):
        return await message.answer("Платёж не найден в журнале.")
    try:
        uid = next(iter([r for r in [creator.revoke_payment(charge)] if r]), None)
    except Exception:  # noqa: BLE001
        uid = None
    if uid is None:
        return await message.answer("Этот платёж уже возвращали.")
    user_id, taken = uid
    try:
        await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge)
    except Exception as e:  # noqa: BLE001
        log.exception("refund")
        return await message.answer(f"❌ Telegram не вернул Stars: {str(e)[:200]}\nКредиты уже сняты ({taken}); начислите обратно /grant {user_id} {taken}, если возврат нужно отменить.")
    await message.answer(f"✅ Возврат выполнен: пользователь {user_id}, снято кредитов {taken}.")
    try:
        await bot.send_message(user_id, "↩️ Платёж Stars возвращён, кредиты за него сняты.")
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("delete_me"))
async def cmd_delete_me(message: Message):
    await message.answer("Удалить ваши сценарии, настройки и имя из бота? Баланс и история платежей остаются обезличенно (учёт). "
                         "Действие необратимо.", reply_markup=_kb([("🗑 Да, удалить", "cr:del"), ("Отмена", "m:back")]))


@router.callback_query(F.data == "cr:del")
async def cb_delete_me(cb: CallbackQuery):
    await safe_answer(cb)
    await asyncio.to_thread(creator.delete_user_data, cb.from_user.id)
    await cb.message.answer("🗑 Данные удалены. Если снова нажмёте /start - начнёте с чистых настроек.")


@router.message(Command("support"))
async def cmd_support_msg(message: Message, bot: Bot, command: CommandObject):
    if not command.args:
        st = state.load(message.from_user.id)
        st["awaiting"] = "cr_support"
        state.save(message.from_user.id, st)
        return await message.answer("🆘 Напишите одним сообщением, что случилось - я передам администратору.",
                                    reply_markup=_kb([("✖ Отмена", "cr:home")]))
    await _to_admins(message, bot, command.args)


async def _to_admins(message: Message, bot: Bot, text: str) -> None:
    sent = 0
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, f"🆘 Поддержка: от {message.from_user.id} ({message.from_user.full_name}):\n{text[:1500]}\n\n"
                                          f"Ответить: /reply {message.from_user.id} текст")
            sent += 1
        except Exception:  # noqa: BLE001
            pass
    await message.answer("✅ Передано администратору. Ответ придёт в этот чат." if sent else
                         "Не удалось передать сообщение, попробуйте позже.")


@router.message(Command("reply"))
async def cmd_reply(message: Message, bot: Bot, command: CommandObject):
    """Ответ админа пользователю: /reply <id> текст."""
    if not _admin(message):
        return
    uid, _, text = (command.args or "").partition(" ")
    if not uid.lstrip("-").isdigit() or not text.strip():
        return await message.answer("Формат: /reply <id> текст ответа")
    try:
        await bot.send_message(int(uid), "💬 Ответ поддержки:\n" + text.strip()[:3500])
        await message.answer("Отправлено.")
    except Exception as e:  # noqa: BLE001
        await message.answer(f"Не доставлено: {str(e)[:200]}")


# ---------- сценарий и файлы ----------

@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, bot: Bot):
    uid = message.from_user.id
    st = state.load(uid)
    if st.get("awaiting") == "cr_support":
        st["awaiting"] = None
        state.save(uid, st)
        return await _to_admins(message, bot, message.text or "")
    if await adminui.handle_text(message, uid, st, bot):
        return
    if await creatorui.handle_text(message, uid, st, bot):
        return
    st = state.load(uid)
    if await smartui.handle_text(message, uid, st):
        return
    await message.answer("Не понял сообщение. Пришлите ссылку на видео, ответ нейросети (JSON) или откройте меню.",
                         reply_markup=_kb([("🏠 Меню", "cr:home"), ("📖 Инструкция", "cr:guide")]))


@router.message(F.document | F.video | F.photo)
async def on_doc(message: Message, bot: Bot):
    uid = message.from_user.id
    st = state.load(uid)
    if await creatorui.handle_media(message, bot, uid, st):
        return
    if message.document and await smartui.handle_media(message, bot, uid, st):
        return
    if message.photo:
        return await message.answer("Картинки принимаю как логотип в «🏷 Бренд-профиль».", reply_markup=_kb([("🏷 Бренд-профиль", "cr:br")]))
    await message.answer("Видео принимаю в разделе «✂️ Нарезка», сценарий - файлом .json (до 512 КБ).",
                         reply_markup=_kb([("✂️ Нарезка", "cr:cut"), ("🏠 Меню", "cr:home")]))


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env.creator")
    if not config.CREATOR:
        raise SystemExit("CREATOR_MODE=1 не задан: этот запуск предназначен только для экземпляра креаторов")
    db.init()
    library.init()
    profile.init()
    creator.init()
    adminstats.init()
    dp.include_router(adminui.router)
    dp.include_router(router)
    dp.include_router(creatorui.router)
    dp.include_router(smartui.router)
    session = None
    if config.TELEGRAM_API_URL:
        session = AiohttpSession(api=TelegramAPIServer.from_base(config.TELEGRAM_API_URL, is_local=True))
    bot = Bot(config.BOT_TOKEN, session=session)
    log.info("Creator gateway started")
    asyncio.create_task(adminui.report_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
