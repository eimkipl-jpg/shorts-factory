"""Библиотека материалов: единый индекс фонов, клипов, музыки и ваших видео (SQLite).

Типы: bg (backgrounds/<тема>), clip (clips/<тема>), music (clips/_music), own (clips/_own),
sfx (clips/_sfx/<тег> и встроенные assets/sfx), sat (clips/_satisfying/<вид>: залипательные видео для историй).
Тема = папка, она становится тегом. Дополнительные теги, источник и лицензию можно задать вручную.
"""
import random
import time
from pathlib import Path

from common import config
from common.db import connect

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"}
TYPES = {"bg": "Фоны", "clip": "Клипы", "music": "Музыка", "own": "Мои видео", "sfx": "Звуки", "sat": "Залипательные"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    type      TEXT NOT NULL,
    path      TEXT NOT NULL UNIQUE,   -- относительно ROOT
    tags      TEXT NOT NULL DEFAULT '',  -- через пробел, в нижнем регистре
    source    TEXT NOT NULL DEFAULT '',
    license   TEXT NOT NULL DEFAULT '',
    duration  REAL,
    uses      INTEGER NOT NULL DEFAULT 0,
    last_used REAL,
    added_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(type);
"""


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(assets)")}
        if "title" not in cols:  # миграция: человекочитаемое название материала
            c.execute("ALTER TABLE assets ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        if "title_lc" not in cols:
            c.execute("ALTER TABLE assets ADD COLUMN title_lc TEXT NOT NULL DEFAULT ''")


def _rel(p: Path) -> str:
    return str(p.relative_to(config.ROOT))


def _duration(p: Path) -> float | None:
    try:
        from common.media import audio_duration, video_duration
        return audio_duration(p) if p.suffix.lower() in AUDIO_EXT else video_duration(p)
    except Exception:  # noqa: BLE001
        return None


def _walk() -> list[tuple[str, Path, str]]:
    """(тип, файл, тема-тег) для всего, что лежит в папках библиотеки."""
    found = []
    if config.BACKGROUNDS_DIR.exists():
        for f in config.BACKGROUNDS_DIR.rglob("*"):
            if f.is_file() and f.suffix.lower() in VIDEO_EXT:
                found.append(("bg", f, f.parent.name if f.parent != config.BACKGROUNDS_DIR else ""))
    if config.CLIPS_DIR.exists():
        for f in config.CLIPS_DIR.rglob("*"):
            if not f.is_file():
                continue
            top = f.relative_to(config.CLIPS_DIR).parts[0] if f.parent != config.CLIPS_DIR else ""
            ext = f.suffix.lower()
            if top == "_music" and ext in AUDIO_EXT:
                # подпапка _music/<настроение>/ становится тегом трека
                rel = f.relative_to(config.CLIPS_DIR / "_music").parts
                found.append(("music", f, rel[0] if len(rel) > 1 else ""))
            elif top == "_sfx" and ext in AUDIO_EXT:
                rel = f.relative_to(config.CLIPS_DIR / "_sfx").parts
                found.append(("sfx", f, rel[0] if len(rel) > 1 else f.stem.split("_")[0]))
            elif top == "_own" and ext in VIDEO_EXT:
                found.append(("own", f, ""))
            elif top == "_satisfying" and ext in VIDEO_EXT:
                # залипательные видео для режима «История»: подпапка _satisfying/<вид>/ становится тегом
                rel = f.relative_to(config.CLIPS_DIR / "_satisfying").parts
                found.append(("sat", f, rel[0] if len(rel) > 1 else ""))
            elif ext in VIDEO_EXT and top not in ("_music", "_own", "_sfx", "_satisfying"):
                found.append(("clip", f, top))
    sfx_dir = config.ASSETS_DIR / "sfx"  # встроенные звуки: тег = имя файла до «_»
    if sfx_dir.exists():
        for f in sfx_dir.iterdir():
            if f.is_file() and f.suffix.lower() in AUDIO_EXT:
                found.append(("sfx", f, f.stem.split("_")[0]))
    return found


def scan() -> dict:
    """Синхронизирует индекс с диском: добавляет новые файлы, убирает исчезнувшие."""
    init()
    try:
        from core import sfxgen
        sfxgen.ensure_builtin()
    except Exception:  # noqa: BLE001  - без ffmpeg встроенных звуков просто не будет
        pass
    found = {_rel(f): (t, f, tag) for t, f, tag in _walk()}
    added = removed = 0
    with connect() as c:
        rows = {r["path"]: r["type"] for r in c.execute("SELECT path, type FROM assets")}
        known = set(rows)
        for path in known & found.keys():   # тип сменился (например, папка стала залипательными видео): правим тип и тег
            t, f, tag = found[path]
            if rows[path] != t:
                c.execute("UPDATE assets SET type=?, tags=? WHERE path=?", (t, tag.lower(), path))
        for path in known - found.keys():
            c.execute("DELETE FROM assets WHERE path=?", (path,))
            removed += 1
        for path in found.keys() - known:
            t, f, tag = found[path]
            c.execute(
                "INSERT INTO assets(type,path,tags,duration,added_at) VALUES(?,?,?,?,?)",
                (t, path, tag.lower(), _duration(f), time.time()),
            )
            added += 1
    return {"added": added, "removed": removed, "total": len(found)}


def stats() -> dict[str, int]:
    init()
    with connect() as c:
        rows = c.execute("SELECT type, COUNT(*) n FROM assets GROUP BY type").fetchall()
    return {r["type"]: r["n"] for r in rows}


def tag_counts(type_: str) -> list[tuple[str, int]]:
    init()
    counts: dict[str, int] = {}
    with connect() as c:
        for r in c.execute("SELECT tags FROM assets WHERE type=?", (type_,)):
            for t in (r["tags"] or "").split() or ["без-темы"]:
                counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))


def search(type_: str | None = None, tags: list[str] | None = None, limit: int = 10) -> list[dict]:
    init()
    sql, args = "SELECT * FROM assets WHERE 1=1", []
    if type_:
        sql += " AND type=?"
        args.append(type_)
    for t in tags or []:
        t = t.lstrip("#").lower()
        if t == "без-темы":
            sql += " AND tags=''"
        else:
            sql += " AND ((' '||tags||' ') LIKE ? OR title_lc LIKE ?)"  # тег или слово из названия
            args += [f"% {t} %", f"%{t}%"]
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args)]


def get(asset_id: int) -> dict | None:
    init()
    with connect() as c:
        r = c.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    return dict(r) if r else None


def by_path(path: Path) -> dict | None:
    init()
    with connect() as c:
        r = c.execute("SELECT * FROM assets WHERE path=?", (_rel(path),)).fetchone()
    return dict(r) if r else None


def by_rel(rel: str | None) -> dict | None:
    """Запись по пути относительно ROOT (так пути хранятся в индексе и в рецептах)."""
    if not rel:
        return None
    init()
    with connect() as c:
        r = c.execute("SELECT * FROM assets WHERE path=?", (rel,)).fetchone()
    return dict(r) if r else None


def add_tags(asset_id: int, tags: list[str]) -> bool:
    a = get(asset_id)
    if not a:
        return False
    new = sorted(set((a["tags"] or "").split()) | {t.lstrip("#").lower() for t in tags if t.strip("#")})
    with connect() as c:
        c.execute("UPDATE assets SET tags=? WHERE id=?", (" ".join(new), asset_id))
    return True


def set_title(asset_id: int, title: str) -> bool:
    if not get(asset_id):
        return False
    title = " ".join(title.split())[:200]
    with connect() as c:
        c.execute("UPDATE assets SET title=?, title_lc=? WHERE id=?", (title, title.lower(), asset_id))
    return True


def own_titles() -> dict[str, str]:
    """{имя файла: название} для «моих видео»; пустое название = None в словаре нет."""
    init()
    with connect() as c:
        rows = c.execute("SELECT path, title FROM assets WHERE type='own'").fetchall()
    return {Path(r["path"]).name: r["title"] for r in rows if r["title"]}


def set_info(asset_id: int, source: str | None = None, license_: str | None = None) -> bool:
    if not get(asset_id):
        return False
    with connect() as c:
        if source is not None:
            c.execute("UPDATE assets SET source=? WHERE id=?", (source, asset_id))
        if license_ is not None:
            c.execute("UPDATE assets SET license=? WHERE id=?", (license_, asset_id))
    return True


def touch(path: Path) -> None:
    """Отмечает использование файла. Никогда не роняет рендер."""
    try:
        init()
        with connect() as c:
            c.execute(
                "UPDATE assets SET uses=uses+1, last_used=? WHERE path=?", (time.time(), _rel(path))
            )
    except Exception:  # noqa: BLE001
        pass


def use_counts(files: list[Path]) -> dict[Path, int]:
    """Сколько раз каждый файл уже шёл в ролики (для приоритета «свежих» материалов)."""
    try:
        init()
        with connect() as c:
            uses = {r["path"]: r["uses"] for r in c.execute("SELECT path, uses FROM assets")}
        return {f: uses.get(_rel(f), 0) for f in files}
    except Exception:  # noqa: BLE001
        return {f: 0 for f in files}


def choose(files: list[Path]) -> Path:
    """Случайный файл среди наименее использованных: материалы не повторяются, пока есть свежие."""
    try:
        init()
        with connect() as c:
            uses = {r["path"]: r["uses"] for r in c.execute("SELECT path, uses FROM assets")}
        low = min(uses.get(_rel(f), 0) for f in files)
        files = [f for f in files if uses.get(_rel(f), 0) == low] or files
    except Exception:  # noqa: BLE001
        pass
    pick = random.choice(files)
    touch(pick)
    return pick
