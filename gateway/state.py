"""Состояние мастера «Создать» (по пользователю, в SQLite) и пресеты настроек."""
import copy
import json
import re

from common import db, profile
from common.db import connect
from common.looks import merged_look, merged_sfx

SCHEMA = """
CREATE TABLE IF NOT EXISTS wizard (user_id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS presets (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name    TEXT NOT NULL,
    data    TEXT NOT NULL,
    UNIQUE(user_id, name)
);
"""


def default(user_id: int) -> dict:
    ch = db.get_user_channel(user_id)
    p = profile.get(ch)
    return {
        "video": {"kind": "clips", "theme": None, "clips": 4, "seconds": 30, "file": None, "start": None, "end": None, "orig_sound": None,
                  "picks": [], "len": "full", "orig_vol": None, "duck": True, "min_h": 0, "balance": None},
        "speech": {"kind": "none", "target": "en", "voice": p["voice"], "rate": 0},
        "music": p["music"], "vol": int(p["vol"]), "subs": p["subs"] == "1", "subs_src": "speech", "subs_lang": None,
        "style": "none", "look": merged_look(None), "sfx": merged_sfx(None),
        "channel": ch, "awaiting": None, "lists": {},
    }


def ensure(st: dict) -> dict:
    """Дополняет состояние, сохранённое старой версией бота, новыми полями."""
    st["look"] = merged_look(st.get("look"))
    st["sfx"] = merged_sfx(st.get("sfx"))
    st["speech"].setdefault("rate", 0)
    st["video"].setdefault("picks", [])
    st["video"].setdefault("len", "full")
    st["video"].setdefault("orig_vol", None)
    st["video"].setdefault("duck", True)
    st["video"].setdefault("min_h", 0)
    st["video"].setdefault("balance", None)
    st.setdefault("lists", {})
    st.setdefault("proj", None)
    st.setdefault("subs_src", "speech")
    st.setdefault("subs_lang", None)
    return st


def load(user_id: int) -> dict:
    with connect() as c:
        c.executescript(SCHEMA)
        row = c.execute("SELECT data FROM wizard WHERE user_id=?", (user_id,)).fetchone()
    return ensure(json.loads(row["data"])) if row else default(user_id)


def save(user_id: int, st: dict) -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        c.execute(
            "INSERT INTO wizard(user_id,data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
            (user_id, json.dumps(st, ensure_ascii=False)),
        )


def waiting(user_id: int) -> str | None:
    try:
        return load(user_id).get("awaiting")
    except Exception:  # noqa: BLE001
        return None


# ---------- пресеты ----------

PRESET_KEYS = ("music", "vol", "subs", "subs_src", "subs_lang", "style", "look", "sfx")


def snapshot(st: dict) -> dict:
    """Что запоминает пресет: всё, кроме конкретного файла/фрагмента, канала и служебных полей."""
    v = {k: st["video"].get(k) for k in ("kind", "theme", "clips", "seconds", "orig_sound", "len", "orig_vol", "duck", "min_h", "balance")}
    return {"video": v, "speech": dict(st["speech"]), **{k: copy.deepcopy(st[k]) for k in PRESET_KEYS}}


def apply(st: dict, data: dict) -> dict:
    st["video"].update({**data.get("video", {}), "file": None, "start": None, "end": None, "picks": []})
    st["speech"].update(data.get("speech", {}))
    for k in PRESET_KEYS:
        if k in data:
            st[k] = copy.deepcopy(data[k])
    return ensure(st)


def list_presets(user_id: int) -> list[dict]:
    with connect() as c:
        c.executescript(SCHEMA)
        rows = c.execute("SELECT id, name, data FROM presets WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "data": json.loads(r["data"])} for r in rows]


def save_preset(user_id: int, name: str, st: dict) -> str:
    name = re.sub(r"\s+", " ", name).strip()[:30]
    with connect() as c:
        c.executescript(SCHEMA)
        c.execute(
            "INSERT INTO presets(user_id,name,data) VALUES(?,?,?) "
            "ON CONFLICT(user_id,name) DO UPDATE SET data=excluded.data",
            (user_id, name, json.dumps(snapshot(st), ensure_ascii=False)),
        )
    return name


def get_preset(user_id: int, preset_id: int) -> dict | None:
    with connect() as c:
        c.executescript(SCHEMA)
        r = c.execute("SELECT id, name, data FROM presets WHERE user_id=? AND id=?", (user_id, preset_id)).fetchone()
    return {"id": r["id"], "name": r["name"], "data": json.loads(r["data"])} if r else None


def delete_preset(user_id: int, preset_id: int) -> bool:
    with connect() as c:
        c.executescript(SCHEMA)
        return c.execute("DELETE FROM presets WHERE user_id=? AND id=?", (user_id, preset_id)).rowcount > 0
