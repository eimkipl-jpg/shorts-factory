"""Банк идей в боте: идеи по каналам -> «Взять в работу» -> проект с видео -> промт для нейросети -> JSON -> ролик.

Кнопки (callback_data начинается с id:):
  id:home  каналы и «➕ Создать канал»           id:nc  создать канал (дальше бот ждёт название)
  id:ch:<i>  экран канала (идеи подтягиваются с Диска)    id:ls:<new|work|done>:<стр>  списки
  id:i:<n>  карточка идеи   id:take:<n>  взять в работу   id:del:<n> / id:delok:<n>  удалить   id:ret:<n>  вернуть в работу
  id:new  новая идея (бот ждёт текст)        id:p:<n>  открыть проект    id:pm  экран проекта    id:pr  обновить с Диска
  id:url  скачать видео по ссылке            id:up  принимать файлы      id:lb / id:lbs:<k> / id:lbp:<стр> / id:lbc:<n>  из библиотеки
  id:pt  тон -> id:ptn:<тон>  промт           id:idea  текст идеи         id:json  как вернуть JSON
  id:fin / id:finok  идея готова              id:close  закрыть проект
Команды: /ideas (открыть банк), /idea <текст> (добавить идею в текущий канал).
Пока проект открыт, присланный JSON (текстом или файлом) разбирается с файлами этого проекта.
"""
import asyncio
import logging
from io import BytesIO
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config, db
from core import ideas, mix, scenario, voicestyle
from gateway import scenario_ui, state
from gateway.ui import safe_answer, safe_edit

log = logging.getLogger("ideasui")
router = Router()

PAGE = 8


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ok(uid: int) -> bool:
    return uid in config.ALLOWED_USER_IDS


def _ctx(st: dict) -> dict:
    c = st["lists"].get("id")
    if not isinstance(c, dict):
        c = st["lists"]["id"] = {"ch": None, "status": "new", "names": [], "page": 0}
    return c


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _drive_note(ok: bool) -> str:
    return "" if ok else "\n⚠️ На Диск не загрузилось: работаю по локальным файлам, при следующей синхронизации они могут пропасть."


# ---------- экраны ----------

def home_screen():
    rows = []
    for i, ch in enumerate(config.CHANNELS):
        rows.append([_b(f"📺 {ch}", f"id:ch:{i}")])
    rows.append([_b("➕ Создать канал", "id:nc")])
    rows.append([_b("⬅ Меню", "m:back")])
    text = ("💡 Банк идей\nВыберите канал. Идеи — это txt-файлы в папке _ideas/<канал> на Google Диске "
            "(или команда /idea текст). Каналов: %d." % len(config.CHANNELS))
    return text, _kb(rows)


def channel_screen(st: dict, ch: str):
    c = _ctx(st)
    c["ch"] = ch
    new, work, done = (ideas.list_ideas(ch, s) for s in ("new", "work", "done"))
    text = (f"💡 Канал «{ch}»\nНовых идей: {len(new)} · в работе: {len(work)} · готово: {len(done)}\n"
            f"Папка на Диске: _ideas/{ch}\nВыберите идею, чтобы прочитать и взять в работу.")
    return text, _list_kb(st, ch, "new", 0, new, len(work), len(done))


def _list_kb(st: dict, ch: str, status: str, page: int, items: list[dict], n_work: int = 0, n_done: int = 0):
    c = _ctx(st)
    c.update(ch=ch, status=status, names=[i["name"] for i in items], page=page)
    pages = max((len(items) - 1) // PAGE + 1, 1)
    page = min(page, pages - 1)
    rows = []
    cb = "id:p:" if status == "work" else "id:i:"
    for k, it in enumerate(items[page * PAGE:(page + 1) * PAGE]):
        rows.append([_b(("💡 " if status == "new" else "📂 " if status == "work" else "✅ ") + _short(it["title"], 44),
                        f"{cb}{page * PAGE + k}")])
    if pages > 1:
        rows.append([_b("◀", f"id:ls:{status}:{(page - 1) % pages}"), _b(f"{page + 1}/{pages}", f"id:ls:{status}:{page}"),
                     _b("▶", f"id:ls:{status}:{(page + 1) % pages}")])
    if status == "new":
        rows.append([_b("➕ Новая идея", "id:new")])
        rows.append([_b(f"📂 В работе ({n_work})", "id:ls:work:0"), _b(f"✅ Готово ({n_done})", "id:ls:done:0")])
        rows.append([_b("🔄 Обновить с Диска", f"id:ch:{config.CHANNELS.index(ch)}"), _b("⬅ Каналы", "id:home")])
    else:
        rows.append([_b("⬅ К идеям", f"id:ch:{config.CHANNELS.index(ch)}")])
    return _kb(rows)


def list_screen(st: dict, ch: str, status: str, page: int = 0):
    items = ideas.list_ideas(ch, status)
    title = {"new": "Новые идеи", "work": "📂 В работе", "done": "✅ Готово"}[status]
    text = f"{title} · канал «{ch}»" + ("" if items else "\nПока пусто.")
    return text, _list_kb(st, ch, status, page, items,
                          len(ideas.list_ideas(ch, "work")), len(ideas.list_ideas(ch, "done")))


def idea_screen(idea: dict, idx: int, ch: str):
    text = f"💡 {idea['title']}\n\n{idea['text'].strip()[:3300]}"
    if idea["status"] == "done":
        rows = [[_b("↩️ Вернуть в работу", f"id:ret:{idx}")]]
    else:
        rows = [[_b("▶️ Взять в работу", f"id:take:{idx}")]]
    rows.append([_b("🗑 Удалить", f"id:del:{idx}"), _b("⬅ Назад", f"id:ls:{idea['status']}:0")])
    return text, _kb(rows)


def project_screen(st: dict, ch: str, name: str):
    files = ideas.project_files(ch, name)
    idea = ideas.read_idea(ch, "work", name)
    total = sum(f["duration"] for f in files)
    lines = [f"📂 Проект «{idea['title'] if idea else name}» · канал «{ch}»"]
    if files:
        lines.append(f"Видео в проекте: {len(files)} · ≈ {total:.0f} с")
        for i, f in enumerate(files[:15], 1):
            lines.append(f"  {i}. {_short(f['name'], 40)} · {f['duration']:.0f} с")
        if len(files) > 15:
            lines.append(f"  …и ещё {len(files) - 15}")
    else:
        lines.append("Видео пока нет. Добавьте: ссылкой, файлом в чат, из библиотеки или положите в папку проекта на Диске.")
    lines.append(f"Папка на Диске: _projects/{ch}/{name}")
    lines.append("\nДальше: добавьте видео → «📋 Промт для нейросети» → пришлите сюда готовый JSON (текстом или файлом).")
    rows = [
        [_b("🔗 Скачать по ссылке", "id:url"), _b("📤 Прислать файлы", "id:up")],
        [_b("📥 Из библиотеки", "id:lb"), _b("🔄 С Диска", "id:pr")],
        [_b("📋 Промт для нейросети", "id:pt")],
        [_b("🧾 Текст идеи", "id:idea"), _b("📜 Как вернуть JSON", "id:json")],
        [_b("✅ Идея готова", "id:fin"), _b("⬅ К идеям", "id:close")],
    ]
    return "\n".join(lines), _kb(rows)


def _proj(st: dict) -> tuple[str, str] | None:
    p = st.get("proj")
    if isinstance(p, dict) and p.get("ch") in config.CHANNELS and p.get("name"):
        return p["ch"], p["name"]
    return None


async def _show(cb_or_msg, text: str, kb) -> None:
    if isinstance(cb_or_msg, CallbackQuery):
        await safe_edit(cb_or_msg.message, text, kb)
    else:
        await cb_or_msg.answer(text, reply_markup=kb)


async def _show_project(target, st: dict, uid: int, pull: bool = False) -> None:
    p = _proj(st)
    if not p:
        return await _show(target, *home_screen())
    note = ""
    if pull:
        if isinstance(target, CallbackQuery):
            await _show(target, "⏳ Обновляю проект с Диска… Большие видео скачиваются дольше, подождите.", None)
        ok, msg = await asyncio.to_thread(ideas.pull_project_ex, *p)
        if not ok:
            note = (f"\n\n⚠️ С Диска загрузилось не всё: {msg.strip()[-160:] or 'нет ответа от rclone'}. "
                    "Нажмите «🔄 С Диска» ещё раз (докачает остальное) или проверьте rclone на сервере.")
    text, kb = await asyncio.to_thread(project_screen, st, *p)
    await _show(target, text + note, kb)


# ---------- команды ----------

@router.message(Command("ideas"))
async def cmd_ideas(message: Message):
    if message.from_user and _ok(message.from_user.id):
        text, kb = home_screen()
        await message.answer(text, reply_markup=kb)


@router.message(Command("idea"))
async def cmd_idea(message: Message):
    if not (message.from_user and _ok(message.from_user.id)):
        return
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        return await message.answer(f"/idea <текст идеи> — добавит идею в текущий канал. Первая строка — название.\n\n{ideas.TEMPLATE_HINT}",
                                    reply_markup=_kb([[_b("💡 Открыть банк идей", "id:home")]]))
    ch = db.get_user_channel(message.from_user.id)
    try:
        res = await asyncio.to_thread(ideas.add_idea, ch, text)
    except ValueError as e:
        return await message.answer(str(e))
    await message.answer(f"💡 Идея сохранена в канал «{ch}»: {res['title']}" + _drive_note(res["drive_ok"]),
                         reply_markup=_kb([[_b("Открыть банк идей", "id:home")]]))


# ---------- кнопки ----------

@router.callback_query(F.data.startswith("id:"))
async def on_id(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    parts = cb.data.split(":")
    act = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    st = state.load(uid)
    st.setdefault("proj", None)
    c = _ctx(st)
    await safe_answer(cb)
    try:
        if act == "home":
            st["proj"], st["awaiting"] = None, None
            state.save(uid, st)
            return await _show(cb, *home_screen())
        if act == "nc":
            st["awaiting"] = "chan_new"
            state.save(uid, st)
            return await cb.message.answer("📺 Пришлите название нового канала (2-30 символов: буквы, цифры, «_», «-»), например MemesOfRakhim.\n"
                                           "Для него создадутся папки идей и проектов на сервере и на Диске.")
        if act == "ch":
            ch = config.CHANNELS[int(arg)]
            st["proj"], st["awaiting"] = None, None
            await asyncio.to_thread(ideas.pull_ideas, ch)
            text, kb = await asyncio.to_thread(channel_screen, st, ch)
            state.save(uid, st)
            return await _show(cb, text, kb)
        ch = c.get("ch")
        if act == "ls":
            status, page = arg, int(parts[3]) if len(parts) > 3 else 0
            if not ch:
                return await _show(cb, *home_screen())
            text, kb = await asyncio.to_thread(list_screen, st, ch, status, page)
            state.save(uid, st)
            return await _show(cb, text, kb)
        if act in ("i", "take", "del", "delok", "ret", "p"):
            names = c.get("names") or []
            idx = int(arg)
            if not ch or not (0 <= idx < len(names)):
                return await cb.message.answer("Список устарел, откройте канал заново.", reply_markup=_kb([[_b("💡 Банк идей", "id:home")]]))
            name, status = names[idx], c.get("status", "new")
            if act == "i":
                idea = ideas.read_idea(ch, status, name)
                if not idea:
                    return await cb.message.answer("Идея не найдена, обновите список.")
                return await _show(cb, *idea_screen(idea, idx, ch))
            if act == "del":
                return await _show(cb, f"Удалить идею «{name}» навсегда (и с Диска)?",
                                   _kb([[_b("🗑 Да, удалить", f"id:delok:{idx}"), _b("Отмена", f"id:i:{idx}")]]))
            if act == "delok":
                await asyncio.to_thread(ideas.delete_idea, ch, status, name)
                text, kb = await asyncio.to_thread(list_screen, st, ch, status, 0)
                state.save(uid, st)
                return await _show(cb, "🗑 Удалено.\n\n" + text, kb)
            if act in ("take", "ret"):
                res = await asyncio.to_thread(ideas.take, ch, name)
                st["proj"] = {"ch": ch, "name": res["name"]}
                st["awaiting"] = None
                state.save(uid, st)
                await cb.message.answer(f"▶️ Идея «{res['title']}» в работе. Папка проекта: _projects/{ch}/{res['name']}" + _drive_note(res["drive_ok"]))
                return await _show_project(cb, st, uid)
            if act == "p":
                st["proj"] = {"ch": ch, "name": name}
                st["awaiting"] = None
                state.save(uid, st)
                return await _show_project(cb, st, uid, pull=True)
        if act == "new":
            ch = ch or db.get_user_channel(uid)
            c["ch"] = ch
            st["awaiting"] = "idea_new"
            state.save(uid, st)
            return await cb.message.answer(f"💡 Пришлите текст идеи для канала «{ch}» одним сообщением.\n\n{ideas.TEMPLATE_HINT}")

        # ---- всё ниже относится к открытому проекту ----
        p = _proj(st)
        if not p:
            return await cb.message.answer("Проект не открыт. Откройте идею в работе.", reply_markup=_kb([[_b("💡 Банк идей", "id:home")]]))
        if act == "pm":
            st["awaiting"] = None
            state.save(uid, st)
            return await _show_project(cb, st, uid)
        if act == "pr":
            return await _show_project(cb, st, uid, pull=True)
        if act == "close":
            st["proj"], st["awaiting"] = None, None
            state.save(uid, st)
            text, kb = await asyncio.to_thread(list_screen, st, p[0], "work", 0)
            return await _show(cb, text, kb)
        if act == "url":
            st["awaiting"] = "proj_url"
            state.save(uid, st)
            return await cb.message.answer("🔗 Пришлите ссылку на видео (YouTube и др.): оно скачается в папку проекта и загрузится на Диск.\n"
                                           "Используйте только свои материалы или те, на которые у вас есть права.")
        if act == "up":
            st["awaiting"] = "proj_upload"
            state.save(uid, st)
            return await cb.message.answer("📤 Присылайте видео в чат (как видео или как файл): всё сохранится в проект. "
                                           f"Файлы больше {20} МБ Telegram не отдаёт, их кладите прямо в папку проекта на Диске "
                                           f"(_projects/{p[0]}/{p[1]}) и жмите «🔄 С Диска».",
                                           reply_markup=_kb([[_b("✅ Хватит, к проекту", "id:pm")]]))
        if act == "idea":
            idea = ideas.read_idea(p[0], "work", p[1])
            return await cb.message.answer(f"🧾 {idea['title']}\n\n{idea['text'].strip()[:3500]}" if idea else "Текст идеи не найден.")
        if act == "json":
            return await cb.message.answer(
                "📜 Получили JSON от нейросети? Просто пришлите его сюда текстом или файлом .json, пока проект открыт.\n"
                "Бот проверит сценарий с видео этого проекта (в сценах пишите own — имя файла из списка), покажет сводку и замечания, "
                "дальше «🚀 Собрать».")
        if act == "pt":
            rows = [[_b(name, f"id:ptn:{key}")] for key, (name, _) in voicestyle.TONES.items()]
            rows.append([_b("Без тона (только правила)", "id:ptn:none")])
            return await cb.message.answer("Какой тон у рассказчика?", reply_markup=_kb(rows))
        if act == "ptn":
            tone = arg if arg in voicestyle.TONES else None
            await asyncio.to_thread(ideas.pull_project, *p)
            ctx = await asyncio.to_thread(ideas.project_ctx, *p)
            brief = await asyncio.to_thread(ideas.brief_of, *p)
            await scenario_ui._send_long(cb.message, scenario.make_prompt(ctx, brief=brief, tone=tone), "prompt_for_ai.txt")
            return await cb.message.answer(
                f"Промт собран: идея + {len(ctx['project_files'])} видео проекта. Отправьте его нейросети, а готовый JSON пришлите сюда.",
                reply_markup=_kb([[_b("⬅ К проекту", "id:pm")]]))
        if act == "fin":
            return await _show(cb, "Отметить идею как сделанную? Она перейдёт в «Готово», файлы проекта останутся.",
                               _kb([[_b("✅ Да", "id:finok"), _b("Отмена", "id:pm")]]))
        if act == "finok":
            await asyncio.to_thread(ideas.finish, *p)
            ch = p[0]
            st["proj"], st["awaiting"] = None, None
            text, kb = await asyncio.to_thread(list_screen, st, ch, "work", 0)
            state.save(uid, st)
            return await _show(cb, "✅ Идея перенесена в «Готово».\n\n" + text, kb)
        if act == "lb":
            rows = _lib_sources(st)
            state.save(uid, st)
            return await _show(cb, "📥 Откуда взять видео?", _kb(rows))
        if act in ("lbs", "lbp", "lbc"):
            return await _lib(cb, st, uid, p, act, arg)
    except (ValueError, RuntimeError, OSError) as e:
        log.warning("ideasui %s: %s", cb.data, e)
        await cb.message.answer(f"Не получилось: {e}")


# ---------- перенос из библиотеки ----------

def _lib_sources(st: dict) -> list[list[InlineKeyboardButton]]:
    themes = mix.list_themes()
    st["lists"]["lb_src"] = ["_own"] + themes
    rows = [[_b("🎥 Мои видео (_own)", "id:lbs:0")]]
    rows += [[_b(f"🎞 {t}", f"id:lbs:{i + 1}") for i, t in list(enumerate(themes))[j:j + 2]] for j in range(0, len(themes[:30]), 2)]
    rows.append([_b("⬅ К проекту", "id:pm")])
    return rows


def _lib_files(src: str) -> list[Path]:
    if src == "_own":
        files = mix.own_clips()
    else:
        try:
            files = mix._clips(src)
        except RuntimeError:
            files = []
    return sorted(files, key=lambda f: f.name.lower())


async def _lib(cb: CallbackQuery, st: dict, uid: int, p: tuple[str, str], act: str, arg: str | None) -> None:
    lb = st["lists"].setdefault("lb", {"src": "_own", "files": [], "page": 0})
    page = 0
    if act == "lbs":
        srcs = st["lists"].get("lb_src") or ["_own"]
        i = int(arg)
        lb["src"] = srcs[i] if 0 <= i < len(srcs) else "_own"
        lb["files"] = [str(f) for f in await asyncio.to_thread(_lib_files, lb["src"])]
    elif act == "lbp":
        page = int(arg)
    elif act == "lbc":
        i = int(arg)
        if not (0 <= i < len(lb["files"])):
            return await cb.message.answer("Список устарел, откройте библиотеку заново.")
        page = lb.get("page", 0)
        src = Path(lb["files"][i])
        res = await asyncio.to_thread(ideas.add_file, p[0], p[1], src)
        await safe_answer(cb, f"Скопировано: {res['path'].name}")
        if not res["drive_ok"]:
            await cb.message.answer("⚠️ Скопировано в проект, но на Диск не загрузилось.")
    lb["page"] = page
    files = [Path(f) for f in lb["files"]]
    pages = max((len(files) - 1) // PAGE + 1, 1)
    page = min(page, pages - 1)
    lb["page"] = page
    have = {f.name for f in await asyncio.to_thread(lambda: list(ideas.project_dir(*p).glob("*")))}
    from common import library
    titles = library.own_titles() if lb["src"] == "_own" else {}
    rows = []
    for k, f in enumerate(files[page * PAGE:(page + 1) * PAGE]):
        label = _short(titles.get(f.name) or f.name, 40)
        rows.append([_b(("✅ " if f.name in have else "➕ ") + label, f"id:lbc:{page * PAGE + k}")])
    if pages > 1:
        rows.append([_b("◀", f"id:lbp:{(page - 1) % pages}"), _b(f"{page + 1}/{pages}", f"id:lbp:{page}"),
                     _b("▶", f"id:lbp:{(page + 1) % pages}")])
    rows.append([_b("📚 Другой источник", "id:lb"), _b("⬅ К проекту", "id:pm")])
    name = "Мои видео" if lb["src"] == "_own" else f"тема «{lb['src']}»"
    state.save(uid, st)
    await _show(cb, f"📥 {name}: {len(files)} видео. Нажмите на видео, чтобы скопировать его в проект (✅ — уже в проекте).",
                _kb(rows))


# ---------- текст и файлы ----------

async def _review(message: Message, uid: int, st: dict, raw: str) -> None:
    p = _proj(st)
    try:
        data = scenario.extract_json(raw)
    except ValueError as e:
        return await message.answer(f"❌ {e}\nПришлите исправленный JSON (или файл .json).")
    await asyncio.to_thread(ideas.pull_project, *p)
    ctx = await asyncio.to_thread(ideas.project_ctx, *p)
    res = scenario.parse(data, ctx)
    if res["errors"]:
        lines = "\n".join(f"• {e}" for e in res["errors"][:12])
        extra = "\n\n⚠️ Заодно:\n" + "\n".join(f"• {w}" for w in res["warnings"][:5]) if res["warnings"] else ""
        return await message.answer(
            f"❌ В сценарии есть ошибки, ролик не собран:\n{lines}{extra}\n\n"
            "Отправьте этот список нейросети с просьбой исправить JSON и пришлите результат снова.")
    res["recipe"]["project"] = ctx["project"]
    res["project"] = {"ch": p[0], "name": p[1]}
    st["lists"]["scenario"] = res
    state.save(uid, st)
    idea = ideas.read_idea(*p[:1], "work", p[1])
    head = f"📂 Проект: {idea['title'] if idea else p[1]}\n"
    await message.answer(head + scenario.summary(res),
                         reply_markup=_kb([[_b("🚀 Собрать", "sc:go"), _b("✖ Отмена", "sc:cancel")]]))


async def after_go(message: Message, uid: int, pending: dict) -> None:
    """Вызывается после постановки задачи из проекта: возвращает экран проекта."""
    pr = pending.get("project")
    if not pr:
        return
    st = state.load(uid)
    st["proj"] = {"ch": pr["ch"], "name": pr["name"]}
    state.save(uid, st)
    await message.answer("Ролик в очереди. Когда результат устроит, отметьте идею готовой.",
                         reply_markup=_kb([[_b("📂 К проекту", "id:pm"), _b("✅ Идея готова", "id:fin")]]))


async def handle_text(message: Message, uid: int, st: dict) -> bool:
    aw = st.get("awaiting")
    text = (message.text or "").strip()
    if aw == "chan_new":
        try:
            name = await asyncio.to_thread(ideas.create_channel, text)
        except ValueError as e:
            await message.answer(f"{e}\nПришлите другое название или нажмите /ideas.")
            return True
        st["awaiting"] = None
        state.save(uid, st)
        await message.answer(f"📺 Канал «{name}» создан: папки _ideas/{name} и _projects/{name} есть на сервере и на Диске. "
                             "Чтобы ролики этого канала собирались от его имени, выберите его в «⚙️ Профиль» или командой /channel.")
        text, kb = home_screen()
        await message.answer(text, reply_markup=kb)
        return True
    if aw == "idea_new":
        ch = _ctx(st).get("ch") or db.get_user_channel(uid)
        try:
            res = await asyncio.to_thread(ideas.add_idea, ch, text)
        except ValueError as e:
            await message.answer(str(e))
            return True
        st["awaiting"] = None
        await message.answer(f"💡 Идея сохранена: {res['title']}" + _drive_note(res["drive_ok"]))
        await asyncio.to_thread(ideas.pull_ideas, ch)
        screen, kb = await asyncio.to_thread(channel_screen, st, ch)
        state.save(uid, st)
        await message.answer(screen, reply_markup=kb)
        return True
    if aw == "proj_url" and _proj(st):
        p = _proj(st)
        st["awaiting"] = None
        state.save(uid, st)
        await message.answer("⏳ Скачиваю, это может занять несколько минут…")
        from core import sources
        try:
            pdir = ideas.project_dir(*p)
            pdir.mkdir(parents=True, exist_ok=True)
            res = await asyncio.to_thread(sources.add_url, text, pdir, ideas.remote_dir_of(*p))
        except Exception as e:  # noqa: BLE001
            await message.answer(f"Не удалось скачать: {e}")
            return True
        drive = "✅ загружено на Диск" if res["drive_ok"] else f"⚠️ на Диск не загрузилось ({res['drive_msg'] or 'ошибка rclone'})"
        await message.answer(f"Готово: {Path(res['path']).name}\n{drive}", reply_markup=_kb([[_b("📂 К проекту", "id:pm")]]))
        return True
    if _proj(st) and scenario_ui.looks_like_json(text):
        await _review(message, uid, st, text)
        return True
    return False


async def handle_media(message: Message, bot: Bot, uid: int, st: dict) -> bool:
    p = _proj(st)
    if not p:
        return False
    doc = message.document
    if doc and ((doc.file_name or "").lower().endswith(".json") or doc.mime_type == "application/json"):
        if (doc.file_size or 0) > 512 * 1024:
            await message.answer("Файл слишком большой для сценария (больше 512 КБ).")
            return True
        buf = BytesIO()
        await bot.download(doc, destination=buf)
        try:
            raw = buf.getvalue().decode("utf-8-sig")
        except UnicodeDecodeError:
            await message.answer("Файл не в кодировке UTF-8.")
            return True
        await _review(message, uid, st, raw)
        return True
    if st.get("awaiting") != "proj_upload":
        return False
    obj = message.video or message.video_note or doc
    if obj is None:
        return False
    name = getattr(obj, "file_name", None) or f"video_{message.message_id}.mp4"
    if doc and Path(name).suffix.lower() not in ideas.VIDEO_EXT and not (doc.mime_type or "").startswith("video/"):
        await message.answer("Это не видео. Пришлите mp4/mov/mkv/webm.")
        return True
    if not Path(name).suffix:
        name += ".mp4"
    pdir = ideas.project_dir(*p)
    pdir.mkdir(parents=True, exist_ok=True)
    dest = ideas.unique_file(pdir, name)
    try:
        await bot.download(obj, destination=dest)
    except TelegramBadRequest as e:
        dest.unlink(missing_ok=True)
        if "too big" in str(e).lower():
            await message.answer("Файл больше 20 МБ, Telegram не отдаёт его боту. Положите его в папку проекта на Диске "
                                 f"(_projects/{p[0]}/{p[1]}) и нажмите «🔄 С Диска».")
            return True
        raise
    ok, msg = await asyncio.to_thread(ideas.push_file, dest, ideas.remote_dir_of(*p))
    await message.answer(f"Сохранено: {dest.name}" + ("" if ok else "\n⚠️ На Диск не загрузилось."),
                         reply_markup=_kb([[_b("✅ Хватит, к проекту", "id:pm")]]))
    return True
