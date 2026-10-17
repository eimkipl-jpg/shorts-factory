"""Очередь задач на SQLite. Статусы: new -> processing -> uploading -> done | failed."""
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

from common import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',
    input_type  TEXT NOT NULL,            -- audio | text
    chat_id     INTEGER,
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS user_state (
    user_id  INTEGER PRIMARY KEY,
    channel  TEXT NOT NULL
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    config.ensure_dirs()
    with connect() as c:
        c.executescript(SCHEMA)


def create_task(channel: str, input_type: str, chat_id: int) -> int:
    now = time.time()
    with connect() as c:
        cur = c.execute(
            "INSERT INTO tasks(channel,status,input_type,chat_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (channel, "draft", input_type, chat_id, now, now),
        )
        return cur.lastrowid


def mark_ready(task_id: int) -> None:
    """Задача попадает в очередь только когда все файлы уже лежат в папке."""
    set_status(task_id, "new")


def set_status(task_id: int, status: str, error: Optional[str] = None) -> None:
    with connect() as c:
        c.execute(
            "UPDATE tasks SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error, time.time(), task_id),
        )


def claim_next() -> Optional[sqlite3.Row]:
    """Атомарно берёт самую старую новую задачу."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if config.CREATOR:      # платные ролики идут вперёд бесплатных демо
            try:
                row = c.execute("SELECT t.* FROM tasks t LEFT JOIN cr_tasks k ON k.task_id=t.id WHERE t.status='new' "
                                "ORDER BY COALESCE(k.paid,0) DESC, t.id LIMIT 1").fetchone()
            except sqlite3.OperationalError:
                row = None
            if row is None:
                row = c.execute("SELECT * FROM tasks WHERE status='new' ORDER BY id LIMIT 1").fetchone()
        else:
            row = c.execute("SELECT * FROM tasks WHERE status='new' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        c.execute(
            "UPDATE tasks SET status='processing', attempts=attempts+1, updated_at=? WHERE id=?",
            (time.time(), row["id"]),
        )
        return row


def recover_interrupted() -> None:
    """После перезапуска сервера возвращаем зависшие задачи в очередь."""
    with connect() as c:
        c.execute(
            "UPDATE tasks SET status='new', updated_at=? WHERE status IN ('processing','uploading')",
            (time.time(),),
        )


def get_task(task_id: int) -> Optional[sqlite3.Row]:
    with connect() as c:
        return c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()


def queue_size() -> int:
    with connect() as c:
        return c.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('new','processing','uploading')"
        ).fetchone()[0]


def get_user_channel(user_id: int) -> str:
    with connect() as c:
        row = c.execute("SELECT channel FROM user_state WHERE user_id=?", (user_id,)).fetchone()
    if row and row["channel"] in config.CHANNELS:
        return row["channel"]
    return config.CHANNELS[0]


def set_user_channel(user_id: int, channel: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO user_state(user_id,channel) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET channel=excluded.channel",
            (user_id, channel),
        )


def recent_tasks(limit: int = 8) -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute(
            "SELECT id, channel, status, input_type, error FROM tasks "
            "WHERE status != 'draft' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def retry_task(task_id: int) -> bool:
    """Возвращает упавшую задачу в очередь (папка задачи должна ещё существовать)."""
    if not (config.TASKS_DIR / f"task_{task_id}").exists():
        return False
    with connect() as c:
        cur = c.execute(
            "UPDATE tasks SET status='new', attempts=0, error=NULL, updated_at=? "
            "WHERE id=? AND status='failed'", (time.time(), task_id),
        )
        return cur.rowcount > 0
