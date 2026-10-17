"""Задачник в боте: недельный план по каналам, напоминания за 2 часа, повторы, утренний отчёт.

Экран «Задачи» - список каналов. Канал открывает задачи на день (◀ ▶ - другой день), у каждой задачи есть карточка с инструментами:
текст, время, день, копия, повтор, другой канал, удаление. «🗓 Неделя» - обзор недели и ввод плана на неделю одним сообщением.
Время hh.mm или hh:mm у задачи: за 2 часа до него приходит напоминание «время · задача · канал».
Все даты считаются по часовому поясу задачника (⚙, по умолчанию Asia/Bangkok).
"""
import asyncio
import datetime as dt
import logging
import re
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config, db, planner
from core import ideas
from gateway import state
from gateway.ui import safe_answer, safe_edit

log = logging.getLogger("plannerui")
router = Router()

PAGE = 7
SHOW = 300               # символов задачи в списке; целиком - в карточке
QUICK = "pl_quick"       # текст сообщением = задача выбранного дня и канала
PLAN, EDIT, TIME, TPL = "pl_plan", "pl_edit", "pl_time", "pl_tpl"
TTL = 900                # секунд без действий, после которых обычный текст снова идёт в остальной бот


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ok(uid: int) -> bool:
    return uid in config.ALLOWED_USER_IDS


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _ctx(st: dict, uid: int) -> dict:
    c = st["lists"].get("pl")
    if not isinstance(c, dict):
        c = st["lists"]["pl"] = {}
    if not config.has_channel(c.get("ch") or ""):
        c["ch"] = db.get_user_channel(uid)
    c.setdefault("date", planner.today().isoformat())
    c.setdefault("page", 0)
    c.setdefault("wk", 0)
    return c


def _arm(st: dict, mode: str = QUICK, arg=None) -> None:
    st["awaiting"] = mode
    st["pl_arg"] = arg
    st["pl_ts"] = time.time()


def _disarm(st: dict) -> None:
    if str(st.get("awaiting") or "").startswith("pl_"):
        st["awaiting"] = None
        st["pl_arg"] = None


def leave(uid: int) -> None:
    """Выключает режим «текст = задача» (человек ушёл в другой раздел бота)."""
    st = state.load(uid)
    if str(st.get("awaiting") or "").startswith("pl_"):
        _disarm(st)
        state.save(uid, st)


def _day_of(c: dict) -> dt.date:
    try:
        return dt.date.fromisoformat(c["date"])
    except (KeyError, ValueError):
        return planner.today()


def _fmt_day(d: dt.date) -> str:
    return f"{planner.DOW_NAMES[d.weekday()]} {d.strftime('%d.%m')}"


def _full_day(d: dt.date) -> str:
    return f"{planner.DOW_FULL[d.weekday()]} {d.strftime('%d.%m')}"


def _first(title: str) -> str:
    return title.strip().split("\n")[0]


# ---------- каналы ----------

def home(uid: int, st: dict):
    planner.ensure_today()
    _ctx(st, uid)
    _disarm(st)
    day = planner.today()
    rows = []
    for i, ch in enumerate(config.CHANNELS):
        v = planner.day_view(ch, day)
        n = sum(1 for r in v["items"] if r["status"] == "open") + len(v["over"]) + len(v["videos"])
        rows.append([_b(f"{ch} · {n}" if n else ch, f"pl:c:{i}")])
    rows.append([_b("⚙️ Отчёт и время", "pl:set"), _b("⬅ Меню", "m:back")])
    return f"📋 Задачи · {_full_day(day)}\nВыберите канал.", _kb(rows)


# ---------- день канала ----------

def _line(n: int, r, day: dt.date) -> str:
    if r["kind"] == "video":
        age = planner.age_days(r, day)
        return f"{n}. 🎬 {_first(r['title'])}" + (f"  ⚠️{age}д" if age > 0 else "")
    mark = "✅" if r["status"] == "done" else "☐"
    pre = ("🔁 " if r["tpl_id"] else "") + (f"{r['at']} " if r["at"] else "")
    body = r["title"].strip()
    if len(body) > SHOW:
        body = body[:SHOW].rstrip() + "…"
    lines = body.split("\n")
    tail = ""
    if r["status"] == "open" and r["due"] and r["due"] < day.isoformat():
        try:
            tail = f"  ⚠️ {_fmt_day(dt.date.fromisoformat(r['due']))}"
        except ValueError:
            pass
    out = f"{n}. {mark} {pre}{lines[0]}{tail}"
    for ln in lines[1:]:
        out += ("\n    " + ln) if ln.strip() else "\n"
    return out


def channel_screen(uid: int, st: dict, note: str = ""):
    planner.ensure_today()
    c = _ctx(st, uid)
    _arm(st)
    ch, day, today = c["ch"], _day_of(c), planner.today()
    v = planner.day_view(ch, day)
    flat: list[tuple[str, object]] = []
    for title, rows in (("Просрочено", v["over"]), ("", v["items"]), ("Без даты", v["nodate"]), ("На загрузку", v["videos"])):
        flat += [(title, r) for r in rows]
    pages = max((len(flat) - 1) // PAGE + 1, 1)
    page = min(max(int(c.get("page", 0)), 0), pages - 1)
    c["page"] = page
    chunk = flat[page * PAGE:(page + 1) * PAGE]
    when = ("сегодня" if day == today else "завтра" if day == today + dt.timedelta(days=1)
            else "вчера" if day == today - dt.timedelta(days=1) else "")
    head = f"{ch} · {_full_day(day)}" + (f" · {when}" if when else "")
    body, prev = [], None
    for i, (title, r) in enumerate(chunk):
        if title != prev:
            if title:
                body.append(("\n" if body else "") + title)
            elif prev:
                body.append("\n" + ("Сегодня" if day == today else _fmt_day(day)))
            prev = title
        body.append(_line(page * PAGE + i + 1, r, day))
    text = (note + "\n\n" if note else "") + head + "\n\n" + ("\n".join(body) if body else "Задач нет. Напишите задачу сообщением.")
    if len(text) > 4000:
        text = text[:3990] + "…"
    rows = []
    if chunk:
        rows.append([_b(("↩ " if r["status"] == "done" else "☑ ") + str(page * PAGE + i + 1), f"pl:t:{r['id']}") for i, (_, r) in enumerate(chunk)])
        rows.append([_b("⚙ " + str(page * PAGE + i + 1), f"pl:i:{r['id']}") for i, (_, r) in enumerate(chunk)])
    if pages > 1:
        rows.append([_b("◀", f"pl:pg:{(page - 1) % pages}"), _b(f"{page + 1}/{pages}", "pl:noop"), _b("▶", f"pl:pg:{(page + 1) % pages}")])
    prv, nxt = day - dt.timedelta(days=1), day + dt.timedelta(days=1)
    rows.append([_b(f"◀ {_fmt_day(prv)}", f"pl:d:{prv.isoformat()}"), _b("Сегодня", f"pl:d:{today.isoformat()}"),
                 _b(f"{_fmt_day(nxt)} ▶", f"pl:d:{nxt.isoformat()}")])
    rows.append([_b("🗓 Неделя", "pl:w"), _b("🔁 Повторы", "pl:tpl"), _b("🎬 Видео", "pl:rdy")])
    if v["over"]:
        rows.append([_b(f"🧹 Убрать просроченные ({len(v['over'])})", "pl:co")])
    rows.append([_b("⬅ Каналы", "pl:home")])
    return text, _kb(rows)


# ---------- карточка задачи ----------

def _due_text(r, day: dt.date) -> str:
    if not r["due"]:
        return "без даты"
    try:
        d = dt.date.fromisoformat(r["due"])
    except ValueError:
        return r["due"]
    word = {0: "сегодня", 1: "завтра", -1: "вчера"}.get((d - day).days)
    return f"{_fmt_day(d)}" + (f" ({word})" if word else "")


def _card(uid: int, st: dict, item_id: int):
    r = planner.get(item_id)
    if r is None:
        return channel_screen(uid, st)
    _disarm(st)
    day = planner.today()
    tpl = ""
    if r["tpl_id"]:
        t = next((x for x in planner.templates(None) if x["id"] == r["tpl_id"]), None)
        tpl = f" · 🔁 {planner.repeat_label(t['repeat'])}" if t else ""
    mark = "🎬" if r["kind"] == "video" else ("✅" if r["status"] == "done" else "☐")
    meta = " · ".join(x for x in (r["at"], _due_text(r, day), r["channel"]) if x)
    text = f"{mark} {r['title']}\n\n{meta}{tpl}"
    i = r["id"]
    rows = [[_b("↩ Вернуть" if r["status"] == "done" else "✅ Выполнено", f"pl:t:{i}:c")]]
    if r["kind"] == "task":
        rows[0].append(_b("✏ Текст", f"pl:et:{i}"))
        rows.append([_b("🕒 Время", f"pl:tm:{i}"), _b("📅 День", f"pl:dp:{i}"), _b("⏭ Завтра", f"pl:due:{i}:1")])
        rows.append([_b("📋 Копия", f"pl:cp:{i}"), _b("🔁 Повтор", f"pl:rpm:{i}"), _b("📺 Канал", f"pl:mv:{i}")])
    else:
        rows.append([_b("📺 Канал", f"pl:mv:{i}")])
    rows.append([_b("🗑 Удалить", f"pl:del:{i}"), _b("⬅ К списку", "pl:back")])
    return text, _kb(rows)


def _date_picker(title: str, target: str, back: str, extra: list | None = None):
    """Даты на две недели вперёд. target - префикс callback, к которому добавляется дата ГГГГ-ММ-ДД."""
    day = planner.today()
    rows, row = [], []
    for n in range(14):
        d = day + dt.timedelta(days=n)
        row.append(_b("сегодня" if n == 0 else "завтра" if n == 1 else _fmt_day(d), f"{target}{d.isoformat()}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows += (extra or [])
    rows.append([_b("⬅ Назад", back)])
    return title, _kb(rows)


def _time_screen(r):
    i = r["id"]
    opts = ["09:00", "10:00", "12:00", "15:00", "18:00", "21:00"]
    rows = [[_b(t, f"pl:tmv:{i}:{t.replace(':', '')}") for t in opts[:3]], [_b(t, f"pl:tmv:{i}:{t.replace(':', '')}") for t in opts[3:]],
            [_b("Без времени", f"pl:tmv:{i}:x"), _b("⬅ Назад", f"pl:i:{i}")]]
    return (f"🕒 Время · {_first(r['title'])}\nВыберите кнопкой или напишите сообщением, например 14.30\n"
            "За 2 часа до времени придёт напоминание."), _kb(rows)


def _repeat_menu(r):
    i = r["id"]
    rows = [[_b("Каждый день", f"pl:rp:{i}:daily"), _b("По будням", f"pl:rp:{i}:weekdays")]]
    if r["due"]:
        try:
            dow = dt.date.fromisoformat(r["due"]).weekday()
            rows.append([_b(f"Каждый {planner.DOW_NAMES[dow]}", f"pl:rp:{i}:dow:{dow}")])
        except ValueError:
            pass
    if r["tpl_id"]:
        rows.append([_b("Не повторять", f"pl:rpx:{i}")])
    rows.append([_b("⬅ Назад", f"pl:i:{i}")])
    return f"🔁 Повтор · {_first(r['title'])}", _kb(rows)


# ---------- неделя и план ----------

def _week_start(c: dict) -> dt.date:
    return planner.week_start(planner.today()) + dt.timedelta(days=7 * int(c.get("wk", 0)))


def week_screen(uid: int, st: dict, note: str = ""):
    c = _ctx(st, uid)
    _disarm(st)
    start = _week_start(c)
    today = planner.today()
    rows_by_day = planner.week_rows(c["ch"], start)
    end = start + dt.timedelta(days=6)
    lines = [f"{c['ch']} · {start.strftime('%d.%m')} – {end.strftime('%d.%m')}"]
    for d, rows in rows_by_day.items():
        lines.append(f"\n{_fmt_day(d)}" + ("  ← сегодня" if d == today else ""))
        if not rows:
            lines.append("  —")
        for r in rows:
            lines.append(f"  {'✅' if r['status'] == 'done' else '☐'} {(r['at'] + ' ') if r['at'] else ''}{_short(_first(r['title']), 60)}")
    text = (note + "\n\n" if note else "") + "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "…"
    days = [_b(planner.DOW_NAMES[k] + ("•" if start + dt.timedelta(days=k) == today else ""),
               f"pl:d:{(start + dt.timedelta(days=k)).isoformat()}") for k in range(7)]
    kb = [days,
          [_b("◀ Неделя", f"pl:wn:{int(c['wk']) - 1}"), _b("Эта", "pl:wn:0"), _b("Неделя ▶", f"pl:wn:{int(c['wk']) + 1}")],
          [_b("📥 План на неделю", "pl:wp"), _b("📑 Копия на следующую", "pl:wc")],
          [_b("⬅ К дню", "pl:back")]]
    return text, _kb(kb)


def _plan_prompt(c: dict):
    start = _week_start(c)
    end = start + dt.timedelta(days=6)
    text = (f"📥 План на неделю {start.strftime('%d.%m')} – {end.strftime('%d.%m')} · {c['ch']}\n"
            "Пришлите одним сообщением:\n\n"
            "пн\n10.00 Снять 3 мема\nЗагрузить Short\n"
            "+ строка с плюсом дополняет задачу выше\n"
            "вт\n12.30 Монтаж\n"
            "#Другой_канал (строка с # меняет канал)\n"
            "ср\n...\n\n"
            "Строка с днём (пн, вторник, 14.10) начинает день, каждая следующая строка - задача. "
            "Время hh.mm - за 2 часа придёт напоминание.")
    return text, _kb([[_b("Отмена", "pl:w")]])


# ---------- повторы, видео, настройки ----------

def _tpl_screen(uid: int, st: dict):
    c = _ctx(st, uid)
    _disarm(st)
    tpls = planner.templates(c["ch"])
    lines = [f"🔁 Повторы · {c['ch']}", "Каждое утро из повтора появляется задача на сегодня."]
    rows = []
    for t in tpls:
        mark = "✅" if t["active"] else "⏸"
        rows.append([_b(_short(f"{mark} {(t['at'] + ' ') if t['at'] else ''}{t['title']} · {planner.repeat_label(t['repeat'])}", 46), f"pl:tt:{t['id']}"),
                     _b("🗑", f"pl:td:{t['id']}")])
    if tpls:
        lines.append("Строка - пауза или включение, 🗑 - удалить.")
    rows.append([_b("➕ Новый повтор", "pl:ta"), _b("📦 Стандартные", "pl:ts")])
    rows.append([_b("⬅ К списку", "pl:back")])
    return "\n".join(lines), _kb(rows)


def _ready_screen(uid: int, st: dict, note: str = ""):
    c = _ctx(st, uid)
    _disarm(st)
    rows_db = planner.ready_tasks(c["ch"], 10)
    text = (note + "\n\n" if note else "") + f"🎬 Готовые ролики · {c['ch']}\nНажмите на ролик - он встанет в список «На загрузку»."
    rows = []
    tz = planner.tz()
    for r in rows_db:
        when = dt.datetime.fromtimestamp(r["updated_at"], tz).strftime("%d.%m %H:%M")
        rows.append([_b(f"🎬 task_{r['id']} · {when}", f"pl:rd:{r['id']}")])
    if rows_db:
        rows.append([_b(f"➕ Добавить все ({len(rows_db)})", "pl:rda")])
    else:
        text += "\n\nНовых роликов нет. Ролик по номеру: /vid 63."
    rows.append([_b("⬅ К списку", "pl:back")])
    return text, _kb(rows)


def _settings_screen():
    h = planner.report_hour()
    text = (f"⚙️ Утренний отчёт: {'выключен' if h < 0 else f'{h:02d}:00'}\nЧасовой пояс: {planner.setting('tz', planner.DEFAULT_TZ)}\n"
            f"Сейчас там {planner.now().strftime('%H:%M, %d.%m')} - по этому времени считаются дни, задачи и напоминания.")
    rows = [[_b(f"⏰ Час отчёта: {'выкл' if h < 0 else f'{h:02d}:00'}", "pl:sh")],
            [_b(f"🌏 Пояс: {planner.setting('tz', planner.DEFAULT_TZ)}", "pl:stz")],
            [_b("📨 Отчёт сейчас", "pl:rep"), _b("⬅ Каналы", "pl:home")]]
    return text, _kb(rows)


async def send_report(bot: Bot, only: int | None = None) -> None:
    text, _ = await asyncio.to_thread(planner.report_text)
    kb = _kb([[_b("📋 Задачи", "pl:home")]])
    for uid in ([only] if only else sorted(config.ALLOWED_USER_IDS)):
        try:
            await bot.send_message(uid, text, reply_markup=kb)
        except Exception as e:  # noqa: BLE001
            log.warning("отчёт не отправлен %s: %s", uid, e)


async def send_reminders(bot: Bot) -> int:
    """Напоминание за 2 часа до времени задачи: «время · задача · канал»."""
    sent = 0
    for r, when in await asyncio.to_thread(planner.due_reminders):
        planner.mark_reminded(r["id"])
        left = int((when - planner.now()).total_seconds() // 60)
        tail = "" if left <= 0 else f" (через {left // 60} ч {left % 60:02d} мин)" if left >= 60 else f" (через {left} мин)"
        text = f"⏰ {r['at']} · {r['title']} · {r['channel']}{tail}"
        kb = _kb([[_b("✅ Готово", f"pl:rt:{r['id']}"), _b("⏭ Завтра", f"pl:rm:{r['id']}")]])
        for uid in sorted(config.ALLOWED_USER_IDS):
            try:
                await bot.send_message(uid, text, reply_markup=kb)
                sent += 1
            except Exception as e:  # noqa: BLE001
                log.warning("напоминание не отправлено %s: %s", uid, e)
    return sent


async def report_loop(bot: Bot) -> None:
    """Раз в минуту: задачи из повторов, напоминания за 2 часа, утренний отчёт (один раз в сутки)."""
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(planner.ensure_today)
            await send_reminders(bot)
            hour = planner.report_hour()
            if hour >= 0:
                n = planner.now()
                sched = n.replace(hour=hour, minute=0, second=0, microsecond=0)
                today_s = n.date().isoformat()
                if sched <= n < sched + dt.timedelta(hours=6) and planner.setting("last_report") != today_s:
                    planner.set_setting("last_report", today_s)
                    await send_report(bot)
        except Exception:  # noqa: BLE001
            log.exception("цикл задачника")
        await asyncio.sleep(60)


# ---------- добавление ----------

def _split_tasks(text: str) -> list[str]:
    """Одно сообщение = одна задача (абзацы сохраняются). Несколько задач - разделите строкой «---»."""
    parts, cur = [], []
    for ln in text.splitlines():
        if ln.strip() in ("---", "===", "***"):
            parts.append("\n".join(cur))
            cur = []
        else:
            cur.append(ln)
    parts.append("\n".join(cur))
    return [p.strip() for p in parts if p.strip(" \n-•*")]


def _add_text(text: str, uid: int, c: dict, force_repeat: bool = False) -> list[dict]:
    """Задачи из сообщения. Время, срок, повтор и #канал ищутся в первой строке, остальное остаётся текстом задачи.
    Без срока задача ставится на день, открытый на экране."""
    shown = _day_of(c)
    channels = list(config.CHANNELS)
    made = []
    for chunk in _split_tasks(text):
        first, _, rest = chunk.partition("\n")
        if not first.strip(" -•*"):
            first, _, rest = rest.strip("\n").partition("\n")
        p = planner.parse_task(first, channels, shown)
        title = p["title"] + (("\n" + rest.rstrip()) if rest.strip() else "")
        ch = p["channel"] or c["ch"]
        rule = p["repeat"] or ("daily" if force_repeat else None)
        due = p["due"] or shown.isoformat()
        at = f"{p['at']} " if p["at"] else ""
        if rule:
            tpl = planner.add_template(ch, title, rule, p["due"] or planner.today().isoformat(), p["at"])
            made.append({"text": f"🔁 {at}{title} · {planner.repeat_label(rule)}", "tpl": tpl, "id": None})
        else:
            item = planner.add_task(ch, title, due, at=p["at"])
            made.append({"text": f"{at}{title}", "tpl": None, "id": item, "due": due})
    planner.ensure_today()
    return made


def _undo_row(made: list[dict]):
    if len(made) != 1:
        return None
    m = made[0]
    return [_b("↩ Отменить", f"pl:ud:{m['id']}" if m["id"] else f"pl:ut:{m['tpl']}")]


def _looks_like_other_input(text: str) -> bool:
    t = text.strip()
    return t.startswith("{") or t.startswith("[") or len(t) > 1500 or ("|" in t and "\n" in t and len(t) > 300)


def _parse_time(text: str) -> tuple[bool, str | None]:
    """«14.30», «14:30», «1430», «9» -> (True, 'HH:MM'); «-», «нет» -> (True, None); непонятное -> (False, None)."""
    t = text.strip().lower()
    if t in ("-", "нет", "без", "без времени", "0"):
        return True, None
    at, _rest = planner.extract_time(t)
    if at is not None:
        return True, at
    t2 = t.replace(" ", "")
    if t2.isdigit() and len(t2) in (3, 4) and int(t2[:-2]) < 24 and int(t2[-2:]) < 60:
        return True, f"{int(t2[:-2]):02d}:{t2[-2:]}"
    if t2.isdigit() and len(t2) <= 2 and int(t2) < 24:
        return True, f"{int(t2):02d}:00"
    m = re.fullmatch(r"([01]?\d|2[0-3])\.([0-5]\d)", t2)
    if m:
        return True, f"{int(m.group(1)):02d}:{m.group(2)}"
    return False, None


async def handle_text(message: Message, uid: int, st: dict) -> bool:
    aw = st.get("awaiting")
    text = (message.text or "").strip()
    if aw in (QUICK, PLAN, EDIT, TIME, TPL):
        if time.time() - float(st.get("pl_ts") or 0) > TTL:
            _disarm(st)
            state.save(uid, st)
            return False
        if _looks_like_other_input(text):
            return False
        c = _ctx(st, uid)
        arg = st.get("pl_arg")
        if aw == QUICK:
            made = _add_text(text, uid, c)
            if not made:
                await message.answer("Не нашёл текста задачи. Напишите, что нужно сделать.")
                return True
            shown = _day_of(c).isoformat()
            moved = [m for m in made if m.get("due") and m["due"] != shown]
            note = "✅ " + "\n".join(m["text"] for m in made)
            if moved:
                note += f"\n📅 {_fmt_day(dt.date.fromisoformat(moved[0]['due']))}"
            screen, kb = channel_screen(uid, st, note)
            undo = _undo_row(made)
            if undo:
                kb.inline_keyboard.insert(0, undo)
            state.save(uid, st)
            await message.answer(screen, reply_markup=kb)
        elif aw == EDIT:
            planner.set_title(int(arg), text)
            _disarm(st)
            state.save(uid, st)
            text_, kb_ = _card(uid, st, int(arg))
            await message.answer(text_, reply_markup=kb_)
        elif aw == TIME:
            ok, at = _parse_time(text)
            if not ok:
                await message.answer("Не понял время. Пример: 14.30 (или «-», чтобы убрать).")
                return True
            planner.set_at(int(arg), at)
            _disarm(st)
            state.save(uid, st)
            text_, kb_ = _card(uid, st, int(arg))
            await message.answer(text_, reply_markup=kb_)
        elif aw == TPL:
            made = _add_text(text, uid, c, force_repeat=True)
            _disarm(st)
            scr = _tpl_screen(uid, st)
            state.save(uid, st)
            await message.answer("\n".join(m["text"] for m in made) + "\n\n" + scr[0], reply_markup=scr[1])
        else:   # PLAN
            items = planner.parse_plan(text, list(config.CHANNELS), _week_start(c), c["ch"])
            if not items:
                await message.answer("Не нашёл задач. Начните со строки с днём (пн, вторник, 14.10), дальше - по задаче в строке.")
                return True
            ids = [planner.add_task(i["channel"], i["title"], i["due"], at=i["at"]) for i in items]
            by_day: dict[str, int] = {}
            for i in items:
                k = _fmt_day(dt.date.fromisoformat(i["due"]))
                by_day[k] = by_day.get(k, 0) + 1
            c["plan_ids"] = ids
            _disarm(st)
            scr = week_screen(uid, st, f"📥 Добавлено задач: {len(ids)} (" + ", ".join(f"{k} {v}" for k, v in by_day.items()) + ")")
            scr[1].inline_keyboard.insert(0, [_b("↩ Отменить план", "pl:pu")])
            state.save(uid, st)
            await message.answer(scr[0], reply_markup=scr[1])
        return True
    if aw == "pf_chan_new":
        try:
            name = await asyncio.to_thread(ideas.create_channel, text)
        except ValueError as e:
            await message.answer(f"{e}\nПришлите другое название или нажмите /menu.")
            return True
        db.set_user_channel(uid, name)
        st["awaiting"] = None
        state.save(uid, st)
        await message.answer(
            f"📺 Канал «{name}» создан и выбран.\nПапки: на сервере и на Диске — {config.RCLONE_BASE_PATH}/{name} (готовые ролики), "
            f"_ideas/{name}, _projects/{name}. Канал уже виден везде: конструктор, профиль, идеи, истории, задачник.\n"
            "Настройки по умолчанию (голос, музыка, субтитры) меняются в «⚙️ Профиль».",
            reply_markup=_kb([[_b("⚙️ Профиль", "pf:show"), _b("📋 Задачи", "pl:home")]]))
        return True
    return False


# ---------- команды ----------

@router.message(Command("plan"))
async def cmd_plan(message: Message):
    if not _ok(message.from_user.id):
        return
    uid = message.from_user.id
    st = state.load(uid)
    text, kb = home(uid, st)
    state.save(uid, st)
    await message.answer(text, reply_markup=kb)


@router.message(Command("task"))
async def cmd_task(message: Message, command: CommandObject):
    uid = message.from_user.id
    if not _ok(uid):
        return
    st = state.load(uid)
    c = _ctx(st, uid)
    if not (command.args or "").strip():
        return await message.answer("Формат: /task 10.30 Снять мем завтра #Канал. Или откройте /plan.")
    c["date"] = planner.today().isoformat()
    made = _add_text(command.args, uid, c)
    screen, kb = channel_screen(uid, st, "✅ " + "\n".join(m["text"] for m in made))
    state.save(uid, st)
    await message.answer(screen, reply_markup=kb)


@router.message(Command("vid"))
async def cmd_vid(message: Message, command: CommandObject):
    uid = message.from_user.id
    if not _ok(uid):
        return
    arg = (command.args or "").strip().lower().removeprefix("task_")
    if not arg.isdigit():
        return await message.answer("Формат: /vid 63 (номер задачи, например task_63). Или «🎬 Видео» в канале.")
    t = db.get_task(int(arg))
    if t is None:
        return await message.answer(f"Задачи task_{arg} нет.")
    title = await asyncio.to_thread(planner.video_title, t["channel"], t["id"])
    new = planner.add_video(t["id"], t["channel"], title)
    note = "" if t["status"] == "done" else f"\n⚠️ Задача пока в статусе «{t['status']}»."
    await message.answer((f"🎬 task_{t['id']} поставлен на загрузку ({t['channel']})." if new else f"task_{t['id']} уже в задачнике.") + note,
                         reply_markup=_kb([[_b("📋 Задачи", "pl:home")]]))


@router.message(Command("report"))
async def cmd_report(message: Message):
    if _ok(message.from_user.id):
        await send_report(message.bot, only=message.from_user.id)


# ---------- кнопки ----------

@router.callback_query(F.data.startswith("pl:"))
async def on_button(cb: CallbackQuery):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    parts = cb.data.split(":")
    act = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    # кнопки под напоминанием: экран задачника не открываем
    if act in ("rt", "rm"):
        r = planner.get(int(arg))
        if r is None:
            return await safe_answer(cb, "Задачи уже нет")
        if act == "rt":
            if r["status"] == "open":
                planner.toggle(r["id"])
            await safe_answer(cb, "Выполнено")
            return await safe_edit(cb.message, f"✅ {r['at'] or ''} · {r['title']} · {r['channel']}".replace("  ", " "))
        planner.move_to(r["id"], (planner.today() + dt.timedelta(days=1)).isoformat())
        await safe_answer(cb, "Перенесено на завтра")
        return await safe_edit(cb.message, f"⏭ завтра · {r['at'] or ''} · {r['title']} · {r['channel']}".replace("  ", " "))

    st = state.load(uid)
    c = _ctx(st, uid)
    alert = None
    screen = None
    today = planner.today()

    if act == "noop":
        return await safe_answer(cb)
    elif act == "home":
        screen = home(uid, st)
    elif act == "c":
        chs = list(config.CHANNELS)
        if int(arg) < len(chs):
            c["ch"] = chs[int(arg)]
            db.set_user_channel(uid, c["ch"])
        c["date"], c["page"] = today.isoformat(), 0
        screen = channel_screen(uid, st)
    elif act == "back":
        screen = channel_screen(uid, st)
    elif act == "d":
        c["date"], c["page"] = arg, 0
        screen = channel_screen(uid, st)
    elif act == "pg":
        c["page"] = int(arg)
        screen = channel_screen(uid, st)
    elif act == "t":
        planner.toggle(int(arg))
        screen = _card(uid, st, int(arg)) if len(parts) > 3 else channel_screen(uid, st)
    elif act == "i":
        screen = _card(uid, st, int(arg))
    elif act == "et":
        r = planner.get(int(arg))
        if r is not None:
            _arm(st, EDIT, r["id"])
            screen = (f"✏ Пришлите новый текст задачи сообщением.\n\nСейчас:\n{r['title']}", _kb([[_b("Отмена", f"pl:i:{r['id']}")]]))
    elif act == "tm":
        r = planner.get(int(arg))
        if r is not None:
            _arm(st, TIME, r["id"])
            screen = _time_screen(r)
    elif act == "tmv":
        v = parts[3]
        planner.set_at(int(arg), None if v == "x" else f"{v[:2]}:{v[2:]}")
        screen = _card(uid, st, int(arg))
    elif act == "due":
        item = int(arg)
        due = parts[3]
        planner.move_to(item, (today + dt.timedelta(days=int(due))).isoformat() if due.isdigit() else due)
        screen = _card(uid, st, item)
    elif act == "dp":
        item = int(arg)
        screen = _date_picker("📅 На какой день перенести?", f"pl:due:{item}:", f"pl:i:{item}", [[_b("Без даты", f"pl:nd:{item}")]])
    elif act == "nd":
        planner.move_to(int(arg), None)
        screen = _card(uid, st, int(arg))
    elif act == "cp":
        item = int(arg)
        screen = _date_picker("📋 Копия задачи - на какой день?", f"pl:cpd:{item}:", f"pl:i:{item}")
    elif act == "cpd":
        new = planner.copy_item(int(arg), parts[3])
        alert = "Копия создана" if new else None
        screen = _card(uid, st, int(arg))
    elif act == "rpm":
        r = planner.get(int(arg))
        if r is not None:
            screen = _repeat_menu(r)
    elif act == "rp":
        rule = ":".join(parts[3:])
        planner.make_repeating(int(arg), rule)
        alert = "Повтор: " + planner.repeat_label(rule)
        screen = _card(uid, st, int(arg))
    elif act == "rpx":
        planner.stop_repeat(int(arg))
        alert = "Повтор отключён"
        screen = _card(uid, st, int(arg))
    elif act == "mv":
        item = int(arg)
        r = planner.get(item)
        chs = list(config.CHANNELS)
        if r is not None and len(chs) > 1:
            nxt = chs[(chs.index(r["channel"]) + 1) % len(chs)] if r["channel"] in chs else chs[0]
            planner.set_channel(item, nxt)
            alert = f"Канал: {nxt}"
        screen = _card(uid, st, item)
    elif act == "del":
        item = int(arg)
        r = planner.get(item)
        if r is not None and r["tpl_id"]:
            screen = (f"«{_short(r['title'], 80)}» создаётся повтором.\nУбрать только эту задачу или весь повтор?",
                      _kb([[_b("Только эту", f"pl:dely:{item}")], [_b("🗑 Весь повтор", f"pl:delt:{item}")], [_b("Нет", f"pl:i:{item}")]]))
        else:
            screen = (f"Удалить «{_short(r['title'], 80)}»?" if r else "Уже удалено",
                      _kb([[_b("🗑 Да", f"pl:dely:{item}"), _b("Нет", f"pl:i:{item}")]]))
    elif act in ("dely", "ud"):
        planner.skip(int(arg))
        alert = "Удалено"
        screen = channel_screen(uid, st)
    elif act == "delt":
        r = planner.get(int(arg))
        if r is not None and r["tpl_id"]:
            planner.delete_template(r["tpl_id"])
        planner.delete(int(arg))
        alert = "Повтор удалён"
        screen = channel_screen(uid, st)
    elif act == "co":
        n = len([1 for r in planner.day_view(c["ch"], planner.today())["over"]])
        screen = (f"Убрать все просроченные задачи канала {c['ch']} ({n})?\nЗадачи из повторов завтра появятся снова по расписанию.",
                  _kb([[_b("🧹 Да, убрать", "pl:coy"), _b("Нет", "pl:back")]]))
    elif act == "coy":
        alert = f"Убрано: {planner.clear_overdue(c['ch'])}"
        screen = channel_screen(uid, st)
    elif act == "ut":
        planner.delete_template(int(arg))
        alert = "Повтор отменён"
        screen = channel_screen(uid, st)
    elif act == "w":
        screen = week_screen(uid, st)
    elif act == "wn":
        c["wk"] = int(arg)
        screen = week_screen(uid, st)
    elif act == "wp":
        _arm(st, PLAN)
        screen = _plan_prompt(c)
    elif act == "wc":
        n = planner.copy_week(c["ch"], _week_start(c))
        alert = f"Скопировано задач: {n}" if n else "В этой неделе нечего копировать"
        if n:
            c["wk"] = int(c.get("wk", 0)) + 1
        screen = week_screen(uid, st)
    elif act == "pu":
        for i in c.pop("plan_ids", []):
            planner.delete(i)
        alert = "План отменён"
        screen = week_screen(uid, st)
    elif act == "tpl":
        screen = _tpl_screen(uid, st)
    elif act == "tt":
        planner.toggle_template(int(arg))
        screen = _tpl_screen(uid, st)
    elif act == "td":
        planner.delete_template(int(arg))
        screen = _tpl_screen(uid, st)
    elif act == "ta":
        _arm(st, TPL)
        screen = ("🔁 Новый повтор. Напишите, например:\n10.00 Загрузить Short каждый день\nОтветить на комментарии по пн ср пт\n"
                  "Без слов «каждый…» повтор будет ежедневным.", _kb([[_b("Отмена", "pl:tpl")]]))
    elif act == "ts":
        n = planner.add_starter(c["ch"])
        planner.ensure_today()
        alert = f"Добавлено повторов: {n}" if n else "Стандартные уже добавлены"
        screen = _tpl_screen(uid, st)
    elif act == "rdy":
        screen = _ready_screen(uid, st)
    elif act == "rd":
        tid = int(arg)
        t = db.get_task(tid)
        if t is not None:
            await safe_answer(cb)
            title = await asyncio.to_thread(planner.video_title, t["channel"], tid)
            new = planner.add_video(tid, t["channel"], title)
            screen = _ready_screen(uid, st, f"🎬 task_{tid} {'поставлен на загрузку' if new else 'уже в списке'}: {title}")
            state.save(uid, st)
            return await safe_edit(cb.message, *screen)
    elif act == "rda":
        added = 0
        for r in planner.ready_tasks(c["ch"], 10):
            title = await asyncio.to_thread(planner.video_title, r["channel"], r["id"], False)
            added += 1 if planner.add_video(r["id"], r["channel"], title) else 0
        alert = f"Добавлено: {added}"
        c["date"] = today.isoformat()
        screen = channel_screen(uid, st)
    elif act == "set":
        screen = _settings_screen()
    elif act == "sh":
        hs = [*planner.HOUR_CHOICES, -1]
        cur = planner.report_hour()
        planner.set_setting("report_hour", str(hs[(hs.index(cur) + 1) % len(hs)] if cur in hs else planner.DEFAULT_HOUR))
        screen = _settings_screen()
    elif act == "stz":
        cur = planner.setting("tz", planner.DEFAULT_TZ)
        tzs = planner.TZ_CHOICES
        planner.set_setting("tz", tzs[(tzs.index(cur) + 1) % len(tzs)] if cur in tzs else tzs[0])
        planner.set_setting("last_report", "")
        c["date"] = planner.today().isoformat()
        screen = _settings_screen()
    elif act == "rep":
        await safe_answer(cb, "Отправляю")
        state.save(uid, st)
        await send_report(cb.message.bot, only=uid)
        return

    if screen is None:
        screen = channel_screen(uid, st)
    state.save(uid, st)
    await safe_answer(cb, alert)
    await safe_edit(cb.message, *screen)
