"""Короткие сообщения в Telegram из воркера (он живёт отдельно от бота): сообщить, что долгая задача закончилась."""
import logging

import requests

from common import config

log = logging.getLogger("notify")


def send(chat_id: int | None, text: str, buttons: list | None = None) -> bool:
    """Никогда не бросает исключений: уведомление не должно ронять задачу.
    buttons - [[(текст, callback_data), ...], ...] - кнопки под сообщением."""
    if not chat_id or not config.BOT_TOKEN:
        return False
    base = (config.TELEGRAM_API_URL or "https://api.telegram.org").rstrip("/")
    payload = {"chat_id": chat_id, "text": text[:4000]}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
    try:
        r = requests.post(f"{base}/bot{config.BOT_TOKEN}/sendMessage", json=payload, timeout=20)
        return r.status_code == 200
    except requests.RequestException as e:
        log.warning("не удалось отправить уведомление: %s", e)
        return False


def _base() -> str:
    return (config.TELEGRAM_API_URL or "https://api.telegram.org").rstrip("/")


def send_video(chat_id: int | None, path, caption: str = "", width: int = 1080, height: int = 1920) -> tuple[bool, str]:
    """Отправляет готовый ролик в чат (Bot API, до 50 МБ). Возвращает (успех, причина)."""
    if not chat_id or not config.BOT_TOKEN:
        return False, "нет chat_id или токена"
    try:
        with open(path, "rb") as f:
            r = requests.post(f"{_base()}/bot{config.BOT_TOKEN}/sendVideo",
                              data={"chat_id": chat_id, "caption": caption[:1000], "supports_streaming": "true",
                                    **({"width": width, "height": height} if width and height else {})},
                              files={"video": ("video.mp4", f, "video/mp4")}, timeout=600)
        if r.status_code == 200:
            return True, ""
        return False, f"{r.status_code}: {r.text[:200]}"
    except (OSError, requests.RequestException) as e:
        log.warning("не удалось отправить видео: %s", e)
        return False, str(e)[:200]


def send_document(chat_id: int | None, path, caption: str = "", filename: str | None = None) -> tuple[bool, str]:
    if not chat_id or not config.BOT_TOKEN:
        return False, "нет chat_id или токена"
    try:
        with open(path, "rb") as f:
            r = requests.post(f"{_base()}/bot{config.BOT_TOKEN}/sendDocument", data={"chat_id": chat_id, "caption": caption[:1000]},
                              files={"document": (filename or getattr(path, "name", "file"), f)}, timeout=600)
        return (True, "") if r.status_code == 200 else (False, f"{r.status_code}: {r.text[:200]}")
    except (OSError, requests.RequestException) as e:
        return False, str(e)[:200]


def send_media_group(chat_id: int | None, paths: list, caption: str = "") -> tuple[bool, str]:
    """До 10 видео одним альбомом (подпись - у первого)."""
    import json as _json
    if not chat_id or not config.BOT_TOKEN or not paths:
        return False, "нет данных"
    media, files, handles = [], {}, []
    try:
        for i, p in enumerate(paths[:10]):
            key = f"v{i}"
            item = {"type": "video", "media": f"attach://{key}", "supports_streaming": True}
            if i == 0 and caption:
                item["caption"] = caption[:1000]
            media.append(item)
            fh = open(p, "rb")
            handles.append(fh)
            files[key] = (f"{key}.mp4", fh, "video/mp4")
        r = requests.post(f"{_base()}/bot{config.BOT_TOKEN}/sendMediaGroup",
                          data={"chat_id": chat_id, "media": _json.dumps(media)}, files=files, timeout=900)
        return (True, "") if r.status_code == 200 else (False, f"{r.status_code}: {r.text[:200]}")
    except (OSError, requests.RequestException) as e:
        return False, str(e)[:200]
    finally:
        for fh in handles:
            fh.close()


def send_photo(chat_id: int | None, path, caption: str = "") -> tuple[bool, str]:
    if not chat_id or not config.BOT_TOKEN:
        return False, "нет chat_id или токена"
    try:
        with open(path, "rb") as f:
            r = requests.post(f"{_base()}/bot{config.BOT_TOKEN}/sendPhoto", data={"chat_id": chat_id, "caption": caption[:1000]},
                              files={"photo": ("photo.jpg", f, "image/jpeg")}, timeout=120)
        return (True, "") if r.status_code == 200 else (False, f"{r.status_code}: {r.text[:200]}")
    except (OSError, requests.RequestException) as e:
        return False, str(e)[:200]
