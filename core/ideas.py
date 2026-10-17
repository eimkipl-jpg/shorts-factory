"""Банк идей и проекты.

На Диске (ShortsFactory):
  _ideas/<канал>/*.txt              новые идеи (первая строка файла = название, дальше идея и шаблоны видео)
  _ideas/<канал>/_work/<имя>.txt    идеи «в работе»
  _ideas/<канал>/_done/<имя>.txt    сделанные
  _projects/<канал>/<имя>/          видео и файлы проекта (имя папки = имя файла идеи в _work)
На сервере те же папки лежат в ROOT/ideas и ROOT/projects.

Диск - главный: перед показом списка идеи подтягиваются с Диска (rclone sync), а созданные в боте сразу загружаются на Диск.
Файлы проектов только добавляются (rclone copy), чтобы ничего не стереть случайно.
"""
import logging
import re
import shutil
import subprocess
from pathlib import Path

from common import config
from common.media import video_duration

log = logging.getLogger("ideas")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
WORK, DONE = "_work", "_done"
IDEA_FILE = "идея.txt"          # копия идеи внутри папки проекта
CHANNEL_RE = re.compile(r"^[\w-]{2,30}$", re.UNICODE)

TEMPLATE_HINT = (
    "Формат txt: первая строка — название идеи, дальше любой текст: суть, герои, тон, длина, шаблоны видео. Например:\n"
    "Почему год на Венере короче суток\n"
    "Идея: удивить фактом, закончить возвратом к началу. 30-40 секунд, спокойный тон.\n"
    "Шаблон: хук-вопрос → 3 факта → вывод. Видео: космос, планеты, крупные планы."
)


# ---------- имена и пути ----------

def safe_channel(name: str) -> str:
    return re.sub(r"\s+", "_", (name or "").strip())


def slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^\w]+", "-", (text or "").strip().lower(), flags=re.UNICODE).strip("-_")
    return s[:limit].strip("-_") or "идея"


def _check_channel(channel: str) -> str:
    if not config.has_channel(channel):
        raise ValueError(f"нет канала «{channel}»")
    return channel


def ideas_dir(channel: str, status: str = "new") -> Path:
    base = config.IDEAS_DIR / _check_channel(channel)
    sub = {"new": "", "work": WORK, "done": DONE}[status]
    return base / sub if sub else base


def project_dir(channel: str, name: str) -> Path:
    p = (config.PROJECTS_DIR / _check_channel(channel) / name).resolve()
    if not p.is_relative_to(config.PROJECTS_DIR.resolve()):
        raise ValueError("недопустимое имя проекта")
    return p


def _remote_ideas(channel: str, status: str = "new") -> str:
    sub = {"new": "", "work": f"/{WORK}", "done": f"/{DONE}"}[status]
    return f"{config.IDEAS_REMOTE_PATH}/{channel}{sub}"


def _remote_project(channel: str, name: str) -> str:
    return f"{config.PROJECTS_REMOTE_PATH}/{channel}/{name}"


# ---------- rclone ----------

def _rc(*args: str, timeout: int = 600) -> tuple[bool, str]:
    if not config.DRIVE_ENABLED:
        return False, "Диск отключён"
    try:
        res = subprocess.run(["rclone", *args, "--retries", "3"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)[:200]
    return res.returncode == 0, res.stderr[-200:]


def _remote(path: str) -> str:
    return f"{config.RCLONE_REMOTE}:{path}"


def _pull(mode: str, remote_path: str, local: Path) -> tuple[bool, str]:
    local.mkdir(parents=True, exist_ok=True)
    ok, msg = _rc(mode, _remote(remote_path), str(local))
    if not ok:
        log.warning("rclone %s %s не удался (работаем по локальным файлам): %s", mode, remote_path, msg)
    return ok, msg


def push_file(local: Path, remote_dir: str) -> tuple[bool, str]:
    return _rc("copy", str(local), _remote(remote_dir), timeout=900)


# ---------- каналы ----------

def create_channel(raw: str) -> str:
    """Новый канал: имя в списке каналов + папки идей и проектов (локально и на Диске). Возвращает имя."""
    name = safe_channel(raw)
    if not CHANNEL_RE.match(name):
        raise ValueError("Название канала: 2-30 символов, буквы, цифры, «_» и «-» (пробелы заменятся на «_»).")
    if config.has_channel(name):
        raise ValueError(f"Канал «{name}» уже есть.")
    config.add_channel(name)
    for d in (ideas_dir(name), ideas_dir(name, "work"), ideas_dir(name, "done"), config.PROJECTS_DIR / name):
        d.mkdir(parents=True, exist_ok=True)
    for remote in (f"{config.RCLONE_BASE_PATH}/{name}",   # сюда выгружаются готовые ролики (task_N/final.mp4)
                   _remote_ideas(name), _remote_ideas(name, "work"), _remote_ideas(name, "done"),
                   f"{config.PROJECTS_REMOTE_PATH}/{name}"):
        _rc("mkdir", _remote(remote), timeout=120)
    return name


# ---------- идеи ----------

def _read(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def title_of(text: str, fallback: str = "идея") -> str:
    for line in text.splitlines():
        line = re.sub(r"^\s*(#+|заголовок:|название:)\s*", "", line.strip(), flags=re.IGNORECASE).strip()
        if line:
            return line[:80]
    return fallback


def pull_ideas(channel: str) -> bool:
    """Подтягивает идеи канала с Диска (зеркало). False - Диск недоступен, показываем локальное."""
    ok, _ = _pull("sync", _remote_ideas(channel), ideas_dir(channel))
    for st in ("work", "done"):
        ideas_dir(channel, st).mkdir(parents=True, exist_ok=True)
    return ok


def list_ideas(channel: str, status: str = "new") -> list[dict]:
    d = ideas_dir(channel, status)
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.name.startswith("_"):
            continue
        text = _read(f)
        out.append({"file": f.name, "name": f.stem, "title": title_of(text, f.stem), "text": text, "status": status})
    return out


def read_idea(channel: str, status: str, name: str) -> dict | None:
    f = ideas_dir(channel, status) / f"{name}.txt"
    if not f.exists():
        return None
    text = _read(f)
    return {"file": f.name, "name": name, "title": title_of(text, name), "text": text, "status": status}


def _unique_name(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def _all_names(channel: str) -> set[str]:
    names = set()
    for st in ("new", "work", "done"):
        d = ideas_dir(channel, st)
        if d.exists():
            names |= {f.stem for f in d.glob("*.txt")}
    return names


def add_idea(channel: str, text: str) -> dict:
    """Создаёт файл идеи (из /idea или кнопки) и загружает на Диск."""
    text = (text or "").strip()
    if len(text) < 3:
        raise ValueError("Пустая идея.")
    ideas_dir(channel).mkdir(parents=True, exist_ok=True)
    name = _unique_name(slug(title_of(text)), _all_names(channel))
    f = ideas_dir(channel) / f"{name}.txt"
    f.write_text(text + "\n", "utf-8")
    ok, msg = push_file(f, _remote_ideas(channel))
    return {"name": name, "title": title_of(text), "drive_ok": ok, "drive_msg": msg}


def _move(channel: str, name: str, src: str, dst: str) -> bool:
    """Переносит файл идеи между статусами (локально и на Диске)."""
    a, b = ideas_dir(channel, src) / f"{name}.txt", ideas_dir(channel, dst) / f"{name}.txt"
    if not a.exists():
        raise ValueError("идея не найдена (обновите список)")
    b.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(a), str(b))
    ok, _ = _rc("moveto", _remote(f"{_remote_ideas(channel, src)}/{name}.txt"),
                _remote(f"{_remote_ideas(channel, dst)}/{name}.txt"), timeout=120)
    if not ok:   # на Диске файла не было (создан без выгрузки): загрузим на новое место
        ok, _ = push_file(b, _remote_ideas(channel, dst))
    return ok


def take(channel: str, name: str) -> dict:
    """«Взять в работу»: идея уходит в _work, создаётся папка проекта (с копией идеи) на сервере и на Диске."""
    if (ideas_dir(channel, "new") / f"{name}.txt").exists():
        if (ideas_dir(channel, "work") / f"{name}.txt").exists():   # такое имя уже в работе: берём свободное
            new = _unique_name(name, _all_names(channel))
            (ideas_dir(channel) / f"{name}.txt").rename(ideas_dir(channel) / f"{new}.txt")
            name = new
        drive_ok = _move(channel, name, "new", "work")
    elif (ideas_dir(channel, "done") / f"{name}.txt").exists():
        drive_ok = _move(channel, name, "done", "work")
    elif (ideas_dir(channel, "work") / f"{name}.txt").exists():
        drive_ok = True
    else:
        raise ValueError("идея не найдена (обновите список)")
    pdir = project_dir(channel, name)
    pdir.mkdir(parents=True, exist_ok=True)
    idea = read_idea(channel, "work", name)
    (pdir / IDEA_FILE).write_text(idea["text"], "utf-8")
    ok, msg = push_file(pdir / IDEA_FILE, _remote_project(channel, name))
    return {"name": name, "title": idea["title"], "drive_ok": drive_ok and ok, "path": pdir}


def finish(channel: str, name: str) -> bool:
    return _move(channel, name, "work", "done")


def delete_idea(channel: str, status: str, name: str) -> None:
    f = ideas_dir(channel, status) / f"{name}.txt"
    f.unlink(missing_ok=True)
    _rc("deletefile", _remote(f"{_remote_ideas(channel, status)}/{name}.txt"), timeout=120)


# ---------- проекты ----------

def pull_project_ex(channel: str, name: str, timeout: int = 300) -> tuple[bool, str]:
    """Подтягивает файлы проекта с Диска. Возвращает (успех, причина сбоя). Зависшее соединение обрывается через минуту простоя,
    а вся загрузка - через timeout секунд (большие видео докачаются при следующем нажатии)."""
    local = project_dir(channel, name)
    local.mkdir(parents=True, exist_ok=True)
    ok, msg = _rc("copy", _remote(_remote_project(channel, name)), str(local),
                  "--timeout", "60s", "--contimeout", "20s", "--transfers", "4", timeout=timeout)
    if not ok:
        log.warning("rclone copy проекта %s/%s не удался: %s", channel, name, msg)
    return ok, msg


def pull_project(channel: str, name: str) -> bool:
    return pull_project_ex(channel, name)[0]


def project_files(channel: str, name: str) -> list[dict]:
    d = project_dir(channel, name)
    out = []
    if d.exists():
        for f in sorted(d.iterdir(), key=lambda p: p.name.lower()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXT:
                try:
                    dur = video_duration(f)
                except Exception:  # noqa: BLE001
                    dur = 0.0
                out.append({"name": f.name, "path": f, "duration": dur})
    return out


def unique_file(d: Path, name: str) -> Path:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip() or "video.mp4"
    p = d / name
    n = 2
    while p.exists():
        p = d / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    return p


def add_file(channel: str, name: str, src: Path, move: bool = False) -> dict:
    """Кладёт файл в папку проекта (копия или перенос) и загружает на Диск."""
    d = project_dir(channel, name)
    d.mkdir(parents=True, exist_ok=True)
    dest = unique_file(d, src.name)
    if src.resolve() != dest.resolve():
        (shutil.move if move else shutil.copy2)(str(src), str(dest))
    ok, msg = push_file(dest, _remote_project(channel, name))
    return {"path": dest, "drive_ok": ok, "drive_msg": msg}


def remote_dir_of(channel: str, name: str) -> str:
    return _remote_project(channel, name)


# ---------- сценарий и промт ----------

def project_ctx(channel: str, name: str) -> dict:
    """Контекст для промта и разбора JSON: к обычному добавляются видео проекта (они идут первыми в списке own)."""
    from core import scenario
    ctx = scenario.build_ctx(channel)
    files = project_files(channel, name)
    names = [f["name"] for f in files]
    ctx["own"] = names + [n for n in ctx["own"] if n not in names]
    ctx["own_titles"] = {**(ctx.get("own_titles") or {}), **{f["name"]: Path(f["name"]).stem for f in files}}
    ctx["own_durations"] = {**(ctx.get("own_durations") or {}), **{f["name"]: f["duration"] for f in files}}
    ctx["project"] = f"{channel}/{name}"
    ctx["project_files"] = names
    ctx["channel"] = channel
    return ctx


def brief_of(channel: str, name: str) -> str:
    idea = read_idea(channel, "work", name) or {"text": name}
    return idea["text"].strip()
