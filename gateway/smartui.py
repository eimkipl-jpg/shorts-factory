"""Режим «🎞 Умный монтаж» в боте: сценарий со сценами → видеоряд подбирается под смысл каждой фразы.

Кнопки (sm:...):
  sm:home                      экран режима
  sm:p, sm:pl:<язык>           промт для нейросети (после выбора языка бот ждёт тему)
  sm:set, sm:set:<раздел>      настройки: главный экран и разделы (голос, картинки, графика, сценарий, монтаж)
  sm:o:<ключ>, sm:s:<ключ>     переключить настройку (smart.OPTIONS) / голос, музыку, поиск в интернете
  sm:reset                     сбросить настройки (голос и музыка остаются)
  sm:go, sm:cancel             собрать / отменить присланный сценарий
"""
import asyncio
import json
import logging
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config, db, library, profile
from core import scenario, smart, story
from gateway import state
from gateway.ui import safe_answer, safe_edit

log = logging.getLogger("smartui")
router = Router()

HELP = ("🎞 Умный монтаж\n"
        "Сценарий по сценам: к каждой фразе нейросеть выбирает картинку - видео, фото, иллюстрацию, архивный снимок или "
        "нарисованную карточку (цифры, графики, цепочки с иконками). Смена кадра попадает в начало каждой мысли.\n\n"
        "1. «📋 Промт для сценария» → язык → тема.\n"
        "2. Отправьте промт нейросети, ответ-JSON пришлите сюда текстом или файлом .json.\n"
        "3. Проверьте сводку (сцены и запросы кадров) и нажмите «🚀 Собрать».\n\n"
        "🗺 Проверить карту до сборки: /map 1850 1910 Бангкок, Ханой")


def _kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows if row])


def _ok(uid: int) -> bool:
    if config.CREATOR:      # бот для креаторов открыт всем; блокировка и лимиты - в common/creator.py
        return True
    return uid in config.ALLOWED_USER_IDS


def _mark(on: bool) -> str:
    return "✅ " if on else ""


def cfg_of(st: dict) -> dict:
    saved = st.get("smart") or {}
    if saved and saved.get("v") != smart.CFG_V:     # настройки до патча 36: новые рекомендуемые значения (см. smart.upgrade_cfg)
        saved = smart.upgrade_cfg(saved)
        st["smart"] = saved
    return {**smart.DEFAULT_CFG, **saved}


def _voice_options() -> list[str]:
    return ["auto", *smart.el_voices().values(), *config.VOICES]


def home_screen(st: dict):
    cfg = cfg_of(st)
    keys = bool(config.PEXELS_API_KEY or config.PIXABAY_API_KEY)
    text = (f"{HELP}\n\nСейчас: голос {smart.voice_label(cfg['voice']) if cfg['voice'] != 'auto' else 'по языку'}; музыка: {cfg['music']}; "
            f"стоки: {'вкл' if cfg['stock'] else 'выкл'}; субтитры: {'да' if cfg['subs'] else 'нет'}; "
            f"графика: {smart.GRAPHICS[smart.graphics_level(cfg)][2]}; темп: {smart.PACE.get(cfg.get('pace'), smart.PACE['normal'])[2]}")
    if cfg["stock"] and not keys:
        text += "\n\n❗ Ключи Pexels/Pixabay не заданы: бот возьмёт клипы только из вашей библиотеки (ключи - в .env: PEXELS_API_KEY, PIXABAY_API_KEY)."
    return text, _kb([("📋 Промт для сценария", "sm:p")], [("⚙️ Настройки", "sm:set")], [("⬅ Меню", "m:back")])


def settings_screen(st: dict):
    """Главный экран настроек: краткая сводка и разделы."""
    cfg = cfg_of(st)
    voice = smart.voice_label(cfg["voice"]) if cfg["voice"] != "auto" else "по языку"
    text = ("⚙️ Умный монтаж - настройки для всех следующих роликов\n\n"
            f"🎙 Голос: {voice}, темп {smart.opt_label(cfg, 'pace')}, музыка: {cfg['music']} ({smart.opt_label(cfg, 'music_vol')})\n"
            f"🖼 Картинки: {smart.opt_label(cfg, 'img_mode')}; подбор {smart.opt_label(cfg, 'strict').split(':')[0]}\n"
            f"🎨 Графика: {smart.opt_label(cfg, 'graphics')}, оформление {smart.opt_label(cfg, 'theme')}; "
            f"субтитры: {smart.opt_label(cfg, 'sub_style')}\n"
            f"📝 Сценарий: {smart.opt_label(cfg, 'length')}, подача «{smart.opt_label(cfg, 'tone')}»\n"
            f"✨ Переходы: {smart.opt_label(cfg, 'style')}\n\n"
            "Длина, подача, доля графики и источники картинок попадают в промт: после смены возьмите новый промт.")
    rows = [[(title, f"sm:set:{sec}")] for sec, title in smart.SECTIONS.items()]
    rows.append([("↩ Сбросить по умолчанию", "sm:reset"), ("⬅ Меню", "cr:home") if config.CREATOR else ("⬅ Назад", "sm:home")])
    return text, _kb(*rows)


SECTION_HELP = {
    "voice": "Голос: «ElevenLabs · …» - ваши голоса из .env, остальные - бесплатные Edge. Темп - скорость речи и длина пауз между фразами.",
    "visual": "Как показывать картинки и где их искать. Строгий подбор берёт картинку, только если её описание совпало с запросом "
              "(и с именами и годами из него); иначе сцена получает крупную иконку по смыслу.",
    "look": "Графика на весь кадр - доля сцен, которые бот рисует сам. Оформление - цвета фона и текста карточек. Субтитры - вид, "
            "размер, место и регистр. Карты «по сценарию» - нейросеть ставит историческую карту туда, где важно «где» "
            "(проверить карту: /map 1850 Бангкок).",
    "script": "Длина и подача попадают в промт для нейросети.",
    "edit": "Переходы между сценами. «Резкие склейки» - самый динамичный вариант для Shorts.",
}


# подробные пояснения к каждой настройке (экран настройки и «📖 Гайд» раздела)
OPT_GUIDE = {
    "voice": "Голос диктора. «По языку» - мужской голос под язык сценария. Для Shorts на русском чаще берут Дмитрия "
             "(уверенный) или Светлану (мягкая). Нажмите «▶ Прослушать», чтобы услышать выбранный голос.",
    "music": "Фоновая музыка из библиотеки по настроению. «auto» - нейросеть выберет сама по сценарию, «off» - без музыки. "
             "Музыка автоматически приглушается под голос.",
    "music_vol": "Громкость музыки под голосом. 10% - фон почти не мешает речи (рекомендуем), 20-30% - для динамичных роликов без сложного текста.",
    "pace": "Скорость речи и паузы между фразами. «Живой» - золотая середина для Shorts. «Быстрый» - для коротких фактов, "
            "«спокойный» - для историй и сложных тем.",
    "img_mode": "«На весь кадр» - картинка заполняет экран и медленно движется (динамичнее). «Карточкой» - картинка целиком "
                "в рамке на размытом фоне: ничего не обрезается, удобно для схем, документов и архивных фото.",
    "strict": "Строгий подбор берёт кадр, только если его описание совпало с запросом. Если совпадения нет - вместо случайного "
              "кадра будет крупная иконка по смыслу. Обычный - кадр будет всегда, но иногда мимо.",
    "src_video": "Стоковые видео (Pexels, Pixabay): живое движение, лучший вариант для большинства сцен.",
    "src_photo": "Стоковые фотографии: когда нужного видео нет. Бот добавит плавное движение камеры.",
    "src_illustration": "Рисованные иллюстрации и векторы: хорошо для абстрактных тем (экономика, психология, наука).",
    "src_archive": "Архив Wikimedia Commons: исторические фото, карты, портреты, документы. Автор и лицензия попадут в описание.",
    "graphics": "Доля сцен, которые бот рисует сам: цифры, графики, цепочки причин, сравнения. «Минимум» - 2-3 карточки на ролик, "
                "«много» - почти инфографика. Значение попадает в промт.",
    "theme": "Цвета карточек и фона графики: тёмное цветное (универсально), светлое (лайфстайл, обучение), чёрное (минимализм, премиум).",
    "sub_style": "Вид субтитров. «Подсветка слова» - текущее слово выделяется цветом, самый удерживающий вариант для Shorts. "
                 "«Спокойные» - две строки без анимации, для длинных объяснений. «Выключены» - если субтитры добавите сами.",
    "sub_size": "Размер субтитров. Крупный читается на телефоне без напряжения, мелкий оставляет больше места картинке.",
    "sub_pos": "Где субтитры: «ниже центра» - в зоне взгляда (рекомендуем); «внизу» - если внизу кадра нет интерфейса площадки. "
               "Учтите: в Shorts и Reels нижнюю часть закрывают подпись и кнопки.",
    "sub_case": "ЗАГЛАВНЫЕ выглядят энергичнее и лучше читаются мельком; обычный регистр - спокойнее и для длинных фраз.",
    "maps": "Исторические карты: нейросеть ставит карту с границами нужного года там, где важно «где и когда». Выключите для "
            "тем, где карты не нужны.",
    "length": "Длина ролика попадает в промт. 30-45 с - максимальная досматриваемость; 45-60 с - когда тема требует объяснения.",
    "tone": "Подача текста в промте: научпоп, рассказ другу, с юмором, драматично или экспертно.",
    "style": "Переходы между сценами. «Резкие склейки» - самый динамичный и профессиональный вариант для Shorts; «плавные» и "
             "«кино» - для историй и атмосферы; «хайп» - для развлекательного контента.",
}

SECTION_GUIDE = {
    "voice": "🎙 Голос и звук - гайд\n\n• Голос подбирайте под аудиторию: мужской уверенный - для фактов и бизнеса, женский мягкий - "
             "для лайфстайла и психологии.\n• Темп «живой» подходит почти всегда; «быстрый» - только для коротких роликов до 30 с.\n"
             "• Музыку держите на 10%: если голос плохо слышно, зритель уходит в первые секунды.",
    "visual": "🖼 Картинки - гайд\n\n• Оставьте включёнными все источники: бот сам выберет лучший кадр.\n• Строгий подбор - если "
              "важна точность (история, наука); обычный - для развлекательных роликов.\n• Для документов, карт и схем - режим "
              "«карточкой», чтобы ничего не обрезалось.",
    "look": "🎨 Графика и субтитры - гайд\n\n• Для Shorts: субтитры «подсветка слова», крупные, ниже центра, ЗАГЛАВНЫЕ.\n"
            "• Нижние 20% экрана в Shorts и Reels закрыты подписью и кнопками - не ставьте туда важное.\n"
            "• Графики «минимум» достаточно: 2-3 карточки с цифрами делают ролик убедительнее, больше - утомляет.",
    "script": "📝 Сценарий - гайд\n\n• Первая фраза - вопрос или неожиданный факт: у вас 1-2 секунды, чтобы зритель остался.\n"
              "• 30-45 секунд - лучший баланс досматриваемости и смысла.\n• После смены длины или подачи возьмите новый промт.",
    "edit": "✨ Монтаж - гайд\n\n• Резкие склейки - стандарт коротких видео: ощущение темпа без «эффектов ради эффектов».\n"
            "• Плавные переходы и «кино» уместны в историях и атмосферных роликах.",
}

VOICE_NAMES = {"ru-RU-DmitryNeural": "Дмитрий (рус, муж.)", "ru-RU-SvetlanaNeural": "Светлана (рус, жен.)",
               "en-GB-RyanNeural": "Ryan (англ. брит., муж.)", "en-GB-SoniaNeural": "Sonia (англ. брит., жен.)",
               "en-US-GuyNeural": "Guy (англ. амер., муж.)", "en-US-JennyNeural": "Jenny (англ. амер., жен.)",
               "de-DE-ConradNeural": "Conrad (нем., муж.)", "de-DE-KatjaNeural": "Katja (нем., жен.)",
               "es-ES-AlvaroNeural": "Álvaro (исп., муж.)", "es-ES-ElviraNeural": "Elvira (исп., жен.)",
               "fr-FR-HenriNeural": "Henri (фр., муж.)", "fr-FR-DeniseNeural": "Denise (фр., жен.)"}
SAMPLE = {"ru": "Привет! Так будет звучать озвучка вашего ролика. Коротко, ясно и без лишних пауз.",
          "en": "Hi! This is how the voice-over of your video will sound. Short, clear and to the point.",
          "de": "Hallo! So klingt die Stimme in Ihrem Video.", "es": "¡Hola! Así sonará la voz de tu vídeo.",
          "fr": "Bonjour ! Voici la voix de votre vidéo."}
_PREVIEW_LAST: dict[int, float] = {}


def _vname(v: str) -> str:
    if v == "auto":
        return "по языку сценария"
    return VOICE_NAMES.get(v) or smart.voice_label(v)


def section_screen(st: dict, sec: str):
    cfg = cfg_of(st)
    rows = []
    if sec == "voice":
        rows.append([("🎙 Голос: " + _vname(cfg["voice"]), "sm:vv")])
        rows.append([("🎵 Музыка: " + cfg["music"], "sm:mm")])
    if sec == "visual":
        rows.append([(_mark(cfg["stock"]) + "Искать в интернете (стоки, архив)", "sm:s:stock")])
    for key, (s_, label, _) in smart.OPTIONS.items():
        if s_ == sec:
            rows.append([(f"{label}: {smart.opt_label(cfg, key)}", f"sm:oo:{key}")])
    rows.append([("📖 Гайд по разделу", f"sm:g:{sec}")])
    rows.append([("⬅ К настройкам", "sm:set")])
    return f"{smart.SECTIONS[sec]}\n{SECTION_HELP.get(sec, '')}\n\nНажмите на настройку - откроются варианты и пояснение.", _kb(*rows)


def option_screen(st: dict, key: str):
    """Одна настройка: все варианты кнопками (текущий отмечен) и пояснение, что выбрать."""
    cfg = cfg_of(st)
    sec, label, vals = smart.OPTIONS[key]
    cur = smart.opt(cfg, key)
    rows = [[(_mark(v == cur) + str(name), f"sm:ov:{key}:{i}")] for i, (v, name) in enumerate(vals)]
    rows.append([("⬅ Назад", f"sm:set:{sec}")])
    return f"{label}\nСейчас: {smart.opt_label(cfg, key)}\n\n💡 {OPT_GUIDE.get(key, '')}", _kb(*rows)


def voice_screen(st: dict):
    cfg = cfg_of(st)
    opts = _voice_options()
    rows = [[(_mark(cfg["voice"] == v) + _vname(v), f"sm:vs:{i}")] for i, v in enumerate(opts)]
    rows.append([("▶ Прослушать выбранный", "sm:vp")])
    rows.append([("⬅ Назад", "sm:set:voice")])
    return f"🎙 Голос диктора\nСейчас: {_vname(cfg['voice'])}\n\n💡 {OPT_GUIDE['voice']}", _kb(*rows)


def music_screen(st: dict):
    cfg = cfg_of(st)
    opts = ["off", "auto"] + [t for t, _ in library.tag_counts("music") if t != "без-темы"][:20]
    names = {"off": "без музыки", "auto": "auto - по сценарию"}
    rows = [[(_mark(cfg["music"] == v) + names.get(v, v), f"sm:ms:{i}")] for i, v in enumerate(opts)]
    rows.append([("⬅ Назад", "sm:set:voice")])
    return f"🎵 Музыка\nСейчас: {names.get(cfg['music'], cfg['music'])}\n\n💡 {OPT_GUIDE['music']}", _kb(*rows)


async def _send_long(message: Message, text: str, filename: str) -> None:
    if len(text) <= 3900:
        await message.answer(text)
    else:
        await message.answer_document(BufferedInputFile(text.encode("utf-8"), filename=filename),
                                      caption="Промт длинный, поэтому файлом: скопируйте текст целиком.")


@router.message(Command("smart"))
async def cmd_smart(message: Message):
    if message.from_user and _ok(message.from_user.id):
        st = state.load(message.from_user.id)
        text, kb = home_screen(st)
        await message.answer(text, reply_markup=kb)


_MAP_LAST: dict[int, float] = {}
MAP_HELP = ("🗺 Проверка карты: /map <годы> <места через запятую>\n"
            "Пример: /map 1850 1890 1910 Бангкок, Ханой, Рангун\n"
            "Год до нашей эры - с минусом: /map -44 Рим. Без года - карта сегодняшнего мира: /map Таиланд\n"
            "Места - современные города или страны: бот подсветит государство, которому они принадлежали в этот год.")


@router.message(Command("map"))
async def cmd_map(message: Message):
    """Быстрый кадр карты по годам - проверить границы до сборки ролика."""
    if not (message.from_user and _ok(message.from_user.id)):
        return
    from core import histmap
    if config.CREATOR:
        import time as _t
        last = _MAP_LAST.get(message.from_user.id, 0)
        if _t.time() - last < 30:
            return await message.answer("🗺 Карты можно проверять раз в 30 секунд.")
        _MAP_LAST[message.from_user.id] = _t.time()
    args = (message.text or "").split(maxsplit=1)[1:] or [""]
    import re as _re
    m = _re.match(r"^\s*((?:-?\d{1,4}\s*(?:до н\.?\s?э\.?)?[\s,→>-]*)*)(.*)$", args[0])
    years = [int(y) for y in _re.findall(r"-?\d{1,4}", m.group(1))] if m else []
    if m and "до н" in m.group(1):
        years = [-abs(y) for y in years]
    places = [p.strip() for p in (m.group(2) if m else "").split(",") if p.strip()]
    if not places:
        return await message.answer(MAP_HELP)
    warn: list[str] = []
    spec = histmap.normalize({"years": years, "highlight": [{"at": p} for p in places]}, "карта", warn)
    if not spec:
        return await message.answer("❌ " + "\n".join(warn or ["карта не получилась"]) + "\n\n" + MAP_HELP)
    await message.answer("🗺 Рисую карту…")
    work = config.TMP_DIR / f"mapprev_{message.from_user.id}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        shots = await asyncio.to_thread(histmap.preview, work, spec)
    except Exception as e:  # noqa: BLE001
        log.exception("превью карты")
        return await message.answer(f"❌ Карта не нарисовалась: {str(e)[:300]}")
    note = ("\n⚠️ " + "\n⚠️ ".join(warn)) if warn else ""
    for k, f in enumerate(shots):
        cap = histmap.describe({**spec, "years": [spec["years"][k]]}) if not spec.get("modern") else histmap.describe(spec)
        await message.answer_photo(FSInputFile(f), caption=(cap + (note if k == len(shots) - 1 else ""))[:1000])


@router.callback_query(F.data.startswith("sm:"))
async def on_sm(cb: CallbackQuery):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    parts = cb.data.split(":")
    act = parts[1]
    st = state.load(uid)
    await safe_answer(cb)
    if act == "home":
        if st.get("awaiting") in ("sm_topic", "sm_main", "sm_facts"):
            st["awaiting"] = None
            state.save(uid, st)
        return await safe_edit(cb.message, *home_screen(st))
    if act == "p":
        return await safe_edit(cb.message, "📋 На каком языке озвучка?", _kb([("Русский", "sm:pl:ru"), ("English", "sm:pl:en")], [("⬅ Назад", "sm:home")]))
    if act == "pl":
        lang = parts[2] if len(parts) > 2 and parts[2] in story.LANG_NAMES else "ru"
        st["awaiting"] = "sm_topic"
        st["lists"]["sm_lang"] = lang
        state.save(uid, st)
        st["lists"]["sm_brief"] = {}
        state.save(uid, st)
        return await cb.message.answer("Шаг 1 из 4. О чём ролик? Напишите тему одним сообщением, например:\n"
                                       "«История съёмок фильма „Любовь и голуби“» или «Почему инфляция съедает сбережения».",
                                       reply_markup=_kb([("✖ Отмена", "sm:home")]))
    if act == "bz":       # блиц перед промтом: пропустить шаг, сразу промт, форма рассказа
        brief = st["lists"].setdefault("sm_brief", {})
        if not brief.get("topic"):
            return await cb.message.answer("Тема не найдена - начните заново: «📋 Промт для сценария».")
        what = parts[2] if len(parts) > 2 else ""
        if what == "a" and len(parts) > 3 and parts[3] in ANGLES:
            brief["angle"] = parts[3]
            return await _brief_prompt(cb.message, uid, st)
        if what == "go":
            return await _brief_prompt(cb.message, uid, st)
        if what == "skip":
            return await _brief_next(cb.message, uid, st)
    if act == "set":
        if len(parts) > 2 and parts[2] in smart.SECTIONS:
            return await safe_edit(cb.message, *section_screen(st, parts[2]))
        return await safe_edit(cb.message, *settings_screen(st))
    if act == "reset":
        keep = {k: cfg_of(st)[k] for k in ("voice", "music")}
        st["smart"] = {**smart.DEFAULT_CFG, **keep}
        state.save(uid, st)
        return await safe_edit(cb.message, *settings_screen(st))
    if act == "g" and len(parts) >= 3 and parts[2] in SECTION_GUIDE:
        return await cb.message.answer(SECTION_GUIDE[parts[2]], reply_markup=_kb([("⬅ К разделу", f"sm:set:{parts[2]}")]))
    if act == "oo" and len(parts) >= 3 and parts[2] in smart.OPTIONS:
        return await safe_edit(cb.message, *option_screen(st, parts[2]))
    if act == "ov" and len(parts) >= 4 and parts[2] in smart.OPTIONS:
        cfg = cfg_of(st)
        key = parts[2]
        vals = smart.OPTIONS[key][2]
        try:
            cfg[key] = vals[int(parts[3])][0]
        except (ValueError, IndexError):
            return
        if key == "sub_style":
            cfg["subs"] = cfg[key] != "off"
        st["smart"] = cfg
        state.save(uid, st)
        return await safe_edit(cb.message, *option_screen(st, key))
    if act == "vv":
        return await safe_edit(cb.message, *voice_screen(st))
    if act == "vs" and len(parts) >= 3:
        opts = _voice_options()
        try:
            v = opts[int(parts[2])]
        except (ValueError, IndexError):
            return
        cfg = cfg_of(st)
        cfg["voice"] = v
        st["smart"] = cfg
        state.save(uid, st)
        return await safe_edit(cb.message, *voice_screen(st))
    if act == "vp":
        return await _voice_preview(cb.message, uid, cfg_of(st))
    if act == "mm":
        return await safe_edit(cb.message, *music_screen(st))
    if act == "ms" and len(parts) >= 3:
        opts = ["off", "auto"] + [t for t, _ in library.tag_counts("music") if t != "без-темы"][:20]
        try:
            v = opts[int(parts[2])]
        except (ValueError, IndexError):
            return
        cfg = cfg_of(st)
        cfg["music"] = v
        st["smart"] = cfg
        state.save(uid, st)
        return await safe_edit(cb.message, *music_screen(st))
    if act == "o" and len(parts) >= 3 and parts[2] in smart.OPTIONS:
        cfg = cfg_of(st)
        key = parts[2]
        vals = [v for v, _ in smart.OPTIONS[key][2]]
        cur = smart.opt(cfg, key)
        cfg[key] = vals[(vals.index(cur) + 1) % len(vals)]
        if key == "sub_style":
            cfg["subs"] = cfg[key] != "off"
        st["smart"] = cfg
        state.save(uid, st)
        return await safe_edit(cb.message, *section_screen(st, smart.OPTIONS[key][0]))
    if act == "s" and len(parts) >= 3:
        cfg = cfg_of(st)
        key = parts[2]
        if key == "voice":
            opts = _voice_options()
            cfg["voice"] = opts[(opts.index(cfg["voice"]) + 1) % len(opts)] if cfg["voice"] in opts else "auto"
        elif key == "music":
            opts = ["off", "auto"] + [t for t, _ in library.tag_counts("music") if t != "без-темы"]
            cfg["music"] = opts[(opts.index(cfg["music"]) + 1) % len(opts)] if cfg["music"] in opts else "off"
        elif key == "stock":
            cfg["stock"] = not cfg["stock"]
        elif key == "subs":
            cfg["subs"] = not cfg["subs"]
        elif key == "pace":
            order = list(smart.PACE)
            cur = cfg.get("pace") if cfg.get("pace") in smart.PACE else "normal"
            cfg["pace"] = order[(order.index(cur) + 1) % len(order)]
        elif key == "graphics":
            order = list(smart.GRAPHICS)
            cfg["graphics"] = order[(order.index(smart.graphics_level(cfg)) + 1) % len(order)]
        st["smart"] = cfg
        state.save(uid, st)
        sec = {"voice": "voice", "music": "voice", "pace": "voice", "stock": "visual", "subs": "look", "graphics": "look"}.get(key)
        return await safe_edit(cb.message, *(section_screen(st, sec) if sec else settings_screen(st)))
    if act == "hum":
        raw = st["lists"].get("smart_raw")
        pending = st["lists"].get("smart_pending") or {}
        if not raw:
            return await cb.message.answer("Сценарий не найден, пришлите JSON заново.")
        await _send_long(cb.message, smart.make_humanize_prompt(raw, pending.get("lang", "ru")), "smart_humanize_prompt.txt")
        return await cb.message.answer("Отправьте этот промт нейросети (лучше в том же чате, где писался сценарий). Она перепишет только "
                                       "тексты сцен живым языком, картинки и факты останутся. Ответ (JSON) пришлите мне - покажу сводку заново.",
                                       reply_markup=_kb([("🎞 К умному монтажу", "sm:home")]))
    if act == "edit":
        raw = st["lists"].get("smart_raw")
        pending = st["lists"].get("smart_pending") or {}
        if not raw:
            return await cb.message.answer("Сценарий не найден, пришлите JSON заново.")
        await _send_long(cb.message, smart.make_edit_prompt(raw, pending.get("lang", "ru"), cfg_of(st)), "smart_edit_prompt.txt")
        return await cb.message.answer("Отправьте этот промт нейросети (лучше в том же чате, где писался сценарий). "
                                       "Исправленный JSON пришлите мне - покажу сводку заново.",
                                       reply_markup=_kb([("🎞 К умному монтажу", "sm:home")]))
    if act == "cancel":
        st["lists"].pop("smart_pending", None)
        state.save(uid, st)
        return await cb.message.answer("Сценарий отменён.", reply_markup=_kb([("🎞 К умному монтажу", "sm:home")]))
    if act == "go":
        if config.CREATOR:      # отказ по лимитам не должен терять присланный сценарий
            from common import creator
            okc, why = creator.check(uid)
            if not okc:
                if why == "no_credits":
                    return await cb.message.answer("🎟 Бесплатные ролики закончились. Сценарий сохранён - после пополнения нажмите «Собрать».",
                                                   reply_markup=_kb([("⭐ Купить ролики", "buy:menu")], [("🚀 Собрать", "sm:go")]))
                return await cb.message.answer("⛔ " + why + "\nСценарий сохранён - нажмите «Собрать», когда можно.",
                                               reply_markup=_kb([("🚀 Собрать", "sm:go")]))
        if config.CREATOR and config.REQUIRE_CHANNEL:
            from common import creator
            if not creator.is_admin(uid) and creator.get(uid)["credits"] <= 0 and creator.free_left(creator.get(uid)) > 0:
                try:
                    mem = await cb.bot.get_chat_member(config.REQUIRE_CHANNEL, uid)
                    subscribed = mem.status in ("member", "administrator", "creator")
                except Exception:  # noqa: BLE001 - канал недоступен боту: не блокируем людей из-за настроек
                    subscribed = True
                if not subscribed:
                    return await cb.message.answer(f"📣 Бесплатное демо - для подписчиков {config.REQUIRE_CHANNEL}. "
                                                   "Подпишитесь и нажмите «Собрать» ещё раз (сценарий сохранён).",
                                                   reply_markup=_kb([("🚀 Собрать", "sm:go")]))
        pending = st["lists"].pop("smart_pending", None)
        if not pending:
            return await cb.message.answer("Сценарий не найден, пришлите JSON заново.")
        state.save(uid, st)
        return await create_task(cb.message, pending, cfg_of(st), uid)


async def _voice_preview(message: Message, uid: int, cfg: dict) -> None:
    """Короткий образец выбранного голоса с текущим темпом (Edge TTS; раз в 10 секунд на человека)."""
    import time as _t
    if _t.time() - _PREVIEW_LAST.get(uid, 0) < 10:
        return await message.answer("⏳ Прослушивание - раз в 10 секунд.")
    _PREVIEW_LAST[uid] = _t.time()
    voice = cfg["voice"] if cfg["voice"] != "auto" else story.VOICE_BY_LANG.get("ru", config.TTS_VOICE)
    if voice.startswith("el:"):
        return await message.answer("Образцы есть только для бесплатных голосов Edge.")
    from core import tts
    lang = voice[:2].lower()
    rate = smart.PACE.get(cfg.get("pace"), smart.PACE["normal"])[0] or None
    out = config.TMP_DIR / f"voice_{uid}.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(tts.synthesize_edge, SAMPLE.get(lang, SAMPLE["en"]), out, voice, rate)
        await message.answer_audio(FSInputFile(out, filename="voice.mp3"), title=_vname(voice), performer="Образец голоса",
                                   caption=f"🎙 {_vname(voice)}, темп «{smart.PACE.get(cfg.get('pace'), smart.PACE['normal'])[2]}»")
    except Exception as e:  # noqa: BLE001
        log.warning("образец голоса: %s", e)
        await message.answer("Не удалось получить образец, попробуйте позже.")
    finally:
        out.unlink(missing_ok=True)


ANGLES = {"story": ("📖 История по порядку", "история по порядку событий: завязка, поворот, развязка"),
          "facts": ("🔢 Факты или топ-список", "набор сильных фактов или топ-список, от интересного к самому неожиданному"),
          "qa": ("❓ Вопрос → ответ", "вопрос в начале и подробный ответ на него к финалу"),
          "versus": ("⚖️ Сравнение", "сравнение двух вещей, эпох или вариантов с выводом"),
          "free": ("🤷 На усмотрение нейросети", "")}
BRIEF_STEPS = [("sm_main", "Шаг 2 из 4. 🎯 Главная мысль: что зритель должен понять или узнать к концу ролика?\n"
                           "Например: «фильм почти закрыли, а он стал хитом» или «копить надо на отдельной карте»."),
               ("sm_facts", "Шаг 3 из 4. 📌 Что обязательно должно прозвучать: факты, имена, даты, цифры?\n"
                            "Например: «снимали в Карелии и Москве в 1983, Меньшов сам переписал сценарий».")]


async def _brief_next(message: Message, uid: int, st: dict) -> None:
    """Следующий шаг блица: главная мысль -> обязательные факты -> форма рассказа -> промт."""
    brief = st["lists"].setdefault("sm_brief", {})
    for key, question in BRIEF_STEPS:
        field = {"sm_main": "main", "sm_facts": "facts"}[key]
        if field not in brief:
            brief[field] = ""          # показан - дальше не спрашиваем, даже если пропустят
            st["awaiting"] = key
            state.save(uid, st)
            return await message.answer(question, reply_markup=_kb([("⏭ Пропустить", "sm:bz:skip"), ("⚡ Сразу промт", "sm:bz:go")],
                                                                   [("✖ Отмена", "sm:home")]))
    st["awaiting"] = None
    state.save(uid, st)
    rows = [[(name, f"sm:bz:a:{k}")] for k, (name, _) in ANGLES.items()]
    await message.answer("Шаг 4 из 4. Как рассказать?", reply_markup=_kb(*rows))


def _ev(uid: int, ev: str, detail: str = "") -> None:
    """Событие для админ-статистики бота креаторов (в личном боте ничего не пишет)."""
    if config.CREATOR:
        from common import adminstats
        adminstats.event(uid, ev, detail)


def brief_topic(brief: dict) -> str:
    """Ответы блица -> строка темы для промта."""
    parts = [brief.get("topic", "")]
    if brief.get("main"):
        parts.append(f"ГЛАВНАЯ МЫСЛЬ (ролик ведёт к ней): {brief['main']}")
    if brief.get("facts"):
        parts.append(f"ОБЯЗАТЕЛЬНО УПОМЯНУТЬ: {brief['facts']}")
    angle = ANGLES.get(brief.get("angle") or "free", ("", ""))[1]
    if angle:
        parts.append(f"ФОРМА РАССКАЗА: {angle}")
    return "\n".join(p for p in parts if p)


async def _brief_prompt(message: Message, uid: int, st: dict) -> None:
    brief = st["lists"].get("sm_brief") or {}
    lang = st["lists"].get("sm_lang", "ru")
    st["awaiting"] = None
    state.save(uid, st)
    ctx = await asyncio.to_thread(scenario.build_ctx, db.get_user_channel(uid))
    await _send_long(message, smart.make_prompt(brief_topic(brief), lang, ctx, cfg_of(st)), "smart_prompt.txt")
    _ev(uid, "prompt", lang)
    await message.answer("Отправьте этот промт нейросети. Готовый JSON пришлите мне.", reply_markup=_kb([("🎞 К умному монтажу", "sm:home")]))


def _eta_text(ahead: int) -> str:
    n = ahead + 1
    mins = max(4, n * 4)
    return f"Вы {n}-й в очереди, ориентировочно {mins} мин." if ahead else "Ваш ролик сейчас первый - готовность примерно через 4-5 минут."


async def create_task(message: Message, res: dict, cfg: dict, uid: int | None = None) -> None:
    channel, m = res["channel"], res["meta"]
    if config.CREATOR:
        from common import creator
        uid = uid or (message.from_user.id if message.from_user else 0)
        okc, why = creator.check(uid)
        if not okc:
            if why == "no_credits":
                return await message.answer("🎟 Бесплатные ролики закончились. Чтобы продолжить - пополните баланс.",
                                            reply_markup=_kb([("⭐ Купить ролики", "buy:menu")]))
            return await message.answer("⛔ " + why)
    voice = smart.voice_for(cfg, m, res["lang"])
    if voice.startswith("el:") and not config.ELEVENLABS_API_KEY:
        return await message.answer("❗ Выбран голос ElevenLabs, но ELEVENLABS_API_KEY не задан в .env. Выберите другой голос в настройках.",
                                   reply_markup=_kb([("⚙️ Настройки", "sm:set")]))
    task_id = db.create_task(channel, "smart", message.chat.id)
    task_dir = config.TASKS_DIR / f"task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)
    data = {"task_id": task_id, "channel": channel, "input_type": "smart", **profile.for_task(channel),
            "title": m["title"], "description": m["description"], "tags": m["tags"],
            "voice": voice, "rate": m.get("rate", 0),
            "music": m.get("music", cfg.get("music", "off")), "music_vol": m.get("music_vol", cfg.get("music_vol", 10)),
            "subs": bool(cfg.get("subs", True)), "lang": res["lang"],
            "smart": {"scenes": res["scenes"], "lang": res["lang"], "hero": res.get("hero"), "frame": res.get("frame") or {}},
            "smart_cfg": cfg, "source": "smart_json"}
    if config.CREATOR:
        try:
            demo = creator.reserve(uid, task_id)
        except RuntimeError:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)
            db.set_status(task_id, "rejected")
            return await message.answer("🎟 Бесплатные ролики закончились.", reply_markup=_kb([("⭐ Купить ролики", "buy:menu")]))
        if demo and config.WATERMARK:
            data["watermark"] = config.WATERMARK
        from core import brand as brandmod
        ust = state.load(uid)                   # формат и бренд-профиль креатора
        bp = brandmod.get(ust)
        data["format"], data["fit"], data["brand"] = ust.get("format", "9:16"), ust.get("fit", "blur"), bp
        data["smart_cfg"] = {**data["smart_cfg"], "sub_accent": brandmod.color_hex(bp)}
        data["description"] = brandmod.with_tags(data.get("description") or "", bp)
        (task_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        creator.log_submit(uid, m["title"])
        ahead = creator.queue_position(task_id)
        db.mark_ready(task_id)
        u = creator.get(uid)
        if creator.is_admin(uid):
            left = "∞"
        elif config.FREE_MODE:
            left = f"роликов на сегодня {max(0, config.DAILY_LIMIT - creator.today_count(uid))} из {config.DAILY_LIMIT}"
        else:
            left = f"бесплатных {creator.free_left(u)}, кредитов {u['credits']}"
        return await message.answer(f"✅ Принято: «{m['title'][:60]}», сцен {len(res['scenes'])}"
                                    + (" (демо с водяным знаком)" if demo and config.WATERMARK else "")
                                    + f".\n{_eta_text(ahead)} Бот пришлёт видео сам - можно закрыть чат.\nОсталось: {left}."
                                    + (f"\n{creator.activity_line()}" if creator.activity_line() else ""),
                                    reply_markup=_kb([("🎞 Ещё ролик", "sm:home")]))
    (task_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    db.mark_ready(task_id)
    await message.answer(f"Принято: task_{task_id}, «{m['title'][:60]}», канал {channel}, сцен {len(res['scenes'])}. В очереди: {db.queue_size()}",
                         reply_markup=_kb([("🎞 Ещё ролик", "sm:home")]))


_REVIEW_LAST: dict[int, float] = {}


def _scenario_text(data: dict) -> str:
    """Весь текст сценария для проверки по стоп-словам: заголовок, фразы, запросы кадров."""
    parts = [str(data.get("title") or ""), str(data.get("description") or "")]
    for sc in data.get("scenes") or []:
        if isinstance(sc, dict):
            parts.append(str(sc.get("text") or ""))
            vis = sc.get("visual")
            if isinstance(vis, dict):
                parts.append(str(vis.get("query") or ""))
    return " ".join(parts)


async def review(message: Message, uid: int, st: dict, data: dict) -> None:
    if config.CREATOR:
        import time as _t
        if _t.time() - _REVIEW_LAST.get(uid, 0) < 5:
            return await message.answer("⏳ Не так быстро: подождите несколько секунд между сценариями.")
        _REVIEW_LAST[uid] = _t.time()
        from common import creator
        bad = creator.blocked(_scenario_text(data))
        if bad:
            return await message.answer("⛔ Сценарий отклонён правилами сервиса. Измените тему и пришлите заново.")
    ctx = await asyncio.to_thread(scenario.build_ctx, db.get_user_channel(uid))
    ctx["channel"] = db.get_user_channel(uid)
    ctx["cfg"] = cfg_of(st)
    res = await asyncio.to_thread(smart.parse, data, ctx)
    _ev(uid, "json_err" if res["errors"] else "json_ok", f"сцен {len(res.get('scenes') or [])}")
    if res["errors"]:
        lines = "\n".join(f"• {e}" for e in res["errors"][:10])
        return await message.answer(f"❌ В сценарии есть ошибки, ролик не собран:\n{lines}\n\n"
                                    "Отправьте этот список нейросети с просьбой исправить JSON и пришлите результат снова.")
    st["lists"]["smart_pending"] = res
    st["lists"]["smart_raw"] = data
    state.save(uid, st)
    await message.answer(smart.summary(res, cfg_of(st)),
                         reply_markup=_kb([("🚀 Собрать", "sm:go"), ("✖ Отмена", "sm:cancel")],
                                          [("🗣 Сделать текст живее", "sm:hum")],
                                          [("✍️ Промт для доработки", "sm:edit")], [("⚙️ Настройки", "sm:set")]))


def _json_of(raw: str) -> dict | None:
    t = (raw or "").lstrip()
    if not (t.startswith("{") or t.startswith("```")):
        return None
    try:
        data = scenario.extract_json(raw)
    except ValueError:
        return None
    return data if smart.is_smart(data) else None


async def handle_text(message: Message, uid: int, st: dict) -> bool:
    text = (message.text or "").strip()
    aw = st.get("awaiting")
    if aw in ("sm_topic", "sm_main", "sm_facts"):
        brief = st["lists"].setdefault("sm_brief", {})
        brief[{"sm_topic": "topic", "sm_main": "main", "sm_facts": "facts"}[aw]] = text[:600]
        state.save(uid, st)
        await _brief_next(message, uid, st)
        return True
    data = _json_of(text)
    if data is None:
        return False
    await review(message, uid, st, data)
    return True


async def handle_media(message: Message, bot: Bot, uid: int, st: dict) -> bool:
    doc = message.document
    if not (doc and ((doc.file_name or "").lower().endswith(".json") or doc.mime_type == "application/json") and (doc.file_size or 0) <= 512 * 1024):
        return False
    buf = BytesIO()
    await bot.download(doc, destination=buf)
    try:
        data = _json_of(buf.getvalue().decode("utf-8-sig"))
    except UnicodeDecodeError:
        data = None
    if data is None:
        return False
    await review(message, uid, st, data)
    return True
