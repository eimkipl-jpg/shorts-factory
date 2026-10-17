"""Транспортный узел: rclone move папки задачи на Google Drive.
`rclone move` удаляет локальные файлы только после успешной передачи и проверки.
"""
import subprocess
from pathlib import Path

from common import config


def upload_task(task_dir: Path, channel: str) -> None:
    if not config.DRIVE_ENABLED:
        raise RuntimeError("Диск отключён в этом экземпляре: выгрузка невозможна")
    dest = f"{config.RCLONE_REMOTE}:{config.RCLONE_BASE_PATH}/{channel}/{task_dir.name}"
    res = subprocess.run(
        ["rclone", "move", str(task_dir), dest, "--delete-empty-src-dirs", "--retries", "3"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"rclone не смог выгрузить {task_dir.name}: {res.stderr[-800:]}")
