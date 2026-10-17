"""Задачник: задачи по каналам, видео на загрузку, повторяющиеся задачи (ежедневные и др.), утренний отчёт.

Хранится в той же SQLite, что и очередь, но в своих таблицах: воркера задачник не касается, из очереди только читает.
Даты - в часовом поясе задачника (по умолчанию Asia/Bangkok), в базе как строки YYYY-MM-DD.
"""
import datetime as dt
import json
import re
import subprocess
import time
from zoneinfo import ZoneInfo

from common import config
from common.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS todo (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'task',      -- task | video
    title      TEXT NOT NULL,
    due        TEXT,                              -- YYYY-MM-DD или пусто
    status     TEXT NOT NULL DEFAULT 'open',      -- open | done
    tpl_id     INTEGER,                           -- из какого повтора создана
    task_id    INTEGER,                           -- для видео: номер задачи воркера
    created_at REAL NOT NULL,
    done_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_todo_open ON todo(status, channel);
CREATE UNIQUE INDEX IF NOT EXISTS idx_todo_video ON todo(task_id) WHERE task_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS todo_tpl (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT NOT NULL,
    title      TEXT NOT NULL,
    repeat     TEXT NOT NULL,                     -- daily | weekdays | dow:0,2,4 | every:N
    anchor     TEXT NOT NULL,                     -- дата начала
    active     INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS planner_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_TZ = "Asia/Bangkok"
TZ_CHOICES = ["Asia/Bangkok", "Europe/Moscow", "UTC"]
HOUR_CHOICES = [6, 7, 8, 9, 10, 12, 18, 21]
DEFAULT_HOUR = 8
STARTER = ["Загрузить 1 Short", "Ответить на комментарии", "Проверить аналитику"]

DOW_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
DOW_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_DOW_WORDS = {"пн": 0, "пон": 0, "вт": 1, "вто": 1, "ср": 2, "сре": 2, "чт": 3, "чет": 3,
              "пт": 4, "пят": 4, "сб": 5, "суб": 5, "вс": 6, "вос": 6}


_INIT_DONE = False


def init() -> None:
    global _INIT_DONE
    with connect() as c:
        c.executescript(SCHEMA)
        if not _INIT_DONE:
            for table, col, ddl in (("todo", "at", "TEXT"), ("todo", "rem", "INTEGER NOT NULL DEFAULT 0"), ("todo_tpl", "at", "TEXT")):
                cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in cols:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {ddl}')
            _INIT_DONE = True


# ---------- настройки и время ----------

def setting(key: str, default: str = "") -> str:
    init()
    with connect() as c:
        row = c.execute("SELECT value FROM planner_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    init()
    with connect() as c:
        c.execute("INSERT INTO planner_settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def tz() -> ZoneInfo:
    for name in (setting("tz", DEFAULT_TZ), DEFAULT_TZ, "UTC"):
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - нет базы часовых поясов или неверное имя
            continue
    return ZoneInfo("UTC")


def now() -> dt.datetime:
    return dt.datetime.now(tz())


def today() -> dt.date:
    return now().date()


def report_hour() -> int:
    """Час утреннего отчёта; -1 - отчёт выключен."""
    try:
        return int(setting("report_hour", str(DEFAULT_HOUR)))
    except ValueError:
        return DEFAULT_HOUR


# ---------- разбор текста задачи ----------

def _dow_of(word: str) -> int | None:
    w = word.lower().strip(".,;:!?")
    if w in _DOW_WORDS:
        return _DOW_WORDS[w]
    if len(w) >= 3 and w[:3] in _DOW_WORDS and w[:3] not in ("вс",):
        return _DOW_WORDS[w[:3]]
    return None


def _next_dow(day: dt.date, dow: int) -> dt.date:
    """Ближайший день недели строго после day."""
    return day + dt.timedelta(days=(dow - day.weekday() - 1) % 7 + 1)


_TIME_COLON = re.compile(r"(?<![\d.])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_TIME_DOT_LEAD = re.compile(r"^\s*(?:[-•*]\s*)?(?:в\s+|к\s+)?([01]?\d|2[0-3])\.([0-5]\d)(?![\d.])")
_TIME_DOT_V = re.compile(r"\b(?:в|к)\s+([01]?\d|2[0-3])\.([0-5]\d)(?![\d.])")


def extract_time(s: str) -> tuple[str | None, str]:
    """Время hh:mm или hh.mm в тексте -> («09:30», текст без него). Точка считается временем в начале строки или после «в»/«к»:
    иначе «12.10» - это дата."""
    for rx in (_TIME_COLON, _TIME_DOT_LEAD, _TIME_DOT_V):
        m = rx.search(s)
        if m:
            at = f"{int(m.group(1)):02d}:{m.group(2)}"
            cut = s[:m.start()] + " " + s[m.end():]
            return at, re.sub(r"[ \t]+", " ", cut).strip(" ,.;:-—\t")
    return None, s


def parse_task(text: str, channels: list[str], day: dt.date) -> dict:
    """«#канал Снять 3 мема завтра» -> {title, due, repeat, channel}. repeat: None | daily | weekdays | dow:0,2 | every:N."""
    raw = " ".join((text or "").split()).strip(" -•*")
    out = {"title": "", "due": None, "repeat": None, "channel": None, "at": None}
    out["at"], raw = extract_time(raw)
    s = raw
    for m in re.finditer(r"#([\w\-]+)", s):
        for ch in channels:
            if ch.lower() == m.group(1).lower():
                out["channel"] = ch
                s = s.replace(m.group(0), " ", 1)
                break
    low = s.lower()

    def cut(m: re.Match) -> None:
        nonlocal s, low
        s = (s[:m.start()] + " " + s[m.end():])
        low = s.lower()

    m = re.search(r"\b(каждый\s+день|ежедневно|каждодневно|каждый\s+день)\b", low)
    if m:
        out["repeat"] = "daily"
        cut(m)
    if not out["repeat"]:
        m = re.search(r"\b(по\s+будням|в\s+будни|по\s+будним\s+дням|будни)\b", low)
        if m:
            out["repeat"] = "weekdays"
            cut(m)
    if not out["repeat"]:
        m = re.search(r"\bкаждые\s+(\d+)\s+(?:дня|дней|день)\b", low)
        if m and int(m.group(1)) >= 1:
            out["repeat"] = f"every:{int(m.group(1))}"
            cut(m)
    if not out["repeat"]:
        m = re.search(r"\b(?:кажд\w+|по|раз\s+в)\s+((?:[а-яё]+[\s,]+(?:и\s+)?)*[а-яё]+)", low)
        if m:
            days, used = [], 0
            toks = re.findall(r"[а-яё]+", m.group(1))
            for t in toks:
                if t == "и":
                    used += 1
                    continue
                d = _dow_of(t)
                if d is None:
                    break
                days.append(d)
                used += 1
            if days:
                out["repeat"] = "dow:" + ",".join(str(d) for d in sorted(set(days)))
                # вырезаем триггер и только распознанные слова
                pat = r"\b(?:кажд\w+|по|раз\s+в)\s+" + r"[\s,]*".join(r"(?:и\s+)?" + re.escape(t) for t in toks[:used] if t != "и")
                m2 = re.search(pat, low)
                if m2:
                    cut(m2)
    # дата (для повтора - дата начала)
    m = re.search(r"\b(послезавтра|завтра|сегодня)\b", low)
    if m:
        out["due"] = day + dt.timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[m.group(1)])
        cut(m)
    if out["due"] is None:
        m = re.search(r"\bчерез\s+(\d+)\s*(дн\w*|день|дня|нед\w*)", low)
        if m:
            n = int(m.group(1)) * (7 if m.group(2).startswith("нед") else 1)
            out["due"] = day + dt.timedelta(days=min(n, 365))
            cut(m)
    if out["due"] is None:
        m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b", low)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else day.year
            if y < 100:
                y += 2000
            try:
                due = dt.date(y, mo, d)
                if not m.group(3) and (day - due).days > 30:   # недавняя дата - это просрочка, давняя - следующий год
                    due = dt.date(y + 1, mo, d)
                out["due"] = due
                cut(m)
            except ValueError:
                pass
    if out["due"] is None and not out["repeat"]:
        m = re.search(r"\b(?:в|во)\s+([а-яё]+)", low)
        if m:
            d = _dow_of(m.group(1))
            if d is not None:
                out["due"] = _next_dow(day, d)
                cut(m)
    title = re.sub(r"\s+", " ", s).strip(" ,.;:-—")
    out["title"] = (title or raw)[:200]
    out["due"] = out["due"].isoformat() if out["due"] else None
    return out


def repeat_label(rule: str) -> str:
    if rule == "daily":
        return "каждый день"
    if rule == "weekdays":
        return "по будням"
    if rule.startswith("dow:"):
        return "каждый " + ", ".join(DOW_NAMES[int(x)] for x in rule[4:].split(",") if x.isdigit())
    if rule.startswith("every:"):
        return f"каждые {rule[6:]} дн."
    return rule


def repeat_applies(rule: str, anchor: dt.date, day: dt.date) -> bool:
    if day < anchor:
        return False
    if rule == "daily":
        return True
    if rule == "weekdays":
        return day.weekday() < 5
    if rule.startswith("dow:"):
        return day.weekday() in {int(x) for x in rule[4:].split(",") if x.isdigit()}
    if rule.startswith("every:"):
        try:
            n = max(int(rule[6:]), 1)
        except ValueError:
            return False
        return (day - anchor).days % n == 0
    return False


# ---------- задачи ----------

def _row(c, sql: str, *args):
    return c.execute(sql, args).fetchone()


def add_task(channel: str, title: str, due: str | None = None, kind: str = "task",
             tpl_id: int | None = None, task_id: int | None = None, at: str | None = None) -> int:
    init()
    if at and not due and kind == "task":
        due = today().isoformat()
    with connect() as c:
        cur = c.execute('INSERT INTO todo(channel,kind,title,due,tpl_id,task_id,created_at,"at") VALUES(?,?,?,?,?,?,?,?)',
                        (channel, kind, title[:1500], due, tpl_id, task_id, time.time(), at))
        return cur.lastrowid


def get(item_id: int):
    init()
    with connect() as c:
        return _row(c, "SELECT * FROM todo WHERE id=?", item_id)


def toggle(item_id: int) -> str | None:
    """open <-> done. Возвращает новый статус."""
    init()
    with connect() as c:
        r = _row(c, "SELECT status FROM todo WHERE id=?", item_id)
        if r is None:
            return None
        new = "done" if r["status"] == "open" else "open"
        c.execute("UPDATE todo SET status=?, done_at=? WHERE id=?", (new, time.time() if new == "done" else None, item_id))
        return new


def set_due(item_id: int, due: str | None) -> None:
    init()
    with connect() as c:
        c.execute("UPDATE todo SET due=? WHERE id=?", (due, item_id))


def set_channel(item_id: int, channel: str) -> None:
    init()
    with connect() as c:
        c.execute("UPDATE todo SET channel=? WHERE id=?", (channel, item_id))


def delete(item_id: int) -> None:
    init()
    with connect() as c:
        c.execute("DELETE FROM todo WHERE id=?", (item_id,))


def _chan_sql(channel: str | None) -> tuple[str, tuple]:
    return ("AND channel=?", (channel,)) if channel else ("", ())


def open_items(channel: str | None) -> list:
    init()
    sql, args = _chan_sql(channel)
    with connect() as c:
        return c.execute(f"SELECT * FROM todo WHERE status='open' {sql} ORDER BY COALESCE(due,'9999'), COALESCE(\"at\",'99:99'), id", args).fetchall()


def done_items(channel: str | None, limit: int = 15) -> list:
    init()
    sql, args = _chan_sql(channel)
    with connect() as c:
        return c.execute(f"SELECT * FROM todo WHERE status='done' {sql} ORDER BY done_at DESC LIMIT ?", (*args, limit)).fetchall()


def done_on(channel: str | None, day: dt.date) -> list:
    """Выполненное в указанный день (остаётся на экране «Сегодня» до конца дня)."""
    init()
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz()).timestamp()
    sql, args = _chan_sql(channel)
    with connect() as c:
        return c.execute(f"SELECT * FROM todo WHERE status='done' AND done_at>=? AND done_at<? {sql} ORDER BY done_at",
                         (start, start + 86400, *args)).fetchall()


def done_count_on(channel: str | None, day: dt.date) -> int:
    init()
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz()).timestamp()
    sql, args = _chan_sql(channel)
    with connect() as c:
        return c.execute(f"SELECT COUNT(*) FROM todo WHERE status='done' AND done_at>=? AND done_at<? {sql}",
                         (start, start + 86400, *args)).fetchone()[0]


def groups(channel: str | None, view: str, day: dt.date) -> list[tuple[str, list]]:
    """Экран по видам: today | tomorrow | week | done -> [(заголовок группы, [строки])]."""
    if view == "done":
        rows = done_items(channel)
        return [("✅ Сделано (последние)", rows)] if rows else []
    items = open_items(channel)
    iso = day.isoformat()
    out: list[tuple[str, list]] = []
    if view == "today":
        over = [r for r in items if r["kind"] == "task" and r["due"] and r["due"] < iso]
        now_ = [r for r in items if r["kind"] == "task" and r["due"] == iso]
        vids = [r for r in items if r["kind"] == "video"]
        later = [r for r in items if r["kind"] == "task" and (not r["due"] or r["due"] > iso)]
        for title, rows in (("🔴 Просрочено", over), ("🟠 Сегодня", now_), ("🎬 Видео на загрузку", vids),
                            ("📝 В планах", later[:20]), ("✅ Выполнено сегодня", done_on(channel, day))):
            if rows:
                out.append((title, rows))
    elif view == "tomorrow":
        d = (day + dt.timedelta(days=1)).isoformat()
        rows = [r for r in items if r["due"] == d]
        if rows:
            out.append(("🗓 Завтра", rows))
    elif view == "week":
        for k in range(1, 8):
            d = day + dt.timedelta(days=k)
            rows = [r for r in items if r["due"] == d.isoformat()]
            if rows:
                out.append((f"🗓 {DOW_NAMES[d.weekday()]} {d.strftime('%d.%m')}", rows))
    return out


def age_days(row, day: dt.date) -> int:
    """Сколько дней задача просрочена; для видео без срока - сколько дней оно ждёт загрузки."""
    if not row["due"]:
        if row["kind"] == "video":
            return max((day - dt.datetime.fromtimestamp(row["created_at"], tz()).date()).days, 0)
        return 0
    try:
        return (day - dt.date.fromisoformat(row["due"])).days
    except ValueError:
        return 0


# ---------- повторы ----------

def add_template(channel: str, title: str, repeat: str, anchor: str | None = None, at: str | None = None) -> int:
    init()
    with connect() as c:
        cur = c.execute('INSERT INTO todo_tpl(channel,title,repeat,anchor,created_at,"at") VALUES(?,?,?,?,?,?)',
                        (channel, title[:1500], repeat, anchor or today().isoformat(), time.time(), at))
        return cur.lastrowid


def templates(channel: str | None) -> list:
    init()
    sql, args = _chan_sql(channel)
    with connect() as c:
        return c.execute(f"SELECT * FROM todo_tpl WHERE 1=1 {sql} ORDER BY id", args).fetchall()


def toggle_template(tpl_id: int) -> None:
    init()
    with connect() as c:
        c.execute("UPDATE todo_tpl SET active=1-active WHERE id=?", (tpl_id,))


def delete_template(tpl_id: int) -> None:
    """Удаляет повтор вместе с его ещё не выполненными задачами (выполненные остаются в истории)."""
    init()
    with connect() as c:
        c.execute("DELETE FROM todo_tpl WHERE id=?", (tpl_id,))
        c.execute("DELETE FROM todo WHERE tpl_id=? AND status IN ('open','skip')", (tpl_id,))


def make_repeating(item_id: int, rule: str) -> int | None:
    """Делает существующую задачу повторяющейся: создаётся повтор, сама задача становится его сегодняшним экземпляром."""
    init()
    r = get(item_id)
    if r is None or r["kind"] != "task":
        return None
    tpl = add_template(r["channel"], r["title"], rule, today().isoformat(), r["at"])
    with connect() as c:
        c.execute("UPDATE todo SET tpl_id=?, due=COALESCE(due, ?) WHERE id=?", (tpl, today().isoformat(), item_id))
    return tpl


def add_starter(channel: str) -> int:
    """Три ежедневные задачи для старта; уже существующие не дублируются."""
    have = {t["title"] for t in templates(channel)}
    n = 0
    for title in STARTER:
        if title not in have:
            add_template(channel, title, "daily")
            n += 1
    return n


def ensure_today(day: dt.date | None = None) -> int:
    """Создаёт задачи на сегодня из повторов. Невыполненная прошлая задача того же повтора остаётся открытой
    (она просрочена и попадёт в отчёт), вторая такая же не создаётся."""
    init()
    day = day or today()
    iso = day.isoformat()
    made = 0
    with connect() as c:
        for t in c.execute("SELECT * FROM todo_tpl WHERE active=1").fetchall():
            try:
                anchor = dt.date.fromisoformat(t["anchor"])
            except ValueError:
                continue
            if not repeat_applies(t["repeat"], anchor, day):
                continue
            if c.execute("SELECT 1 FROM todo WHERE tpl_id=? AND (status='open' OR due=?)", (t["id"], iso)).fetchone():
                continue
            c.execute('INSERT INTO todo(channel,kind,title,due,tpl_id,created_at,"at") VALUES(?,?,?,?,?,?,?)',
                      (t["channel"], "task", t["title"], iso, t["id"], time.time(), t["at"]))
            made += 1
    return made


# ---------- видео из очереди ----------

def ready_tasks(channel: str | None, limit: int = 10) -> list:
    """Готовые (выгруженные) задачи воркера, которых ещё нет в задачнике."""
    init()
    sql, args = _chan_sql(channel)
    with connect() as c:
        return c.execute(
            "SELECT id, channel, updated_at FROM tasks WHERE status='done' "
            f"AND id NOT IN (SELECT task_id FROM todo WHERE task_id IS NOT NULL) {sql} ORDER BY id DESC LIMIT ?",
            (*args, limit)).fetchall()


def video_title(channel: str, task_id: int, remote: bool = True) -> str:
    """Название ролика из metadata.json: локально, а если папку уже удалили - с Диска."""
    raw = None
    local = config.TASKS_DIR / f"task_{task_id}" / "metadata.json"
    try:
        raw = local.read_text("utf-8")
    except OSError:
        if remote and config.DRIVE_ENABLED:
            try:
                res = subprocess.run(
                    ["rclone", "cat", f"{config.RCLONE_REMOTE}:{config.RCLONE_BASE_PATH}/{channel}/task_{task_id}/metadata.json"],
                    capture_output=True, text=True, timeout=20)
                raw = res.stdout if res.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                raw = None
    try:
        title = (json.loads(raw or "{}").get("title") or "").strip()
    except ValueError:
        title = ""
    return title or "Ролик"


def add_video(task_id: int, channel: str, title: str) -> int | None:
    """Ролик на загрузку. None - такой уже есть."""
    init()
    try:
        return add_task(channel, f"task_{task_id} · {title}", None, "video", task_id=task_id)
    except Exception:  # noqa: BLE001 - уникальный индекс: ролик уже в списке
        return None


# ---------- отчёт ----------

def report_text(day: dt.date | None = None) -> tuple[str, int]:
    """Текст утреннего отчёта о срочном и число срочных пунктов."""
    init()
    day = day or today()
    iso = day.isoformat()
    items = open_items(None)
    over = [r for r in items if r["kind"] == "task" and r["due"] and r["due"] < iso]
    now_ = [r for r in items if r["kind"] == "task" and r["due"] == iso]
    vids = [r for r in items if r["kind"] == "video"]
    lines = [f"🌅 Отчёт на {DOW_NAMES[day.weekday()]} {day.strftime('%d.%m')} ({setting('tz', DEFAULT_TZ)})"]
    left = 40

    def block(title: str, rows: list, with_age: bool) -> None:
        nonlocal left
        if not rows:
            return
        lines.append(f"\n{title} ({len(rows)}):")
        for r in rows[:left]:
            age = age_days(r, day) if with_age else 0
            tail = f" — {age} дн." if age > 0 else ""
            first = r["title"].split("\n")[0]
            lines.append(f"• [{r['channel']}] {(r['at'] + ' ') if r['at'] else ''}{first}{tail}")
        if len(rows) > left:
            lines.append(f"…и ещё {len(rows) - left}")
        left = max(left - len(rows), 5)

    block("🔴 Просрочено", over, True)
    block("🟠 На сегодня", now_, False)
    block("🎬 Видео ждут загрузки", vids, True)
    urgent = len(over) + len(now_) + len(vids)
    if not urgent:
        lines.append("\n✅ Срочных задач нет.")
    return "\n".join(lines), urgent


# ---------- недельный план, время, напоминания ----------

def set_at(item_id: int, at: str | None) -> None:
    init()
    with connect() as c:
        c.execute('UPDATE todo SET "at"=?, rem=0, due=COALESCE(due, ?) WHERE id=?', (at, today().isoformat() if at else None, item_id))


def set_title(item_id: int, title: str) -> None:
    init()
    with connect() as c:
        c.execute("UPDATE todo SET title=? WHERE id=?", (title.strip()[:1500], item_id))


def move_to(item_id: int, due: str | None) -> None:
    """Перенос на другой день; напоминание срабатывает заново."""
    init()
    with connect() as c:
        c.execute("UPDATE todo SET due=?, rem=0 WHERE id=?", (due, item_id))


def copy_item(item_id: int, due: str | None) -> int | None:
    r = get(item_id)
    if r is None or r["kind"] != "task":
        return None
    return add_task(r["channel"], r["title"], due, at=r["at"])


def day_view(channel: str | None, day: dt.date) -> dict:
    """Экран канала на день: просроченное (только для сегодняшнего дня и позже), задачи дня, видео на загрузку."""
    init()
    iso = day.isoformat()
    sql, args = _chan_sql(channel)
    order = "ORDER BY COALESCE(\"at\",'99:99'), id"
    with connect() as c:
        items = c.execute(f"SELECT * FROM todo WHERE kind='task' AND status!='skip' AND due=? {sql} {order}", (iso, *args)).fetchall()
        over, vids, nodate = [], [], []
        if day >= today():
            over = c.execute(f"SELECT * FROM todo WHERE kind='task' AND status='open' AND due<? {sql} ORDER BY due, COALESCE(\"at\",'99:99'), id",
                             (today().isoformat(), *args)).fetchall()
        if day == today():
            vids = c.execute(f"SELECT * FROM todo WHERE kind='video' AND status='open' {sql} ORDER BY id", args).fetchall()
            nodate = c.execute(f"SELECT * FROM todo WHERE kind='task' AND status='open' AND due IS NULL {sql} ORDER BY id", args).fetchall()
    return {"over": over, "items": items, "videos": vids, "nodate": nodate}


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def week_rows(channel: str | None, start: dt.date) -> dict:
    init()
    sql, args = _chan_sql(channel)
    out = {}
    with connect() as c:
        for k in range(7):
            d = start + dt.timedelta(days=k)
            out[d] = c.execute(f"SELECT * FROM todo WHERE kind='task' AND status!='skip' AND due=? {sql} ORDER BY COALESCE(\"at\",'99:99'), id",
                               (d.isoformat(), *args)).fetchall()
    return out


def copy_week(channel: str, start: dt.date, shift_days: int = 7) -> int:
    n = 0
    for d, rows in week_rows(channel, start).items():
        for r in rows:
            if r["tpl_id"]:
                continue
            add_task(r["channel"], r["title"], (d + dt.timedelta(days=shift_days)).isoformat(), at=r["at"])
            n += 1
    return n


_DAY_HEAD = re.compile(r"^\s*[-•*#]*\s*(понедельник|вторник|среда|среду|четверг|пятница|пятницу|суббота|субботу|воскресенье|пн|вт|ср|чт|пт|сб|вс|завтра|сегодня)"
                       r"(?:[\s,]+(\d{1,2})\.(\d{1,2}))?\s*[:.\-—]*\s*$", re.I)
_DATE_HEAD = re.compile(r"^\s*[-•*]*\s*(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\s*(?:[(\[]?(?:пн|вт|ср|чт|пт|сб|вс)[)\]]?)?\s*[:\-—]*\s*$", re.I)


def parse_plan(text: str, channels: list[str], start: dt.date, default_channel: str) -> list[dict]:
    """План недели из текста. Строка с днём («пн», «вторник», «12.10») начинает день; каждая следующая строка - задача
    (время в начале: «10.30 Снять мемы» или «10:30 ...»); строка, начинающаяся с «+» или «>», дополняет предыдущую задачу;
    строка «#Канал» переключает канал. Возвращает [{channel, due, at, title}]."""
    out: list[dict] = []
    day, ch = None, default_channel
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^#([\w\-]+)$", line)
        if m:
            for c in channels:
                if c.lower() == m.group(1).lower():
                    ch = c
            continue
        h = _DAY_HEAD.match(line)
        if h:
            w = h.group(1).lower()
            if w == "сегодня":
                day = today()
            elif w == "завтра":
                day = today() + dt.timedelta(days=1)
            else:
                dow = _dow_of(w)
                day = start + dt.timedelta(days=dow if dow is not None else 0)
            if h.group(2):
                try:
                    day = dt.date(day.year, int(h.group(3)), int(h.group(2)))
                except ValueError:
                    pass
            continue
        h = _DATE_HEAD.match(line)
        if h:
            try:
                day = dt.date(start.year, int(h.group(2)), int(h.group(1)))
                continue
            except ValueError:
                pass
        if line[0] in "+>" and out:
            out[-1]["title"] = (out[-1]["title"] + "\n" + line.lstrip("+> ").strip())[:1500]
            continue
        if day is None:
            continue
        at, title = extract_time(line.lstrip("-•* ").strip())
        if title:
            out.append({"channel": ch, "due": day.isoformat(), "at": at, "title": title[:1500]})
    return out


def due_reminders(now_: dt.datetime | None = None) -> list:
    """Задачи со временем, до которых осталось 2 часа или меньше (и ещё не прошёл час после начала), о которых не напоминали."""
    init()
    now_ = now_ or now()
    out = []
    with connect() as c:
        for r in c.execute('SELECT * FROM todo WHERE kind=\'task\' AND status=\'open\' AND "at" IS NOT NULL AND due IS NOT NULL AND rem=0').fetchall():
            try:
                h, m = r["at"].split(":")
                when = dt.datetime.combine(dt.date.fromisoformat(r["due"]), dt.time(int(h), int(m)), tzinfo=tz())
            except (ValueError, TypeError):
                continue
            if when - dt.timedelta(hours=2) <= now_ < when + dt.timedelta(hours=1):
                out.append((r, when))
    return out


def mark_reminded(item_id: int) -> None:
    init()
    with connect() as c:
        c.execute("UPDATE todo SET rem=1 WHERE id=?", (item_id,))


def stop_repeat(item_id: int) -> None:
    """Прекращает повтор: шаблон удаляется, сама задача остаётся обычной."""
    init()
    r = get(item_id)
    if r is None or not r["tpl_id"]:
        return
    with connect() as c:
        c.execute("DELETE FROM todo_tpl WHERE id=?", (r["tpl_id"],))
        c.execute("UPDATE todo SET tpl_id=NULL WHERE tpl_id=?", (r["tpl_id"],))


def skip(item_id: int) -> None:
    """Убирает задачу из списков. Экземпляр повтора не удаляется, а помечается пропущенным: иначе утром он создался бы снова."""
    init()
    r = get(item_id)
    if r is None:
        return
    if r["tpl_id"]:
        with connect() as c:
            c.execute("UPDATE todo SET status='skip' WHERE id=?", (item_id,))
    else:
        delete(item_id)


def clear_overdue(channel: str | None) -> int:
    init()
    iso = today().isoformat()
    sql, args = _chan_sql(channel)
    with connect() as c:
        ids = [r["id"] for r in c.execute(f"SELECT id FROM todo WHERE kind='task' AND status='open' AND due<? {sql}", (iso, *args)).fetchall()]
    for i in ids:
        skip(i)
    return len(ids)
