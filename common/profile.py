"""Профили каналов: настройки по умолчанию для каждого канала (голос, фон, музыка, субтитры).
Хранятся в SQLite; при создании задачи копируются в metadata.json, поэтому воркер от них не зависит.
"""
from common import config
from common.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_settings (
    channel TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (channel, key)
);
"""

DEFAULTS = {
    "voice": config.TTS_VOICE,
    "bg": "auto",        # auto | название темы фонов
    "music": "off",      # off | auto | тег музыки
    "vol": "12",         # громкость музыки под голосом, %
    "subs": "1",         # 1 - субтитры, 0 - без
    "lang": config.WHISPER_LANGUAGE,  # язык аудио для субтитров: ru | en | auto
    "rate": "0",         # ускорение озвучки по умолчанию, %: действует везде, где у задачи своя скорость не выбрана
}


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def get(channel: str) -> dict:
    init()
    with connect() as c:
        rows = c.execute("SELECT key, value FROM channel_settings WHERE channel=?", (channel,)).fetchall()
    return {**DEFAULTS, **{r["key"]: r["value"] for r in rows}}


def set_value(channel: str, key: str, value: str) -> None:
    init()
    with connect() as c:
        c.execute(
            "INSERT INTO channel_settings(channel,key,value) VALUES(?,?,?) "
            "ON CONFLICT(channel,key) DO UPDATE SET value=excluded.value",
            (channel, key, str(value)),
        )


def for_task(channel: str) -> dict:
    """Поля, которые попадают в metadata.json задачи."""
    p = get(channel)
    return {
        "voice": p["voice"], "bg": p["bg"], "music": p["music"],
        "music_vol": int(p["vol"]), "subs": p["subs"] == "1", "lang": p["lang"],
        "speech_rate": int(p.get("rate") or 0),
    }
