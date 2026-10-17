#!/usr/bin/env bash
# Установка Shorts Factory на Ubuntu/Debian VPS. Запуск: sudo bash install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Запустите через sudo"; exit 1; fi

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-root}"
ROOT_DIR="${FACTORY_ROOT:-/shorts_factory}"

echo "==> Системные пакеты"
apt-get update -y
apt-get install -y ffmpeg python3 python3-venv python3-pip rclone fonts-dejavu-core cron

echo "==> Каталоги в $ROOT_DIR"
mkdir -p "$ROOT_DIR"/{backgrounds,tasks,tmp,logs}
chown -R "$RUN_USER":"$RUN_USER" "$ROOT_DIR"

echo "==> Python-окружение"
sudo -u "$RUN_USER" python3 -m venv "$INSTALL_DIR/venv"
sudo -u "$RUN_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$RUN_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  chown "$RUN_USER":"$RUN_USER" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
  echo "!! Создан .env - заполните BOT_TOKEN, ALLOWED_USER_IDS, ключи Pexels/Pixabay, затем запустите скрипт снова"
fi

echo "==> Сервисы systemd"
for svc in shorts-gateway shorts-worker; do
  sed -e "s|__USER__|$RUN_USER|g" -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
      "$INSTALL_DIR/systemd/$svc.service" > "/etc/systemd/system/$svc.service"
done
sed -e "s|__USER__|$RUN_USER|g" -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__ROOT__|$ROOT_DIR|g" \
    "$INSTALL_DIR/systemd/shorts-factory.cron" > /etc/cron.d/shorts-factory
chmod 644 /etc/cron.d/shorts-factory

systemctl daemon-reload
systemctl enable shorts-gateway shorts-worker
if grep -q "^BOT_TOKEN=123456" "$INSTALL_DIR/.env"; then
  echo "==> .env ещё не заполнен - сервисы включены, но не запущены."
else
  systemctl restart shorts-gateway shorts-worker
  echo "==> Сервисы запущены"
fi

cat <<EOF

Дальше:
  1. rclone config          (создайте remote для Google Drive с именем из RCLONE_REMOTE)
  2. sudo -u $RUN_USER $INSTALL_DIR/venv/bin/python -m backgrounds.refill   (первое наполнение банка фонов)
  3. journalctl -u shorts-gateway -u shorts-worker -f   (логи)
EOF
