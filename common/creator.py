"""Бот для креаторов: кредиты, бесплатный старт, лимиты, покупки Stars. Таблицы живут в той же SQLite, что и очередь
(у экземпляра для креаторов своя база: FACTORY_ROOT отдельный)."""
import time
from typing import Optional

from common import config
from common.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS cr_users (
    user_id   INTEGER PRIMARY KEY,
    name      TEXT,
    credits   INTEGER NOT NULL DEFAULT 0,
    free_used INTEGER NOT NULL DEFAULT 0,
    banned    INTEGER NOT NULL DEFAULT 0,
    created   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cr_ledger (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    delta   INTEGER NOT NULL,
    kind    TEXT NOT NULL,         -- free | spend | refund | buy | grant
    ref     TEXT,                  -- task_id или payment charge id
    ts      REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_ledger_pay ON cr_ledger(kind, ref) WHERE kind IN ('buy','refund','revoke');
CREATE TABLE IF NOT EXISTS cr_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ts      REAL NOT NULL,
    title   TEXT
);
CREATE TABLE IF NOT EXISTS cr_tasks (
    task_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    paid    INTEGER NOT NULL,      -- 1 - списан кредит, 0 - бесплатный (с водяным знаком)
    ts      REAL NOT NULL
);
"""


KINDS = {"video": "роликов", "dl": "скачиваний", "cut": "нарезок", "reels": "монтажей ReelsEditor", "ref": "разборов референса"}


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        cols = [r[1] for r in c.execute("PRAGMA table_info(cr_tasks)").fetchall()]
        if "kind" not in cols:       # база патчей 39-41: добавляем вид задачи
            c.execute("ALTER TABLE cr_tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'video'")


def _user(c, uid: int, name: str = "") -> dict:
    row = c.execute("SELECT * FROM cr_users WHERE user_id=?", (uid,)).fetchone()
    if row is None:
        c.execute("INSERT INTO cr_users(user_id,name,created) VALUES(?,?,?)", (uid, name[:64], time.time()))
        row = c.execute("SELECT * FROM cr_users WHERE user_id=?", (uid,)).fetchone()
    return dict(row)


def get(uid: int, name: str = "") -> dict:
    init()
    with connect() as c:
        return _user(c, uid, name)


def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def free_left(u: dict) -> int:
    return max(0, config.FREE_VIDEOS - u["free_used"])


def today_count(uid: int, kind: str = "video") -> int:
    since = time.time() - 86400
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM cr_tasks WHERE user_id=? AND kind=? AND paid>=0 AND ts>?", (uid, kind, since)).fetchone()[0]


def daily_limit(kind: str) -> int:
    return {"video": config.DAILY_LIMIT, "dl": config.DL_DAILY, "cut": config.CUT_DAILY, "reels": config.REELS_DAILY, "ref": config.REF_DAILY}.get(kind, config.DAILY_LIMIT)


def active_count(uid: int) -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM tasks t JOIN cr_tasks k ON k.task_id=t.id "
                         "WHERE k.user_id=? AND t.status IN ('new','processing','uploading')", (uid,)).fetchone()[0]


def free_today_total() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM cr_tasks WHERE paid=0 AND kind='video' AND ts>?", (time.time() - 86400,)).fetchone()[0]


def free_active() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM tasks t JOIN cr_tasks k ON k.task_id=t.id "
                         "WHERE k.paid=0 AND k.kind='video' AND t.status IN ('new','processing','uploading')").fetchone()[0]


def check(uid: int, kind: str = "video") -> tuple[bool, str]:
    """Можно ли поставить задачу: (да/нет, причина). Админы без ограничений.
    kind: video - ролик, dl - скачивание, cut - нарезка (скачивание и нарезка бесплатны всегда, со своими суточными лимитами)."""
    u = get(uid)
    if u["banned"]:
        return False, "Доступ закрыт."
    if is_admin(uid):
        return True, ""
    if active_count(uid) >= config.MAX_QUEUED:
        return False, f"У вас уже {config.MAX_QUEUED} задачи в работе. Дождитесь готовности - бот пришлёт результат."
    lim = daily_limit(kind)
    if today_count(uid, kind) >= lim:
        return False, f"Суточный лимит - {lim} {KINDS.get(kind, 'задач')}. Счётчик обнуляется через 24 часа после первой задачи."
    if kind == "video" and not config.FREE_MODE and u["credits"] <= 0:   # бесплатное демо: общие ограничения против массовых аккаунтов
        if free_left(u) <= 0:
            return False, "no_credits"
        if free_today_total() >= config.FREE_DAILY_TOTAL:
            return False, "Бесплатные демо на сегодня закончились у всех - попробуйте завтра или возьмите пакет: /buy"
        if free_active() >= config.FREE_QUEUE_MAX:
            return False, "Сейчас много бесплатных роликов в очереди - подождите 10-15 минут или возьмите пакет: /buy (платные идут вперёд)"
    from common import db
    if db.queue_size() >= config.MAX_QUEUE_TOTAL:
        return False, "Очередь сейчас переполнена, попробуйте через полчаса."
    return True, ""


def reserve(uid: int, task_id: int, kind: str = "video") -> bool:
    """Списывает кредит (или бесплатное демо) за ролик. Возвращает watermark: True = демо с водяным знаком.
    В бесплатном режиме и для скачивания/нарезки ничего не списывается."""
    init()
    with connect() as c:
        u = _user(c, uid)
        if is_admin(uid):
            paid, free = 1, False
        elif kind != "video" or config.FREE_MODE:
            paid, free = 0, False
        elif u["credits"] > 0:
            c.execute("UPDATE cr_users SET credits=credits-1 WHERE user_id=?", (uid,))
            c.execute("INSERT INTO cr_ledger(user_id,delta,kind,ref,ts) VALUES(?,?,?,?,?)", (uid, -1, "spend", str(task_id), time.time()))
            paid, free = 1, False
        elif free_left(u) > 0:
            c.execute("UPDATE cr_users SET free_used=free_used+1 WHERE user_id=?", (uid,))
            c.execute("INSERT INTO cr_ledger(user_id,delta,kind,ref,ts) VALUES(?,?,?,?,?)", (uid, 0, "free", str(task_id), time.time()))
            paid, free = 0, True
        else:
            raise RuntimeError("нет кредитов")
        c.execute("INSERT OR REPLACE INTO cr_tasks(task_id,user_id,paid,ts,kind) VALUES(?,?,?,?,?)", (task_id, uid, paid, time.time(), kind))
    from common import adminstats
    adminstats.on_task(uid, task_id, kind)
    return free


def refund(task_id: int) -> Optional[int]:
    """Ролик не получился - возвращаем кредит (один раз). Возвращает user_id, если вернули."""
    with connect() as c:
        row = c.execute("SELECT * FROM cr_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        try:
            c.execute("INSERT INTO cr_ledger(user_id,delta,kind,ref,ts) VALUES(?,?,?,?,?)",
                      (row["user_id"], 1 if row["paid"] == 1 else 0, "refund", str(task_id), time.time()))
        except Exception:  # noqa: BLE001 - уже возвращали (уникальный индекс)
            return None
        spent = c.execute("SELECT kind FROM cr_ledger WHERE ref=? AND kind IN ('spend','free') ORDER BY id DESC LIMIT 1",
                          (str(task_id),)).fetchone()
        if spent and spent["kind"] == "spend":
            c.execute("UPDATE cr_users SET credits=credits+1 WHERE user_id=?", (row["user_id"],))
        elif spent and spent["kind"] == "free":
            c.execute("UPDATE cr_users SET free_used=MAX(0,free_used-1) WHERE user_id=?", (row["user_id"],))
        c.execute("UPDATE cr_tasks SET paid=-1 WHERE task_id=?", (task_id,))   # неудачный ролик не считается в суточные лимиты
        return row["user_id"]


def kind_of(task_id: int) -> str:
    with connect() as c:
        row = c.execute("SELECT kind FROM cr_tasks WHERE task_id=?", (task_id,)).fetchone()
    return row["kind"] if row else "video"


def owner_of(task_id: int) -> Optional[int]:
    with connect() as c:
        row = c.execute("SELECT user_id FROM cr_tasks WHERE task_id=?", (task_id,)).fetchone()
    return row["user_id"] if row else None


def add_credits(uid: int, n: int, kind: str = "grant", ref: str = "") -> bool:
    """kind='buy' - идемпотентно по ref (id платежа Telegram): повторное уведомление не начислит дважды."""
    init()
    with connect() as c:
        _user(c, uid)
        try:
            c.execute("INSERT INTO cr_ledger(user_id,delta,kind,ref,ts) VALUES(?,?,?,?,?)", (uid, n, kind, ref or None, time.time()))
        except Exception:  # noqa: BLE001
            return False
        c.execute("UPDATE cr_users SET credits=MAX(0,credits+?) WHERE user_id=?", (n, uid))
    return True


def set_ban(uid: int, banned: bool) -> None:
    get(uid)
    with connect() as c:
        c.execute("UPDATE cr_users SET banned=? WHERE user_id=?", (1 if banned else 0, uid))


def queue_position(task_id: int) -> int:
    """Сколько задач впереди: выполняемые, платные раньше (если ролик платный - только платные с меньшим номером), затем по номеру."""
    with connect() as c:
        row = c.execute("SELECT paid FROM cr_tasks WHERE task_id=?", (task_id,)).fetchone()
        paid = bool(row and row["paid"] == 1)
        base = ("SELECT COUNT(*) FROM tasks t LEFT JOIN cr_tasks k ON k.task_id=t.id WHERE t.id!=? AND "
                "t.status IN ('new','processing','uploading') AND ")
        if paid:
            return c.execute(base + "(t.status!='new' OR (COALESCE(k.paid,0)=1 AND t.id<?))", (task_id, task_id)).fetchone()[0]
        return c.execute(base + "(t.status!='new' OR COALESCE(k.paid,0)=1 OR t.id<?)", (task_id, task_id)).fetchone()[0]


def log_submit(uid: int, title: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO cr_log(user_id,ts,title) VALUES(?,?,?)", (uid, time.time(), (title or "")[:100]))


def purge_log(days: int = 30) -> None:
    with connect() as c:
        c.execute("DELETE FROM cr_log WHERE ts<?", (time.time() - days * 86400,))


def blocked(text: str) -> str:
    """Стоп-слова из <каталог>/blocklist.txt (по слову на строку, регистр не важен). Возвращает найденное слово или ''."""
    try:
        words = [w.strip().lower() for w in (config.ROOT / "blocklist.txt").read_text("utf-8").splitlines() if w.strip() and not w.startswith("#")]
    except OSError:
        return ""
    low = (text or "").lower()
    return next((w for w in words if w in low), "")


def revoke_payment(charge_id: str) -> tuple[int, int] | None:
    """Возврат Stars: находит покупку, снимает кредиты (не глубже нуля). Один раз на платёж. Возвращает (user_id, снято)."""
    with connect() as c:
        row = c.execute("SELECT user_id, delta FROM cr_ledger WHERE kind='buy' AND ref=?", (charge_id,)).fetchone()
        if row is None:
            return None
        try:
            c.execute("INSERT INTO cr_ledger(user_id,delta,kind,ref,ts) VALUES(?,?,?,?,?)", (row["user_id"], -row["delta"], "revoke", charge_id, time.time()))
        except Exception:  # noqa: BLE001
            return None
        have = c.execute("SELECT credits FROM cr_users WHERE user_id=?", (row["user_id"],)).fetchone()["credits"]
        take = min(have, row["delta"])
        c.execute("UPDATE cr_users SET credits=credits-? WHERE user_id=?", (take, row["user_id"]))
        return row["user_id"], take


def payment_known(charge_id: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM cr_ledger WHERE kind='buy' AND ref=?", (charge_id,)).fetchone() is not None


def delete_user_data(uid: int) -> None:
    """Право на удаление: сценарии, настройки, пресеты и имя. Журнал платежей остаётся обезличенным (бухгалтерский учёт)."""
    with connect() as c:
        for sql in ("DELETE FROM wizard WHERE user_id=?", "DELETE FROM presets WHERE user_id=?", "DELETE FROM user_state WHERE user_id=?",
                    "DELETE FROM cr_log WHERE user_id=?", "DELETE FROM cr_events WHERE user_id=?",
                    "UPDATE cr_users SET username='', src='' WHERE user_id=?"):
            try:
                c.execute(sql, (uid,))
            except Exception:  # noqa: BLE001 - таблицы создаются лениво
                pass
        c.execute("UPDATE cr_users SET name=''  WHERE user_id=?", (uid,))
    import shutil
    shutil.rmtree(config.ROOT / "brands" / str(uid), ignore_errors=True)


def done_today() -> int:
    """Сколько задач бот реально выполнил за последние сутки (для честного «сегодня сделано N»)."""
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM tasks WHERE status='done' AND updated_at>?", (time.time() - 86400,)).fetchone()[0]


def activity_line() -> str:
    n = done_today()
    return f"🔥 За сутки бот сделал {n} {_plural(n, 'задачу', 'задачи', 'задач')}." if n >= 3 else ""


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def stats() -> dict:
    with connect() as c:
        users = c.execute("SELECT COUNT(*) FROM cr_users").fetchone()[0]
        day = time.time() - 86400
        videos = c.execute("SELECT COUNT(*) FROM cr_tasks WHERE ts>?", (day,)).fetchone()[0]
        paid = c.execute("SELECT COUNT(*) FROM cr_tasks WHERE ts>? AND paid=1", (day,)).fetchone()[0]
        bought = c.execute("SELECT COALESCE(SUM(delta),0) FROM cr_ledger WHERE kind='buy'").fetchone()[0]
    return {"users": users, "videos24": videos, "paid24": paid, "bought": bought}


def pack_price(credits: int) -> Optional[int]:
    for n, stars in config.CREATOR_PACKS:
        if n == credits:
            return stars
    return None
