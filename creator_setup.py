"""Установка бота для креаторов рядом с основным (патч 39). Запуск из папки проекта: python3 creator_setup.py
Создаёт: .env.creator, отдельный каталог данных, юниты systemd creator-gateway / creator-worker, ночную уборку, swap (если его нет).
Ничего не трогает в основном боте. Повторный запуск безопасен: обновляет настройки и перезапускает сервисы."""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYSTEMD = Path(os.getenv("SYSTEMD_DIR", "/etc/systemd/system"))
CRON = Path(os.getenv("CRON_DIR", "/etc/cron.d"))
SKIP_SYSTEMCTL = os.getenv("NO_SYSTEMCTL") == "1"


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text("utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def ask(prompt: str, default: str = "", env: str = "") -> str:
    if env and os.getenv(env) is not None:
        return os.environ[env]
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def run(*cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def unit_info() -> tuple[str, str]:
    """Пользователь и python основного воркера: берём из его юнита, чтобы второй экземпляр жил так же."""
    user, py = "root", str(HERE / "venv" / "bin" / "python")
    for f in (SYSTEMD / "shorts-worker.service",):
        if f.exists():
            txt = f.read_text("utf-8")
            m = re.search(r"^User=(\S+)", txt, re.M)
            if m:
                user = m.group(1)
            m = re.search(r"^ExecStart=(\S+)", txt, re.M)
            if m:
                py = m.group(1)
    if not Path(py).exists():
        py = shutil.which("python3") or sys.executable
    return user, py


HARDEN = """NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths={rw}
InaccessiblePaths=-{home}/.config/rclone -{here}/.env -{home}/.ssh
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
"""


def write_units(user: str, py: str, envf: Path, croot: Path, home: Path, harden: bool) -> None:
    """Юниты креаторов. Изоляция: сервис видит файловую систему только для чтения, пишет лишь в свой каталог и кэши,
    а токен Диска (rclone), основной .env и ключи ssh для него недоступны даже при взломе процесса. Память ограничена,
    чтобы сбой рендера не утащил за собой сервер и основной бот."""
    rw = f"{croot} /tmp -{home}/.fonts -{home}/.cache -{home}/.local"
    sec = HARDEN.format(rw=rw, home=home, here=HERE) if harden else ""
    common = (f"User={user}\nWorkingDirectory={HERE}\nEnvironment=ENV_FILE={envf}\nEnvironment=RCLONE_CONFIG=/nonexistent/none.conf\n"
              "Restart=always\nRestartSec=5\n" + sec)
    units = {
        "creator-gateway.service": f"[Unit]\nDescription=Shorts Factory - creator bot gateway\nAfter=network-online.target\nWants=network-online.target\n\n"
                                   f"[Service]\nType=simple\n{common}MemoryMax=600M\nExecStart={py} -m gateway.creator_bot\n\n[Install]\nWantedBy=multi-user.target\n",
        "creator-worker.service": f"[Unit]\nDescription=Shorts Factory - creator render worker\nAfter=network-online.target\nWants=network-online.target\n\n"
                                  f"[Service]\nType=simple\n{common}MemoryMax=2300M\nExecStart={py} -m core.worker\n\n[Install]\nWantedBy=multi-user.target\n",
    }
    SYSTEMD.mkdir(parents=True, exist_ok=True)
    for name, text in units.items():
        (SYSTEMD / name).write_text(text, "utf-8")


def ensure_swap() -> str:
    if Path("/proc/swaps").exists() and len(Path("/proc/swaps").read_text().splitlines()) > 1:
        return "swap уже есть"
    if os.getenv("SKIP_SWAP") == "1" or os.geteuid() != 0:
        return "swap пропущен"
    f = Path("/swapfile")
    if f.exists():
        return "файл /swapfile уже есть"
    if run("fallocate", "-l", "2G", str(f)).returncode != 0:
        return "swap не создан (fallocate)"
    f.chmod(0o600)
    run("mkswap", str(f))
    if run("swapon", str(f)).returncode != 0:
        return "swap не включился"
    fstab = Path("/etc/fstab")
    if fstab.exists() and "/swapfile" not in fstab.read_text():
        with fstab.open("a") as fh:
            fh.write("/swapfile none swap sw 0 0\n")
    return "создан swap 2 ГБ"


def install_deps() -> None:
    """yt-dlp (скачивание, нарезка по ссылке, метрики) и Pillow (схемы-инструкции, логотип бренда) - в тот же Python, что у сервисов."""
    _, py = unit_info()
    base = [py, "-m", "pip", "install", "-U", "yt-dlp", "Pillow"]
    res = run(*base)
    if res.returncode != 0 and "externally-managed" in (res.stderr + res.stdout):
        res = run(*base, "--break-system-packages")
    if res.returncode != 0:
        print("⚠️ yt-dlp и Pillow не установились:", (res.stderr or res.stdout)[-400:])
        return
    ver = run(py, "-m", "yt_dlp", "--version").stdout.strip()
    pil = run(py, "-c", "import PIL; print(PIL.__version__)").stdout.strip()
    print(f"yt-dlp {ver} и Pillow {pil} установлены для {py}")
    migrate_env()


def migrate_env() -> None:
    """Новые значения по умолчанию для уже созданного .env.creator (патч 46: ролики до 120 с)."""
    envf = HERE / ".env.creator"
    if not envf.exists():
        return
    text = envf.read_text("utf-8")
    new = text.replace("CREATOR_MAX_SECONDS=75\n", "CREATOR_MAX_SECONDS=120\n")
    if new != text:
        envf.write_text(new, "utf-8")
        print("Длина роликов в боте для креаторов: до 120 с")


def main() -> None:
    if "--deps" in sys.argv:          # обновление: только зависимости и перезапуск сервисов креаторов
        install_deps()
        if not SKIP_SYSTEMCTL:
            run("systemctl", "restart", "creator-gateway", "creator-worker")
            for s_ in ("creator-gateway", "creator-worker"):
                print(s_, run("systemctl", "is-active", s_).stdout.strip())
        return
    main_env = read_env(HERE / ".env")
    root = Path(main_env.get("FACTORY_ROOT", "/shorts_factory"))
    croot = Path(os.getenv("CREATOR_ROOT") or f"{root}_creator")
    print("=== Бот для креаторов: установка ===")
    print(f"Основной бот не затрагивается. Данные креаторов: {croot}\n")
    token = ask("Токен нового бота (из @BotFather → /newbot)", env="CREATOR_TOKEN")
    if not re.match(r"^\d+:[\w-]{30,}$", token):
        sys.exit("Токен похож на неверный. Формат: 123456789:AA... Запустите ещё раз.")
    if token == main_env.get("BOT_TOKEN"):
        sys.exit("Это токен основного бота. Создайте для креаторов нового в @BotFather.")
    admins = ask("Ваш Telegram ID (админ: без лимитов, команды /grant /stats /ban)", main_env.get("ALLOWED_USER_IDS", ""), "CREATOR_ADMINS")
    uname = ask("Имя бота для водяного знака, например @my_shorts_bot (пусто - без знака)", "", "CREATOR_WATERMARK")
    packs = ask("Пакеты «роликов:звёзд»", "10:150,30:400", "CREATOR_PACKS")
    free = ask("Сколько бесплатных демо-роликов", "3", "CREATOR_FREE")
    chan = ask("@канал, подписка на который обязательна для бесплатного демо (пусто - не требовать; бот должен быть админом канала)", "", "CREATOR_CHANNEL")

    for d in ("tasks", "tmp", "logs", "backgrounds", "clips", "assets", "ideas", "projects"):
        (croot / d).mkdir(parents=True, exist_ok=True)
    # встроенные звуки и логотипы основного бота доступны только для чтения через ссылку
    for sub in (root / "assets",):
        link = croot / "assets"
        if sub.exists() and link.is_dir() and not any(link.iterdir()):
            link.rmdir()
            link.symlink_to(sub, target_is_directory=True)

    keep = ["PEXELS_API_KEY", "PIXABAY_API_KEY", "TTS_ENGINE", "TTS_VOICE", "TTS_VOICES", "WHISPER_MODEL", "TELEGRAM_API_URL"]
    lines = ["# Бот для креаторов (создано creator_setup.py)", f"BOT_TOKEN={token}", "CREATOR_MODE=1", "CREATOR_FREE_MODE=1", "DRIVE_ENABLED=0",
             f"FACTORY_ROOT={croot}", "CHANNELS=creator", f"ADMIN_IDS={admins}", f"FREE_VIDEOS={free}", f"CREATOR_PACKS={packs}",
             "DAILY_LIMIT=10", "MAX_QUEUED=2", "MAX_QUEUE_TOTAL=30", "FREE_DAILY_TOTAL=12", "FREE_QUEUE_MAX=5", "CREATOR_MAX_SECONDS=75", "TASK_TIMEOUT=1200",
             f"REQUIRE_CHANNEL={chan}", "KEEP_FAILED_DAYS=1", f"WATERMARK={uname}",
             "RENDER_LOCK=/tmp/shorts_render.lock"]
    lines += [f"{k}={main_env[k]}" for k in keep if main_env.get(k)]
    envf = HERE / ".env.creator"
    envf.write_text("\n".join(lines) + "\n", "utf-8")
    envf.chmod(0o600)

    user, py = unit_info()
    home = Path(os.path.expanduser(f"~{user}"))
    write_units(user, py, envf, croot, home, harden=os.getenv("NO_HARDEN") != "1")
    CRON.mkdir(parents=True, exist_ok=True)
    pre = f"cd {HERE} && ENV_FILE={envf} {py}"
    (CRON / "creator-cleanup").write_text(
        f"0 4 * * * {user} {pre} -m transport.cleanup >> {croot}/logs/cleanup.log 2>&1\n"
        f"30 3 * * * {user} {pre} -m common.backup >> {croot}/logs/backup.log 2>&1\n", "utf-8")

    print("swap:", ensure_swap())
    install_deps()
    if not SKIP_SYSTEMCTL:
        run("systemctl", "daemon-reload")
        for s_ in ("creator-gateway", "creator-worker"):
            run("systemctl", "enable", s_)
            run("systemctl", "restart", s_)
        time.sleep(8)
        bad = [s_ for s_ in ("creator-gateway", "creator-worker") if run("systemctl", "is-active", s_).stdout.strip() != "active"]
        if bad and os.getenv("NO_HARDEN") != "1":
            print(f"⚠️ {', '.join(bad)} не запустились с защитной изоляцией - ставлю обычный режим (без изоляции файловой системы).")
            print(run("journalctl", "-u", bad[0], "-n", "8", "--no-pager").stdout[-600:])
            write_units(user, py, envf, croot, home, harden=False)
            run("systemctl", "daemon-reload")
            for s_ in ("creator-gateway", "creator-worker"):
                run("systemctl", "restart", s_)
            time.sleep(5)
        # основной воркер читает общий замок рендера из нового кода - перезапуск подхватывает его
        run("systemctl", "restart", "shorts-worker", "shorts-gateway")
        for s_ in ("creator-gateway", "creator-worker"):
            print(s_, run("systemctl", "is-active", s_).stdout.strip())
    print("\nГотово. Откройте нового бота в Telegram и нажмите /start.")
    print("Логи: journalctl -u creator-gateway -f   |   journalctl -u creator-worker -f")


if __name__ == "__main__":
    main()
