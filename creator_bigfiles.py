"""Большие файлы в боте для креаторов: свой сервер Telegram Bot API на этом VPS (Docker, образ aiogram/telegram-bot-api).

Облачный Bot API даёт ботам скачивать файлы только до 20 МБ и отправлять до 50 МБ. Свой сервер в локальном режиме
снимает ограничение: принимать до 2 ГБ, отправлять до 2 ГБ. Нужны api_id и api_hash с my.telegram.org.
Включается только для бота креаторов (.env.creator); основной бот не трогается.

    python3 creator_bigfiles.py         включить
    python3 creator_bigfiles.py --off   вернуть облачный Bot API
    python3 creator_bigfiles.py --check проверить, что сервер отвечает
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENVF = HERE / ".env.creator"
NAME = "tg-bot-api"
PORT = 8081
LOCAL = f"http://127.0.0.1:{PORT}"
DATA = "/var/lib/telegram-bot-api"
IMAGE = os.getenv("BOTAPI_IMAGE", "aiogram/telegram-bot-api:latest")
DRY = os.getenv("DRY_RUN") == "1"


def sh(*cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    if DRY:
        print("  $", " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        sys.exit(f"Команда не удалась: {' '.join(cmd)}\n{res.stderr[-600:]}")
    return res


def read_env() -> tuple[str, dict]:
    if not ENVF.exists():
        sys.exit("Не найден .env.creator - сначала установите бота: python3 creator_setup.py")
    text = ENVF.read_text("utf-8")
    env = {m.group(1): m.group(2).strip() for m in re.finditer(r"^([A-Z_]+)=(.*)$", text, re.M)}
    return text, env


def set_env(key: str, value: str | None) -> None:
    text, _ = read_env()
    lines = [ln for ln in text.splitlines() if not ln.startswith(key + "=")]
    if value is not None:
        lines.append(f"{key}={value}")
    if DRY:
        print(f"  .env.creator: {key}={value}")
        return
    ENVF.write_text("\n".join(lines) + "\n", "utf-8")
    ENVF.chmod(0o600)


def api(base: str, token: str, method: str, timeout: int = 20) -> dict:
    if DRY:
        print(f"  GET {base}/bot***/{method}")
        return {"ok": True, "result": {"username": "dry_run_bot"}}
    try:
        with urllib.request.urlopen(f"{base}/bot{token}/{method}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        body = getattr(e, "read", lambda: b"")()
        try:
            return json.loads(body.decode()) if body else {"ok": False, "description": str(e)}
        except ValueError:
            return {"ok": False, "description": str(e)}


def services(action: str) -> None:
    for s in ("creator-gateway", "creator-worker"):
        sh("systemctl", action, s)


def ensure_docker() -> None:
    if sh("which", "docker").returncode == 0 and not DRY:
        return
    print("Ставлю Docker (apt)…")
    sh("apt-get", "update", "-q")
    sh("apt-get", "install", "-y", "-q", "docker.io", check=True)
    sh("systemctl", "enable", "--now", "docker", check=True)


def wait_local(token: str, seconds: int = 60) -> dict:
    t0 = time.time()
    last = {}
    while time.time() - t0 < seconds:
        last = api(LOCAL, token, "getMe", timeout=5)
        if last.get("ok"):
            return last
        time.sleep(2)
    return last


def turn_on() -> None:
    _, env = read_env()
    token = env.get("BOT_TOKEN", "")
    if not token:
        sys.exit("В .env.creator нет BOT_TOKEN")
    if env.get("TELEGRAM_API_URL") == LOCAL:
        print("Свой сервер Bot API уже включён. Проверка: python3 creator_bigfiles.py --check")
        return
    print("Нужны api_id и api_hash: зайдите на https://my.telegram.org → API development tools → создайте приложение "
          "(название любое) и скопируйте App api_id и App api_hash.\n")
    api_id = os.getenv("TG_API_ID") or input("api_id: ").strip()
    api_hash = os.getenv("TG_API_HASH") or input("api_hash: ").strip()
    if not api_id.isdigit() or not re.fullmatch(r"[0-9a-f]{32}", api_hash):
        sys.exit("api_id - число, api_hash - 32 символа 0-9a-f. Запустите ещё раз.")
    ensure_docker()
    sh("mkdir", "-p", DATA)
    sh("docker", "rm", "-f", NAME)
    print("Запускаю сервер Bot API в Docker…")
    sh("docker", "run", "-d", "--name", NAME, "--restart", "unless-stopped", "-p", f"127.0.0.1:{PORT}:8081",
       "-v", f"{DATA}:{DATA}", "-e", f"TELEGRAM_API_ID={api_id}", "-e", f"TELEGRAM_API_HASH={api_hash}",
       "-e", "TELEGRAM_LOCAL=1", IMAGE, check=True)
    print("Останавливаю бота креаторов и переношу его с облачного сервера (logOut)…")
    services("stop")
    res = api("https://api.telegram.org", token, "logOut")
    if not res.get("ok") and "Logged out" not in str(res.get("description", "")):
        print("⚠️ logOut в облаке ответил:", res.get("description"), "- продолжаю (бот мог уже быть перенесён).")
    me = wait_local(token)
    if not me.get("ok"):
        services("start")
        sys.exit(f"Свой сервер не ответил: {me.get('description')}. Логи: docker logs {NAME}. "
                 "Бот пока работает через облако? Нет - после logOut облако ждёт 10 минут; повторите позже или запустите --off.")
    set_env("TELEGRAM_API_URL", LOCAL)
    services("start")
    time.sleep(4)
    ok = all(sh("systemctl", "is-active", s).stdout.strip() == "active" or DRY for s in ("creator-gateway", "creator-worker"))
    print(f"\n✅ Готово: бот @{me['result'].get('username')} работает через свой сервер Bot API.")
    print("Теперь он принимает видео до 2 ГБ и отправляет ролики без пережатия до 50 МБ." if ok else
          "⚠️ Сервисы не запустились: journalctl -u creator-gateway -n 30")


def turn_off() -> None:
    _, env = read_env()
    token = env.get("BOT_TOKEN", "")
    services("stop")
    if env.get("TELEGRAM_API_URL"):
        res = api(env["TELEGRAM_API_URL"], token, "logOut")
        print("logOut на своём сервере:", "ok" if res.get("ok") else res.get("description"))
    set_env("TELEGRAM_API_URL", None)
    sh("docker", "rm", "-f", NAME)
    services("start")
    print("Бот креаторов возвращён на облачный Bot API (лимиты 20 МБ / 50 МБ). Если бот не отвечает - "
          "после logOut облако может ждать до 10 минут.")


def check() -> None:
    _, env = read_env()
    url = env.get("TELEGRAM_API_URL") or "https://api.telegram.org"
    me = api(url, env.get("BOT_TOKEN", ""), "getMe")
    print(("✅ " if me.get("ok") else "❌ ") + f"{url}: " + (f"@{me['result'].get('username')}" if me.get("ok") else str(me.get("description"))))
    print(sh("docker", "ps", "--filter", f"name={NAME}", "--format", "{{.Names}} {{.Status}}").stdout.strip() or "контейнер не запущен")


if __name__ == "__main__":
    if os.geteuid() != 0 and not DRY:
        sys.exit("Запускайте от root")
    {"--off": turn_off, "--check": check}.get(sys.argv[1] if len(sys.argv) > 1 else "", turn_on)()
