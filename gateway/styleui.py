"""Конструктор монтажного стиля в боте: переходы, длительность, зум, цвет, эффекты -> сохранить под своим именем.

Кнопки: ms:hub, ms:v:<t|d|z|g|f>, ms:t:<переход>, ms:ta, ms:tn, ms:d:<с>, ms:z:<зум>, ms:g:<цвет|none>, ms:f:<эффект>,
ms:rnd, ms:reset, ms:save (дальше бот ждёт название), ms:my, ms:use:<id>, ms:edit:<id>, ms:del:<id>.
Свои стили лежат в ROOT/montage_styles.json (core/montage.py) и появляются в списке «Монтаж» со звёздочкой.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from common import config
from core import montage as M
from gateway import state
from gateway.ui import safe_answer, safe_edit

log = logging.getLogger("styleui")
router = Router()


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _mark(cond: bool) -> str:
    return "✅ " if cond else ""


def _ok(uid: int) -> bool:
    return uid in config.ALLOWED_USER_IDS


def new_draft() -> dict:
    return {"transition": ["fade", "slideleft", "circleopen"], "d": 0.3, "zoom": "slow", "grade": None, "fx": [], "edit_id": None}


def _draft(st: dict) -> dict:
    d = st.get("mdraft")
    if not isinstance(d, dict):
        d = st["mdraft"] = new_draft()
    for k, v in new_draft().items():
        d.setdefault(k, v)
    return d


def _rows(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _grid(btns: list[InlineKeyboardButton], width: int) -> list[list[InlineKeyboardButton]]:
    return [btns[i:i + width] for i in range(0, len(btns), width)]


def screen(st: dict, view: str = "hub"):
    d = _draft(st)
    done = [_b("✔ Готово", "ms:hub")]
    if view == "t":
        text = ("↔ Переходы между клипами\nОтметьте один или несколько: в каждой склейке бот берёт случайный из отмеченных. "
                "Ничего не отмечено - жёсткая склейка без перехода.")
        rows = _grid([_b(_mark(k in d["transition"]) + name, f"ms:t:{k}") for k, name in M.TRANS_LABELS.items()], 3)
        rows.append([_b("Все", "ms:ta"), _b("Сбросить", "ms:tn")])
        rows.append(done)
    elif view == "d":
        text = "⏱ Длительность перехода, секунд. Короче - резче и динамичнее, длиннее - плавнее."
        rows = [[_b(_mark(abs(d["d"] - x) < 1e-6) + ("резко" if x == 0 else str(x)), f"ms:d:{x}") for x in M.DURATIONS[:3]],
                [_b(_mark(abs(d["d"] - x) < 1e-6) + str(x), f"ms:d:{x}") for x in M.DURATIONS[3:]], done]
    elif view == "z":
        text = ("🔍 Зум: дыхание - лёгкое покачивание, наезд - медленное приближение, пульс - ритмичное, всплески - резкие толчки.")
        rows = _grid([_b(_mark(d["zoom"] == k) + name, f"ms:z:{k}") for k, name in M.ZOOMS.items()], 2)
        rows.append(done)
    elif view == "g":
        text = "🎨 Цветокоррекция: общий тон всего ролика."
        rows = [[_b(_mark(d["grade"] is None) + "Без коррекции", "ms:g:none")]]
        rows += _grid([_b(_mark(d["grade"] == k) + name, f"ms:g:{k}") for k, name in M.GRADE_LABELS.items()], 2)
        rows.append(done)
    elif view == "f":
        text = "✨ Эффекты поверх картинки (можно несколько)."
        rows = _grid([_b(_mark(k in d["fx"]) + name, f"ms:f:{k}") for k, name in M.FX_LABELS.items()], 2)
        rows.append(done)
    elif view == "my":
        mine = M.user_styles()
        text = "📂 Мои стили. ▶ выбрать для ролика, ✏ изменить в конструкторе, 🗑 удалить." if mine else "Своих стилей пока нет: соберите в конструкторе и нажмите «Сохранить и применить»."
        rows = []
        for sid, spec in mine.items():
            rows.append([_b(_mark(st.get("style") == sid) + "▶ " + spec["label"], f"ms:use:{sid}"), _b("✏", f"ms:edit:{sid}"), _b("🗑", f"ms:del:{sid}")])
        rows.append([_b("🛠 В конструктор", "ms:hub")])
    else:
        edit = d.get("edit_id")
        text = ("🛠 Конструктор монтажного стиля" + (f" (правка «{M.user_styles().get(edit, {}).get('label', '')}»)" if edit else "") + "\n\n"
                + M.describe(d) + "\n\nСобирайте по частям, затем «Сохранить и применить»: стиль получит название и появится в списке «Монтаж».")
        rows = [[_b(f"↔ Переходы ({len(d['transition'])})", "ms:v:t"), _b("⏱ Длительность", "ms:v:d")],
                [_b("🔍 Зум", "ms:v:z"), _b("🎨 Цвет", "ms:v:g")],
                [_b(f"✨ Эффекты ({len(d['fx'])})", "ms:v:f"), _b("🎲 Случайный набор", "ms:rnd")],
                [_b("💾 Сохранить и применить", "ms:save")],
                [_b("📂 Мои стили", "ms:my"), _b("♻ Сначала", "ms:reset")],
                [_b("⬅ К монтажу", "w:b:51")]]
    return text, _rows(rows)


@router.message(Command("style", "montage"))
async def cmd_style(message: Message):
    if not message.from_user or not _ok(message.from_user.id):
        return
    st = state.load(message.from_user.id)
    st["awaiting"] = None
    text, kb = screen(st)
    state.save(message.from_user.id, st)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ms:"))
async def on_ms(cb: CallbackQuery):
    uid = cb.from_user.id
    if not _ok(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    st = state.load(uid)
    d = _draft(st)
    parts = cb.data.split(":")
    act = parts[1]
    val = parts[2] if len(parts) > 2 else None
    view = "hub"
    if st.get("awaiting") == "mstyle_name":
        st["awaiting"] = None
    if act == "v":
        view = val if val in ("t", "d", "z", "g", "f") else "hub"
    elif act == "t" and val in M.TRANS_LABELS:
        if val in d["transition"]:
            d["transition"].remove(val)
        else:
            d["transition"].append(val)
        view = "t"
    elif act == "ta":
        d["transition"], view = list(M.TRANS_LABELS), "t"
    elif act == "tn":
        d["transition"], view = [], "t"
    elif act == "d":
        try:
            d["d"] = float(val)
        except (TypeError, ValueError):
            pass
        view = "d"
    elif act == "z" and val in M.ZOOMS:
        d["zoom"], view = val, "z"
    elif act == "g":
        d["grade"] = val if val in M.GRADES else None
        view = "g"
    elif act == "f" and val in M.FX_LABELS:
        if val in d["fx"]:
            d["fx"].remove(val)
        else:
            d["fx"].append(val)
        view = "f"
    elif act == "rnd":
        d.update(M.random_spec())
    elif act == "reset":
        st["mdraft"] = new_draft()
    elif act == "my":
        view = "my"
    elif act == "use" and val in M.user_styles():
        st["style"], view = val, "my"
    elif act == "edit" and val in M.user_styles():
        spec = M.user_styles()[val]
        st["mdraft"] = {**new_draft(), **{k: spec[k] for k in ("transition", "d", "zoom", "grade", "fx")}, "transition": list(spec["transition"] or []),
                        "edit_id": val, "label": spec["label"]}
    elif act == "del" and val:
        M.delete_user_style(val)
        if st.get("style") == val:
            st["style"] = "none"
        view = "my"
    elif act == "save":
        st["awaiting"] = "mstyle_name"
        state.save(uid, st)
        await cb.message.answer("Как назвать стиль? Пришлите название (до 30 символов) или «-», тогда назову сам. "
                                "Стиль с таким же названием обновится.")
        return await cb.answer()
    state.save(uid, st)
    text, kb = screen(st, view)
    await safe_answer(cb)
    await safe_edit(cb.message, text, kb)


async def handle_text(message: Message, uid: int, st: dict) -> bool:
    """Название для сохранения стиля. True, если сообщение забрал конструктор."""
    if st.get("awaiting") != "mstyle_name":
        return False
    d = _draft(st)
    name = (message.text or "").strip()
    if name in ("-", "—", ""):
        name = f"Стиль {len(M.user_styles()) + 1}"
    sid = M.save_user_style(name[:30], d, d.get("edit_id"))
    st["style"] = sid
    st["awaiting"] = None
    d["edit_id"] = None
    state.save(uid, st)
    await message.answer(f"✅ Стиль «{M.user_styles()[sid]['label']}» сохранён и выбран для ролика.\n\n{M.describe(M.user_styles()[sid])}",
                         reply_markup=_rows([[_b("⬅ К монтажу", "w:b:51"), _b("🛠 Ещё один", "ms:reset")]]))
    return True
