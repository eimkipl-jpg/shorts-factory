"""Бот для креаторов: главное меню и разделы помимо умного монтажа (этап 1, патч 42).

Кнопки (cr:...):
  cr:home                         главное меню
  cr:dl, cr:met                   скачать видео / метрики по ссылке (бот ждёт ссылку)
  cr:cut                          нарезка: ждёт ссылку или файл; cr:cm|cl|cs|ct:<знач> - режим, длина, чувствительность, сколько
  cr:cgo                          запустить нарезку
  cr:u:<dl|cut|met>               что сделать с присланной ссылкой
  cr:pub, cr:ap                   публикация и метрики / автопубликация (в разработке)
  cr:guide, cr:gd:<имя>           инструкции-схемы
  cr:soon:<что>                   разделы следующих этапов
"""
import asyncio
import json
import logging
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config, creator, db
from core import fetch
from gateway import state
from gateway.ui import safe_answer, safe_edit

log = logging.getLogger("creatorui")
router = Router()

LENS = {"1-6": "1–6 с", "2-10": "2–10 с", "4-15": "4–15 с", "8-30": "8–30 с"}
SENS = {"low": "низкая", "mid": "средняя", "high": "высокая"}
MODES = {"cuts": "✂️ По переходам", "best": "⭐ Лучшие моменты"}
TOPS = (5, 8, 12, 20)
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")

SOON = {
}
_MET_LAST: dict[int, float] = {}


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows if r])


def _mark(on: bool) -> str:
    return "✅ " if on else ""


def tg_lim() -> str:
    return "до 2 ГБ" if config.TELEGRAM_API_URL else "до 20 МБ"


def limits_text() -> str:
    return (f"в сутки - до {config.DAILY_LIMIT} роликов, {config.REELS_DAILY} монтажей ReelsEditor, "
            f"{config.DL_DAILY} скачиваний и {config.CUT_DAILY} нарезок")


def menu_screen(uid: int):
    u = creator.get(uid)
    if creator.is_admin(uid):
        bal = "👑 Админ: без ограничений"
    elif config.FREE_MODE:
        bal = "🎁 Сейчас всё бесплатно: " + limits_text()
    else:
        bal = f"💰 Баланс: бесплатных {creator.free_left(u)}, кредитов {u['credits']}"
    text = ("🎬 Фабрика роликов для Shorts, Reels и TikTok\n\n"
            "🎞 Умный монтаж - ролик по сценарию: озвучка, субтитры, кадры под каждую фразу, карточки и карты\n"
            "🎬 ReelsEditor - монтаж вашего видео «в камеру»: без пауз и «э-э», с зумами, вставками и субтитрами\n"
            "📢 UGC-реклама - несколько вариантов рекламного ролика с разными хуками\n"
            "🔍 Разбор референса - чем цепляет чужой ролик и как сделать похожий\n"
            "✂️ Нарезка - длинное видео на фрагменты\n"
            "⬇️ Скачать - видео по ссылке\n"
            "📊 Метрики - цифры любого ролика по ссылке\n\n"
            f"{bal}\n\nНе знаете, с чего начать? Нажмите «📖 Инструкция».")
    rows = [
        [("🎞 Умный монтаж", "sm:home"), ("🎬 ReelsEditor", "cr:re")],
        [("✂️ Нарезка", "cr:cut"), ("⬇️ Скачать видео", "cr:dl")],
        [("📢 UGC-реклама", "cr:ugc"), ("🔍 Разбор референса", "cr:ref")],
        [("🏷 Бренд-профиль", "cr:br"), (f"📐 Формат {state.load(uid).get('format', '9:16')}", "cr:fmt")],
        [("⚙️ Настройки", "sm:set"), ("📊 Публикация и метрики", "cr:pub")],
        [("📖 Инструкция", "cr:guide")],
    ]
    if creator.is_admin(uid):
        rows.append([("👑 Админка: кто пользуется ботом", "adm:h")])
    if not config.FREE_MODE:
        rows.append([("💰 Баланс и покупка", "buy:menu")])
    return text, _kb(*rows)


def guide_screen():
    text = ("📖 Инструкция\nВыберите, что хотите сделать - пришлю схему по шагам.\n\n"
            "Если что-то пошло не так - кнопка «🆘 Поддержка» или команда /support.")
    return text, _kb([("🧭 Как работает бот", "cr:gd:overview")], [("🎞 Умный монтаж", "cr:gd:smart"), ("🎬 ReelsEditor", "cr:gd:reels")],
                     [("📢 UGC-реклама", "cr:gd:ugc"), ("🔍 Разбор референса", "cr:gd:ref")],
                     [("✂️ Нарезка", "cr:gd:cut"), ("⬇️ Скачать", "cr:gd:dl")], [("📊 Метрики", "cr:gd:metrics")],
                     [("🆘 Поддержка", "cr:sup"), ("⬅ Меню", "cr:home")])


def pub_screen():
    text = ("📊 Публикация и метрики\n\n"
            "📈 Метрики по ссылке - просмотры, лайки, комментарии и вовлечённость любого ролика (своего или конкурента).\n"
            "🚀 Автопубликация - бот будет сам выкладывать готовые ролики на площадки.")
    return text, _kb([("📈 Метрики по ссылке", "cr:met")], [("🚀 Автопубликация", "cr:ap")],
                     [("📖 Как это работает", "cr:gd:metrics")], [("⬅ Меню", "cr:home")])


def format_screen(st: dict):
    from core import formats
    cur, fit = formats.norm(st.get("format")), st.get("fit", "blur")
    text = ("📐 Формат ролика\n\n" + "\n".join(f"{'✅' if k == cur else '▫️'} {k} - {d}" for k, (_, _, d) in formats.FORMATS.items())
            + "\n\nReelsEditor монтирует сразу в выбранном формате. Умный монтаж рисует графику под 9:16, поэтому его ролик "
              "переводится в формат в конце - как вписать, выберите ниже (для 16:9 - всегда на размытом фоне).\n"
              "Нарезка и скачивание сохраняют исходный формат видео.")
    rows = [[(("✅ " if k == cur else "") + k, f"cr:fs:{k.replace(':', 'x')}") for k in formats.FORMATS]]
    rows += [[(("✅ " if k == fit else "") + v, f"cr:ff:{k}")] for k, v in formats.FITS.items()]
    rows.append([("⬅ Меню", "cr:home")])
    return text, _kb(*rows)


def brand_screen(st: dict):
    from core import brand
    b = brand.get(st)
    text = ("🏷 Бренд-профиль - подставляется в каждый ролик (умный монтаж и ReelsEditor)\n\n"
            f"Название: {b['name'] or '—'}\n"
            f"🎨 Цвет: {brand.COLORS[b['color']][0]} - подсветка слов в субтитрах и плашка призыва\n"
            f"🖼 Логотип: {'загружен, ' + brand.LOGO_POS[b['logo_pos']] if b['logo'] else 'нет'}\n"
            f"📣 Призыв в конце (последние 3 с): {b['cta'] or '—'}\n"
            f"#️⃣ Хэштеги к описанию: {b['tags'] or '—'}")
    rows = [[("✏️ Название", "cr:bn"), ("🎨 Цвет", "cr:bc")],
            [("🖼 Логотип", "cr:bl"), ("📍 Где логотип", "cr:bp")],
            [("📣 Призыв", "cr:bt"), ("#️⃣ Хэштеги", "cr:bh")],
            [("🗑 Сбросить профиль", "cr:bx"), ("⬅ Меню", "cr:home")]]
    return text, _kb(*rows)


def ugc_screen(st: dict):
    u = st["lists"].setdefault("ugc", {"n": 3, "lang": "ru"})
    text = ("📢 UGC-реклама\nРолик «как от обычного человека» о вашем товаре - в нескольких вариантах с разными хуками, "
            "чтобы проверить, какой зайдёт.\n\n"
            "1. Нажмите «Описать товар» и одним сообщением напишите: что это, для кого, главная польза, цена или акция.\n"
            "2. Бот пришлёт промт - отправьте его в ChatGPT, Claude или Gemini.\n"
            "3. Ответ (JSON с вариантами) пришлите сюда - бот соберёт все варианты.\n\n"
            "Призыв и название берутся из «🏷 Бренд-профиль». Нейросеть просим не выдумывать цифры и отзывы.")
    rows = [[(("✅ " if u["n"] == n else "") + f"{n} варианта" if n < 5 else ("✅ " if u["n"] == n else "") + f"{n} вариантов",
              f"cr:un:{n}") for n in (2, 3, 5)],
            [(("✅ " if u["lang"] == k else "") + v, f"cr:ul:{k}") for k, v in (("ru", "Русский"), ("en", "English"))],
            [("✍️ Описать товар", "cr:ub")], [("📖 Инструкция", "cr:gd:ugc"), ("⬅ Меню", "cr:home")]]
    return text, _kb(*rows)


def ref_screen():
    text = ("🔍 Разбор референса\nПришлите ролик, который нравится (свой или чужой): ссылку или файл ({tg_lim()}), до "
            f"{config.REF_MAX_MINUTES} минут.\n\nБот покажет: сколько планов и склеек, темп речи, какой хук в первые 3 секунды, "
            "есть ли призыв, раскадровку и текст. Потом - кнопка «Сделать похожий»: промт, который повторит структуру на вашей теме.")
    return text, _kb([("📖 Инструкция", "cr:gd:ref"), ("⬅ Меню", "cr:home")])


def _cut_cfg(st: dict) -> dict:
    c = st["lists"].setdefault("crcut", {})
    for k, v in {"src": None, "title": "", "mode": "cuts", "len": "2-10", "sens": "mid", "top": 8}.items():
        c.setdefault(k, v)
    return c


def cut_screen(st: dict):
    c = _cut_cfg(st)
    src = c["src"] or {}
    what = src.get("url") or (Path(src["file"]).name if src.get("file") else "")
    text = (f"✂️ Нарезка: {c['title'] or what}\n\n"
            f"Режим: {MODES[c['mode']]}\n"
            "• по переходам - каждая сцена между сменами кадра отдельным фрагментом; короче минимума отбрасываются, "
            "длиннее максимума делятся поровну\n"
            "• лучшие моменты - только самые громкие и динамичные куски\n\n"
            "Чувствительность: высокая - режет чаще (быстрый монтаж), низкая - только на явных сменах кадра.")
    rows = [[(_mark(c["mode"] == k) + n, f"cr:cm:{k}") for k, n in MODES.items()],
            [(_mark(c["len"] == k) + "⏱ " + n, f"cr:cl:{k}") for k, n in LENS.items()],
            [(_mark(c["sens"] == k) + n, f"cr:cs:{k}") for k, n in SENS.items()]]
    if c["mode"] == "best":
        rows.append([(_mark(c["top"] == n) + f"лучших {n}", f"cr:ct:{n}") for n in TOPS])
    rows += [[("▶ Нарезать", "cr:cgo")], [("🔁 Другое видео", "cr:cut"), ("⬅ Меню", "cr:home")]]
    return text, _kb(*rows)


def _reels_cfg(st: dict) -> dict:
    from core import reels
    return {**reels.DEFAULT, **(st.get("reels") or {})}


def reels_screen(st: dict):
    from core import reels
    cfg = _reels_cfg(st)
    src = st["lists"].get("reels_src") or {}
    what = src.get("url") or src.get("name") or ""
    lab = lambda k: dict(reels.OPTIONS[k][1]).get(cfg[k], str(cfg[k]))  # noqa: E731
    text = ("🎬 ReelsEditor - монтаж видео, где вы говорите в камеру\n"
            "Бот вырежет паузы, «э-э» и оговорки, поставит зумы на акцентах, вставки со стоков и динамические субтитры.\n\n"
            + (f"📹 Видео: {what}\n\n" if what else
               ((f"📤 Пришлите видео сюда: файлом ({tg_lim()}) или ссылкой " if config.TELEGRAM_API_URL else
                 "📤 Пришлите видео сюда: файлом (до 20 МБ - лучше «как видео», Telegram сам сожмёт) или ссылкой ")
                + f"на Google Диск, Яндекс Диск, YouTube. До {config.REELS_MAX_MINUTES} минут.\n\n"))
            + "\n".join(f"{reels.OPTIONS[k][0]}: {lab(k)}" for k in reels.OPTIONS)
            + "\n🔤 Субтитры и музыка - как в «⚙️ Настройки»")
    rows = [[(f"{reels.OPTIONS[k][0]}", f"cr:ro:{k}") for k in list(reels.OPTIONS)[i:i + 2]] for i in range(0, len(reels.OPTIONS), 2)]
    rows.append([("🔤 Субтитры", "sm:set:look"), ("🎵 Музыка", "sm:mm")])
    if what:
        rows.append([("▶ Смонтировать", "cr:rgo")])
        rows.append([("🔁 Другое видео", "cr:rnew")])
    rows.append([("📖 Инструкция", "cr:gd:reels"), ("⬅ Меню", "cr:home")])
    return text, _kb(*rows)


def reels_opt_screen(st: dict, key: str):
    from core import reels
    cfg = _reels_cfg(st)
    label, vals = reels.OPTIONS[key]
    rows = [[(_mark(cfg[key] == v) + n, f"cr:rv:{key}:{i}")] for i, (v, n) in enumerate(vals)]
    rows.append([("⬅ Назад", "cr:re")])
    return f"{label}\n\n💡 {reels.GUIDE.get(key, '')}", _kb(*rows)


async def _await(message: Message, uid: int, what: str, text: str) -> None:
    st = state.load(uid)
    st["awaiting"] = what
    state.save(uid, st)
    await message.answer(text, reply_markup=_kb([("✖ Отмена", "cr:home")]))


@router.callback_query(F.data.startswith("cr:"))
async def on_cr(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    act = parts[1]
    if act == "ap":
        return await safe_answer(cb, "🚧 Автопубликация в разработке. Скоро бот сможет сам выкладывать ролики "
                                     "в YouTube, TikTok и VK по расписанию.", show_alert=True)
    await safe_answer(cb)
    if act == "soon":
        what = SOON.get(parts[2] if len(parts) > 2 else "", "")
        return await safe_edit(cb.message, f"🚧 В разработке\n\n{what}\n\nПоявится в одном из ближайших обновлений.",
                               _kb([("⬅ Меню", "cr:home")]))
    st = state.load(uid)
    if act == "home":
        if st.get("awaiting", "") and str(st.get("awaiting")).startswith(("cr_", "sm_")):
            st["awaiting"] = None
            state.save(uid, st)
        return await safe_edit(cb.message, *menu_screen(uid))
    if act == "guide":
        return await safe_edit(cb.message, *guide_screen())
    if act == "sup":
        return await _await(cb.message, uid, "cr_support", "🆘 Напишите одним сообщением, что случилось - я передам администратору.")
    if act == "gd":
        name = parts[2] if len(parts) > 2 else "overview"
        try:
            from core import guide
            png = await asyncio.to_thread(guide.path, name)
        except Exception:  # noqa: BLE001
            log.exception("инструкция")
            return await cb.message.answer("Не удалось нарисовать схему, попробуйте позже.")
        return await cb.message.answer_photo(FSInputFile(png), reply_markup=_kb([("📖 Другие инструкции", "cr:guide"), ("⬅ Меню", "cr:home")]))
    if act == "pub":
        return await safe_edit(cb.message, *pub_screen())
    if act == "ugc":
        return await safe_edit(cb.message, *ugc_screen(st))
    if act in ("un", "ul") and len(parts) > 2:
        u = st["lists"].setdefault("ugc", {"n": 3, "lang": "ru"})
        if act == "un" and parts[2] in ("2", "3", "5"):
            u["n"] = int(parts[2])
        if act == "ul" and parts[2] in ("ru", "en"):
            u["lang"] = parts[2]
        state.save(uid, st)
        return await safe_edit(cb.message, *ugc_screen(st))
    if act == "ub":
        return await _await(cb.message, uid, "cr_ugc", "✍️ Опишите товар одним сообщением: что это, для кого, главная польза, "
                            "цена или акция. Например: «Термокружка 450 мл, держит тепло 8 часов, для тех, кто ездит на работу, "
                            "сейчас скидка 20%».")
    if act == "ug" and len(parts) > 2:
        return await _ugc_build(cb.message, uid, st, parts[2])
    if act == "ref":
        st["awaiting"] = "cr_ref"
        state.save(uid, st)
        return await safe_edit(cb.message, *ref_screen())
    if act == "rfp":
        if not st["lists"].get("ref_last"):
            return await cb.message.answer("Сначала сделайте разбор: «🔍 Разбор референса».")
        return await _await(cb.message, uid, "cr_ref_topic", "✍️ О чём ваш ролик? Напишите тему - бот даст промт, который повторит "
                            "структуру и темп референса на ней.")
    if act == "fmt":
        return await safe_edit(cb.message, *format_screen(st))
    if act == "fs" and len(parts) > 2:
        from core import formats
        f = parts[2].replace("x", ":")
        if f in formats.FORMATS:
            st["format"] = f
            state.save(uid, st)
        return await safe_edit(cb.message, *format_screen(st))
    if act == "ff" and len(parts) > 2:
        from core import formats
        if parts[2] in formats.FITS:
            st["fit"] = parts[2]
            state.save(uid, st)
        return await safe_edit(cb.message, *format_screen(st))
    if act.startswith("b") and act in ("br", "bn", "bc", "bcs", "bl", "bp", "bps", "bt", "bh", "bx"):
        return await _brand_cb(cb.message, uid, st, act, parts)
    if act in ("re", "rnew"):
        if act == "rnew":
            st["lists"].pop("reels_src", None)
        if not st["lists"].get("reels_src"):
            st["awaiting"] = "cr_reels"           # можно сразу присылать видео
        state.save(uid, st)
        return await safe_edit(cb.message, *reels_screen(st))
    if act == "ro" and len(parts) > 2:
        from core import reels
        if parts[2] in reels.OPTIONS:
            return await safe_edit(cb.message, *reels_opt_screen(st, parts[2]))
        return
    if act == "rv" and len(parts) > 3:
        from core import reels
        key = parts[2]
        if key in reels.OPTIONS:
            try:
                val = reels.OPTIONS[key][1][int(parts[3])][0]
            except (ValueError, IndexError):
                return
            st["reels"] = {**_reels_cfg(st), key: val}
            state.save(uid, st)
            return await safe_edit(cb.message, *reels_opt_screen(st, key))
        return
    if act == "rgo":
        return await _reels_go(cb.message, uid, st)
    if act == "dl":
        return await _await(cb.message, uid, "cr_dl", "⬇️ Пришлите ссылку на видео: YouTube, TikTok, Instagram, VK и другие.\n"
                            f"До {config.DL_MAX_MINUTES} минут; готовое видео придёт в чат (до 50 МБ, тяжелее - бот ужмёт).\n\n"
                            "Скачивайте только то, на что у вас есть права.")
    if act == "met":
        return await _await(cb.message, uid, "cr_met", "📈 Пришлите ссылку на ролик (YouTube, TikTok, Instagram, VK) - покажу его цифры.")
    if act == "cut":
        tg_limit = tg_lim()
        return await _await(cb.message, uid, "cr_cut", f"✂️ Пришлите видео для нарезки: ссылку или файл ({tg_limit}). "
                            f"Длина - до {config.CUT_MAX_MINUTES} минут.")
    if act == "u" and len(parts) > 2:
        url = st["lists"].get("cr_url")
        if not url:
            return await cb.message.answer("Ссылка устарела - пришлите её ещё раз.")
        return await _use_url(cb.message, uid, st, url, parts[2], bot)
    if act in ("cm", "cl", "cs", "ct") and len(parts) > 2:
        c = _cut_cfg(st)
        key = {"cm": "mode", "cl": "len", "cs": "sens", "ct": "top"}[act]
        val = int(parts[2]) if act == "ct" else parts[2]
        if (act == "cm" and val in MODES) or (act == "cl" and val in LENS) or (act == "cs" and val in SENS) or (act == "ct" and val in TOPS):
            c[key] = val
            state.save(uid, st)
        return await safe_edit(cb.message, *cut_screen(st))
    if act == "cgo":
        return await _cut_go(cb.message, uid, st)


# ---------- задачи ----------

def _queue_note(task_id: int) -> str:
    ahead = creator.queue_position(task_id)
    act = creator.activity_line()
    return (f"Вы {ahead + 1}-й в очереди." if ahead else "Вы первый в очереди.") + (f"\n{act}" if act else "")


async def _new_task(message: Message, uid: int, kind: str, payload: dict) -> int | None:
    okc, why = creator.check(uid, kind)
    if not okc:
        await message.answer("⛔ " + why)
        return None
    task_id = db.create_task("creator", kind, message.chat.id)
    task_dir = config.TASKS_DIR / f"task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "metadata.json").write_text(json.dumps({"task_id": task_id, "channel": "creator", "input_type": kind, kind: payload},
                                                       ensure_ascii=False, indent=2), "utf-8")
    creator.reserve(uid, task_id, kind)
    db.mark_ready(task_id)
    return task_id


async def _dl_go(message: Message, uid: int, url: str) -> None:
    if not fetch.available():
        return await message.answer("Скачивание пока не настроено на сервере (нет yt-dlp). Напишите администратору: /support")
    bad = await asyncio.to_thread(fetch.check_url, url)
    if bad:
        return await message.answer(f"❌ {bad}")
    tid = await _new_task(message, uid, "dl", {"url": url})
    if tid:
        await message.answer(f"⬇️ Скачиваю. {_queue_note(tid)} Видео придёт в этот чат.",
                             reply_markup=_kb([("⬇️ Скачать ещё", "cr:dl"), ("⬅ Меню", "cr:home")]))


async def _metrics(message: Message, uid: int, url: str) -> None:
    if not fetch.available():
        return await message.answer("Метрики пока не настроены на сервере (нет yt-dlp). Напишите администратору: /support")
    if time.time() - _MET_LAST.get(uid, 0) < 15 and not creator.is_admin(uid):
        return await message.answer("⏳ Метрики - раз в 15 секунд.")
    _MET_LAST[uid] = time.time()
    bad = await asyncio.to_thread(fetch.check_url, url)
    if bad:
        return await message.answer(f"❌ {bad}")
    note = await message.answer("📊 Смотрю цифры…")
    try:
        d = await asyncio.to_thread(fetch.info, url)
    except fetch.FetchError as e:
        return await note.edit_text(f"❌ Не получилось: {e}")
    except Exception:  # noqa: BLE001
        log.exception("метрики")
        return await note.edit_text("❌ Площадка не ответила, попробуйте позже.")
    await note.edit_text(fetch.metrics_text(d), reply_markup=_kb([("📈 Другой ролик", "cr:met"), ("⬅ Меню", "cr:home")]))
    from common import adminstats
    adminstats.event(uid, "metrics", (d.get("extractor") or d.get("extractor_key") or "")[:20] if isinstance(d, dict) else "")


async def _cut_go(message: Message, uid: int, st: dict) -> None:
    c = _cut_cfg(st)
    if not c["src"]:
        return await message.answer("Сначала пришлите видео.", reply_markup=_kb([("✂️ Нарезка", "cr:cut")]))
    lo, hi = (float(x) for x in c["len"].split("-"))
    payload = {**c["src"], "title": c["title"] or "video", "mode": c["mode"], "min": lo, "max": hi, "sens": c["sens"], "top": c["top"]}
    tid = await _new_task(message, uid, "cut", payload)
    if tid:
        await message.answer(f"✂️ Нарезаю: {MODES[c['mode']]}, {LENS[c['len']]}. {_queue_note(tid)}\n"
                             "Длинное видео анализируется несколько минут - фрагменты придут в чат.",
                             reply_markup=_kb([("⬅ Меню", "cr:home")]))


async def _brand_cb(message: Message, uid: int, st: dict, act: str, parts: list[str]) -> None:
    from core import brand
    b = brand.get(st)
    if act == "br":
        return await safe_edit(message, *brand_screen(st))
    if act == "bn":
        return await _await(message, uid, "cr_b_name", "✏️ Пришлите название бренда или канала (до 40 знаков).")
    if act == "bt":
        return await _await(message, uid, "cr_b_cta", "📣 Пришлите призыв для конца ролика (до 60 знаков), например: "
                            "«Подпишись - каждый день новое про деньги». Чтобы убрать призыв, пришлите «-».")
    if act == "bh":
        return await _await(message, uid, "cr_b_tags", "#️⃣ Пришлите хэштеги через пробел или запятую (до 15), например: "
                            "деньги финансы инвестиции. Чтобы убрать, пришлите «-».")
    if act == "bl":
        return await _await(message, uid, "cr_b_logo", "🖼 Пришлите логотип картинкой (PNG с прозрачным фоном выглядит лучше всего; "
                            "можно файлом, чтобы Telegram не сжал).")
    if act == "bc":
        rows = [[(("✅ " if b["color"] == k else "") + n, f"cr:bcs:{k}") for k, (n, _) in list(brand.COLORS.items())[i:i + 3]]
                for i in range(0, len(brand.COLORS), 3)]
        rows.append([("⬅ Назад", "cr:br")])
        return await safe_edit(message, "🎨 Цвет бренда: подсветка слов в субтитрах и плашка призыва.", _kb(*rows))
    if act == "bcs" and len(parts) > 2 and parts[2] in brand.COLORS:
        st["brand"] = {**b, "color": parts[2]}
        state.save(uid, st)
        return await safe_edit(message, *brand_screen(st))
    if act == "bp":
        rows = [[(("✅ " if b["logo_pos"] == k else "") + n, f"cr:bps:{k}")] for k, n in brand.LOGO_POS.items()]
        rows.append([("⬅ Назад", "cr:br")])
        return await safe_edit(message, "📍 Где показывать логотип. Нижние углы в Shorts и Reels закрыты кнопками - поэтому только верх.",
                               _kb(*rows))
    if act == "bps" and len(parts) > 2 and parts[2] in brand.LOGO_POS:
        st["brand"] = {**b, "logo_pos": parts[2]}
        state.save(uid, st)
        return await safe_edit(message, *brand_screen(st))
    if act == "bx":
        import shutil as _sh
        _sh.rmtree(brand.folder(uid), ignore_errors=True)
        st["brand"] = {}
        state.save(uid, st)
        return await safe_edit(message, *brand_screen(st))


async def _brand_text(message: Message, uid: int, st: dict, aw: str, text: str) -> None:
    from core import brand
    b = brand.get(st)
    val = "" if text.strip() == "-" else text.strip()
    if aw == "cr_b_name":
        b["name"] = val[:40]
    elif aw == "cr_b_cta":
        b["cta"] = val[:60]
    elif aw == "cr_b_tags":
        b["tags"] = brand.clean_tags(val)
    st["brand"] = b
    st["awaiting"] = None
    state.save(uid, st)
    t, kb = brand_screen(st)
    await message.answer(t, reply_markup=kb)


async def _brand_logo(message: Message, bot: Bot, uid: int, st: dict) -> bool:
    from core import brand
    obj = message.photo[-1] if message.photo else message.document
    if obj is None:
        return False
    if message.document and not ((message.document.mime_type or "").startswith("image/")):
        await message.answer("Нужна картинка (PNG или JPG).")
        return True
    if (obj.file_size or 0) > 5 * 1024 * 1024:
        await message.answer("Картинка больше 5 МБ - пришлите поменьше.")
        return True
    d = brand.folder(uid)
    d.mkdir(parents=True, exist_ok=True)
    raw = d / "upload.bin"
    await bot.download(obj, destination=raw)
    try:
        from PIL import Image
        im = Image.open(raw)
        im.load()
        im = im.convert("RGBA")
        im.thumbnail((800, 800))
        im.save(d / "logo.png", "PNG")
    except Exception:  # noqa: BLE001
        await message.answer("Не удалось прочитать картинку - пришлите PNG или JPG.")
        return True
    finally:
        raw.unlink(missing_ok=True)
    st["brand"] = {**brand.get(st), "logo": str((d / "logo.png").relative_to(config.ROOT))}
    st["awaiting"] = None
    state.save(uid, st)
    t, kb = brand_screen(st)
    await message.answer("✅ Логотип сохранён.\n\n" + t, reply_markup=kb)
    return True


async def _ctx(uid: int) -> dict:
    from core import scenario
    ch = db.get_user_channel(uid)
    ctx = await asyncio.to_thread(scenario.build_ctx, ch)
    ctx["channel"] = ch
    return ctx


async def _ugc_prompt(message: Message, uid: int, st: dict, product: str) -> None:
    from core import brand, refs, smart
    from gateway import smartui
    u = st["lists"].get("ugc") or {"n": 3, "lang": "ru"}
    b = brand.get(st)
    ctx = await _ctx(uid)
    prompt = smart.make_prompt(refs.ugc_brief(product, b), u["lang"], ctx, smartui.cfg_of(st)) + refs.ugc_block(u["n"], b, u["lang"])
    await smartui._send_long(message, prompt, "ugc_prompt.txt")
    await message.answer(f"Отправьте этот промт нейросети. Ответ - JSON с {u['n']} вариантами - пришлите сюда текстом или файлом .json.",
                         reply_markup=_kb([("📢 К UGC-рекламе", "cr:ugc")]))


async def ugc_review(message: Message, uid: int, st: dict, data: dict) -> None:
    from core import smart
    from gateway import smartui
    ctx = await _ctx(uid)
    ctx["cfg"] = smartui.cfg_of(st)
    ok, bad = [], []
    for k, v in enumerate(data.get("variants") or [], 1):
        if not isinstance(v, dict):
            bad.append(f"вариант {k}: не сценарий")
            continue
        res = await asyncio.to_thread(smart.parse, v, ctx)
        if res["errors"]:
            bad.append(f"вариант {k}: " + "; ".join(res["errors"][:3]))
        else:
            ok.append(res)
    if not ok:
        return await message.answer("❌ Ни один вариант не прошёл проверку:\n" + "\n".join(bad[:8]) +
                                    "\n\nОтправьте этот список нейросети с просьбой исправить JSON.")
    st["lists"]["ugc_pending"] = ok
    state.save(uid, st)
    lines = [f"📢 Вариантов готово к сборке: {len(ok)}"]
    for k, r in enumerate(ok, 1):
        hook = r["scenes"][0]["text"] if r["scenes"] else ""
        lines.append(f"\n{k}. «{r['meta']['title'][:60]}» - сцен {len(r['scenes'])}\n   🪝 {hook[:120]}")
    if bad:
        lines.append("\n⚠️ Пропущены: " + "; ".join(bad[:4]))
    lines.append(f"\nКаждый вариант - отдельный ролик (в сутки до {config.DAILY_LIMIT}).")
    rows = [[("🚀 Собрать все", "cr:ug:all")]] + [[(f"▶ Только вариант {k}", f"cr:ug:{k}") for k in range(1, len(ok) + 1)]]
    rows.append([("✖ Отмена", "cr:ug:x")])
    await message.answer("\n".join(lines)[:4000], reply_markup=_kb(*rows))


async def _ugc_build(message: Message, uid: int, st: dict, what: str) -> None:
    from gateway import smartui
    pending = st["lists"].get("ugc_pending") or []
    if what == "x":
        st["lists"].pop("ugc_pending", None)
        state.save(uid, st)
        return await message.answer("Варианты отменены.", reply_markup=_kb([("📢 К UGC-рекламе", "cr:ugc")]))
    if not pending:
        return await message.answer("Варианты не найдены - пришлите JSON заново.")
    pick = pending if what == "all" else ([pending[int(what) - 1]] if what.isdigit() and 0 < int(what) <= len(pending) else [])
    for res in pick:
        await smartui.create_task(message, res, smartui.cfg_of(st), uid)


async def _ref_go(message: Message, uid: int, payload: dict) -> None:
    tid = await _new_task(message, uid, "ref", payload)
    if tid:
        await message.answer(f"🔍 Разбираю. {_queue_note(tid)} Отчёт и раскадровка придут сюда через 1-3 минуты.",
                             reply_markup=_kb([("⬅ Меню", "cr:home")]))


async def _ref_prompt(message: Message, uid: int, st: dict, topic: str) -> None:
    from core import refs, smart
    from gateway import smartui
    a = st["lists"].get("ref_last")
    if not a:
        return await message.answer("Сначала сделайте разбор: «🔍 Разбор референса».")
    lang = a.get("lang") if a.get("lang") in ("ru", "en") else "ru"
    ctx = await _ctx(uid)
    await smartui._send_long(message, smart.make_prompt(topic, lang, ctx, smartui.cfg_of(st)) + refs.ref_block(a), "ref_prompt.txt")
    await message.answer("Отправьте промт нейросети, ответ (JSON) пришлите сюда - покажу сводку и соберу ролик.",
                         reply_markup=_kb([("⬅ Меню", "cr:home")]))


async def _reels_go(message: Message, uid: int, st: dict) -> None:
    from gateway import smartui
    src = st["lists"].get("reels_src")
    if not src:
        return await message.answer("Сначала пришлите видео.", reply_markup=_kb([("🎬 ReelsEditor", "cr:re")]))
    payload = {k: v for k, v in src.items() if k in ("url", "file")}
    from core import brand
    payload.update(cfg=_reels_cfg(st), scfg=smartui.cfg_of(st), format=st.get("format", "9:16"), brand=brand.get(st))
    tid = await _new_task(message, uid, "reels", payload)
    if tid:
        await message.answer(f"🎬 Монтирую. {_queue_note(tid)}\nМинута сырого видео обрабатывается 2-4 минуты - ролик придёт в чат.",
                             reply_markup=_kb([("⬅ Меню", "cr:home")]))


async def _use_url(message: Message, uid: int, st: dict, url: str, what: str, bot: Bot) -> None:
    if what == "dl":
        return await _dl_go(message, uid, url)
    if what == "met":
        return await _metrics(message, uid, url)
    if what == "ref":
        bad = await asyncio.to_thread(fetch.check_url, url)
        if bad:
            return await message.answer(f"❌ {bad}")
        return await _ref_go(message, uid, {"url": url})
    if what == "reels":
        bad = await asyncio.to_thread(fetch.check_url, url)
        if bad:
            return await message.answer(f"❌ {bad}")
        st["lists"]["reels_src"] = {"url": url, "name": url[:60]}
        st["awaiting"] = None
        state.save(uid, st)
        text, kb = reels_screen(st)
        return await message.answer(text, reply_markup=kb)
    if what == "cut":
        bad = await asyncio.to_thread(fetch.check_url, url)
        if bad:
            return await message.answer(f"❌ {bad}")
        c = _cut_cfg(st)
        c["src"], c["title"] = {"url": url}, ""
        st["awaiting"] = None
        state.save(uid, st)
        text, kb = cut_screen(st)
        return await message.answer(text, reply_markup=kb)


def _variants_json(raw: str) -> dict | None:
    """Ответ нейросети для UGC: {"variants": [...]}."""
    t = (raw or "").lstrip()
    if not (t.startswith("{") or t.startswith("```")) or '"variants"' not in t:
        return None
    from core import scenario
    try:
        data = scenario.extract_json(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("variants"), list) else None


async def handle_text(message: Message, uid: int, st: dict, bot: Bot) -> bool:
    """Ссылки для скачивания, нарезки и метрик. Ссылка без выбранного раздела - спросим, что с ней сделать."""
    text = (message.text or "").strip()
    aw = st.get("awaiting") or ""
    if aw in ("cr_b_name", "cr_b_cta", "cr_b_tags"):
        await _brand_text(message, uid, st, aw, text)
        return True
    if aw in ("cr_ugc", "cr_ref_topic"):
        st["awaiting"] = None
        state.save(uid, st)
        if len(text) < 5:
            await message.answer("Слишком коротко - опишите подробнее.")
            return True
        await (_ugc_prompt(message, uid, st, text) if aw == "cr_ugc" else _ref_prompt(message, uid, st, text))
        return True
    data = _variants_json(text)
    if data is not None:
        await ugc_review(message, uid, st, data)
        return True
    url = fetch.find_url(text)
    if aw in ("cr_dl", "cr_met", "cr_cut", "cr_reels", "cr_ref"):
        if not url:
            await message.answer("Жду ссылку вида https://… Или нажмите «Отмена».", reply_markup=_kb([("✖ Отмена", "cr:home")]))
            return True
        st["awaiting"] = None
        state.save(uid, st)
        await _use_url(message, uid, st, url, {"cr_dl": "dl", "cr_met": "met", "cr_cut": "cut", "cr_reels": "reels", "cr_ref": "ref"}[aw], bot)
        return True
    if url and not aw and not text.lstrip().startswith("{"):
        st["lists"]["cr_url"] = url
        state.save(uid, st)
        await message.answer("Что сделать с этой ссылкой?", reply_markup=_kb([("⬇️ Скачать", "cr:u:dl"), ("✂️ Нарезать", "cr:u:cut")],
                                                                          [("🎬 ReelsEditor", "cr:u:reels"), ("🔍 Разбор", "cr:u:ref")],
                                                                          [("📈 Метрики", "cr:u:met")]))
        return True
    return False


async def handle_media(message: Message, bot: Bot, uid: int, st: dict) -> bool:
    """Видео файлом: для нарезки или ReelsEditor. Видео без выбранного раздела - спросим, что с ним сделать."""
    aw = st.get("awaiting")
    if aw == "cr_b_logo":
        return await _brand_logo(message, bot, uid, st)
    doc = message.document
    if doc and (doc.file_name or "").lower().endswith(".json") and (doc.file_size or 0) <= 512 * 1024:
        from io import BytesIO
        buf = BytesIO()
        await bot.download(doc, destination=buf)
        try:
            data = _variants_json(buf.getvalue().decode("utf-8-sig"))
        except UnicodeDecodeError:
            data = None
        if data is not None:
            await ugc_review(message, uid, st, data)
            return True
        return False
    if message.photo:
        return False
    if aw not in ("cr_cut", "cr_reels", "cr_ref"):
        if message.video:
            await message.answer("Что сделать с этим видео?", reply_markup=_kb([("🎬 ReelsEditor", "cr:re"), ("✂️ Нарезка", "cr:cut")]))
            return True
        return False
    v = message.video or message.document
    if v is None:
        return False
    name = (getattr(v, "file_name", None) or "video.mp4").lower()
    mime = getattr(v, "mime_type", "") or ""
    if message.document and not (mime.startswith("video/") or name.endswith(VIDEO_EXT)):
        await message.answer("Это не видео. Пришлите видеофайл или ссылку.")
        return True
    limit = 2000 if config.TELEGRAM_API_URL else 20
    if (v.file_size or 0) > limit * 1024 * 1024:
        await message.answer(f"Файл больше {limit} МБ - Telegram не даст боту его скачать. Пришлите ссылку на видео "
                             "(YouTube, VK, облако) или файл поменьше.")
        return True
    folder = config.ROOT / "uploads" / str(uid)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*"):            # храним только последнее видео человека
        old.unlink(missing_ok=True)
    ext = Path(name).suffix if Path(name).suffix in VIDEO_EXT else ".mp4"
    dest = folder / f"src_{int(time.time())}{ext}"
    note = await message.answer("⏳ Загружаю видео…" + (" Большой файл - это может занять несколько минут." if (v.file_size or 0) > 100 * 2**20 else ""))
    try:
        if config.TELEGRAM_API_URL:     # свой сервер Bot API сначала сам скачивает файл у Telegram - ждём дольше
            f = await bot.get_file(v.file_id, request_timeout=1800)
            await bot.download_file(f.file_path, destination=dest, timeout=1800)
        else:
            await bot.download(v, destination=dest)
    except Exception as e:  # noqa: BLE001
        log.warning("загрузка видео: %s", e)
        await note.edit_text("❌ Не удалось получить файл от Telegram. Пришлите ещё раз или ссылкой.")
        return True
    st["awaiting"] = None
    await note.delete()
    if aw == "cr_ref":
        state.save(uid, st)
        await _ref_go(message, uid, {"file": str(dest.relative_to(config.ROOT))})
        return True
    if aw == "cr_reels":
        st["lists"]["reels_src"] = {"file": str(dest.relative_to(config.ROOT)), "name": Path(name).name[:40]}
        state.save(uid, st)
        text, kb = reels_screen(st)
        await message.answer(text, reply_markup=kb)
        return True
    c = _cut_cfg(st)
    c["src"], c["title"] = {"file": str(dest.relative_to(config.ROOT))}, Path(name).stem[:40]
    state.save(uid, st)
    text, kb = cut_screen(st)
    await message.answer(text, reply_markup=kb)
    return True
