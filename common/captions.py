"""Разбор подписи ролика: «Заголовок | Описание | #теги»."""
import re


def parse_caption(text: str | None) -> dict | None:
    """'Заголовок | Описание | #теги' -> dict. None, если формат нарушен."""
    if not text:
        return None
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return None
    tags = re.findall(r"#\w+", parts[2])
    if not tags:
        return None
    return {"title": parts[0][:100], "description": parts[1], "tags": tags}
