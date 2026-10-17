"""Проверка кадров по смыслу через Claude (по картинкам-превью): отвечает, подходит ли кадр под нужную сцену.

Нужен ANTHROPIC_API_KEY в .env (ключ из console.anthropic.com). Без ключа умный монтаж работает по тегам и описаниям роликов.
В Anthropic уходят только маленькие картинки-превью и короткое описание сцены; стоимость - доли цента на ролик.
"""
import base64
import json
import logging
import os
import re
import subprocess
from pathlib import Path

import requests

from common import config

log = logging.getLogger("vision")
API = "https://api.anthropic.com/v1/messages"
MAX_IMAGES = 8
TIMEOUT = 40


def _key() -> str:
    return getattr(config, "ANTHROPIC_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")


def _model() -> str:
    return getattr(config, "VISION_MODEL", "") or os.getenv("VISION_MODEL", "claude-haiku-4-5-20251001")


def available() -> bool:
    return bool(_key())


def _fetch(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.content if 1000 < len(r.content) < 4_000_000 else None
    except Exception as e:  # noqa: BLE001
        log.info("превью не загрузилось %s: %s", url[:80], e)
        return None


def frame_of(video: Path, at: float | None = None) -> bytes | None:
    """Кадр из середины видео (jpeg 360 px по ширине)."""
    try:
        t = at if at is not None else 1.0
        res = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                              "-vf", "scale=360:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                             capture_output=True, timeout=60)
        return res.stdout or None
    except Exception as e:  # noqa: BLE001
        log.info("кадр не извлечён %s: %s", video, e)
        return None


def _prompt(need: str, context: str) -> str:
    return (f"Ты подбираешь кадры для вертикального ролика (YouTube Shorts). Нужно показать: «{need}».\n"
            f"Фраза диктора в этой сцене: «{context}».\n"
            "Оцени каждую картинку выше от 0 до 10: 10 - в кадре именно это, снято красиво и без лишнего; 6 - подходит; "
            "3 - только отдалённо; 0 - не то. Резко снижай оценку (не выше 3), если: в кадре другой главный объект; "
            "видны узнаваемые места, вывески или надписи другой страны, чем в фразе (например, небоскрёбы Дубая вместо Таиланда); "
            "крупные посторонние надписи, логотипы, водяные знаки; кадр мультяшный, очень тёмный или размытый. "
            "Верни ТОЛЬКО JSON-массив чисел по порядку картинок, например [7, 2, 9].")


def score(images: list[bytes | None], need: str, context: str) -> list[float] | None:
    """Оценки 0-10 для каждой картинки (None вместо картинки = 0). None в ответе - проверка недоступна."""
    if not available() or not images:
        return None
    idx = [i for i, b in enumerate(images[:MAX_IMAGES]) if b]
    if not idx:
        return [0.0] * len(images)
    content: list[dict] = []
    for n, i in enumerate(idx, 1):
        content.append({"type": "text", "text": f"Картинка {n}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(images[i]).decode()}})
    content.append({"type": "text", "text": _prompt(need, context)})
    try:
        r = requests.post(API, headers={"x-api-key": _key(), "anthropic-version": "2023-06-01",
                                        "content-type": "application/json"},
                          json={"model": _model(), "max_tokens": 120, "messages": [{"role": "user", "content": content}]},
                          timeout=TIMEOUT)
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        arr = json.loads(re.search(r"\[[^\]]*\]", text).group(0))
        vals = [max(0.0, min(10.0, float(x))) for x in arr]
    except Exception as e:  # noqa: BLE001
        log.warning("проверка кадров через Claude не удалась: %s", str(e)[:200])
        return None
    out = [0.0] * len(images)
    for n, i in enumerate(idx):
        out[i] = vals[n] if n < len(vals) else 0.0
    return out


def fetch_many(urls: list[str | None]) -> list[bytes | None]:
    return [_fetch(u) if u else None for u in urls]
