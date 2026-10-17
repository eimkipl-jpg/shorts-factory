"""Картинки для умного монтажа: стоковые фото и иллюстрации Pixabay (тот же PIXABAY_API_KEY) и архивные фото
Wikimedia Commons (без ключа: исторические события, люди, места, документы). Картинка в ролике оживает:
медленный наезд или панорама, у горизонтальных - размытая подложка (smart._image_part).

Commons: берутся только свободные лицензии (общественное достояние, CC0, CC BY, CC BY-SA); автор и лицензия
записываются в credits, бот добавляет их в описание ролика.
"""
import html
import logging
import re
from pathlib import Path

import requests

from common import config

log = logging.getLogger("imagesrc")
TIMEOUT = 25
MAX_BYTES = 15 * 1024 * 1024
UA = {"User-Agent": "ShortsFactoryBot/1.0 (personal video tool; contact via Telegram)"}
FREE = ("public domain", "pd", "cc0", "cc by", "cc-by", "attribution")
IMG_DIR_NAME = "images_cache"


def img_dir() -> Path:
    """Кэш картинок вне папки клипов: её зеркалит с Диска rclone sync и стёр бы скачанное."""
    d = config.ROOT / IMG_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def search_pixabay(query: str, kind: str = "photo", per_page: int = 10) -> list[dict]:
    """kind: photo | illustration | vector. -> [{id, url, thumb, tags, w, h, page, credit}]"""
    if not config.PIXABAY_API_KEY or not query:
        return []
    r = requests.get("https://pixabay.com/api/", params={
        "key": config.PIXABAY_API_KEY, "q": query[:100], "image_type": kind, "per_page": max(3, per_page),
        "safesearch": "true", "order": "popular"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        url = h.get("largeImageURL") or h.get("webformatURL")
        if not url:
            continue
        out.append({"provider": "pixabay", "id": str(h["id"]), "url": url, "thumb": h.get("webformatURL") or url,
                    "tags": str(h.get("tags") or ""), "w": h.get("imageWidth"), "h": h.get("imageHeight"),
                    "page": h.get("pageURL", ""), "credit": ""})
    return out


def _strip(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def search_commons(query: str, per_page: int = 10) -> list[dict]:
    """Архивные фото и документы Wikimedia Commons со свободной лицензией."""
    if not query:
        return []
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=TIMEOUT, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": per_page, "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
        "iiurlwidth": 2560})     # крупнее: при 1600 px горизонтальный снимок растягивался почти вдвое
    r.raise_for_status()
    pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        meta = info.get("extmetadata") or {}
        lic = _strip((meta.get("LicenseShortName") or {}).get("value", ""))
        if not any(f in lic.lower() for f in FREE):
            continue
        if (info.get("width") or 0) < 500:
            continue
        artist = _strip((meta.get("Artist") or {}).get("value", ""))[:80] or "неизвестный автор"
        title = re.sub(r"^File:|\.[a-z]+$", "", p.get("title", ""), flags=re.I)
        desc = _strip((meta.get("ImageDescription") or {}).get("value", ""))[:300]
        out.append({"provider": "commons", "id": str(p.get("pageid")), "url": info.get("thumburl") or info.get("url"),
                    "thumb": info.get("thumburl") or info.get("url"), "tags": f"{title} {desc}",
                    "w": info.get("thumbwidth") or info.get("width"), "h": info.get("thumbheight") or info.get("height"),
                    "page": info.get("descriptionurl", ""), "credit": f"{title} — {artist}, {lic}, Wikimedia Commons"})
    return out


def search_pexels_photos(query: str, per_page: int = 15) -> list[dict]:
    """Фото Pexels (тот же ключ, что для видео). Лицензия Pexels: можно бесплатно и без указания автора."""
    if not config.PEXELS_API_KEY or not query:
        return []
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": config.PEXELS_API_KEY},
                     params={"query": query[:100], "per_page": per_page}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for p in r.json().get("photos", []):
        src = p.get("src") or {}
        url = src.get("large2x") or src.get("large") or src.get("original")
        if not url:
            continue
        slug = re.sub(r"[-/]+", " ", (p.get("url") or "").rstrip("/").rsplit("/", 1)[-1])
        out.append({"provider": "pexelsimg", "id": str(p["id"]), "url": url, "thumb": src.get("medium") or url,
                    "tags": f"{p.get('alt') or ''} {slug}", "w": p.get("width"), "h": p.get("height"),
                    "page": p.get("url", ""), "credit": ""})
    return out


def search_openverse(query: str, per_page: int = 15, category: str | None = None) -> list[dict]:
    """Openverse (WordPress): изображения под CC0, PDM, CC BY, CC BY-SA - только с разрешением коммерческого использования
    и изменений (кадрируем и двигаем). Автор и лицензия - в подпись (обязательное указание авторства)."""
    if not query:
        return []
    params = {"q": query[:100], "page_size": per_page, "license_type": "commercial,modification", "mature": "false"}
    if category:
        params["category"] = category
    r = requests.get("https://api.openverse.org/v1/images/", headers=UA, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        url = it.get("url")
        if not url or (it.get("width") or 0) and (it.get("width") or 0) < 700:
            continue
        tags = " ".join(t.get("name", "") for t in (it.get("tags") or []) if isinstance(t, dict))
        code = (it.get("license") or "").lower()
        lic = {"cc0": "CC0", "pdm": "Public Domain Mark"}.get(code) or f"CC {code.upper()} {it.get('license_version') or ''}".strip()
        creator = (it.get("creator") or "неизвестный автор")[:60]
        title = (it.get("title") or "")[:80]
        out.append({"provider": "openverse", "id": str(it.get("id")), "url": url, "thumb": it.get("thumbnail") or url,
                    "tags": f"{title} {tags}", "w": it.get("width"), "h": it.get("height"),
                    "page": it.get("foreign_landing_url", ""), "credit": f"{title} — {creator}, {lic}, via Openverse"})
    return out


SPACE_WORDS = {"nasa", "space", "moon", "mars", "rocket", "apollo", "astronaut", "galaxy", "planet", "iss", "shuttle", "orbit",
               "satellite", "nebula", "jupiter", "saturn", "venus", "telescope", "hubble", "webb", "comet", "asteroid", "eclipse"}


def search_nasa(query: str, per_page: int = 12) -> list[dict]:
    """Фото NASA (общественное достояние США): космос, ракеты, астронавты, Земля с орбиты."""
    if not query:
        return []
    r = requests.get("https://images-api.nasa.gov/search", headers=UA, params={"q": query[:100], "media_type": "image"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for it in ((r.json().get("collection") or {}).get("items") or [])[:per_page]:
        d = (it.get("data") or [{}])[0]
        link = next((l.get("href") for l in it.get("links") or [] if l.get("render") == "image" or "thumb" in (l.get("href") or "")), None)
        if not link or not d.get("nasa_id"):
            continue
        tags = " ".join(d.get("keywords") or [])
        out.append({"provider": "nasa", "id": re.sub(r"[^\w-]", "_", d["nasa_id"])[:80], "url": link.replace("~thumb", "~large"),
                    "fallback": [link.replace("~thumb", "~medium"), link], "thumb": link,
                    "tags": f"{d.get('title') or ''} {tags} {(d.get('description') or '')[:200]}",
                    "page": f"https://images.nasa.gov/details/{d['nasa_id']}", "credit": f"{(d.get('title') or '')[:70]} — NASA"})
    return out


def _safe(fn, *a, **kw) -> list[dict]:
    """Один источник упал или ограничил запросы - остальные продолжают работать."""
    try:
        return fn(*a, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("источник картинок %s: %s", getattr(fn, "__name__", fn), str(e)[:120])
        return []


def _mix(*lists: list[dict]) -> list[dict]:
    out = []
    for i in range(max((len(x) for x in lists), default=0)):
        for lst in lists:
            if i < len(lst):
                out.append(lst[i])
    return out


def download(item: dict) -> Path:
    ext = ".png" if str(item["url"]).lower().split("?")[0].endswith(".png") else ".jpg"
    dest = img_dir() / f"{item['provider']}_{item['id']}{ext}"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    url = item["url"]
    for alt in item.get("fallback") or []:       # NASA: не у всех снимков есть «~large»
        try:
            if requests.head(url, timeout=10, headers=UA, allow_redirects=True).status_code == 200:
                break
        except requests.RequestException:
            pass
        url = alt
    try:
        with requests.get(url, stream=True, timeout=TIMEOUT, headers=UA) as r:
            r.raise_for_status()
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    done += len(chunk)
                    if done > MAX_BYTES:
                        raise RuntimeError("картинка больше 15 МБ")
                    f.write(chunk)
        tmp.rename(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def search(query: str, vtype: str) -> list[dict]:
    """vtype: photo | illustration | archive. Несколько источников вперемешку; упавший источник пропускается."""
    words = set(re.findall(r"[a-z]+", (query or "").lower()))
    space = bool(words & SPACE_WORDS)
    if vtype == "archive":
        main = _mix(_safe(search_nasa, query) if space else [], _safe(search_commons, query))
        return main if len(main) >= 4 else main + _safe(search_openverse, query, 10)
    if vtype == "illustration":
        main = _safe(search_pixabay, query, "illustration") + _safe(search_pixabay, query, "vector", 5)
        return main if len(main) >= 4 else main + _safe(search_openverse, query, 10, "illustration")
    main = _mix(_safe(search_pexels_photos, query), _safe(search_pixabay, query, "photo"), _safe(search_nasa, query) if space else [])
    return main if len(main) >= 4 else main + _safe(search_openverse, query, 10, "photograph")
