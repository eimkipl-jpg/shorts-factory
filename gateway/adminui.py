"""Админка бота для креаторов (патч 48): кто пользуется ботом, что делают, где ошибки.
Вход: /admin (или /stats) и кнопка «👑 Админка» в меню - видна только ADMIN_IDS. Карточка человека: /user <id>."""
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import adminstats as A
from common import config, creator, db
from gateway import state
from gateway.ui import safe_answer, safe_edit

log = logging.getLogger("adminui")
router = Router()

_BOT_NAME: dict = {}


def _kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows if r])


def _is_admin(uid: int) -> bool:
    return creator.is_admin(uid)


def home_screen(p: str = "today"):
    text = A.overview_text(p) + f"\n\n📥 Сейчас в очереди: {db.queue_size()}"
    per = [(("• " if k == p else "") + name, f"adm:h:{k}") for k, (name, _) in A.PERIODS.items()]
    return text[:4000], _kb(per[:2], per[2:], [("👥 Люди", "adm:us:seen:0"), ("🧯 Ошибки", "adm:err")],
                            [("📄 Выгрузка в Excel (CSV)", "adm:csv"), ("🔔 Уведомления", "adm:n")],
                            [("🔗 Ссылки с метками", "adm:lk"), ("🔄 Обновить", f"adm:h:{p}")], [("⬅ Меню", "cr:home")])


MODES = {"seen": "недавно были", "new": "новые", "top": "больше задач"}


def users_screen(mode: str = "seen", page: int = 0):
    per = 8
    rows, total = A.users_page(page, per, mode)
    pages = max(1, (total + per - 1) // per)
    L = [f"👥 Люди: {total} · {MODES.get(mode, '')} · стр. {page + 1}/{pages}", "(без админов; ✅ - сколько задач получили)", ""]
    btns = []
    for u in rows:
        L.append(f"• {A.user_label(u)}\n   был {A.ago(u.get('last_seen') or u['created'])} · задач {u['tasks']} (✅{u['done']})")
        btns.append([(f"👤 {(u.get('name') or str(u['user_id']))[:28]}", f"adm:u:{u['user_id']}")])
    if not rows:
        L.append("Пока никого.")
    nav = []
    if page > 0:
        nav.append(("◀", f"adm:us:{mode}:{page - 1}"))
    if page + 1 < pages:
        nav.append(("▶", f"adm:us:{mode}:{page + 1}"))
    modes = [(("• " if m == mode else "") + name, f"adm:us:{m}:0") for m, name in MODES.items()]
    return "\n".join(L)[:4000], _kb(*btns, nav, modes, [("⬅ Статистика", "adm:h")])


def user_screen(uid: int):
    u = creator.get(uid) if A.user_card(uid) else None
    rows = []
    if u is not None and uid not in config.ADMIN_IDS:
        rows.append([("✅ Разблокировать", f"adm:ub:{uid}") if u["banned"] else ("⛔ Заблокировать", f"adm:bn:{uid}"),
                     ("✉️ Написать", f"adm:msg:{uid}")])
        if not config.FREE_MODE:
            rows.append([("➕ 5 кредитов", f"adm:g:{uid}:5"), ("➕ 20 кредитов", f"adm:g:{uid}:20")])
    rows.append([("🔄 Обновить", f"adm:u:{uid}"), ("⬅ Люди", "adm:us:seen:0")])
    return A.user_text(uid), _kb(*rows)


def notify_screen():
    rows = [[(("🔔 " if A.notify_on(k) else "🔕 ") + name, f"adm:nt:{k}")] for k, name in A.NOTIFY_NAMES.items()]
    text = ("🔔 Уведомления админу\nНажмите, чтобы включить или выключить.\n\n"
            "• Новый пользователь - когда кто-то впервые нажал /start\n"
            "• Первая задача - человек впервые заказал ролик, нарезку и т.д.\n"
            "• Ошибка задачи - задача человека не получилась\n"
            f"• Ежедневный отчёт - итоги вчерашнего дня, в {config.REPORT_HOUR}:00 (UTC{config.REPORT_TZ:+g})")
    return text, _kb(*rows, [("⬅ Статистика", "adm:h")])


async def links_screen(bot: Bot):
    if "u" not in _BOT_NAME:
        try:
            _BOT_NAME["u"] = (await bot.get_me()).username
        except Exception:  # noqa: BLE001
            _BOT_NAME["u"] = "ваш_бот"
    b = _BOT_NAME["u"]
    text = ("🔗 Ссылки с метками - чтобы знать, откуда пришли люди\n\n"
            "Добавьте к ссылке на бота ?start= и любое слово (латиница, цифры, _ и -):\n"
            f"https://t.me/{b}?start=insta\nhttps://t.me/{b}?start=tiktok_bio\nhttps://t.me/{b}?start=blogger_ivan\n\n"
            "Каждый новый человек запоминается с меткой первой ссылки. Сколько пришло по каждой - в статистике "
            "(блок «Откуда пришли»), метка человека - в его карточке и в выгрузке CSV.")
    return text, _kb([("⬅ Статистика", "adm:h")])


@router.message(Command("admin", "stats"))
async def cmd_admin(message: Message):
    if not _is_admin(message.from_user.id):
        return
    text, kb = await asyncio.to_thread(home_screen, "today")
    await message.answer(text, reply_markup=kb)


@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject):
    if not _is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip().lstrip("@")
    if not arg:
        return await message.answer("Формат: /user <id> или /user @username")
    uid = int(arg) if arg.lstrip("-").isdigit() else _find_username(arg)
    if uid is None:
        return await message.answer("Не нашёл такого пользователя.")
    text, kb = await asyncio.to_thread(user_screen, uid)
    await message.answer(text, reply_markup=kb)


def _find_username(name: str):
    A.init()
    with db.connect() as c:
        row = c.execute("SELECT user_id FROM cr_users WHERE lower(username)=lower(?)", (name,)).fetchone()
    return row["user_id"] if row else None


@router.callback_query(F.data.startswith("adm:"))
async def on_adm(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    if not _is_admin(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    p = cb.data.split(":")
    act = p[1]
    if act == "csv":
        await cb.answer("Готовлю файл…")
        data = await asyncio.to_thread(A.users_csv)
        name = f"users_{A.day_key()}.csv"
        return await cb.message.answer_document(BufferedInputFile(data, name), caption="👥 Все пользователи (открывается в Excel)")
    await safe_answer(cb)
    if act == "h":
        return await safe_edit(cb.message, *await asyncio.to_thread(home_screen, p[2] if len(p) > 2 and p[2] in A.PERIODS else "today"))
    if act == "us":
        mode = p[2] if len(p) > 2 and p[2] in MODES else "seen"
        page = int(p[3]) if len(p) > 3 and p[3].isdigit() else 0
        return await safe_edit(cb.message, *await asyncio.to_thread(users_screen, mode, page))
    if act == "u" and len(p) > 2 and p[2].lstrip("-").isdigit():
        st = state.load(uid)
        if (st.get("awaiting") or "").startswith("adm_msg:"):      # «Отмена» после «Написать»
            st["awaiting"] = None
            state.save(uid, st)
        return await safe_edit(cb.message, *await asyncio.to_thread(user_screen, int(p[2])))
    if act in ("bn", "ub") and len(p) > 2 and p[2].lstrip("-").isdigit():
        target = int(p[2])
        if target not in config.ADMIN_IDS:
            await asyncio.to_thread(creator.set_ban, target, act == "bn")
        return await safe_edit(cb.message, *await asyncio.to_thread(user_screen, target))
    if act == "g" and len(p) > 3 and p[2].lstrip("-").isdigit() and p[3].isdigit():
        target = int(p[2])
        await asyncio.to_thread(creator.add_credits, target, int(p[3]), "grant")
        return await safe_edit(cb.message, *await asyncio.to_thread(user_screen, target))
    if act == "msg" and len(p) > 2 and p[2].lstrip("-").isdigit():
        st = state.load(uid)
        st["awaiting"] = f"adm_msg:{p[2]}"
        state.save(uid, st)
        return await cb.message.answer(f"✉️ Напишите одним сообщением текст для пользователя {p[2]} - отправлю от имени поддержки.",
                                       reply_markup=_kb([("✖ Отмена", f"adm:u:{p[2]}")]))
    if act == "err":
        return await safe_edit(cb.message, await asyncio.to_thread(A.errors_text), _kb([("⬅ Статистика", "adm:h")]))
    if act == "n":
        return await safe_edit(cb.message, *await asyncio.to_thread(notify_screen))
    if act == "nt" and len(p) > 2 and p[2] in A.NOTIFY_NAMES:
        await asyncio.to_thread(A.kv_set, p[2], "0" if A.notify_on(p[2]) else "1")
        return await safe_edit(cb.message, *await asyncio.to_thread(notify_screen))
    if act == "lk":
        return await safe_edit(cb.message, *await links_screen(bot))


async def handle_text(message: Message, uid: int, st: dict, bot: Bot) -> bool:
    """Текст админа для пользователя (после кнопки «✉️ Написать»)."""
    aw = st.get("awaiting") or ""
    if not aw.startswith("adm_msg:") or not _is_admin(uid):
        return False
    st["awaiting"] = None
    state.save(uid, st)
    target = int(aw.split(":", 1)[1])
    try:
        await bot.send_message(target, "💬 Сообщение поддержки:\n" + (message.text or "").strip()[:3500])
        await message.answer("✅ Отправлено.", reply_markup=_kb([("👤 Карточка", f"adm:u:{target}")]))
    except Exception as e:  # noqa: BLE001
        await message.answer(f"Не доставлено (человек мог заблокировать бота): {str(e)[:150]}")
    return True


async def report_loop(bot: Bot) -> None:
    """Раз в день в REPORT_HOUR присылает админам итоги вчерашнего дня; заодно чистит старые события."""
    await asyncio.sleep(20)
    while True:
        try:
            today = A.day_key()
            last = await asyncio.to_thread(A.kv_get, "report_day", "")
            if not last:                       # первый запуск после установки: вчерашних данных ещё нет
                await asyncio.to_thread(A.kv_set, "report_day", today)
            elif last != today and A.now_local().hour >= config.REPORT_HOUR:
                await asyncio.to_thread(A.kv_set, "report_day", today)
                await asyncio.to_thread(A.purge)
                if A.notify_on("n_daily"):
                    text = await asyncio.to_thread(A.daily_report_text)
                    for a in config.ADMIN_IDS:
                        try:
                            await bot.send_message(a, text, reply_markup=_kb([("👑 Подробнее", "adm:h:7")]))
                        except Exception:  # noqa: BLE001
                            pass
        except Exception:  # noqa: BLE001
            log.exception("report_loop")
        await asyncio.sleep(120)
