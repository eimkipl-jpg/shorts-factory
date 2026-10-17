"""Задачи бота для креаторов помимо роликов: скачивание видео по ссылке и нарезка (воркер, под общим замком рендера).

dl:  metadata {"dl": {"url": ...}}                              -> видео в чат (до 50 МБ, тяжелее - пережимается)
cut: metadata {"cut": {"url"|"file", "mode", "min", "max", "sens", "top", "title"}} -> фрагменты альбомами или zip-архивами
Исходники и результаты лежат в папке задачи и удаляются после отправки."""
import logging
import shutil
import zipfile
from pathlib import Path

from common import config, notify
from common.media import video_duration
from core import cutter, fetch

log = logging.getLogger("creatortasks")

TG_MAX = (1900 if config.TELEGRAM_API_URL else 48) * 1024 * 1024    # облачный Bot API - 50 МБ, свой сервер - 2 ГБ


def run(task, task_dir: Path, meta: dict) -> None:
    if task["input_type"] == "dl":
        return _download(task, task_dir, meta)
    if task["input_type"] == "cut":
        return _cut(task, task_dir, meta)
    if task["input_type"] == "reels":
        return _reels(task, task_dir, meta)
    if task["input_type"] == "ref":
        return _ref(task, task_dir, meta)
    raise RuntimeError(f"неизвестный тип задачи {task['input_type']}")


def _download(task, task_dir: Path, meta: dict) -> None:
    from core.worker import _fit_size
    url = meta["dl"]["url"]
    video = fetch.download(url, task_dir / "src", config.DL_MAX_MINUTES, height=1080)
    send = _fit_size(video)
    dur = video_duration(send) or 0
    title = (meta["dl"].get("title") or "").strip()
    ok, why = notify.send_video(task["chat_id"], send, (f"⬇️ {title}\n" if title else "⬇️ Готово\n") + f"⏱ {int(dur) // 60}:{int(dur) % 60:02}",
                                width=0, height=0)
    if not ok:     # видео не принялось как видео (редкие кодеки) - отправляем файлом
        ok, why = notify.send_document(task["chat_id"], send, "⬇️ Готово (файлом)", "video.mp4")
    if not ok:
        raise RuntimeError(f"не удалось отправить видео: {why}")


def _source(task_dir: Path, c: dict, max_minutes: int | None = None) -> Path:
    max_minutes = max_minutes or config.CUT_MAX_MINUTES
    if c.get("url"):
        return fetch.download(c["url"], task_dir / "src", max_minutes, height=1080)
    src = Path(c.get("file") or "")
    if not src.is_absolute():
        src = config.ROOT / src
    root = (config.ROOT / "uploads").resolve()
    if not src.is_file() or root not in src.resolve().parents:   # только файлы из папки загрузок пользователей
        raise RuntimeError("исходное видео не найдено - пришлите его заново")
    dur = video_duration(src) or 0
    if dur > max_minutes * 60 + 1:
        raise RuntimeError(f"видео длиннее {max_minutes} мин")
    return src


def _zip_batches(paths: list[Path], out_dir: Path) -> list[Path]:
    """Раскладывает фрагменты по zip-архивам до 48 МБ каждый."""
    zips, cur, size = [], [], 0
    for p in paths:
        s = p.stat().st_size
        if cur and size + s > TG_MAX:
            zips.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += s
    if cur:
        zips.append(cur)
    out = []
    for i, group in enumerate(zips, 1):
        z = out_dir / f"cuts_{i}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
            for p in group:
                zf.write(p, p.name)
        out.append(z)
    return out


def _cut(task, task_dir: Path, meta: dict) -> None:
    c = meta["cut"]
    video = _source(task_dir, c)
    out_dir = task_dir / "clips"
    shutil.rmtree(out_dir, ignore_errors=True)
    res = cutter.run(video, "cuts", c.get("mode", "cuts"), float(c.get("min", 2)), float(c.get("max", 10)), c.get("sens", "mid"),
                     int(c.get("top", 8)), c.get("title") or "video", out_dir=out_dir)
    paths: list[Path] = [p for p in res.get("paths", []) if p.exists()]
    if not paths:
        raise RuntimeError("не получилось ни одного фрагмента: попробуйте меньшую минимальную длину или чувствительность «высокая»")
    spans = "\n".join(f"{i}. {int(s // 60)}:{int(s % 60):02}–{int(e // 60)}:{int(e % 60):02}" + (f" (оценка {sc})" if sc else "")
                      for i, (_, s, e, sc) in enumerate(res["files"][:30], 1))
    head = (f"✂️ Нарезка готова: фрагментов {len(paths)}, переходов найдено {res['cuts']}.\n{spans}"
            + ("\n…" if len(res["files"]) > 30 else ""))
    chat = task["chat_id"]
    total = sum(p.stat().st_size for p in paths)
    if len(paths) == 1 and total <= TG_MAX:
        ok, why = notify.send_video(chat, paths[0], head)
        if ok:
            return
    if 2 <= len(paths) <= 10 and total <= TG_MAX:
        ok, why = notify.send_media_group(chat, paths, head)
        if ok:
            return
        log.warning("альбом не отправился (%s), шлю архивом", why)
    notify.send(chat, head)
    zips = _zip_batches(paths, task_dir)
    for i, z in enumerate(zips, 1):
        ok, why = notify.send_document(chat, z, f"Архив {i} из {len(zips)}" if len(zips) > 1 else "Все фрагменты", z.name)
        if not ok:
            raise RuntimeError(f"не удалось отправить архив: {why}")


def _reels(task, task_dir: Path, meta: dict) -> None:
    """ReelsEditor: сырое видео «говорящей головы» -> смонтированный ролик 9:16."""
    from core import reels
    from core.worker import _fit_size
    c = meta["reels"]
    video = _source(task_dir, c, config.REELS_MAX_MINUTES)
    res = reels.build(video, task_dir, c.get("cfg") or {}, c.get("scfg") or {}, c.get("lang"), c.get("format", "9:16"), c.get("brand"))
    send = _fit_size(res["path"])
    ok, why = notify.send_video(task["chat_id"], send, "🎬 ReelsEditor: готово\n" + reels.report_text(res["stats"]))
    if not ok:
        raise RuntimeError(f"не удалось отправить ролик: {why}")
    from core import brand as brandmod
    text = brandmod.with_tags(res["text"].strip(), c.get("brand"))
    if text:
        notify.send(task["chat_id"], "📝 Текст ролика (для описания и проверки):\n\n" + text[:3500])


def _ref(task, task_dir: Path, meta: dict) -> None:
    """Разбор референса: отчёт, раскадровка, текст; разбор сохраняется человеку для промта «сделай похожий»."""
    from common import creator
    from core import refs
    from gateway import state
    c = meta["ref"]
    video = _source(task_dir, c, config.REF_MAX_MINUTES)
    a = refs.analyze(video, task_dir / "ref")
    uid = creator.owner_of(task["id"])
    if uid:
        st = state.load(uid)
        st["lists"]["ref_last"] = {k: v for k, v in a.items() if k != "storyboard"}
        state.save(uid, st)
    if a.get("storyboard"):
        notify.send_photo(task["chat_id"], a["storyboard"], "🎞 Раскадровка: первый кадр каждого плана")
    notify.send(task["chat_id"], refs.report(a), [[("✍️ Сделать похожий на свою тему", "cr:rfp")], [("⬅ Меню", "cr:home")]])
    if a.get("text"):
        notify.send(task["chat_id"], "📝 Текст референса:\n\n" + a["text"][:3500])
