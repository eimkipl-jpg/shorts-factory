"""Фразы для хуков и призывов: у каждого канала свой список, а если он пуст, берутся стандартные (RU/EN по языку озвучки).

Режим «случайный» берёт фразу из списка и не повторяет предыдущую (последняя запоминается отдельно для канала и вида).
Хранится в ROOT/phrases.json: {"канал": {"hook": [...], "cta": [...]}}; последние выбранные - в phrases_last.json.
"""
import json
import logging
import os
import random
import re
import threading
from pathlib import Path

from common import config

log = logging.getLogger("phrases")
KINDS = ("hook", "cta")
MAX_LEN = 80
MAX_PER_LIST = 100
_lock = threading.Lock()

DEFAULTS = {
    "hook": {
        "ru": ["Смотри до конца", "Ты не поверишь", "Подожди до конца", "Это нужно видеть", "Вот это поворот",
               "Такого ты ещё не видел", "Досмотри, не пожалеешь", "Никто этого не ожидал", "Финал удивит",
               "Не моргай", "Главное будет в конце", "Невероятно, но факт", "Ты тоже так делал?", "Дождись финала"],
        "en": ["Watch till the end", "You won't believe this", "Wait for it", "This is wild", "Nobody saw this coming",
               "Don't blink", "Look closely", "The ending hits different", "Stay until the end", "Trust me on this one",
               "This changes everything", "Be ready for this"],
    },
    "cta": {
        "ru": ["Подпишись!", "Продолжение в профиле", "Поделись с другом", "Лайк, если согласен", "Подпишись, чтобы не пропустить",
               "Больше такого на канале", "Напиши в комментариях", "Отправь тому, кто оценит", "Сохрани, чтобы не потерять"],
        "en": ["Follow for more", "Like if you agree", "Share with a friend", "Part 2 on my channel", "Subscribe so you don't miss out",
               "Comment what you think", "Send this to someone who needs it", "Save this for later", "More like this on my channel"],
    },
}


def _file(name: str) -> Path:
    return config.ROOT / name


def _read(name: str) -> dict:
    try:
        data = json.loads(_file(name).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(name: str, data: dict) -> None:
    path = _file(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("не удалось сохранить %s: %s", name, e)


def clean(text: str) -> str:
    return " ".join(str(text).split())[:MAX_LEN].strip()


def defaults(kind: str, lang: str | None) -> list[str]:
    return list(DEFAULTS[kind].get((lang or "ru")[:2].lower(), DEFAULTS[kind]["en"]))


def custom(channel: str, kind: str) -> list[str]:
    items = (_read("phrases.json").get(channel) or {}).get(kind) or []
    return [x for x in items if isinstance(x, str) and x.strip()]


def pool(channel: str, kind: str, lang: str | None) -> list[str]:
    """Свой список канала, а если он пуст - стандартный."""
    return custom(channel, kind) or defaults(kind, lang)


def add(channel: str, kind: str, texts) -> int:
    """Добавляет фразы (строка с переносами или список). Возвращает, сколько добавлено (без повторов)."""
    items = texts.splitlines() if isinstance(texts, str) else list(texts)
    with _lock:
        data = _read("phrases.json")
        cur = (data.setdefault(channel, {}).setdefault(kind, []))
        have = {x.lower() for x in cur}
        added = 0
        for t in items:
            t = clean(re.sub(r"^\s*(?:[-•*]|\d+[.)])\s+", "", t)) if isinstance(t, str) else ""
            if t and t.lower() not in have and len(cur) < MAX_PER_LIST:
                cur.append(t)
                have.add(t.lower())
                added += 1
        _write("phrases.json", data)
    return added


def remove(channel: str, kind: str, index: int) -> bool:
    with _lock:
        data = _read("phrases.json")
        cur = (data.get(channel) or {}).get(kind) or []
        if not 0 <= index < len(cur):
            return False
        cur.pop(index)
        _write("phrases.json", data)
    return True


def clear(channel: str, kind: str) -> None:
    with _lock:
        data = _read("phrases.json")
        if (data.get(channel) or {}).get(kind):
            data[channel][kind] = []
            _write("phrases.json", data)


def pick(channel: str, kind: str, lang: str | None, extra=None) -> str:
    """Случайная фраза из списка канала (или из extra - списка, заданного в сценарии), не равная предыдущей."""
    items = [clean(x) for x in (extra or []) if clean(x)] or pool(channel, kind, lang)
    if not items:
        return ""
    key = f"{channel}|{kind}"
    with _lock:
        last = _read("phrases_last.json")
        options = [x for x in items if x != last.get(key)] or items
        choice = random.choice(options)
        last[key] = choice
        _write("phrases_last.json", last)
    return choice
