"""Сборщик мусора (cron, раз в сутки).
Удаляет: осиротевшие папки задач (нет в очереди или уже done/rejected), старые failed, содержимое tmp/.
"""
import shutil
import time

from common import config, db


def main() -> None:
    db.init()
    now = time.time()
    removed = 0
    for d in config.TASKS_DIR.glob("task_*"):
        try:
            task_id = int(d.name.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        task = db.get_task(task_id)
        age_days = (now - d.stat().st_mtime) / 86400
        if task is None or (task["status"] in ("done", "rejected", "draft") and age_days > 0.1):
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
        elif task["status"] == "failed" and age_days > config.KEEP_FAILED_DAYS:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    for f in config.TMP_DIR.glob("*"):
        if now - f.stat().st_mtime > 86400:
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
    pruned = _prune_creator(now) if config.CREATOR else 0
    if config.CREATOR:
        from common import creator
        creator.init()
        creator.purge_log(30)
    print(f"cleanup: удалено папок задач: {removed}" + (f", кэша: {pruned}" if config.CREATOR else ""))


def _prune_creator(now: float) -> int:
    """Экземпляр для креаторов живёт на ограниченном диске: кэш клипов и картинок чистим по возрасту."""
    n = 0
    from pathlib import Path
    roots = [(config.CLIPS_DIR, 2), (config.ROOT / "images_cache", 3), (config.ROOT / "smart", 2), (config.ROOT / "uploads", 1)]
    if config.TELEGRAM_API_URL:       # свой сервер Bot API хранит присланные файлы у себя
        roots.append((Path("/var/lib/telegram-bot-api"), 1))
    for root, days in roots:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            try:
                if f.is_file() and now - f.stat().st_mtime > days * 86400:
                    f.unlink()
                    n += 1
            except OSError:
                pass
    return n


if __name__ == "__main__":
    main()
