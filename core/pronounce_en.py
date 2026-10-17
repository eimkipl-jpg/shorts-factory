"""Английская часть словаря произношения: числа, годы, даты, деньги, единицы, сокращения, бренды, кириллица -> латиница.
Вызывается из core/pronounce.py для английских голосов. Данные: core/pronounce_en_data.py.
"""
from __future__ import annotations

import re

from core import pronounce_en_data as E

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
         "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = ["", "thousand", "million", "billion", "trillion"]
_ORD_IRR = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_MONTH_ABBR = {m[:3].lower(): m for m in MONTHS} | {"sept": "September"}
_MONTH_NAMES = {m.lower() for m in MONTHS} | set(_MONTH_ABBR)


def _below_1000(n: int) -> str:
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(_ONES[h] + " hundred")
    if r:
        if r < 20:
            parts.append(_ONES[r])
        else:
            t, u = divmod(r, 10)
            parts.append(_TENS[t] + ("-" + _ONES[u] if u else ""))
    return " ".join(parts)


def cardinal(n: int) -> str:
    if n < 0:
        return "minus " + cardinal(-n)
    if n == 0:
        return "zero"
    if n >= 10 ** 15:
        return " ".join(_ONES[int(c)] for c in str(n))
    out = []
    i = 0
    while n:
        n, g = divmod(n, 1000)
        if g:
            out.append(_below_1000(g) + (" " + _SCALES[i] if i else ""))
        i += 1
    return " ".join(reversed(out))


def ordinal_word(w: str) -> str:
    head, sep, last = re.split(r"([ -])(?=[^ -]+$)", w, maxsplit=1) if re.search(r"[ -]", w) else ("", "", w)
    if last in _ORD_IRR:
        last = _ORD_IRR[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + sep + last


def ordinal(n: int) -> str:
    return ordinal_word(cardinal(n))


def plural(w: str) -> str:
    return w[:-1] + "ies" if w.endswith("y") else w + "s"


def year_words(n: int) -> str:
    if n == 2000:
        return "two thousand"
    if 2001 <= n <= 2009:
        return "two thousand " + _ONES[n - 2000]
    h, p = divmod(n, 100)
    if p == 0:
        return cardinal(h) + " hundred"
    return cardinal(h) + " " + ("oh " + _ONES[p] if p < 10 else cardinal(p))


def digits_words(s: str) -> str:
    return " ".join(_ONES[int(c)] for c in s if c.isdigit())


def decimal_words(int_s: str, frac: str) -> str:
    return f"{cardinal(int(int_s.replace(',', '')))} point {digits_words(frac)}"


def spell(w: str) -> str:
    return " ".join(w)


# ---------- контекст ----------
_YEAR_PREV = {"in", "since", "by", "until", "till", "from", "of", "year", "during", "before", "after", "circa", "through", "early", "late", "mid"}
_WORD_PREV = re.compile(r"([A-Za-z]+)[\s\"'(]*$")
_WORD_NEXT = re.compile(r"[\s]*([A-Za-z]+)")
_NUMBER_NOUNS_PLURAL = True


def _prev(s: str, pos: int) -> str:
    m = _WORD_PREV.search(s[:pos])
    return m.group(1).lower() if m else ""


def _next(s: str, pos: int) -> str:
    m = _WORD_NEXT.match(s, pos)
    return m.group(1) if m else ""


def _is_year_ctx(s: str, start: int, nxt: str) -> bool:
    prev = _prev(s, start)
    low = nxt.lower()
    if low in _MONTH_NAMES:
        return True
    if prev in _MONTH_NAMES or re.search(r"(?:%s)\w*\.?\s+\d{1,2}(?:st|nd|rd|th)?,\s*$" % "|".join(m[:3] for m in MONTHS), s[:start], re.I):
        return True
    if re.search(r"(?:from|between)\s+(?:\w+\s+)?\d{4}\s+(?:to|and)\s*$", s[:start], re.I):
        return True
    if prev in _YEAR_PREV:
        return not (low.endswith("s") and low not in ("ad", "bc") and low not in _MONTH_NAMES) or low in ("s",)
    return False


# ---------- единицы и деньги ----------
_CUR = {"$": ("dollar", "dollars", "cent", "cents"), "€": ("euro", "euros", "cent", "cents"), "£": ("pound", "pounds", "penny", "pence"),
        "¥": ("yen", "yen", "sen", "sen"), "₽": ("ruble", "rubles", "kopek", "kopeks")}
_CUR_CODE = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "RUB": "₽"}
_SCALE_SUF = {"k": "thousand", "K": "thousand", "M": "million", "B": "billion", "T": "trillion", "bn": "billion", "mn": "million",
              "thousand": "thousand", "million": "million", "billion": "billion", "trillion": "trillion"}
_N = r"\d{1,3}(?:,\d{3})+|\d+"


def _money(m: re.Match) -> str:
    sym = m.group("c")
    a, f = m.group("n"), m.group("f")
    sc = (m.group("s") or "").strip()
    one, many, c1, cn = _CUR[sym]
    n = int(a.replace(",", ""))
    if sc:
        num = decimal_words(a, f) if f else cardinal(n)
        return f"{num} {_SCALE_SUF[sc]} {many}"
    if f is None:
        return f"{cardinal(n)} {one if n == 1 else many}"
    if len(f) == 2:
        cents = int(f)
        parts = []
        if n or not cents:
            parts.append(f"{cardinal(n)} {one if n == 1 else many}")
        if cents:
            parts.append(cardinal(cents) if n else f"{cardinal(cents)} {c1 if cents == 1 else cn}")
        return " ".join(parts)
    return f"{decimal_words(a, f)} {many}"


# ---------- главный числовой проход ----------
_NUM = re.compile(
    rf"(?<![\w.,/$€£¥₽])(?P<sign>[-−–+](?=\d))?(?P<a>{_N})(?:\.(?P<af>\d+)(?!\d))?"
    rf"(?:(?P<ord>(?:st|nd|rd|th)\b)|(?P<dec>s\b)|(?P<sc>(?:bn|mn|[kKMBT])(?![A-Za-z])))?"
    rf"(?:(?P<dash>[–—]|-)(?P<b>{_N})(?:\.(?P<bf>\d+)(?!\d))?(?P<bord>(?:st|nd|rd|th)\b)?)?"
)
_UNIT_RE = re.compile(r"[ ]?(?P<u>%|°[CF]|°|km/h|m/s|\$|€|£|₽|[A-Za-z]+)(?![A-Za-z])")
_EXTRA_UNITS = {"USD": ("dollar", "dollars"), "EUR": ("euro", "euros"), "GBP": ("pound", "pounds"), "RUB": ("ruble", "rubles"),
                "$": ("dollar", "dollars"), "€": ("euro", "euros"), "£": ("pound", "pounds"), "₽": ("ruble", "rubles")}


def _unit_forms(u: str):
    if u == "%":
        return ("percent", "percent")
    if u == "°C":
        return ("degree Celsius", "degrees Celsius")
    if u == "°F":
        return ("degree Fahrenheit", "degrees Fahrenheit")
    if u == "°":
        return ("degree", "degrees")
    return E.UNITS.get(u) or _EXTRA_UNITS.get(u)


def _read(s: str, m: re.Match) -> tuple[str, int]:
    a, af, b, bf = m.group("a"), m.group("af"), m.group("b"), m.group("bf")
    sign = {"-": "minus ", "−": "minus ", "–": "minus ", "+": "plus "}.get(m.group("sign") or "", "")
    end = m.end()
    nxt = _next(s, end)
    a_int = int(a.replace(",", ""))
    b_int = int(b.replace(",", "")) if b else None
    sc = m.group("sc")

    # порядковые: 21st, 3rd
    if m.group("ord"):
        if b and m.group("bord"):
            return f"{sign}{ordinal(a_int)} {ordinal(b_int)}", end
        return f"{sign}{ordinal(a_int)}", end
    # десятилетия: 1990s, 90s, 2000s
    if m.group("dec") and not af and not b:
        if len(a) == 4 and a.endswith("0") and 1100 <= a_int <= 2090:
            h, p = divmod(a_int, 100)
            if p == 0:
                base = ("two thousand" if a_int == 2000 else cardinal(h) + " hundred")
                return sign + plural(base), end
            return f"{sign}{cardinal(h)} {plural(cardinal(p))}", end
        if len(a) == 2 and a.endswith("0"):
            return sign + plural(cardinal(a_int)), end
        return sign + plural(cardinal(a_int)), end
    # масштаб: 5k, 2M, 1.5B
    if sc and not b:
        num = decimal_words(a, af) if af else cardinal(a_int)
        return f"{sign}{num} {_SCALE_SUF[sc]}", end

    # диапазон лет и обычных чисел
    if b:
        yr = lambda v: 1100 <= v <= 2099
        if yr(a_int) and yr(b_int) and not af and not bf and len(a) == 4 and len(b) == 4:
            return f"{sign}{year_words(a_int)} to {year_words(b_int)}", end
        left = decimal_words(a, af) if af else cardinal(a_int)
        right = decimal_words(b, bf) if bf else cardinal(b_int)
        um = _UNIT_RE.match(s, end)
        tail = ""
        if um and _unit_forms(um.group("u")):
            sg, pl = _unit_forms(um.group("u"))
            tail = " " + (sg if (b_int == 1 and not bf) else pl)
            end = um.end()
        return f"{sign}{left} to {right}{tail}", end

    # единица после числа
    um = _UNIT_RE.match(s, end)
    if um:
        forms = _unit_forms(um.group("u"))
        if forms:
            sg, pl = forms
            num = decimal_words(a, af) if af else cardinal(a_int)
            return f"{sign}{num} {sg if (a_int == 1 and not af) else pl}", um.end()

    # даты: «May 5», «5 May»
    if not af and 1 <= a_int <= 31:
        prev = _prev(s, m.start())
        if prev in _MONTH_NAMES:
            return f"{sign}{ordinal(a_int)}", end
        if nxt.lower() in _MONTH_NAMES:
            return f"{sign}the {ordinal(a_int)} of", end
    # годы
    if not af and len(a) == 4 and 1100 <= a_int <= 2099 and _is_year_ctx(s, m.start(), nxt):
        return f"{sign}{year_words(a_int)}", end
    if af:
        return f"{sign}{decimal_words(a, af)}", end
    if len(a) > 15:
        return f"{sign}{digits_words(a)}", end
    return f"{sign}{cardinal(a_int)}", end


def _numbers(text: str) -> str:
    out, i = [], 0
    while True:
        m = _NUM.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        try:
            txt, end = _read(text, m)
        except Exception:  # noqa: BLE001
            txt, end = m.group(0), m.end()
        out.append(txt)
        i = max(end, m.end())
    return "".join(out)


# ---------- прочие шаблоны ----------
def _times(text: str) -> str:
    def ampm(m: re.Match) -> str:
        h, mi, ap = int(m.group(1)), m.group(2), m.group(3).upper()
        if not (1 <= h <= 12) or (mi and int(mi) > 59):
            return m.group(0)
        t = cardinal(h)
        if mi and int(mi):
            t += " " + ("oh " + _ONES[int(mi)] if int(mi) < 10 else cardinal(int(mi)))
        elif not mi:
            t += ""
        else:
            t += " o'clock" if False else ""
        return f"{t} {ap[0]} M"

    text = re.sub(r"(?<![\d:])(\d{1,2})(?::(\d{2}))?\s?([AaPp])\.?[Mm]\b\.?", ampm, text)

    def hm(m: re.Match) -> str:
        h, mi = int(m.group(1)), int(m.group(2))
        prev = _prev(m.string, m.start())
        if h <= 23 and mi <= 59 and m.group(3) is None and prev not in ("ratio", "aspect", "format", "score", "odds"):
            if mi == 0:
                return f"{cardinal(h)} o'clock" if 1 <= h <= 12 else f"{cardinal(h)} hundred" if h else "midnight"
            return f"{cardinal(h)} " + ("oh " + _ONES[mi] if mi < 10 else cardinal(mi))
        if m.group(3) is not None:
            return m.group(0)
        if prev in ("ratio", "aspect", "format"):
            return f"{cardinal(h)} by {cardinal(mi)}"
        return f"{cardinal(h)} to {cardinal(mi)}"

    return re.sub(r"(?<![\d:.])(\d{1,3}):(\d{1,2})(?::(\d{2}))?(?![\d:])", hm, text)


def _iso_dates(text: str) -> str:
    def f(m: re.Match) -> str:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return m.group(0)
        return f"{MONTHS[mo - 1]} {ordinal(d)}, {year_words(y)}"
    return re.sub(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])", f, text)


def _phones(text: str) -> str:
    def f(m: re.Match) -> str:
        d = re.sub(r"\D", "", m.group(0))
        if len(d) not in (10, 11):
            return m.group(0)
        if len(d) == 11:
            d = d[1:]
        return f"{digits_words(d[:3])}, {digits_words(d[3:6])}, {digits_words(d[6:])}"
    return re.sub(r"(?<![\d\w])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\d\w])", f, text)


def _fractions(text: str) -> str:
    names = {(1, 2): "one half", (1, 3): "one third", (2, 3): "two thirds", (1, 4): "one quarter", (3, 4): "three quarters",
             (1, 5): "one fifth", (1, 8): "one eighth", (1, 10): "one tenth"}
    return re.sub(r"(?<![\w./])(\d{1,2})/(\d{1,2})(?![\w./])",
                  lambda m: names.get((int(m.group(1)), int(m.group(2))), m.group(0)), text)


def _dims(text: str) -> str:
    return re.sub(r"(?<![\w.,])(\d+)\s?[x×]\s?(\d+)(?![\w])", lambda m: f"{cardinal(int(m.group(1)))} by {cardinal(int(m.group(2)))}", text)


def _money_pass(text: str) -> str:
    text = re.sub(rf"(?P<c>[$€£¥₽])\s?(?P<n>{_N})(?:\.(?P<f>\d+))?(?P<s>\s?(?:thousand|million|billion|trillion|bn|mn|[kKMBT])(?![A-Za-z]))?", _money, text)
    return re.sub(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s?(USD|EUR|GBP|JPY|RUB)\b",
                  lambda m: _money(re.match(rf"(?P<c>[$€£¥₽])\s?(?P<n>{_N})(?:\.(?P<f>\d+))?(?P<s>)", _CUR_CODE[m.group(2)] + m.group(1))) if re.fullmatch(r"\d[\d,]*(\.\d+)?", m.group(1)) else m.group(0), text)


# ---------- римские цифры ----------
_ROMAN_OK = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_RV = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_KINGS = "Henry|Louis|Charles|Edward|George|William|Elizabeth|Richard|James|John|Peter|Nicholas|Alexander|Ivan|Catherine|Pius|Benedict|Francis|Paul|Leo|Gregory|Napoleon|Philip|Frederick|Victor|Ramses|Ramesses|Tutankhamun|Darius"
_KW = "chapter|part|act|volume|vol|level|stage|phase|season|episode|war|book|section|article|appendix|class|tier|generation|step|round|week|day"


def _rom(tok: str) -> int | None:
    if not tok or not _ROMAN_OK.match(tok):
        return None
    v = 0
    for i, ch in enumerate(tok):
        x = _RV[ch]
        v += -x if i + 1 < len(tok) and _RV[tok[i + 1]] > x else x
    return v


def _romans(text: str) -> str:
    def cen(m: re.Match) -> str:
        v = _rom(m.group(1))
        return f"{ordinal(v)} century" if v else m.group(0)
    text = re.sub(r"(?<![\w])([IVXLCDM]{1,6})(?:th|st|nd|rd)?\s+[Cc]entury\b", cen, text)

    def king(m: re.Match) -> str:
        v = _rom(m.group(2))
        return f"{m.group(1)} the {ordinal(v)}" if v and v <= 40 else m.group(0)
    text = re.sub(rf"\b({_KINGS})\s+([IVXLCDM]{{1,6}})\b(?!\w)", king, text)

    def kw(m: re.Match) -> str:
        v = _rom(m.group(2))
        return f"{m.group(1)} {cardinal(v)}" if v and v <= 100 else m.group(0)
    return re.sub(rf"\b((?:{_KW}))\s+([IVXLCDM]{{1,6}})\b(?!\w)", kw, text, flags=re.I) if False else \
        re.sub(rf"(?i:\b({_KW}))\s+([IVXLCDM]{{1,6}})\b(?!\w)", kw, text)


# ---------- латиница: слова и бренды ----------
_TOKEN = re.compile(r"(?<![\w])(?:[A-Za-z][A-Za-z0-9'’]*(?:[-.][A-Za-z0-9]+)*|\d+[A-Za-z]+)(?![A-Za-z0-9])")


def _words(text: str) -> str:
    table = {k.lower(): v for k, v in E.WORDS.items()}
    # многословные ключи и ключи со знаками - обычной подстановкой
    for k in sorted([k for k in table if re.search(r"[^a-z0-9'’\-]", k)], key=len, reverse=True):
        text = re.sub(r"(?<![\w])" + re.escape(k) + r"(?![\w])", lambda m, v=table[k]: v, text, flags=re.I)

    def look(t: str):
        t = t.lower()
        if t in table:
            return table[t]
        if t.endswith(("'s", "’s")) and t[:-2] in table:
            return table[t[:-2]] + "'s"
        if t.endswith("s") and t[:-1] in table and len(t) > 3:
            return table[t[:-1]] + "s"
        return None

    def sub(m: re.Match) -> str:
        tok = m.group(0)
        r = look(tok)
        if r is not None:
            return r
        if "-" in tok or "." in tok:
            parts = re.split(r"([-.])", tok)
            hit, res = False, []
            for p in parts:
                if p in "-.":
                    res.append(" " if p == "-" else p)
                    continue
                r2 = look(p)
                hit |= r2 is not None
                res.append(r2 if r2 is not None else p)
            if hit:
                return "".join(res)
        return tok
    return _TOKEN.sub(sub, text)


_CAPS = re.compile(r"(?<![\w'-])[A-Z]{2,6}(?![\w'])")


def _caps(text: str) -> str:
    def sub(m: re.Match) -> str:
        w = m.group(0)
        if w in E.SPELLED:
            return spell(w)
        if w in E.WORDLIKE:
            return w
        if not any(c in "AEIOUY" for c in w):
            return spell(w)
        return w
    return _CAPS.sub(sub, text)


# ---------- кириллица в английском тексте ----------
_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l",
       "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
       "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}


def translit(text: str) -> str:
    def word(m: re.Match) -> str:
        w = m.group(0)
        out = "".join(_TR.get(c.lower(), c) for c in w)
        if w.isupper() and len(w) > 1:
            return out.upper()
        return out.capitalize() if w[0].isupper() else out
    return re.sub(r"[А-Яа-яЁё]+", word, text)


# ---------- конвейер ----------
def apply(text: str) -> str:
    t = text
    for pat, rep in E.PHRASES:
        t = re.sub(pat, rep, t)  # регистр важен (Dr. / DM / OMG)
    t = translit(t)
    t = re.sub(r"'(\d0)s\b", r"\1s", t)
    t = _phones(t)
    t = _iso_dates(t)
    t = _romans(t)
    t = _times(t)
    t = _money_pass(t)
    t = _dims(t)
    t = _fractions(t)
    t = _words(t)
    t = _numbers(t)
    t = _caps(t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    return t.strip()
