"""Нарезчик видео в боте: выбрать своё видео -> режим, длина, чувствительность -> задача для воркера.

Кнопки: cu:start (список видео), cu:f:<n> (выбор), cu:pg:<n>, cu:mode:<cuts|best>, cu:len:<мин>-<макс>,
cu:sens:<low|mid|high>, cu:top:<n>, cu:theme (ввод названия темы текстом), cu:go (запуск), cu:list.
Сама нарезка идёт в воркере (core.cutter), по окончании приходит сообщение.
"""
import asyncio
import json
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config, db, library
from core import cutter, sources
from gateway import state
from gateway.ui import safe_answer, safe_edit

log = logging.getLogger("cutui")
router = Router()

PAGE = 8
LENS = {"1-6": "1–6 с", "2-10": "2–10 с", "4-15": "4–15 с", "8-30": "8–30 с"}
SENS = {"low": "низкая", "mid": "средняя", "high": "высокая"}
MODES = {"cuts": "✂️ По переходам", "best": "⭐ Интересные моменты"}
TOPS = (5, 8, 12, 20)


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _ok(uid: int) -> bool:
    return uid in config.ALLOWED_USER_IDS


def _mark(cond: bool) -> str:
    return "✅ " if cond else ""


def _default() -> dict:
    return {"file": None, "title": "", "mode": "cuts", "len": "2-10", "sens": "mid", "top": 8, "theme": None, "page": 0, "files": []}


def _cfg(st: dict) -> dict:
    cfg = st["lists"].setdefault("cut", _default())
    for k, val in _default().items():
        cfg.setdefault(k, val)
    return cfg


def _own_rows() -> list[dict]:
    rows = [a for a in library.search("own", limit=100000) if (config.ROOT / a["path"]).exists()]
    rows.sort(key=lambda a: (a["title"] or Path(a["path"]).name).lower())
    return rows


def _list_screen(st: dict):
    cfg = _cfg(st)
    rows = _own_rows()
    cfg["files"] = [a["path"] for a in rows]
    pages = max((len(rows) - 1) // PAGE + 1, 1)
    page = min(cfg["page"], pages - 1)
    cfg["page"] = page
    text = "✂️ Нарезка видео: выберите ролик из «Мои видео», который нужно порезать на фрагменты."
    if not rows:
        text += "\nВ «Моих видео» пока пусто: добавьте видео по ссылке (📚 Библиотека → ➕ Видео по ссылке)."
    kb = []
    for i, a in enumerate(rows[page * PAGE:(page + 1) * PAGE]):
        dur = f" · {a['duration'] / 60:.1f} мин" if a.get("duration") and a["duration"] >= 90 else (f" · {a['duration']:.0f} с" if a.get("duration") else "")
        kb.append([_b(((a["title"] or Path(a["path"]).name)[:32]) + dur, f"cu:f:{page * PAGE + i}")])
    if pages > 1:
        kb.append([_b("◀", f"cu:pg:{(page - 1) % pages}"), _b(f"{page + 1}/{pages}", f"cu:pg:{page}"), _b("▶", f"cu:pg:{(page + 1) % pages}")])
    kb.append([_b("⬅ Библиотека", "m:lib")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


def _settings_screen(st: dict):
    cfg = _cfg(st)
    a = library.by_rel(cfg["file"])
    dur = (a or {}).get("duration")
    text = (f"✂️ Нарезка: {cfg['title'] or Path(cfg['file']).name}"
            + (f" · {dur / 60:.1f} мин" if dur else "") + "\n\n"
            f"Режим: {MODES[cfg['mode']]}\n"
            "• по переходам: каждая сцена между сменами кадра отдельным клипом; короче минимума отбрасываются, длиннее максимума делятся поровну\n"
            "• интересные моменты: только лучшие (громкий звук + движение)\n\n"
            f"Результат сохранится в библиотеку клипов, тема «{cfg['theme']}», и попадёт на Диск.")
    kb = [
        [_b(_mark(cfg["mode"] == k) + n, f"cu:mode:{k}") for k, n in MODES.items()],
        [_b(_mark(cfg["len"] == k) + "⏱ " + n, f"cu:len:{k}") for k, n in LENS.items()],
        [_b(_mark(cfg["sens"] == k) + f"чувствит. {n}", f"cu:sens:{k}") for k, n in SENS.items()],
    ]
    if cfg["mode"] == "best":
        kb.append([_b(_mark(cfg["top"] == n) + f"лучших: {n}", f"cu:top:{n}") for n in TOPS])
    kb.append([_b(f"🏷 Тема: {cfg['theme']}", "cu:theme")])
    kb.append([_b("👁 Проверить переходы", "cu:prev")])
    kb.append([_b("▶ Нарезать", "cu:go")])
    kb.append([_b("⬅ К списку", "cu:list")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def _show_list(message: Message, uid: int, edit: bool = True) -> None:
    await asyncio.to_thread(library.scan)
    st = state.load(uid)
    text, kb = _list_screen(st)
    state.save(uid, st)
    await (safe_edit(message, text, kb) if edit else message.answer(text, reply_markup=kb))


@router.message(Command("cut"))
async def cmd_cut(message: Message):
    if message.from_user and _ok(message.from_user.id):
        await _show_list(message, message.from_user.id, edit=False)


@router.callback_query(F.data.startswith("cu:"))
async def on_cu(cb: CallbackQuery):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    parts = cb.data.split(":")
    act = parts[1]
    await safe_answer(cb)
    if act in ("start", "list"):
        st = state.load(uid)
        st["awaiting"] = None
        state.save(uid, st)
        return await _show_list(cb.message, uid)
    st = state.load(uid)
    st["awaiting"] = None
    cfg = _cfg(st)
    if act == "pg":
        cfg["page"] = int(parts[2])
        text, kb = _list_screen(st)
        state.save(uid, st)
        return await safe_edit(cb.message, text, kb)
    if act == "f":
        i = int(parts[2])
        if not (0 <= i < len(cfg["files"])):
            return await cb.message.answer("Список устарел, откройте нарезку заново: /cut")
        a = library.by_rel(cfg["files"][i])
        cfg.update(file=cfg["files"][i], title=(a or {}).get("title") or "", mode="cuts")
        cfg["theme"] = sources.slug(cfg["title"] or Path(cfg["file"]).stem)
    elif act == "mode":
        cfg["mode"] = parts[2]
    elif act == "len":
        cfg["len"] = parts[2]
    elif act == "sens":
        cfg["sens"] = parts[2]
    elif act == "top":
        cfg["top"] = int(parts[2])
    elif act == "theme":
        st["awaiting"] = "cut_theme"
        state.save(uid, st)
        return await cb.message.answer("Пришлите название темы для нарезанных клипов (например: мемы, кот, город). "
                                       "Так они будут называться в библиотеке.")
    elif act == "go":
        return await _go(cb.message, uid, st, cfg)
    elif act == "prev":
        return await _preview(cb.message, cfg)
    if not cfg["file"]:
        return await _show_list(cb.message, uid)
    state.save(uid, st)
    text, kb = _settings_screen(st)
    await safe_edit(cb.message, text, kb)


async def _preview(message: Message, cfg: dict) -> None:
    """Картинка с кадрами до/после каждого найденного перехода: видно, где бот видит границы сцен."""
    video = config.ROOT / cfg["file"]
    if not cfg["file"] or not video.exists():
        return await message.answer("Исходное видео не найдено на сервере. Откройте /cut и выберите заново.")
    note = await message.answer("⏳ Ищу переходы, это может занять минуту-две…")
    out = config.TMP_DIR / f"cut_preview_{message.chat.id}.jpg"
    try:
        cuts = await asyncio.to_thread(cutter.detect_cuts, video, cutter.SENS.get(cfg["sens"], 0.30))
        cuts = cutter.effective_cuts(cuts, video_len(video))
        if not cuts:
            return await note.edit_text("Переходов не найдено. Попробуйте чувствительность «высокая».")
        shown = cuts[:24]
        await asyncio.to_thread(cutter.contact_sheet, video, shown, out, 24)
        times = ", ".join(f"{t:.1f}" for t in shown)
        await message.answer_photo(FSInputFile(out), caption=(
            f"Найдено переходов: {len(cuts)} (чувствительность «{SENS[cfg['sens']]}»). Каждая пара кадров: слева до перехода, справа после.\n"
            f"Секунды: {times}\n"
            "Если пара похожа на один и тот же кадр, это лишний переход: снизьте чувствительность. "
            "Если переход есть, а пары для него нет: повысьте."))
        await note.delete()
    except Exception as e:  # noqa: BLE001
        log.exception("preview failed")
        await note.edit_text(f"Не получилось проверить: {str(e)[:300]}")
    finally:
        out.unlink(missing_ok=True)


def video_len(video: Path) -> float:
    from common.media import video_duration
    return video_duration(video) or 1e9


async def _go(message: Message, uid: int, st: dict, cfg: dict) -> None:
    if not cfg["file"] or not (config.ROOT / cfg["file"]).exists():
        return await message.answer("Исходное видео не найдено на сервере. Откройте /cut и выберите заново.")
    lo, hi = (float(x) for x in cfg["len"].split("-"))
    channel = db.get_user_channel(uid)
    task_id = db.create_task(channel, "cut", message.chat.id)
    task_dir = config.TASKS_DIR / f"task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)
    data = {"task_id": task_id, "channel": channel, "input_type": "cut",
            "cut": {"file": cfg["file"], "title": cfg["title"], "theme": cfg["theme"], "mode": cfg["mode"],
                    "min": lo, "max": hi, "sens": cfg["sens"], "top": cfg["top"]}}
    (task_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    db.mark_ready(task_id)
    await message.answer(
        f"Принято: task_{task_id}, нарезка «{cfg['title'] or Path(cfg['file']).name}» ({MODES[cfg['mode']]}). "
        f"В очереди: {db.queue_size()}.\nДлинное видео анализируется несколько минут. Когда закончу, пришлю сообщение; "
        "ход можно смотреть в 📥 Очереди."
    )


async def handle_text(message: Message, uid: int, st: dict) -> bool:
    if st.get("awaiting") != "cut_theme":
        return False
    cfg = _cfg(st)
    name = sources.slug(message.text or "")
    st["awaiting"] = None
    if not cfg["file"]:
        state.save(uid, st)
        await message.answer("Сначала выберите видео: /cut")
        return True
    cfg["theme"] = name
    state.save(uid, st)
    text, kb = _settings_screen(st)
    await message.answer(text, reply_markup=kb)
    return True
