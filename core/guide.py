"""Инструкции-схемы для бота креаторов: PNG с пронумерованными шагами (Pillow, шрифты Fira Sans и иконки Phosphor из проекта).
Картинки рисуются один раз и кэшируются в <каталог>/guides."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common import config

V = 3          # версия рисунка: поменять - картинки перерисуются
W = 1080
BG, CARD, LINE = (14, 21, 37), (25, 35, 58), (52, 66, 98)
TEXT, MUTED = (245, 247, 252), (168, 180, 202)

GUIDES = {
    "overview": {
        "title": "Как работает бот", "accent": (255, 176, 32),
        "steps": [("squares-four", "Выберите режим", "Умный монтаж, нарезка или скачивание - кнопки в главном меню"),
                  ("gear", "Настройте один раз", "Голос, темп, субтитры, оформление. Настройки запоминаются"),
                  ("upload-simple", "Пришлите материал", "Сценарий, ссылку или видео - бот подскажет, что нужно"),
                  ("hourglass", "Подождите в очереди", "Бот напишет, какой вы по счёту и сколько ждать"),
                  ("paper-plane-tilt", "Получите результат", "Готовое видео придёт в этот чат - чат можно закрыть"),
                  ("chart-line-up", "Опубликуйте и смотрите цифры", "Метрики по ссылке на ролик: просмотры, лайки, ER")],
        "foot": "{free}в сутки - до {video} роликов, {dl} скачиваний и {cut} нарезок",
    },
    "smart": {
        "title": "Умный монтаж: ролик по сценарию", "accent": (92, 168, 255),
        "steps": [("pencil-simple", "Тема", "«Умный монтаж» → «Промт для сценария» → язык → тема одним сообщением"),
                  ("copy", "Промт", "Бот пришлёт промт. Скопируйте его целиком"),
                  ("robot", "Нейросеть", "Вставьте промт в ChatGPT, Claude или Gemini"),
                  ("file-text", "Ответ боту", "Перешлите ответ нейросети сюда - текстом или файлом .json"),
                  ("check-circle", "Проверка", "Бот покажет сцены и кадры. Всё ок - «Собрать», нет - «Промт для доработки»"),
                  ("video", "Готово", "Через 4-8 минут видео с озвучкой и субтитрами придёт в чат")],
        "foot": "Голос, субтитры и графику меняйте в «Настройки» - до того, как брать промт",
    },
    "reels": {
        "title": "ReelsEditor: монтаж говорящей головы", "accent": (255, 140, 66),
        "steps": [("video-camera", "Снимите видео", "Говорите в камеру, паузы и «э-э» не страшны - бот их уберёт"),
                  ("upload-simple", "Пришлите боту", "«ReelsEditor» → видео файлом ({lim}) или ссылкой (Google Диск, Яндекс Диск, YouTube)"),
                  ("sliders-horizontal", "Настройте", "Паузы, паразиты, зумы, вставки со стоков; субтитры - в «Настройки»"),
                  ("play", "«Смонтировать»", "Бот вырежет паузы, оговорки и повторы, поставит зумы, вставки и субтитры"),
                  ("video", "Готовый ролик в чате", "Плюс текст ролика - для описания и проверки")],
        "foot": "{bigfiles}До {reels_min} минут видео",
    },
    "ugc": {
        "title": "UGC-реклама: варианты с разными хуками", "accent": (255, 95, 162),
        "steps": [("tag", "Опишите товар", "«UGC-реклама» → «Описать товар»: что это, для кого, польза, цена или акция"),
                  ("copy", "Промт", "Бот пришлёт промт на 2-5 вариантов ролика"),
                  ("robot", "Нейросеть", "Вставьте промт в ChatGPT, Claude или Gemini"),
                  ("file-text", "Ответ боту", "Перешлите JSON с вариантами сюда - бот покажет хуки"),
                  ("rocket", "Соберите", "«Собрать все» или один вариант - ролики придут в чат"),
                  ("chart-line-up", "Сравните", "Запустите варианты и оставьте тот, что лучше смотрят")],
        "foot": "Название и призыв берутся из «Бренд-профиль». Нейросеть просим не выдумывать цифры и отзывы",
    },
    "ref": {
        "title": "Разбор референса и «сделай похожий»", "accent": (46, 211, 198),
        "steps": [("link", "Пришлите ролик", "«Разбор референса» → ссылка или файл ({lim}), до 3 минут"),
                  ("magnifying-glass", "Получите разбор", "Планы и склейки, темп речи, хук первых 3 секунд, призыв, раскадровка"),
                  ("pencil-simple", "«Сделать похожий»", "Напишите свою тему - бот даст промт с той же структурой"),
                  ("robot", "Нейросеть → бот", "Промт в нейросеть, её ответ (JSON) - сюда"),
                  ("video", "Готовый ролик", "Сводка → «Собрать» → видео в чате")],
        "foot": "Повторяем структуру и темп, а не текст: чужие слова и кадры не копируем",
    },
    "cut": {
        "title": "Нарезка: длинное видео на фрагменты", "accent": (72, 199, 142),
        "steps": [("scissors", "Откройте «Нарезка»", "В главном меню"),
                  ("link", "Пришлите видео", "Ссылку (YouTube, VK, TikTok…) или файл ({lim}), до 30 минут"),
                  ("sliders-horizontal", "Выберите режим", "По переходам - каждая сцена; интересные моменты - только лучшие"),
                  ("timer", "Длина и чувствительность", "Сколько секунд в фрагменте и как часто резать"),
                  ("play", "«Нарезать»", "Бот поставит задачу в очередь"),
                  ("archive", "Получите фрагменты", "До 10 штук - альбомом, больше - zip-архивом")],
        "foot": "Нарезайте только своё видео или то, на что у вас есть права",
    },
    "dl": {
        "title": "Скачать видео по ссылке", "accent": (255, 107, 107),
        "steps": [("download-simple", "Откройте «Скачать видео»", "В главном меню"),
                  ("link", "Пришлите ссылку", "YouTube, TikTok, Instagram, VK и другие площадки"),
                  ("hourglass", "Подождите", "Обычно меньше минуты"),
                  ("video", "Видео в чате", "До 50 МБ; тяжёлое бот аккуратно ужмёт")],
        "foot": "Скачивайте только то, на что у вас есть права. YouTube иногда не отдаёт видео серверам - попробуйте позже",
    },
    "metrics": {
        "title": "Метрики ролика по ссылке", "accent": (186, 120, 255),
        "steps": [("chart-bar", "«Публикация и метрики»", "В главном меню → «Метрики по ссылке»"),
                  ("link", "Пришлите ссылку на ролик", "Свой или конкурента: YouTube, TikTok, Instagram, VK"),
                  ("chart-line-up", "Получите цифры", "Просмотры, лайки, комментарии, вовлечённость (ER)")],
        "foot": "Площадки показывают не все цифры. Автопубликация - в разработке",
    },
}


def _fonts():
    from core import fontpack
    fontpack.ensure()
    d = Path.home() / ".fonts"
    f = lambda name, size: ImageFont.truetype(str(d / name), size)  # noqa: E731
    return {"title": f("FiraSans-Black.ttf", 62), "num": f("FiraSans-Black.ttf", 40), "h": f("FiraSans-Black.ttf", 40),
            "t": f("FiraSans-SemiBold.ttf", 31), "foot": f("FiraSans-SemiBold.ttf", 28), "icon": f("Phosphor-Fill.ttf", 44)}


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render(name: str, out: Path) -> Path:
    from core import iconpack
    g = GUIDES[name]
    fn = _fonts()
    acc = g["accent"]
    tmp = Image.new("RGB", (W, 10))
    dr = ImageDraw.Draw(tmp)
    x0, cw = 60, W - 120
    tx = x0 + 150
    tw = cw - 150 - 90
    lim = "до 2 ГБ" if config.TELEGRAM_API_URL else "до 20 МБ"
    blocks = []
    for icon, h, t in g["steps"]:
        t = t.replace("{lim}", lim)
        hl = _wrap(dr, h, fn["h"], tw)
        tl = _wrap(dr, t, fn["t"], tw)
        blocks.append((icon, hl, tl, 40 + 50 * len(hl) + 42 * len(tl) + 34))
    title = _wrap(dr, g["title"], fn["title"], cw)
    foot_text = g.get("foot", "").format(
        free="Всё бесплатно, " if config.FREE_MODE else "", video=config.DAILY_LIMIT, dl=config.DL_DAILY, cut=config.CUT_DAILY,
        reels_min=config.REELS_MAX_MINUTES,
        bigfiles="" if config.TELEGRAM_API_URL else "Длинные файлы присылайте ссылкой: Telegram даёт ботам скачивать только до 20 МБ. ")
    foot_text = foot_text[:1].upper() + foot_text[1:]
    foot = _wrap(dr, foot_text, fn["foot"], cw - 60)
    gap = 46
    H = 80 + 76 * len(title) + 40 + sum(b[3] for b in blocks) + gap * (len(blocks) - 1) + 50 + (40 * len(foot) + 70 if foot else 0) + 40
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = 80
    d.rectangle([x0, y - 30, x0 + 90, y - 22], fill=acc)
    for ln in title:
        d.text((x0, y), ln, font=fn["title"], fill=TEXT)
        y += 76
    y += 40
    for i, (icon, hl, tl, bh) in enumerate(blocks, 1):
        d.rounded_rectangle([x0, y, x0 + cw, y + bh], radius=30, fill=CARD)
        cy = y + 40 + 32
        d.ellipse([x0 + 34, cy - 46, x0 + 126, cy + 46], fill=acc)
        num = str(i)
        d.text((x0 + 80 - d.textlength(num, font=fn["num"]) / 2, cy - 26), num, font=fn["num"], fill=BG)
        glyph = iconpack.resolve(icon)
        if glyph:
            d.text((x0 + cw - 82, y + 34), glyph, font=fn["icon"], fill=acc)
        yy = y + 40
        for ln in hl:
            d.text((tx, yy), ln, font=fn["h"], fill=TEXT)
            yy += 50
        yy += 6
        for ln in tl:
            d.text((tx, yy), ln, font=fn["t"], fill=MUTED)
            yy += 42
        y += bh
        if i < len(blocks):        # стрелка к следующему шагу
            ax = x0 + 80
            d.line([ax, y + 6, ax, y + gap - 10], fill=LINE, width=6)
            d.polygon([(ax - 14, y + gap - 20), (ax + 14, y + gap - 20), (ax, y + gap - 4)], fill=LINE)
            y += gap
    y += 50
    if foot:
        fh = 40 * len(foot) + 40
        d.rounded_rectangle([x0, y, x0 + cw, y + fh], radius=24, outline=acc, width=3)
        yy = y + 20
        for ln in foot:
            d.text((x0 + 30, yy), ln, font=fn["foot"], fill=TEXT)
            yy += 40
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def path(name: str) -> Path:
    """Готовая картинка инструкции (рисуется при первом обращении)."""
    if name not in GUIDES:
        name = "overview"
    tag = f"{int(config.FREE_MODE)}_{config.DAILY_LIMIT}_{config.DL_DAILY}_{config.CUT_DAILY}_{int(bool(config.TELEGRAM_API_URL))}"
    out = config.ROOT / "guides" / f"{name}_v{V}_{tag}.png"
    if not out.exists():
        render(name, out)
    return out
