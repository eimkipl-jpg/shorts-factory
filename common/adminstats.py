"""Аналитика бота для креаторов (патч 48): кто заходил, что открывал, что заказывал и что получил.

Источники:
  cr_users  - + last_seen, username, actions (сколько действий всего), src (метка из ссылки t.me/<бот>?start=<метка>)
  cr_events - журнал действий: visit (первое действие за день), start, cb (кнопка), cmd, text, file, prompt, json_ok, json_err
  cr_tasks + tasks - заказанные задачи и их итог (done / failed / в работе)
  cr_kv     - настройки уведомлений админа и дата последнего ежедневного отчёта
Админы по умолчанию не попадают в цифры (свои тесты не искажают статистику)."""
import csv
import io
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from common import config
from common.db import connect

log = logging.getLogger("adminstats")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cr_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ts      REAL NOT NULL,
    ev      TEXT NOT NULL,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_cr_events_ts ON cr_events(ts);
CREATE INDEX IF NOT EXISTS idx_cr_events_user ON cr_events(user_id, ts);
CREATE TABLE IF NOT EXISTS cr_kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

KIND_NAMES = {"video": "🎞 Умный монтаж", "reels": "🎬 ReelsEditor", "cut": "✂️ Нарезка", "dl": "⬇️ Скачивание", "ref": "🔍 Разбор референса"}

# кнопка -> раздел (для «что открывают»)
SECTIONS = [("sm:home", "🎞 Умный монтаж"), ("sm:p", "🎞 Промт для сценария"), ("cr:re", "🎬 ReelsEditor"), ("cr:cut", "✂️ Нарезка"),
            ("cr:dl", "⬇️ Скачать"), ("cr:ugc", "📢 UGC-реклама"), ("cr:ref", "🔍 Разбор референса"), ("cr:br", "🏷 Бренд-профиль"),
            ("cr:fmt", "📐 Формат"), ("sm:set", "⚙️ Настройки"), ("cr:pub", "📊 Публикация и метрики"), ("cr:met", "📈 Метрики по ссылке"),
            ("cr:guide", "📖 Инструкция"), ("cr:gd:", "📖 Схемы-инструкции"), ("cr:sup", "🆘 Поддержка"), ("buy:", "💰 Покупка")]

NOTIFY_DEFAULTS = {"n_new": "1", "n_first": "1", "n_fail": "1", "n_daily": "1"}
NOTIFY_NAMES = {"n_new": "Новый пользователь", "n_first": "Первая задача человека", "n_fail": "Ошибка задачи", "n_daily": "Ежедневный отчёт"}

_inited = False
_visit_day: dict[int, str] = {}


def init() -> None:
    global _inited
    if _inited:
        return
    from common import creator
    creator.init()
    with connect() as c:
        c.executescript(SCHEMA)
        cols = [r[1] for r in c.execute("PRAGMA table_info(cr_users)").fetchall()]
        for col, ddl in (("last_seen", "REAL"), ("username", "TEXT"), ("actions", "INTEGER NOT NULL DEFAULT 0"), ("src", "TEXT")):
            if col not in cols:
                c.execute(f"ALTER TABLE cr_users ADD COLUMN {col} {ddl}")
    _inited = True


# ---------- время ----------

def tz() -> timezone:
    return timezone(timedelta(hours=config.REPORT_TZ))


def now_local() -> datetime:
    return datetime.now(tz())


def day_start(days_ago: int = 0) -> float:
    d = now_local().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return d.timestamp()


def day_key(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz()).strftime("%Y-%m-%d")


def fmt_dt(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz()).strftime("%d.%m %H:%M")


def ago(ts: float | None) -> str:
    if not ts:
        return "никогда"
    s = max(0, int(time.time() - ts))
    if s < 60:
        return "только что"
    if s < 3600:
        return f"{s // 60} мин назад"
    if s < 86400:
        return f"{s // 3600} ч назад"
    return f"{s // 86400} дн назад"


# ---------- запись ----------

def touch(uid: int, name: str = "", username: str = "", ev: str = "", detail: str = "") -> tuple[dict, bool]:
    """Каждое действие человека: создаёт пользователя при первом визите, обновляет «был в боте», пишет событие.
    Возвращает (строка пользователя, новый ли он)."""
    init()
    now = time.time()
    with connect() as c:
        row = c.execute("SELECT * FROM cr_users WHERE user_id=?", (uid,)).fetchone()
        new = row is None
        if new:
            c.execute("INSERT INTO cr_users(user_id,name,created,last_seen,username,actions) VALUES(?,?,?,?,?,1)",
                      (uid, (name or "")[:64], now, now, (username or "")[:64]))
        else:
            c.execute("UPDATE cr_users SET last_seen=?, actions=actions+1, username=COALESCE(NULLIF(?,''),username), "
                      "name=CASE WHEN name IS NULL OR name='' THEN ? ELSE name END WHERE user_id=?",
                      (now, (username or "")[:64], (name or "")[:64], uid))
        day = day_key(now)
        if _visit_day.get(uid) != day:
            seen = c.execute("SELECT 1 FROM cr_events WHERE user_id=? AND ev='visit' AND ts>=?", (uid, day_start())).fetchone()
            if not seen:
                c.execute("INSERT INTO cr_events(user_id,ts,ev,detail) VALUES(?,?,?,?)", (uid, now, "visit", day))
            _visit_day[uid] = day
            if len(_visit_day) > 20000:
                _visit_day.clear()
        if ev:
            c.execute("INSERT INTO cr_events(user_id,ts,ev,detail) VALUES(?,?,?,?)", (uid, now, ev[:20], (detail or "")[:60]))
        row = c.execute("SELECT * FROM cr_users WHERE user_id=?", (uid,)).fetchone()
    return dict(row), new


def event(uid: int, ev: str, detail: str = "") -> None:
    """Событие воронки (взял промт, прислал JSON...). Никогда не бросает исключений."""
    try:
        init()
        with connect() as c:
            c.execute("INSERT INTO cr_events(user_id,ts,ev,detail) VALUES(?,?,?,?)", (uid, time.time(), ev[:20], (detail or "")[:60]))
    except Exception:  # noqa: BLE001
        log.exception("event")


def set_src(uid: int, src: str) -> None:
    """Метка источника из ссылки t.me/<бот>?start=<метка> - только для нового человека (первое касание)."""
    src = "".join(ch for ch in (src or "") if ch.isalnum() or ch in "_-")[:32]
    if not src:
        return
    with connect() as c:
        c.execute("UPDATE cr_users SET src=? WHERE user_id=? AND (src IS NULL OR src='')", (src, uid))


def purge(days: int = 180) -> None:
    with connect() as c:
        c.execute("DELETE FROM cr_events WHERE ts<? AND ev!='visit'", (time.time() - days * 86400,))
        c.execute("DELETE FROM cr_events WHERE ts<?", (time.time() - 400 * 86400,))


# ---------- настройки ----------

def kv_get(k: str, default: str = "") -> str:
    init()
    with connect() as c:
        row = c.execute("SELECT v FROM cr_kv WHERE k=?", (k,)).fetchone()
    return row["v"] if row else NOTIFY_DEFAULTS.get(k, default)


def kv_set(k: str, v: str) -> None:
    init()
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO cr_kv(k,v) VALUES(?,?)", (k, v))


def notify_on(k: str) -> bool:
    return kv_get(k) == "1"


def notify_admins(text: str, buttons: list | None = None, background: bool = True) -> None:
    """Сообщение всем админам через Bot API (работает и из воркера). По умолчанию в фоне - не тормозит бота."""
    from common import notify

    def _send():
        for a in config.ADMIN_IDS:
            notify.send(a, text, buttons)
    if background:
        threading.Thread(target=_send, daemon=True).start()
    else:
        _send()


def user_label(u: dict) -> str:
    name = (u.get("name") or "").strip() or "без имени"
    un = f" @{u['username']}" if u.get("username") else ""
    return f"{name}{un} ({u['user_id']})"


def on_new_user(u: dict) -> None:
    if not notify_on("n_new") or u["user_id"] in config.ADMIN_IDS:
        return
    src = f"\n🔗 Пришёл по метке: {u['src']}" if u.get("src") else ""
    notify_admins(f"👋 Новый пользователь: {user_label(u)}{src}\nВсего людей: {total_users()}", [[("👤 Карточка", f"adm:u:{u['user_id']}")]])


def on_task(uid: int, task_id: int, kind: str) -> None:
    """Вызывается после постановки задачи: первая задача человека - повод сообщить админу."""
    try:
        if uid in config.ADMIN_IDS or not notify_on("n_first"):
            return
        with connect() as c:
            n = c.execute("SELECT COUNT(*) FROM cr_tasks WHERE user_id=?", (uid,)).fetchone()[0]
            u = c.execute("SELECT * FROM cr_users WHERE user_id=?", (uid,)).fetchone()
        if n == 1 and u:
            notify_admins(f"🎯 Первая задача: {user_label(dict(u))} → {KIND_NAMES.get(kind, kind)} (task_{task_id})",
                          [[("👤 Карточка", f"adm:u:{uid}")]])
    except Exception:  # noqa: BLE001
        log.exception("on_task")


def on_fail(task, err: Exception) -> None:
    """Из воркера: задача человека окончательно не получилась."""
    try:
        if not notify_on("n_fail"):
            return
        with connect() as c:
            row = c.execute("SELECT u.*, k.kind FROM cr_tasks k JOIN cr_users u ON u.user_id=k.user_id WHERE k.task_id=?",
                            (task["id"],)).fetchone()
        if row and row["user_id"] in config.ADMIN_IDS:      # свои тесты админ видит и так
            return
        who = user_label(dict(row)) if row else f"chat {task['chat_id']}"
        kind = row["kind"] if row else task["input_type"]
        btn = [[("👤 Карточка", f"adm:u:{row['user_id']}")]] if row else None
        notify_admins(f"❌ Ошибка task_{task['id']} · {KIND_NAMES.get(kind, kind)}\n👤 {who}\n{str(err)[:400]}", btn, background=False)
    except Exception:  # noqa: BLE001
        log.exception("on_fail")


# ---------- выборки ----------

def _no_admins(col: str = "user_id") -> tuple[str, list]:
    ids = sorted(config.ADMIN_IDS)
    if not ids:
        return "1=1", []
    return f"{col} NOT IN ({','.join('?' * len(ids))})", ids


def total_users() -> int:
    w, a = _no_admins()
    with connect() as c:
        return c.execute(f"SELECT COUNT(*) FROM cr_users WHERE {w}", a).fetchone()[0]


PERIODS = {"today": ("сегодня", 0), "7": ("7 дней", 6), "30": ("30 дней", 29), "all": ("всё время", None)}


def period_start(p: str) -> float:
    days = PERIODS.get(p, PERIODS["today"])[1]
    return 0.0 if days is None else day_start(days)


def overview(p: str = "today") -> dict:
    init()
    t0 = period_start(p)
    w, a = _no_admins()
    wu, au = _no_admins("u.user_id")
    wk, ak = _no_admins("k.user_id")
    r: dict = {"period": PERIODS.get(p, PERIODS["today"])[0]}
    with connect() as c:
        q = lambda sql, args=(): c.execute(sql, args).fetchone()[0]  # noqa: E731
        r["total"] = q(f"SELECT COUNT(*) FROM cr_users WHERE {w}", a)
        r["banned"] = q(f"SELECT COUNT(*) FROM cr_users WHERE banned=1 AND {w}", a)
        r["new"] = q(f"SELECT COUNT(*) FROM cr_users WHERE created>=? AND {w}", [t0] + a)
        r["active"] = q(f"SELECT COUNT(DISTINCT user_id) FROM cr_events WHERE ts>=? AND {w}", [t0] + a)
        r["online"] = q(f"SELECT COUNT(*) FROM cr_users WHERE last_seen>=? AND {w}", [time.time() - 900] + a)
        r["makers"] = q(f"SELECT COUNT(DISTINCT user_id) FROM cr_tasks k WHERE ts>=? AND {wk}", [t0] + ak)
        rows = c.execute(f"SELECT k.kind, t.status, COUNT(*) n FROM cr_tasks k LEFT JOIN tasks t ON t.id=k.task_id "
                         f"WHERE k.ts>=? AND {wk} GROUP BY k.kind, t.status", [t0] + ak).fetchall()
        kinds: dict = {}
        for row in rows:
            st = row["status"] or "?"
            bucket = "done" if st == "done" else "failed" if st in ("failed", "rejected") else "work"
            kinds.setdefault(row["kind"], {"done": 0, "failed": 0, "work": 0})[bucket] += row["n"]
        r["kinds"] = kinds
        dur = c.execute(f"SELECT AVG(t.updated_at-t.created_at) FROM cr_tasks k JOIN tasks t ON t.id=k.task_id "
                        f"WHERE k.ts>=? AND t.status='done' AND {wk}", [t0] + ak).fetchone()[0]
        r["avg_min"] = round(dur / 60, 1) if dur else None
        r["prompts"] = q(f"SELECT COUNT(*) FROM cr_events WHERE ev='prompt' AND ts>=? AND {w}", [t0] + a)
        r["json_ok"] = q(f"SELECT COUNT(*) FROM cr_events WHERE ev='json_ok' AND ts>=? AND {w}", [t0] + a)
        r["json_err"] = q(f"SELECT COUNT(*) FROM cr_events WHERE ev='json_err' AND ts>=? AND {w}", [t0] + a)
        r["metrics"] = q(f"SELECT COUNT(*) FROM cr_events WHERE ev='metrics' AND ts>=? AND {w}", [t0] + a)
        # воронка новичков периода
        nw = f"u.created>=? AND {wu}"
        r["f_new"] = r["new"]
        r["f_section"] = q(f"SELECT COUNT(*) FROM cr_users u WHERE {nw} AND EXISTS (SELECT 1 FROM cr_events e WHERE e.user_id=u.user_id "
                           f"AND e.ev IN ('cb','text','file'))", [t0] + au)
        r["f_task"] = q(f"SELECT COUNT(*) FROM cr_users u WHERE {nw} AND EXISTS (SELECT 1 FROM cr_tasks k WHERE k.user_id=u.user_id)", [t0] + au)
        r["f_done"] = q(f"SELECT COUNT(*) FROM cr_users u WHERE {nw} AND EXISTS (SELECT 1 FROM cr_tasks k JOIN tasks t ON t.id=k.task_id "
                        f"WHERE k.user_id=u.user_id AND t.status='done')", [t0] + au)
        r["f_back"] = q(f"SELECT COUNT(*) FROM cr_users u WHERE {nw} AND (SELECT COUNT(*) FROM cr_events e WHERE e.user_id=u.user_id "
                        f"AND e.ev='visit')>=2", [t0] + au)
        # разделы
        cbs = c.execute(f"SELECT detail, COUNT(*) n, COUNT(DISTINCT user_id) u FROM cr_events WHERE ev='cb' AND ts>=? AND {w} "
                        f"GROUP BY detail", [t0] + a).fetchall()
        sec: dict = {}
        for row in cbs:
            d = row["detail"] or ""
            for pref, name in SECTIONS:
                if d == pref or (pref.endswith(":") and d.startswith(pref)):
                    s = sec.setdefault(name, [0, 0])
                    s[0] += row["n"]
                    s[1] += row["u"]
                    break
        r["sections"] = sorted(sec.items(), key=lambda kv: -kv[1][0])[:8]
        r["sources"] = [(row["src"], row["n"]) for row in c.execute(
            f"SELECT src, COUNT(*) n FROM cr_users WHERE src IS NOT NULL AND src!='' AND created>=? AND {w} GROUP BY src ORDER BY n DESC LIMIT 6",
            [t0] + a).fetchall()]
    r["daily"] = daily_active(14)
    return r


def daily_active(days: int = 14) -> list[tuple[str, int, int]]:
    """[(дд.мм, активных, новых)] за последние N дней, от старых к новым."""
    w, a = _no_admins()
    out = []
    with connect() as c:
        for i in range(days - 1, -1, -1):
            s, e = day_start(i), day_start(i - 1) if i else time.time() + 1
            act = c.execute(f"SELECT COUNT(DISTINCT user_id) FROM cr_events WHERE ev='visit' AND ts>=? AND ts<? AND {w}", [s, e] + a).fetchone()[0]
            new = c.execute(f"SELECT COUNT(*) FROM cr_users WHERE created>=? AND created<? AND {w}", [s, e] + a).fetchone()[0]
            out.append((datetime.fromtimestamp(s, tz()).strftime("%d.%m"), act, new))
    return out


def spark(vals: list[int]) -> str:
    bars = "▁▂▃▄▅▆▇█"
    top = max(vals) if vals else 0
    if not top:
        return "▁" * len(vals)
    return "".join(bars[min(7, round(v / top * 7))] for v in vals)


def _pct(a: int, b: int) -> str:
    return f"{round(a * 100 / b)}%" if b else "—"


def overview_text(p: str = "today") -> str:
    r = overview(p)
    L = [f"👑 Статистика · {r['period']}", "(без учёта админов)", ""]
    L.append(f"👥 Всего людей: {r['total']}" + (f" (заблокировано {r['banned']})" if r["banned"] else "") + f" · сейчас в боте: {r['online']}")
    L.append(f"🆕 Новых: {r['new']}   🙋 Заходили: {r['active']}   🛠 Заказали задачу: {r['makers']}")
    L.append("")
    if r["kinds"]:
        L.append("📦 Задачи (✅ готово / ❌ ошибка / ⏳ в работе):")
        tot = {"done": 0, "failed": 0, "work": 0}
        for k, v in sorted(r["kinds"].items(), key=lambda kv: -sum(kv[1].values())):
            L.append(f"  {KIND_NAMES.get(k, k)}: ✅{v['done']} ❌{v['failed']} ⏳{v['work']}")
            for b in tot:
                tot[b] += v[b]
        fin = tot["done"] + tot["failed"]
        L.append(f"  Итого: {sum(tot.values())}, успешных {_pct(tot['done'], fin)}" + (f", в среднем {r['avg_min']} мин" if r["avg_min"] else ""))
    else:
        L.append("📦 Задач за период нет.")
    if r["prompts"] or r["json_ok"] or r["json_err"]:
        L.append(f"🎞 Умный монтаж: взяли промт {r['prompts']}, прислали JSON {r['json_ok']}" + (f" (с ошибками {r['json_err']})" if r["json_err"] else ""))
    if r["metrics"]:
        L.append(f"📈 Проверили метрики: {r['metrics']}")
    if r["f_new"]:
        L += ["", "🪜 Воронка новичков:",
              f"  пришли {r['f_new']} → нажали что-то {r['f_section']} ({_pct(r['f_section'], r['f_new'])}) → "
              f"заказали {r['f_task']} ({_pct(r['f_task'], r['f_new'])}) → получили результат {r['f_done']} ({_pct(r['f_done'], r['f_new'])})",
              f"  вернулись в другой день: {r['f_back']} ({_pct(r['f_back'], r['f_new'])})"]
    if r["sections"]:
        L += ["", "🖱 Что открывают (нажатий / людей):"] + [f"  {n}: {v[0]} / {v[1]}" for n, v in r["sections"]]
    if r["sources"]:
        L += ["", "🔗 Откуда пришли (метки ссылок):"] + [f"  {s}: {n}" for s, n in r["sources"]]
    d = r["daily"]
    L += ["", "📅 Заходили по дням (14 дн): " + spark([x[1] for x in d]),
          "  " + " · ".join(f"{x[0]}:{x[1]}" + (f"(+{x[2]})" if x[2] else "") for x in d[-7:])]
    return "\n".join(L)


def users_page(page: int = 0, per: int = 8, mode: str = "seen") -> tuple[list[dict], int]:
    """mode: seen - по последнему визиту, new - новые, top - больше всего задач."""
    init()
    w, a = _no_admins("u.user_id")
    order = {"seen": "COALESCE(u.last_seen,u.created) DESC", "new": "u.created DESC", "top": "tasks DESC, u.last_seen DESC"}.get(mode, "u.last_seen DESC")
    with connect() as c:
        total = c.execute(f"SELECT COUNT(*) FROM cr_users u WHERE {w}", a).fetchone()[0]
        rows = c.execute(
            f"SELECT u.*, (SELECT COUNT(*) FROM cr_tasks k WHERE k.user_id=u.user_id) tasks, "
            f"(SELECT COUNT(*) FROM cr_tasks k JOIN tasks t ON t.id=k.task_id WHERE k.user_id=u.user_id AND t.status='done') done "
            f"FROM cr_users u WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?", a + [per, page * per]).fetchall()
    return [dict(r) for r in rows], total


def user_card(uid: int) -> dict | None:
    init()
    with connect() as c:
        u = c.execute("SELECT * FROM cr_users WHERE user_id=?", (uid,)).fetchone()
        if u is None:
            return None
        u = dict(u)
        u["days"] = c.execute("SELECT COUNT(*) FROM cr_events WHERE user_id=? AND ev='visit'", (uid,)).fetchone()[0]
        u["kinds"] = {}
        for row in c.execute("SELECT k.kind, t.status, COUNT(*) n FROM cr_tasks k LEFT JOIN tasks t ON t.id=k.task_id WHERE k.user_id=? "
                             "GROUP BY k.kind, t.status", (uid,)).fetchall():
            st = row["status"] or "?"
            b = "done" if st == "done" else "failed" if st in ("failed", "rejected") else "work"
            u["kinds"].setdefault(row["kind"], {"done": 0, "failed": 0, "work": 0})[b] += row["n"]
        u["last"] = [dict(r) for r in c.execute(
            "SELECT k.task_id, k.kind, k.ts, t.status, t.error FROM cr_tasks k LEFT JOIN tasks t ON t.id=k.task_id WHERE k.user_id=? "
            "ORDER BY k.ts DESC LIMIT 6", (uid,)).fetchall()]
        u["titles"] = [dict(r) for r in c.execute("SELECT ts, title FROM cr_log WHERE user_id=? ORDER BY ts DESC LIMIT 5", (uid,)).fetchall()]
        u["prompts"] = c.execute("SELECT COUNT(*) FROM cr_events WHERE user_id=? AND ev='prompt'", (uid,)).fetchone()[0]
        u["recent"] = [dict(r) for r in c.execute("SELECT ts, ev, detail FROM cr_events WHERE user_id=? AND ev!='visit' "
                                                  "ORDER BY ts DESC LIMIT 8", (uid,)).fetchall()]
    return u


STATUS_ICON = {"done": "✅", "failed": "❌", "rejected": "⛔", "new": "⏳", "processing": "⚙️", "uploading": "📤", "draft": "📝"}
EV_NAMES = {"start": "/start", "cb": "кнопка", "cmd": "команда", "text": "сообщение", "file": "файл", "prompt": "взял промт",
            "json_ok": "прислал JSON", "json_err": "JSON с ошибками", "metrics": "метрики"}


def user_text(uid: int) -> str:
    u = user_card(uid)
    if u is None:
        return f"Пользователь {uid} ещё не заходил в бот."
    from common import creator
    L = [f"👤 {user_label(u)}" + (" · ⛔ заблокирован" if u["banned"] else "") + (" · 👑 админ" if uid in config.ADMIN_IDS else ""),
         f"Пришёл: {fmt_dt(u['created'])}" + (f" по метке «{u['src']}»" if u.get("src") else ""),
         f"Был в боте: {ago(u.get('last_seen'))} · дней с визитами: {u['days']} · действий: {u.get('actions') or 0}"]
    if not config.FREE_MODE:
        L.append(f"Баланс: кредитов {u['credits']}, бесплатных {creator.free_left(u)}")
    if u["kinds"]:
        L.append("")
        L.append("Задачи (✅/❌/⏳): " + "; ".join(f"{KIND_NAMES.get(k, k)} {v['done']}/{v['failed']}/{v['work']}" for k, v in u["kinds"].items()))
    else:
        L.append("\nЗадач пока не заказывал." + (f" Промт брал {u['prompts']} раз." if u["prompts"] else ""))
    if u["last"]:
        L.append("\nПоследние задачи:")
        for t in u["last"]:
            err = f" — {t['error'][:80]}" if t.get("status") == "failed" and t.get("error") else ""
            L.append(f"  {STATUS_ICON.get(t.get('status') or '', '•')} {fmt_dt(t['ts'])} {KIND_NAMES.get(t['kind'], t['kind'])}{err}")
    if u["titles"]:
        L.append("\nТемы роликов:")
        L += [f"  • {t['title']}" for t in u["titles"]]
    if u["recent"]:
        L.append("\nПоследние действия:")
        L += [f"  {fmt_dt(e['ts'])} {EV_NAMES.get(e['ev'], e['ev'])}" + (f" {e['detail']}" if e.get("detail") else "") for e in u["recent"]]
    return "\n".join(L)[:4000]


def errors_text(limit: int = 10) -> str:
    init()
    with connect() as c:
        rows = c.execute("SELECT t.id, t.updated_at, t.error, t.input_type, k.user_id, u.name, u.username FROM tasks t "
                         "LEFT JOIN cr_tasks k ON k.task_id=t.id LEFT JOIN cr_users u ON u.user_id=k.user_id "
                         "WHERE t.status='failed' ORDER BY t.updated_at DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        return "🧯 Ошибок нет."
    L = ["🧯 Последние ошибки:"]
    for r in rows:
        who = (r["name"] or "") + (f" @{r['username']}" if r["username"] else "") + (f" ({r['user_id']})" if r["user_id"] else "")
        L.append(f"\n❌ task_{r['id']} · {fmt_dt(r['updated_at'])} · {KIND_NAMES.get(r['input_type'], r['input_type'])}\n👤 {who.strip() or '—'}\n"
                 f"{(r['error'] or '')[:200]}")
    return "\n".join(L)[:4000]


def daily_report_text() -> str:
    """Итоги вчерашнего дня - для ежедневного сообщения админу."""
    w, a = _no_admins()
    wk, ak = _no_admins("k.user_id")
    s, e = day_start(1), day_start(0)
    with connect() as c:
        q = lambda sql, args: c.execute(sql, args).fetchone()[0]  # noqa: E731
        new = q(f"SELECT COUNT(*) FROM cr_users WHERE created>=? AND created<? AND {w}", [s, e] + a)
        act = q(f"SELECT COUNT(DISTINCT user_id) FROM cr_events WHERE ev='visit' AND ts>=? AND ts<? AND {w}", [s, e] + a)
        tasks = q(f"SELECT COUNT(*) FROM cr_tasks k WHERE k.ts>=? AND k.ts<? AND {wk}", [s, e] + ak)
        done = q(f"SELECT COUNT(*) FROM cr_tasks k JOIN tasks t ON t.id=k.task_id WHERE k.ts>=? AND k.ts<? AND t.status='done' AND {wk}", [s, e] + ak)
        failed = q(f"SELECT COUNT(*) FROM cr_tasks k JOIN tasks t ON t.id=k.task_id WHERE k.ts>=? AND k.ts<? AND t.status='failed' AND {wk}", [s, e] + ak)
        makers = q(f"SELECT COUNT(DISTINCT user_id) FROM cr_tasks k WHERE k.ts>=? AND k.ts<? AND {wk}", [s, e] + ak)
        total = q(f"SELECT COUNT(*) FROM cr_users WHERE {w}", a)
    day = datetime.fromtimestamp(s, tz()).strftime("%d.%m")
    if not (new or act or tasks):
        return f"📊 Отчёт за {day}: в бот никто не заходил. Всего людей: {total}."
    return (f"📊 Отчёт за {day}\n👥 Заходили: {act} (новых {new}) · всего людей {total}\n"
            f"🛠 Заказали задачи: {makers} чел., задач {tasks}: ✅ {done} ❌ {failed}")


def users_csv() -> bytes:
    init()
    with connect() as c:
        rows = c.execute(
            "SELECT u.user_id, u.name, u.username, u.src, u.created, u.last_seen, u.actions, u.credits, u.banned, "
            "(SELECT COUNT(*) FROM cr_events e WHERE e.user_id=u.user_id AND e.ev='visit') days, "
            "(SELECT COUNT(*) FROM cr_tasks k WHERE k.user_id=u.user_id) tasks, "
            "(SELECT COUNT(*) FROM cr_tasks k JOIN tasks t ON t.id=k.task_id WHERE k.user_id=u.user_id AND t.status='done') done, "
            "(SELECT COUNT(*) FROM cr_tasks k JOIN tasks t ON t.id=k.task_id WHERE k.user_id=u.user_id AND t.status='failed') failed "
            "FROM cr_users u ORDER BY u.created").fetchall()
    buf = io.StringIO()
    wr = csv.writer(buf, delimiter=";")
    wr.writerow(["user_id", "имя", "username", "метка", "пришёл", "был в боте", "действий", "кредиты", "заблокирован",
                 "дней с визитами", "задач", "готово", "ошибок", "админ"])
    for r in rows:
        wr.writerow([r["user_id"], r["name"] or "", r["username"] or "", r["src"] or "", fmt_dt(r["created"]), fmt_dt(r["last_seen"]),
                     r["actions"] or 0, r["credits"], r["banned"], r["days"], r["tasks"], r["done"], r["failed"],
                     1 if r["user_id"] in config.ADMIN_IDS else 0])
    return ("﻿" + buf.getvalue()).encode("utf-8")    # BOM - чтобы Excel открыл кириллицу
