# Shorts Factory

Telegram-бот, который превращает сценарий, сырое видео «в камеру» или ссылку в готовый вертикальный ролик для Shorts, Reels и TikTok:
озвучка, субтитры по словам, стоковые кадры, карты, карточки, нарезка. Работает на одном VPS (2 vCPU, 4 ГБ) и устроен как
**продукт для многих пользователей**: кредиты, оплата Telegram Stars, лимиты, защита от злоупотреблений, админ-аналитика.

Основное описание, схема и список возможностей - в [README.md](README.md) (на английском) и [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Подробное руководство пользователя на русском: [docs/USER_GUIDE.ru.md](docs/USER_GUIDE.ru.md).

## Режимы
Умный монтаж по сценарию · ReelsEditor (монтаж говорящей головы) · UGC-реклама · разбор референса · нарезка · скачивание по ссылке ·
бренд-профиль · форматы 9:16 / 4:5 / 1:1 / 16:9 · метрики ролика по ссылке.

## Запуск
```bash
python3 -m venv venv && . venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
cp .env.creator.example .env.creator     # токен от @BotFather и ваш Telegram ID
ENV_FILE=.env.creator python -m gateway.creator_bot
```
Установка на сервер: `python3 creator_setup.py` (создаёт изолированные systemd-сервисы, уборку, резервные копии).

## Лицензия
PolyForm Noncommercial 1.0.0: код можно читать, запускать и изучать в некоммерческих целях. Для коммерческого использования - свяжитесь с автором.
Данные и шрифты третьих лиц: [NOTICE.md](NOTICE.md).
