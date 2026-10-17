"""Словарь произношения для русской озвучки.

prepare(text, voice) -> текст ТОЛЬКО для синтезатора (субтитры берутся из исходного текста и не меняются).
Порядок: свой словарь (/say) -> сокращения с точками -> телефоны, даты, римские цифры, время, размеры ->
латиница (бренды, аббревиатуры) -> числа со склонением и единицами -> одиночные «млн/тыс/руб» -> КАПС-аббревиатуры.
Данные лежат в core/pronounce_data.py, свои слова - в ROOT/pronounce_user.json.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata

from common import config
from core import pronounce_data as D

log = logging.getLogger("pronounce")

USER_FILE = config.ROOT / "pronounce_user.json"

# ============================================================ свой словарь ============================================================


def load_user() -> dict[str, str]:
    try:
        data = json.loads(USER_FILE.read_text("utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001
        log.warning("не прочитал %s: %s", USER_FILE, e)
        return {}


def save_user(d: dict[str, str]) -> None:
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    tmp.replace(USER_FILE)


def _split_lang(word: str) -> tuple[str | None, str]:
    """«en: gif» -> ('en', 'gif'); без префикса правило действует и в русской, и в английской озвучке."""
    m = re.match(r"^(ru|en)\s*:\s*(.+)$", word.strip(), re.I | re.S)
    return (m.group(1).lower(), m.group(2).strip()) if m else (None, word.strip())


def add_user(word: str, reading: str) -> str:
    lang, w = _split_lang(word)
    key = f"{lang}:{w.lower()}" if lang else w.lower()
    d = load_user()
    d[key] = reading.strip()
    save_user(d)
    return key


def remove_user(word: str) -> bool:
    lang, w = _split_lang(word)
    key = f"{lang}:{w.lower()}" if lang else w.lower()
    d = load_user()
    if d.pop(key, None) is None:
        return False
    save_user(d)
    return True


def _apply_user(text: str, lang: str = "ru") -> str:
    user = load_user()
    if not user:
        return text
    items = []
    for key, val in user.items():
        kl, k = _split_lang(key) if re.match(r"^(ru|en):", key, re.I) else (None, key)
        if kl is None or kl == lang:
            items.append((k, val))
    # длинные ключи раньше коротких; «слово*» - любое окончание сохраняется
    for key, val in sorted(items, key=lambda kv: len(kv[0]), reverse=True):
        star = key.endswith("*")
        k = key[:-1] if star else key
        if not k:
            continue
        pat = r"(?<!\w)" + re.escape(k) + (r"(\w*)" if star else r"(?!\w)")
        text = re.sub(pat, lambda m, v=val, st=star: v + (m.group(1) if st else ""), text, flags=re.I)
    return text


# ============================================================ числа ============================================================
NOM, GEN, DAT, INS, PRE, ACC = range(6)
CASE_NAMES = {"nom": NOM, "gen": GEN, "dat": DAT, "ins": INS, "prep": PRE, "acc": ACC}

_ONES = {  # 1..9: [nom, gen, dat, ins, prep] (м/с род; ж/ср отдельно у 1 и 2)
    1: ["один", "одного", "одному", "одним", "одном"], 2: ["два", "двух", "двум", "двумя", "двух"],
    3: ["три", "трёх", "трём", "тремя", "трёх"], 4: ["четыре", "четырёх", "четырём", "четырьмя", "четырёх"],
    5: ["пять", "пяти", "пяти", "пятью", "пяти"], 6: ["шесть", "шести", "шести", "шестью", "шести"],
    7: ["семь", "семи", "семи", "семью", "семи"], 8: ["восемь", "восьми", "восьми", "восемью", "восьми"],
    9: ["девять", "девяти", "девяти", "девятью", "девяти"],
}
_ONE_F = ["одна", "одной", "одной", "одной", "одной"]
_ONE_N = ["одно", "одного", "одному", "одним", "одном"]
_TWO_F = ["две", "двух", "двум", "двумя", "двух"]
_TEEN_STEM = ["десят", "одиннадцат", "двенадцат", "тринадцат", "четырнадцат", "пятнадцат", "шестнадцат", "семнадцат", "восемнадцат", "девятнадцат"]
_TENS = {
    2: ["двадцать", "двадцати", "двадцати", "двадцатью", "двадцати"], 3: ["тридцать", "тридцати", "тридцати", "тридцатью", "тридцати"],
    4: ["сорок", "сорока", "сорока", "сорока", "сорока"], 5: ["пятьдесят", "пятидесяти", "пятидесяти", "пятьюдесятью", "пятидесяти"],
    6: ["шестьдесят", "шестидесяти", "шестидесяти", "шестьюдесятью", "шестидесяти"],
    7: ["семьдесят", "семидесяти", "семидесяти", "семьюдесятью", "семидесяти"],
    8: ["восемьдесят", "восьмидесяти", "восьмидесяти", "восьмьюдесятью", "восьмидесяти"],
    9: ["девяносто", "девяноста", "девяноста", "девяноста", "девяноста"],
}
_HUND = {
    1: ["сто", "ста", "ста", "ста", "ста"], 2: ["двести", "двухсот", "двумстам", "двумястами", "двухстах"],
    3: ["триста", "трёхсот", "трёмстам", "тремястами", "трёхстах"], 4: ["четыреста", "четырёхсот", "четырёмстам", "четырьмястами", "четырёхстах"],
    5: ["пятьсот", "пятисот", "пятистам", "пятьюстами", "пятистах"], 6: ["шестьсот", "шестисот", "шестистам", "шестьюстами", "шестистах"],
    7: ["семьсот", "семисот", "семистам", "семьюстами", "семистах"], 8: ["восемьсот", "восьмисот", "восьмистам", "восьмьюстами", "восьмистах"],
    9: ["девятьсот", "девятисот", "девятистам", "девятьюстами", "девятистах"],
}
_TEN = ["десять", "десяти", "десяти", "десятью", "десяти"]
_ZERO = ["ноль", "нуля", "нулю", "нулём", "нуле"]
# «тысяча» женского рода, остальные мужского: (ед. по падежам, мн. по падежам, форма «2-4» в именительном)
_SCALES = [
    None,
    (["тысяча", "тысячи", "тысяче", "тысячей", "тысяче"], ["тысячи", "тысяч", "тысячам", "тысячами", "тысячах"], "тысячи", "f"),
    D._reg("миллион")[1:] + ("m",), D._reg("миллиард")[1:] + ("m",), D._reg("триллион")[1:] + ("m",),
]


def _ci(case: int) -> int:
    return NOM if case == ACC else case


def _teen(k: int, case: int) -> str:
    stem = _TEEN_STEM[k]
    c = _ci(case)
    if k == 0:
        return _TEN[c]
    return stem + ("ь" if c == NOM else "ью" if c == INS else "и")


def _one(d: int, gender: str, case: int) -> str:
    if d == 1:
        if case == ACC:
            return {"m": "один", "f": "одну", "n": "одно"}[gender]
        return {"m": _ONES[1], "f": _ONE_F, "n": _ONE_N}[gender][case]
    if d == 2 and gender == "f":
        return _TWO_F[_ci(case)]
    return _ONES[d][_ci(case)]


def _group(n: int, gender: str, case: int) -> list[str]:
    w: list[str] = []
    h, r = divmod(n, 100)
    if h:
        w.append(_HUND[h][_ci(case)])
    if r >= 20:
        t, u = divmod(r, 10)
        w.append(_TENS[t][_ci(case)])
        if u:
            w.append(_one(u, gender, case))
    elif r >= 10:
        w.append(_teen(r - 10, case))
    elif r:
        w.append(_one(r, gender, case))
    return w


def noun_form(forms, n: int, case: int, force_gen_sg: bool = False) -> str:
    """Форма существительного при числе n. forms = (род|None, ед[5], мн[5], «2-4»)."""
    sg, pl, few = forms[1], forms[2], forms[3]
    gender = forms[0]
    if force_gen_sg:
        return sg[GEN]
    n = abs(n)
    one = n % 10 == 1 and n % 100 != 11
    twofour = 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14
    if case in (NOM, ACC):
        if one:
            w = sg[NOM]
            if case == ACC and gender == "f":
                w = re.sub("а$", "у", w)
                w = re.sub("я$", "ю", w)
            return w
        return few if twofour else pl[GEN]
    return sg[case] if one else pl[case]


def cardinal(n: int, gender: str = "m", case: int = NOM) -> str:
    if n < 0:
        return "минус " + cardinal(-n, gender, case)
    if n == 0:
        return _ZERO[_ci(case)]
    if n >= 10 ** 15:
        return " ".join(_ONES[int(c)][0] if c != "0" else "ноль" for c in str(n))
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    out: list[str] = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g:
            continue
        if i == 0:
            out += _group(g, gender, case)
        else:
            sc = _SCALES[i]
            gnd = sc[3]
            ws = _group(g, gnd, case)
            if i == 1 and g == 1:
                ws = []          # «тысяча», а не «одна тысяча»
            out += ws
            out.append(noun_form((gnd, sc[0], sc[1], sc[2]), g, case))
    return " ".join(out)


# ---------- порядковые ----------
_ORD_ONES = {1: "первый", 2: "второй", 3: "третий", 4: "четвёртый", 5: "пятый", 6: "шестой", 7: "седьмой", 8: "восьмой", 9: "девятый"}
_ORD_TEENS = ["десятый", "одиннадцатый", "двенадцатый", "тринадцатый", "четырнадцатый", "пятнадцатый", "шестнадцатый", "семнадцатый",
              "восемнадцатый", "девятнадцатый"]
_ORD_TENS = {2: "двадцатый", 3: "тридцатый", 4: "сороковой", 5: "пятидесятый", 6: "шестидесятый", 7: "семидесятый", 8: "восьмидесятый",
             9: "девяностый"}
_ORD_HUND = {1: "сотый", 2: "двухсотый", 3: "трёхсотый", 4: "четырёхсотый", 5: "пятисотый", 6: "шестисотый", 7: "семисотый",
             8: "восьмисотый", 9: "девятисотый"}
_PRE = {2: "двух", 3: "трёх", 4: "четырёх", 5: "пяти", 6: "шести", 7: "семи", 8: "восьми", 9: "девяти", 10: "десяти", 11: "одиннадцати",
        12: "двенадцати", 13: "тринадцати", 14: "четырнадцати", 15: "пятнадцати", 16: "шестнадцати", 17: "семнадцати", 18: "восемнадцати",
        19: "девятнадцати", 20: "двадцати", 30: "тридцати", 40: "сорока", 50: "пятидесяти", 60: "шестидесяти", 70: "семидесяти",
        80: "восьмидесяти", 90: "девяноста", 100: "ста"}
_ORD_END = {
    "m": ["ый", "ого", "ому", "ым", "ом"], "f": ["ая", "ой", "ой", "ой", "ой"],
    "n": ["ое", "ого", "ому", "ым", "ом"], "p": ["ые", "ых", "ым", "ыми", "ых"],
}
_ORD_SOFT = {
    "m": ["ий", "ьего", "ьему", "ьим", "ьем"], "f": ["ья", "ьей", "ьей", "ьей", "ьей"],
    "n": ["ье", "ьего", "ьему", "ьим", "ьем"], "p": ["ьи", "ьих", "ьим", "ьими", "ьих"],
}


def _decl_adj(word: str, gender: str, case: int) -> str:
    """Склоняет порядковое (форма м.р. ед.ч. им.п.). gender: m f n p."""
    if word == "третий":
        stem, table = "трет", _ORD_SOFT
    elif word.endswith(("ый", "ой")):
        if gender == "m" and case in (NOM, ACC):
            return word
        stem, table = word[:-2], _ORD_END
    else:
        return word
    if case == ACC:
        if gender == "f":
            return stem + ("ью" if table is _ORD_SOFT else "ую")
        case = NOM
    return stem + table[gender][case]


def _ord_last(last: int) -> str:
    if last < 10:
        return _ORD_ONES[last]
    if last < 20:
        return _ORD_TEENS[last - 10]
    if last < 100:
        return _ORD_TENS[last // 10]
    return _ORD_HUND[last // 100]


def ordinal(n: int, gender: str = "m", case: int = NOM) -> str:
    """Порядковое числительное: 2025 -> «две тысячи двадцать пятый» (склоняется последнее слово)."""
    if n <= 0 or n >= 10 ** 12:
        return cardinal(n, "m", NOM)
    if n % 1000 == 0 and n < 10 ** 6:
        k = n // 1000
        pre = "" if k == 1 else _PRE.get(k, "")
        if k == 1 or pre:
            return _decl_adj(pre + "тысячный", gender, case)
        return cardinal(k) + " " + _decl_adj("тысячный", gender, case)
    r100 = n % 100
    if 1 <= r100 <= 19:
        last = r100
    elif r100 == 0:
        last = n % 1000
    else:
        last = n % 10 or r100
    pre = n - last
    head = cardinal(pre, "m", NOM) + " " if pre else ""
    return head + _decl_adj(_ord_last(last), gender, case)


# ---------- десятичные и дроби ----------
_FRAC_DEN = {1: "десят", 2: "сот", 3: "тысячн", 4: "десятитысячн", 5: "стотысячн", 6: "миллионн"}


def decimal_words(int_part: int, frac: str, case: int = NOM, sign: str = "") -> str:
    """2,5 -> «две целых пять десятых». Знаменатель всегда в род.п. (так читается чаще всего)."""
    f = frac.rstrip("0") if len(frac) > 6 else frac
    if len(frac) > 6 or not f:
        # слишком длинная дробь: читаем цифры по одной
        digs = " ".join(_ONES[int(c)][0] if c != "0" else "ноль" for c in frac)
        return f"{sign}{cardinal(int_part, 'f', case)} запятая {digs}"
    num = int(f)
    den_stem = _FRAC_DEN[len(f)]
    oblique = case in (GEN, DAT, INS, PRE)
    one_int = int_part % 10 == 1 and int_part % 100 != 11
    one_frac = num % 10 == 1 and num % 100 != 11
    cel = ("одной целой" if oblique else "целая") if (one_int and int_part < 20) else "целых"
    ip = cardinal(int_part, "f", case)
    if one_int and int_part >= 20:
        cel = "целых" if not oblique else "целой"
    fp = cardinal(num, "f", case)
    if one_frac:
        den = den_stem + ("ой" if oblique else "ая")
    else:
        den = den_stem + "ых"
    if one_int and int_part < 20 and oblique:
        ip = ""   # «одной целой» уже содержит число
    return f"{sign}{(ip + ' ') if ip else ''}{cel} {fp} {den}".replace("  ", " ")


# ============================================================ контекст ============================================================
PREP_CASE = {
    **dict.fromkeys(["до", "от", "из", "без", "для", "после", "около", "возле", "у", "кроме", "вместо", "среди", "внутри", "вокруг",
                     "против", "свыше", "более", "менее", "с", "со", "из-за", "насчёт", "ради", "спустя", "вплоть"], GEN),
    **dict.fromkeys(["к", "ко", "по", "благодаря", "навстречу"], DAT),
    **dict.fromkeys(["над", "под", "перед", "между", "за"], INS),
    **dict.fromkeys(["о", "об", "обо", "при", "в", "во", "на"], PRE),
    **dict.fromkeys(["через", "про", "сквозь"], ACC),
}
# «в», «на», «за» по умолчанию винительный (в 5 раз, на 10 минут, за 3 дня), предложный - только по окончанию следующего слова
_ACC_DEFAULT = {"в", "во", "на", "за"}
_MONTH_RE = re.compile(r"^(январ|феврал|март|апрел|июн|июл|август|сентябр|октябр|ноябр|декабр|мая|май|мае|маем)", re.I)
_MONTH_GEN = {m.lower() for m in D.MONTHS_GEN}
_YEAR_WORDS = {"год": NOM, "года": GEN, "году": PRE, "годом": INS, "годе": PRE, "годы": NOM, "годов": GEN, "годам": DAT, "годами": INS,
               "годах": PRE, "г": NOM, "гг": NOM}
_PL_ENDINGS = ("ами", "ями", "ыми", "ими")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")


def _prev_word(s: str, pos: int) -> str:
    m = re.search(r"([A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*)[\s \"«(]*$", s[:pos])
    return m.group(1).lower() if m else ""


def _next_word(s: str, pos: int) -> str:
    m = re.match(r"[\s ]*([A-Za-zА-Яа-яЁё]+)", s[pos:])
    return m.group(1) if m else ""


def _case_for(prev: str, nxt: str, ordinal_ctx: bool = False) -> int:
    """Падеж числа по предлогу перед ним (и окончанию слова после)."""
    c = PREP_CASE.get(prev)
    if c is None:
        return NOM
    nl = nxt.lower()
    if prev in ("с", "со") and nl.endswith(_PL_ENDINGS + ("ым", "ой", "ем", "ом")):
        return INS
    if prev in _ACC_DEFAULT:
        if nl.endswith(("ах", "ях")) or (ordinal_ctx and nl.endswith(("е",)) and nl not in _MONTH_GEN):
            return PRE
        return ACC
    if prev == "по":
        return DAT if ordinal_ctx else ACC
    if prev == "за" and ordinal_ctx:
        return ACC
    return c


def _gender_next(nxt: str, case: int = NOM, digit: int = 1) -> str:
    """Род существительного после числа 1/2 (эвристика по окончанию).
    После 1 слово в именительном («одна собака»), после 2-4 - в родительном ед. («два стола», «две собаки»)."""
    w = nxt.lower()
    if not w:
        return "m"
    if case in (NOM, ACC):
        if digit == 1:
            if w.endswith(("а", "я")) or (case == ACC and w.endswith(("у", "ю"))):
                return "f"
            if w.endswith(("о", "е", "ё")) and not w.endswith(("ое", "ее")):
                return "n"
            return "m"
        if w.endswith(("ы", "и")):
            return "f"
        return "m"
    if w.endswith(("ой", "ей", "ую", "ю", "ы", "и", "е")):
        return "f" if w.endswith(("ой", "ей", "ую", "ю", "ы", "и")) else "m"
    return "m"


# ---------- единицы ----------
_UNIT_RE = re.compile(r"[  ]?(?P<u>%|‰|₽|\$|€|£|°\s?[CcСс]\b|°|км/час|км/ч|м/с|[A-Za-zА-Яа-яЁё]+(?:/[a-zа-я]+)?)")
_SCALE_UNITS = {"тыс", "млн", "млрд", "трлн"}
_CUR_SYMBOLS = {"$": "долларов", "₽": "рублей", "€": "евро", "£": "фунтов"}


def _unit_key(w: str) -> str | None:
    w = w.replace(" ", "")
    if w.startswith("°") and len(w) == 2:
        return "°c"
    if w in D.UNIT_ALIASES:
        k = D.UNIT_ALIASES[w]
        return k if k in D.UNITS else None
    if w in D.UNIT_CASE_SENSITIVE:
        k = w.lower()
        return k if k in D.UNITS else None
    if w in D.UNITS and w == w.lower():
        return w
    return None


# ============================================================ преобразования ============================================================
def _clean_num(s: str) -> int:
    return int(re.sub(r"[   ,]", "", s))


_N = r"\d{1,3}(?:[   ]\d{3})+(?!\d)|\d{1,3}(?:,\d{3}){2,}(?!\d)|\d+"
_SUF = r"ый|ой|ий|ая|ое|ее|ые|ую|ого|ому|ых|ым|ыми|ей|ом|ми|го|му|й|я|е|ю|м|х"
_NUMBER = re.compile(
    rf"(?<![\w.,/])(?P<sign>[+\-−–][ ]?(?=\d))?(?P<a>{_N})(?:[.,](?P<af>\d+)(?!\d))?(?P<as_>-(?:{_SUF})(?![A-Za-zА-Яа-яЁё]))?"
    rf"(?:[ ]?(?P<dash>[–—]|-(?! ))[ ]?(?P<b>{_N})(?:[.,](?P<bf>\d+)(?!\d))?(?P<bs>-(?:{_SUF})(?![A-Za-zА-Яа-яЁё]))?)?"
)


def _suffix_info(suf: str, prev: str, nxt: str) -> tuple[str, int]:
    """Окончание у числа («5-й», «90-х», «3-го») -> (род/число m f n p, падеж)."""
    s = suf.lstrip("-").lower()
    nl = nxt.lower()
    pc = PREP_CASE.get(prev)
    if s in ("й", "ый", "ой", "ий"):
        if pc is not None and prev not in _ACC_DEFAULT:
            return "f", pc if pc != NOM else NOM
        if prev in _ACC_DEFAULT and nl.endswith(("е", "и", "ы")):
            return "f", PRE
        return "m", ACC if pc is not None else NOM
    if s in ("я", "ая"):
        return "f", NOM
    if s in ("ю", "ую"):
        return "f", ACC
    if s in ("е", "ое", "ее", "ые"):
        return ("p" if nl.endswith(("ы", "и")) or s == "ые" else "n"), (ACC if pc is not None else NOM)
    if s in ("го", "ого"):
        return "m", GEN
    if s in ("му", "ому"):
        return "m", DAT
    if s in ("м", "ом", "ым"):
        return "m", PRE if prev in ("в", "во", "на", "о", "об", "при") else INS if s != "ом" else PRE
    if s in ("х", "ых"):
        return "p", PRE if prev in ("в", "во", "на", "о", "об", "при") else GEN
    if s in ("ми", "ыми"):
        return "p", INS
    if s in ("ой", "ей"):
        return "f", INS
    return "m", NOM


def _read_one(int_s: str, frac: str | None, suf: str | None, ctx: dict) -> str:
    """Читает одно число (без диапазона). ctx: prev, nxt, unit, case, gender"""
    n = _clean_num(int_s)
    prev, nxt, case = ctx["prev"], ctx["nxt"], ctx["case"]
    if suf:
        g, c = _suffix_info(suf, prev, nxt)
        return ordinal(n, g, c)
    if frac is not None:
        return decimal_words(n, frac, case)
    return cardinal(n, ctx["gender"], case)


def _number_sub_factory(text_ref: list[str]):
    def sub(m: re.Match) -> str:
        s = text_ref[0]
        a, af, as_ = m.group("a"), m.group("af"), m.group("as_")
        b, bf, bs = m.group("b"), m.group("bf"), m.group("bs")
        sign = {"+": "плюс ", "-": "минус ", "−": "минус ", "–": "минус "}.get((m.group("sign") or " ")[0], "") if m.group("sign") else ""
        prev = _prev_word(s, m.start())
        end = m.end()
        pos = end
        unit_key = None
        unit_raw = ""

        # единица после числа
        um = _UNIT_RE.match(s, end)
        if um and re.match(r"\.[A-Za-zА-Яа-яЁё]", s[um.end():um.end() + 2]):
            um = None                      # «т.д.», «и т.п.» - это не единица
        if um and not as_ and not bs:
            k = _unit_key(um.group("u"))
            if k:
                unit_key = k
                unit_raw = um.group("u")
                pos = um.end()
                if pos < len(s) and s[pos] == "." and unit_raw[-1:].isalpha():
                    tail = s[pos + 1:].lstrip()
                    if tail and not tail[0].isupper():
                        pos += 1           # «5 тыс. руб.» - точка внутри сокращения
        nxt = _next_word(s, pos)
        nxt_l = nxt.lower().rstrip(".")
        last_n = _clean_num(b if b else a)
        has_dec = (bf if b else af) is not None
        a_int = _clean_num(a)

        def done(txt: str) -> tuple[str, int]:
            return sign + txt, pos

        # ------- год / дата -------
        def year_ordinal(n: int, case: int, force_gen=False) -> str:
            return ordinal(n, "m", GEN if force_gen else case)

        # диапазон лет «2010–2020 гг.»
        if b and not has_dec and not af and not as_ and not bs and 1000 <= a_int <= 2999 and 1000 <= last_n <= 2999 \
                and nxt_l in ("г", "гг", "годы", "года", "год", "годов", "годах"):
            if nxt_l in ("г", "гг"):
                pos = _after_word(s, pos)
            noun = "годы" if nxt_l in ("гг", "годы") else nxt
            if nxt_l in ("г", "гг"):
                noun = "годы"
            return _ret(m, sign + f"с {year_ordinal(a_int, GEN, True)} по {ordinal(last_n, 'm', ACC)} {noun}", pos)
        if b and not as_ and not bs and 1 <= a_int <= 31 and nxt_l in _MONTH_GEN and not has_dec and not af:
            pass  # «5–7 мая» обработается ниже как числа в род.п.

        # одиночное число: год
        if not b and not af and not as_ and 1000 <= a_int <= 2999 and not unit_key:
            yw = _YEAR_WORDS.get(nxt_l)
            after_month = bool(_MONTH_RE.match(prev)) if prev else False
            if yw is not None:
                case = yw
                if nxt_l == "году":
                    case = DAT if prev in ("к", "по") else PRE
                if nxt_l in ("г", "гг"):
                    c0 = _case_for(prev, nxt, True)
                    pos = _after_word(s, pos)
                    word = "год" if nxt_l == "г" else "годы"
                    if nxt_l == "г":
                        word = {NOM: "год", ACC: "год", GEN: "года", DAT: "году", INS: "годом", PRE: "году"}[c0]
                    return _ret(m, sign + f"{ordinal(a_int, 'm', c0)} {word}", pos)
                g = "p" if nxt_l in ("годы", "годов", "годам", "годами", "годах") else "m"
                return _ret(m, sign + ordinal(a_int, g, case), pos)
            if after_month or (prev == "с" and nxt_l == "по" and re.match(r"[ ]+по[ ]+\d{4}[ ]+год", s[pos:])):
                return _ret(m, sign + ordinal(a_int, "m", GEN), pos)

        # «21 века», «в 20 веке»
        if not b and not af and not as_ and 1 <= a_int <= 40 and nxt_l in _CENTURY and not unit_key:
            c0 = _CENTURY[nxt_l]
            return _ret(m, sign + ordinal(a_int, "m", c0 if c0 != ACC else NOM), pos)

        # день перед названием месяца: «5 мая», «с 1 января»
        range_to_month = bool(re.match(r"[ ]+(?:по|до)[ ]+\d{1,2}[ ]+(?:" + "|".join(_MONTH_GEN) + ")", s[pos:])) and prev in ("с", "со", "от")
        if range_to_month and not b and not af and not as_ and 1 <= a_int <= 31:
            return _ret(m, sign + ordinal(a_int, "n", GEN), pos)
        if not b and not af and not as_ and 1 <= a_int <= 31 and nxt_l in _MONTH_GEN and not unit_key:
            c0 = ACC if prev == "по" else _case_for(prev, nxt, True)
            return _ret(m, sign + ordinal(a_int, "n", c0), pos)

        # ------- порядковые с окончанием -------
        if as_ or bs:
            ctx = {"prev": prev, "nxt": nxt, "case": NOM, "gender": "m"}
            if b:
                t1 = _read_one(a, af, as_, ctx) if as_ else cardinal(a_int)
                t2 = _read_one(b, bf, bs, {**ctx, "prev": ""}) if bs else cardinal(last_n)
                return _ret(m, sign + f"{t1}{' — ' if False else ' '}{t2}", pos)
            return _ret(m, sign + _read_one(a, af, as_, ctx), pos)

        # ------- обычные числа, дроби, диапазоны, единицы -------
        case = _case_for(prev, nxt)
        if unit_key:
            uf = D.UNITS[unit_key]
            gender = uf[0]
        else:
            gender = _gender_next(nxt, case, last_n % 10) if (last_n % 10 in (1, 2) and last_n % 100 not in (11, 12)) else "m"
            uf = None

        def unit_after(n_val: int, dec: bool, c: int) -> str:
            if not uf:
                return ""
            w = noun_form(uf, n_val, c, force_gen_sg=dec)
            extra = ""
            if unit_key in _SCALE_UNITS:
                m2 = re.match(r"[  ]?([$€£₽])", s[pos:])
                if m2:
                    extra = " " + _CUR_SYMBOLS[m2.group(1)]
                    nonlocal_pos[0] = pos + m2.end()
            return " " + w + extra

        nonlocal_pos = [pos]

        def render(num_s, frac_s, c) -> str:
            n_val = _clean_num(num_s)
            if frac_s is not None:
                return decimal_words(n_val, frac_s, c)
            return cardinal(n_val, gender, c)

        if b:
            a_val = a_int
            if prev and prev in PREP_CASE and prev not in ("от", "до", "с", "со") :
                # «в 5-10 раз»: читаем «пять-десять»
                txt = f"{render(a, af, case)}-{render(b, bf, case)}"
                txt += unit_after(last_n, has_dec, case)
            elif a_val < last_n or has_dec or af:
                txt = f"от {render(a, af, GEN)} до {render(b, bf, GEN)}"
                txt += unit_after(last_n, has_dec, GEN)
            else:
                txt = f"{render(a, af, NOM)} {render(b, bf, NOM)}"
                txt += unit_after(last_n, has_dec, NOM)
            return _ret(m, sign + txt, nonlocal_pos[0])
        txt = render(a, af, case) + unit_after(a_int, af is not None, case)
        return _ret(m, sign + txt, nonlocal_pos[0])

    return sub


def _after_word(s: str, pos: int) -> int:
    m = re.match(r"[\s ]*[A-Za-zА-Яа-яЁё]+\.?", s[pos:])
    return pos + m.end() if m else pos


def _ret(m: re.Match, txt: str, new_end: int):
    return txt, new_end


def _numbers(text: str) -> str:
    """Проход по всем числам: замена идёт вручную, чтобы можно было съедать единицу после числа."""
    out = []
    i = 0
    ref = [text]
    fn = _number_sub_factory(ref)
    while True:
        m = _NUMBER.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        try:
            res = fn(m)
            txt, end = res if isinstance(res, tuple) else (res, m.end())
        except Exception as e:  # noqa: BLE001
            log.warning("не смог прочитать число %r: %s", m.group(0), e)
            txt, end = m.group(0), m.end()
        out.append(txt)
        i = max(end, m.end())
    return "".join(out)


# ---------- прочие шаблоны ----------
def _clean_ver(x: str) -> str:
    return cardinal(int(x))


def _digits_words(s: str) -> str:
    return " ".join("ноль" if c == "0" else _ONES[int(c)][0] for c in s)


def _pair(s: str) -> str:
    return ("ноль " + cardinal(int(s))) if s.startswith("0") and len(s) > 1 and int(s) else cardinal(int(s)) if int(s) else "ноль ноль"


def _phones(text: str) -> str:
    def sub(m: re.Match) -> str:
        d = re.sub(r"\D", "", m.group(0))
        if len(d) != 11:
            return m.group(0)
        head = "плюс семь" if m.group(0).lstrip().startswith("+7") else "восемь"
        g1, g2, g3, g4 = d[1:4], d[4:7], d[7:9], d[9:11]
        parts = [cardinal(int(g1)), cardinal(int(g2)) if not g2.startswith("0") else _digits_words(g2), _pair(g3), _pair(g4)]
        return head + ", " + ", ".join(parts)
    return re.sub(r"(?<![\d\w])(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?![\d\w])", sub, text)


def _dates(text: str) -> str:
    def build(d: int, mo: int, y: int | None, s: str, pos: int, end: int) -> str:
        prev = _prev_word(s, pos)
        c = _case_for(prev, "", True)
        if c == PRE:
            c = ACC
        t = f"{ordinal(d, 'n', c)} {D.MONTHS_GEN[mo - 1]}"
        if y is not None:
            t += f" {ordinal(y, 'm', GEN)} года"
        return t

    def num(m: re.Match) -> str:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            return m.group(0)
        yy = int(y)
        if len(y) == 2:
            yy = 2000 + yy if yy <= 40 else 1900 + yy
        return build(d, mo, yy, m.string, m.start(), m.end())

    def iso(m: re.Match) -> str:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            return m.group(0)
        return build(d, mo, y, m.string, m.start(), m.end())

    text = re.sub(r"(?<![\d.:/-])(\d{4})-(\d{2})-(\d{2})(?![\d-])", iso, text)
    return re.sub(r"(?<![\d.:/])(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?![\d./])", num, text)


_RATIO_WORDS = {"соотношение", "соотношении", "пропорции", "пропорция", "формат", "формате", "соотношением", "разрешение", "формата"}


def _time_score(text: str) -> str:
    def t(m: re.Match) -> str:
        a, b, c = m.group(1), m.group(2), m.group(3)
        prev = _prev_word(m.string, m.start())
        if len(b) == 2 and int(a) <= 23 and int(b) <= 59 and (c is None or (len(c) == 2 and int(c) <= 59)) and prev not in _RATIO_WORDS:
            parts = [cardinal(int(a)), "ноль ноль" if b == "00" else _pair(b)]
            if c is not None:
                parts.append("ноль ноль" if c == "00" else _pair(c))
            return " ".join(parts)
        if c is not None:
            return m.group(0)
        if prev in _RATIO_WORDS:
            return f"{cardinal(int(a))} к {cardinal(int(b), 'm', DAT)}"
        return f"{cardinal(int(a))} {cardinal(int(b))}"
    return re.sub(r"(?<![\d:.])(\d{1,3}):(\d{1,2})(?::(\d{2}))?(?![\d:])", t, text)


def _dims(text: str) -> str:
    return re.sub(r"(?<![\w.,])(\d+)\s?[xXхХ×]\s?(\d+)(?![\w])", lambda m: f"{cardinal(int(m.group(1)))} на {cardinal(int(m.group(2)))}", text)


def _fractions(text: str) -> str:
    def f(m: re.Match) -> str:
        a, b = int(m.group(1)), int(m.group(2))
        if b == 0 or b > 20 or a > 99:
            return m.group(0)
        if b == 2:
            return "половина" if a == 1 else f"{cardinal(a, 'f')} вторых"
        if b == 3:
            return "одна треть" if a == 1 else f"{cardinal(a, 'f')} {'трети' if a % 10 in (2, 3, 4) else 'третей'}"
        if b == 4:
            return "одна четверть" if a == 1 else f"{cardinal(a, 'f')} {'четверти' if a % 10 in (2, 3, 4) else 'четвертей'}"
        den = ordinal(b, "f", NOM) if a % 10 == 1 and a != 11 else ordinal(b, "p", GEN)
        return f"{cardinal(a, 'f')} {den}"
    return re.sub(r"(?<![\w./])(\d{1,2})/(\d{1,2})(?![\w./])", f, text)


_COMPOUND = re.compile(
    r"(?<![\w.,])(\d{1,3})-((?:лет|минут|секунд|час|дн|недел|месяч|этаж|крат|мест|килограмм|километр|метр|процент|градус|тысяч|миллион|"
    r"доллар|рубл|грамм|сантиметр|литр)[а-яё]*)\b", re.I)


def _compound(text: str) -> str:
    def f(m: re.Match) -> str:
        n = int(m.group(1))
        h, r = divmod(n, 100)
        pre = ""
        if h:
            pre += _PRE[100] if h == 1 else _PRE[h] + "сот"
        if r in _PRE:
            pre += _PRE[r]
        elif r == 1:
            pre += "одно"
        elif r:
            t, u = divmod(r, 10)
            pre += _PRE[t * 10] + _PRE[u]
        elif not h:
            pre = "нуле"
        return pre + m.group(2)
    return _COMPOUND.sub(f, text)


# ---------- римские цифры ----------
_ROMAN_OK = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_LOOK = str.maketrans({"Х": "X", "І": "I", "М": "M", "С": "C", "Д": "D", "Л": "L", "х": "x", "і": "i"})
_ROMAN_TOK = r"[IVXLCDMХІМСДЛ]{1,8}"
_CENTURY = {"век": NOM, "века": GEN, "веку": DAT, "веком": INS, "веке": PRE, "веков": GEN, "векам": DAT, "веками": INS, "веках": PRE, "веа": NOM}


def _roman_val(tok: str) -> int | None:
    t = tok.translate(_LOOK).upper()
    if not t or not _ROMAN_OK.match(t):
        return None
    v = 0
    for i, ch in enumerate(t):
        x = D.ROMAN[ch]
        v += -x if i + 1 < len(t) and D.ROMAN[t[i + 1]] > x else x
    return v


def _romans(text: str) -> str:
    # века: «XXI век», «XIX–XX вв.», «в XXI веке»
    def century(m: re.Match) -> str:
        toks = [t for t in re.findall(_ROMAN_TOK, m.group("r")) if t]
        vals = [_roman_val(t) for t in toks]
        if not vals or any(v is None or v <= 0 for v in vals):
            return m.group(0)
        w = m.group("w").lower().rstrip(".")
        prev = _prev_word(m.string, m.start())
        if w in ("в", "вв"):
            case = _case_for(prev, "", True)
            if case == PRE:
                case = PRE
            noun = ("века" if w == "вв" else "век") if case in (NOM, ACC) else {GEN: "века", DAT: "веку", INS: "веком", PRE: "веке"}[case]
            if w == "вв" and case in (GEN, DAT, INS, PRE):
                noun = {GEN: "веков", DAT: "векам", INS: "веками", PRE: "веках"}[case]
            sep = " и "
        else:
            case = _CENTURY[w]
            noun = m.group("w").rstrip(".")
            sep = " и "
        g = "p" if (w == "вв" or noun.endswith(("ов", "ам", "ами", "ах", "века")) and len(vals) > 1) else "m"
        c2 = case if case != ACC else NOM
        words = sep.join(ordinal(v, "m", c2) if len(vals) > 1 or g == "m" else ordinal(v, "m", c2) for v in vals)
        return f"{words} {noun}"

    pat = re.compile(rf"(?<![\w])(?P<r>{_ROMAN_TOK}(?:\s?[–—-]\s?{_ROMAN_TOK})?)\s(?P<w>век|века|веку|веком|веке|веков|векам|веками|веках|вв?\.)(?![\w])")
    text = pat.sub(century, text)

    # имена монархов: «Николай II», «при Людовике XIV»
    def name(m: re.Match) -> str:
        v = _roman_val(m.group("r"))
        if not v or v > 40:
            return m.group(0)
        nm = m.group("n")
        low = nm.lower()
        if low.endswith(("ом", "ем", "ём")):
            c = INS
        elif low.endswith(("у", "ю")):
            c = DAT
        elif low.endswith("е") and not low.endswith(("ие", "ье")):
            c = PRE
        elif low.endswith("а") or low.endswith("я"):
            c = GEN
        else:
            c = NOM
        return f"{nm} {ordinal(v, 'm', c)}"

    text = re.sub(rf"(?<![\w])(?P<n>[А-ЯЁ][а-яё]{{2,}})\s(?P<r>{_ROMAN_TOK})(?![\w])", name, text)

    # «Глава IV», «Часть II» - количественное
    def part(m: re.Match) -> str:
        v = _roman_val(m.group("r"))
        if not v or v > 100:
            return m.group(0)
        w = m.group("w")
        g = "f" if w.lower() in ("глава", "часть", "серия", "фаза", "ступень", "лига", "группа", "стадия", "категория") else "m"
        return f"{w} {cardinal(v, g)}"
    return re.sub(rf"(?<![\w])(?P<w>[Гг]лава|[Чч]асть|[Тт]ом|[Рр]аздел|[Сс]езон|[Ээ]пизод|[Уу]ровень|[Ээ]тап|[Фф]аза|[Аа]кт|[Сс]ерия)\s(?P<r>{_ROMAN_TOK})(?![\w])", part, text)


# ---------- латиница ----------
_LAT_LETTER = {"a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай", "j": "джей", "k": "кей",
               "l": "эл", "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар", "s": "эс", "t": "ти", "u": "ю", "v": "ви",
               "w": "дабл-ю", "x": "икс", "y": "уай", "z": "зет"}
_LAT_TOKEN = re.compile(r"(?<![\w])(?:[A-Za-z][A-Za-z0-9'’]*(?:[-.][A-Za-z0-9]+)*|\d+[A-Za-z]+)(?![A-Za-z0-9])")


def _latin(text: str) -> str:
    lat = {**{k: v for k, v in D.WORDS.items()}, **{k: v for k, v in D.LATIN_ABBR.items()}}
    lat = {k.lower(): v for k, v in lat.items()}

    def look(tok: str) -> str | None:
        t = tok.lower()
        if t in lat:
            return lat[t]
        for suf in ("'s", "’s"):
            if t.endswith(suf) and t[:-2] in lat:
                return lat[t[:-2]]
        return None

    def sub(m: re.Match) -> str:
        tok = m.group(0)
        r = look(tok)
        if r is not None:
            return r
        if "-" in tok or "." in tok:
            parts = re.split(r"([-.])", tok)
            res, hit = [], False
            for p in parts:
                if p in ("-", "."):
                    res.append(" " if p == "-" else p)
                    continue
                r2 = look(p)
                hit |= r2 is not None
                res.append(r2 if r2 is not None else p)
            if hit:
                return "".join(res)
        if re.fullmatch(r"[A-Za-z]\d{1,3}", tok):
            return f"{_LAT_LETTER[tok[0].lower()]} {tok[1:]}"
        if re.fullmatch(r"[A-Za-z]{1,2}", tok) and tok.isupper() and len(tok) == 2:
            return "-".join(_LAT_LETTER[c.lower()] for c in tok)
        return tok

    return _LAT_TOKEN.sub(sub, text)


# ---------- КАПС-аббревиатуры ----------
_RU_LETTER = {"А": "а", "Б": "бэ", "В": "вэ", "Г": "гэ", "Д": "дэ", "Е": "е", "Ё": "ё", "Ж": "жэ", "З": "зэ", "И": "и", "Й": "и-краткое",
              "К": "ка", "Л": "эль", "М": "эм", "Н": "эн", "О": "о", "П": "пэ", "Р": "эр", "С": "эс", "Т": "тэ", "У": "у", "Ф": "эф",
              "Х": "ха", "Ц": "цэ", "Ч": "че", "Ш": "ша", "Щ": "ща", "Ы": "ы", "Ь": "мягкий-знак", "Ъ": "твёрдый-знак", "Э": "э",
              "Ю": "ю", "Я": "я"}
_CAPS = re.compile(r"(?<![\w-])[А-ЯЁ]{2,8}(?![\w])")
_VOWELS = set("АЕЁИОУЫЭЮЯ")


def _caps(text: str) -> str:
    def is_caps(w: str) -> bool:
        return len(w) >= 2 and w.isupper() and re.fullmatch(r"[А-ЯЁ]+", w) is not None

    def sub(m: re.Match) -> str:
        w = m.group(0)
        s = m.string
        if w in D.ABBR:
            if w in D.CONTEXT_ABBR:
                pw = re.search(r"([А-ЯЁа-яё]+)\W*$", s[:m.start()])
                nw = re.match(r"\W*([А-ЯЁа-яё]+)", s[m.end():])
                if (pw and is_caps(pw.group(1))) or (nw and is_caps(nw.group(1))):
                    return w
            return D.ABBR[w]
        if not any(c in _VOWELS for c in w) and len(w) <= 5:
            return "-".join(_RU_LETTER[c] for c in w)
        return w

    return _CAPS.sub(sub, text)


# ============================================================ главная функция ============================================================
def lang_of(text: str, voice: str | None = None) -> str | None:
    """'ru', 'en' или None (другой язык - словарь не трогает текст).
    Кириллический текст всегда читается по русским правилам: Edge-TTS всё равно подменит нерусский голос на русский
    (tts._match_voice), поэтому по голосу язык определяем только для нерусского текста."""
    letters = re.findall(r"[^\W\d_]", text or "", re.UNICODE)
    if not letters:
        return None
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04ff")
    if cyr / len(letters) >= 0.5:
        return "ru"
    v = voice or ""
    pref = v[:2].lower()
    lat = sum(1 for c in letters if c.isascii())
    latin_text = lat / len(letters) >= 0.8
    if v[2:3] == "-" and pref.isalpha():
        if pref == "ru" and latin_text:
            return "en"          # английский текст под русским голосом читаем по английским правилам («3rd», «$5k»)
        return pref if pref in ("ru", "en") else None
    return "en" if latin_text else None


def apply(text: str, lang: str = "ru") -> str:
    """Весь конвейер подмен (без проверки языка)."""
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace(" ", " ").replace(" ", " ")
    t = _apply_user(t, lang)
    if lang == "en":
        from core import pronounce_en
        return pronounce_en.apply(t)
    for pat, rep in D.PHRASES:
        t = re.sub(pat, rep, t, flags=re.I)
    t = _phones(t)
    t = _dates(t)
    t = re.sub(r"(?<![\w.])\d+(?:\.\d+){2,}(?![\w.])", lambda m: " точка ".join(_clean_ver(x) for x in m.group(0).split(".")), t)
    t = _romans(t)
    t = _time_score(t)
    t = _dims(t)
    t = _fractions(t)
    t = _compound(t)
    # «$100», «€2,5 млн» -> «100 $», «2,5 млн €»
    t = re.sub(rf"([$€£₽])\s?({_N})(?:([.,]\d+))?((?:\s?(?:тыс|млн|млрд|трлн)\.?)?)",
               lambda m: f"{m.group(2)}{m.group(3) or ''}{m.group(4)} {m.group(1)}", t)
    t = _latin(t)
    t = _numbers(t)
    for pat, rep in D.PHRASES_LATE:
        # точка в конце предложения («5 тыс. руб. Курс…») остаётся
        t = re.sub(pat, lambda m, r=rep: r + ("." if m.group(0).endswith(".") and re.match(r"\s*(?:[А-ЯЁA-Z]|$)", m.string[m.end():]) else ""),
                   t, flags=re.I)
    t = _caps(t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    return t.strip()


def prepare(text: str, voice: str | None = None) -> str:
    """Подготовка текста для синтезатора. Для не-русских голосов и при PRONOUNCE=0 возвращает текст как есть."""
    if os.getenv("PRONOUNCE", "1") == "0" or not text:
        return text
    try:
        lang = lang_of(text, voice)
        if lang is None:
            return text
        out = apply(text, lang)
        mode = os.getenv("PRONOUNCE_ACCENT", "keep")
        if mode == "strip":
            out = out.replace("́", "")
        elif mode == "plus":
            out = re.sub("([аеёиоуыэюя])́", r"+\1", out, flags=re.I)
        if out != text:
            log.info("озвучка: «%s» -> «%s»", text[:60].replace("\n", " "), out[:60].replace("\n", " "))
        return out
    except Exception as e:  # noqa: BLE001 - словарь не должен ронять озвучку
        log.warning("словарь произношения не сработал (%s), читаю как есть", e)
        return text
