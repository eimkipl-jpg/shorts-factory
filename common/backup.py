"""Ежедневная копия базы (кредиты = деньги): python -m common.backup. Хранит 7 последних копий в <каталог>/backups."""
import sqlite3
import time

from common import config


def main() -> None:
    dst_dir = config.ROOT / "backups"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"factory_{time.strftime('%Y%m%d')}.db"
    src = sqlite3.connect(config.DB_PATH)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    out.close()
    src.close()
    for old in sorted(dst_dir.glob("factory_*.db"))[:-7]:
        old.unlink(missing_ok=True)
    print("backup:", dst)


if __name__ == "__main__":
    main()
