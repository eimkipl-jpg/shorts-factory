"""Исторические карты умного монтажа: границы государств на любой год от 3400 до н. э. до 2024, подсветка, метки, стрелки,
смена границ во времени прямо в кадре.

Данные (папка core/mapdata, подготовлены заранее и сжаты):
  hist.bin   - Cliopatria v0.2.1 (Seshat Global History Databank), CC BY 4.0: ~13,8 тыс. записей «государство, с какого года
               по какой, контур». Контуры упрощены (~1 км) и переведены на сетку 0,005°.
  land.bin, lakes.bin, places.json, names_ru.json - Natural Earth (public domain): суша, крупные озёра, города и страны
               с русскими названиями.
Лицензия CC BY требует указать автора: строка CREDIT добавляется в описание ролика.

Сцена-карта (поле map у сцены):
  {"years": [1850, 1890, 1910] | "year": 1850, "title": "Индокитай",
   "highlight": [{"at": "Бангкок", "label": "Сиам" | {"1890": "..."}, "whole": false, "color": "gold"}],
   "pins": ["Бангкок"], "arrows": [{"from": "Париж", "to": "Ханой"}], "cues": ["слово"]}
at - город или страна, которые в тот год были внутри государства: бот сам находит, кому принадлежала точка.

Рисование: подложка и подсветка - OpenCV (сглаженные полигоны) в двойном разрешении, камера - zoompan,
подписи, метки, год и стрелки - ASS (шрифт графики), они едут вместе с камерой.
"""
import functools
import gzip
import json
import logging
import re
import struct
import zlib
from pathlib import Path

import numpy as np

log = logging.getLogger("histmap")

DATA = Path(__file__).resolve().parent / "mapdata"
YEAR_MIN, YEAR_MAX = -3400, 2024
W, H, S = 1080, 1920, 2                   # кадр и во сколько раз больше рисуется подложка
IW, IH = W * S, H * S
ROI = (70, 420, 1010, 1230)               # куда в кадре вписывается регион: ниже года, выше субтитров
Z0, Z1 = 1.0, 1.10                        # камера: медленный наезд за сцену
XF = 0.6                                  # смена года, с
HL_IN = (0.15, 0.6)                       # подсветка проявляется: начало, длительность
MAX_YEARS, MAX_HL, MAX_PINS, MAX_ARROWS = 4, 4, 4, 2
COLORS = {"gold": "#F2B134", "blue": "#4FA3F7", "green": "#47C68A", "red": "#EF6461", "violet": "#A77BF3",
          "teal": "#2EC4C9", "orange": "#F28C38", "pink": "#F06EAA"}
ORDER = ["gold", "blue", "green", "red", "violet", "teal"]
RU_COLORS = {"жёлтый": "gold", "желтый": "gold", "золотой": "gold", "синий": "blue", "голубой": "blue", "зелёный": "green",
             "зеленый": "green", "красный": "red", "фиолетовый": "violet", "бирюзовый": "teal", "оранжевый": "orange",
             "розовый": "pink"}
SEA_TOP, SEA_BOT = (38, 22, 10), (58, 35, 14)          # BGR: тёмно-синее море
GRID = (66, 44, 22)
LAND = (51, 40, 31)
SHADES = [(69, 53, 43), (76, 59, 48), (63, 49, 39), (81, 63, 52), (72, 58, 46)]
BORDER = (27, 19, 13)
CREDIT = {"ru": "Карты: Cliopatria (Seshat Global History Databank, CC BY 4.0, "
                "github.com/Seshat-Global-History-Databank/cliopatria), изменено; Natural Earth",
          "en": "Maps: Cliopatria (Seshat Global History Databank, CC BY 4.0, "
                "github.com/Seshat-Global-History-Databank/cliopatria), modified; Natural Earth"}

# старые и древние названия -> координаты (широта, долгота) или современное название
ALIASES = {
    "константинополь": "стамбул", "царьград": "стамбул", "византий": "стамбул", "истанбул": "стамбул",
    "петроград": "санкт-петербург", "ленинград": "санкт-петербург", "петербург": "санкт-петербург",
    "сталинград": "волгоград", "царицын": "волгоград", "бомбей": "мумбаи", "калькутта": "колката", "мадрас": "ченнаи",
    "сайгон": "хошимин", "рангун": "янгон", "эдо": "токио", "батавия": "джакарта", "кантон": "гуанчжоу",
    "кенигсберг": "калининград", "данциг": "гданьск", "бреслау": "вроцлав", "пресбург": "братислава",
    "смирна": "измир", "ангора": "анкара", "лондиниум": "лондон", "лютеция": "париж", "тингис": "танжер",
    "карфаген": (36.853, 10.323), "вавилон": (32.536, 44.421), "троя": (39.957, 26.239), "ниневия": (36.359, 43.153),
    "ур": (30.963, 46.103), "мемфис": (29.845, 31.25), "фивы": (25.7, 32.64), "персеполь": (29.935, 52.891),
    "ктесифон": (33.094, 44.581), "пальмира": (34.551, 38.269), "помпеи": (40.749, 14.485), "микены": (37.731, 22.756),
    "спарта": (37.074, 22.43), "сарай": (47.205, 47.398), "сарай-бату": (47.205, 47.398), "каракорум": (47.2, 102.82),
    "теночтитлан": (19.434, -99.133), "мачу-пикчу": (-13.163, -72.545), "ангкор": (13.412, 103.867),
    "аюттхая": (14.353, 100.568), "паган": (21.172, 94.86), "антиохия": (36.202, 36.16), "херсонес": (44.612, 33.493),
    "новгород": (58.52, 31.27), "великий новгород": (58.52, 31.27), "итиль": (46.4, 48.0), "гоа": (15.49, 73.83),
    "малакка": (2.196, 102.25), "аксум": (14.13, 38.72), "мероэ": (16.94, 33.75), "тимбукту": (16.773, -3.007),
    "самарканд": (39.654, 66.976), "бухара": (39.775, 64.428), "хива": (41.378, 60.364), "мерв": (37.66, 62.19),
    "сиам": "бангкок", "персия": (32.0, 53.0), "бирма": "янгон", "цейлон": "шри-ланка", "абиссиния": "эфиопия",
    "конго": (-4.32, 15.31), "ост-индия": "джакарта",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower().replace("ё", "е")).strip(" .,«»\"'")


def _fmt_year(y: int, lang: str = "ru") -> str:
    if y < 0:
        return f"{-y} до н. э." if lang != "en" else f"{-y} BC"
    return str(y)


# ---------- данные ----------

class _Layer:
    """Файл HMAP: заголовок (записи и смещения контуров) + сжатые контуры. Контур распаковывается при первом обращении."""

    def __init__(self, path: Path):
        raw = path.read_bytes()
        if raw[:4] != b"HMAP":
            raise RuntimeError(f"{path.name}: не файл карт")
        n = struct.unpack("<I", raw[4:8])[0]
        head = json.loads(gzip.decompress(raw[8:8 + n]))
        self.blob = raw[8 + n:]
        self.q = head["q"]
        self.recs = head["recs"]
        self.geoms = head["geoms"]
        self.bb = np.array([g[2] for g in self.geoms], dtype=np.float32).reshape(-1, 4)
        self.g = np.array([r["g"] for r in self.recs], dtype=np.int32)
        self.a = np.array([r.get("a", YEAR_MIN) for r in self.recs], dtype=np.int32)
        self.b = np.array([r.get("b", YEAR_MAX) for r in self.recs], dtype=np.int32)
        self.leaf = np.array([not r.get("c") for r in self.recs], dtype=bool)
        self.area = np.array([r.get("s", 0) for r in self.recs], dtype=np.float64)
        self._cache: dict[int, list] = {}

    def geom(self, gi: int) -> list[list[np.ndarray]]:
        """-> [полигон: [кольцо: массив Nx2 (долгота, широта)]]"""
        got = self._cache.get(gi)
        if got is not None:
            return got
        off, ln, _ = self.geoms[gi]
        arr = np.frombuffer(zlib.decompress(self.blob[off:off + ln]), dtype=np.int32)
        i, polys = 1, []
        for _ in range(int(arr[0])):
            nr = int(arr[i])
            i += 1
            rings = []
            for _ in range(nr):
                npt = int(arr[i])
                i += 1
                d = arr[i:i + 2 * npt].reshape(npt, 2).astype(np.int64)
                i += 2 * npt
                rings.append(np.cumsum(d, axis=0).astype(np.float64) / self.q)
            polys.append(rings)
        if len(self._cache) > 4000:
            self._cache.clear()
        self._cache[gi] = polys
        return polys

    def active(self, year: int, leaf: bool | None = True) -> np.ndarray:
        m = (self.a <= year) & (year <= self.b)
        if leaf is not None:
            m &= self.leaf if leaf else ~self.leaf
        return np.nonzero(m)[0]


@functools.cache
def _hist() -> _Layer:
    return _Layer(DATA / "hist.bin")


@functools.cache
def _land() -> _Layer:
    return _Layer(DATA / "land.bin")


@functools.cache
def _lakes() -> _Layer:
    return _Layer(DATA / "lakes.bin")


def available() -> bool:
    return all((DATA / f).exists() for f in ("hist.bin", "land.bin", "places.json"))


@functools.cache
def _places() -> dict[str, tuple[float, float, str, int]]:
    """Нормализованное имя -> (широта, долгота, имя по-русски, население). Из одноимённых - самый крупный."""
    out: dict[str, tuple] = {}
    for ru, en, lat, lon, pop, _kind in json.loads((DATA / "places.json").read_text("utf-8")):
        for name in (ru, en):
            k = _norm(name)
            if k and (k not in out or pop > out[k][3]):
                out[k] = (lat, lon, ru or en, pop)
    return out


@functools.cache
def _names_ru_ne() -> dict[str, str]:
    p = DATA / "names_ru.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def _cache_file() -> Path:
    from common import config
    d = config.ROOT / "mapcache"
    d.mkdir(parents=True, exist_ok=True)
    return d / "names_ru.json"


def _wd_cache() -> dict[str, str]:
    try:
        return json.loads(_cache_file().read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def fetch_ru_names(qids: list[str]) -> dict[str, str]:
    """Русские названия государств из Викидаты (по Q-номерам из Cliopatria), с кэшем на диске. Без сети - что есть в кэше."""
    have = {**_wd_cache()}
    need = [q for q in dict.fromkeys(qids) if q and q not in have and q not in _names_ru_ne()]
    if need:
        try:
            import requests
            for i in range(0, len(need), 50):
                r = requests.get("https://www.wikidata.org/w/api.php",
                                 params={"action": "wbgetentities", "ids": "|".join(need[i:i + 50]), "props": "labels",
                                         "languages": "ru", "format": "json"},
                                 headers={"User-Agent": "shorts-factory/1.0 (historical map labels)"}, timeout=12)
                r.raise_for_status()
                for q, ent in (r.json().get("entities") or {}).items():
                    have[q] = ((ent.get("labels") or {}).get("ru") or {}).get("value") or ""
            _cache_file().write_text(json.dumps(have, ensure_ascii=False), "utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("названия из Викидаты не получены: %s", e)
    return {**have, **_names_ru_ne()}


# ---------- поиск мест и государств ----------

def find_place(at) -> tuple[float, float, str] | None:
    """Город, страна, старое название или [широта, долгота] -> (широта, долгота, подпись)."""
    if isinstance(at, (list, tuple)) and len(at) == 2:
        try:
            lat, lon = float(at[0]), float(at[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon, ""
        except (TypeError, ValueError):
            return None
        return None
    if isinstance(at, dict) and "lat" in at and "lon" in at:
        return find_place([at["lat"], at["lon"]])
    name = str(at or "").strip()
    k = _norm(name)
    if not k:
        return None
    alias = ALIASES.get(k)
    if isinstance(alias, tuple):
        return alias[0], alias[1], name
    if isinstance(alias, str):
        got = _places().get(alias)
        return (got[0], got[1], name) if got else None
    pl = _places()
    if k in pl:
        return pl[k][0], pl[k][1], pl[k][2]
    if len(k) >= 5:                                     # падеж: «Бангкоке» -> «Бангкок»
        stem = k[:max(4, len(k) - 2)]
        cand = [(v[3], v) for n, v in pl.items() if n.startswith(stem) and abs(len(n) - len(k)) <= 3]
        for kk, v in ALIASES.items():
            if kk.startswith(stem) and isinstance(v, tuple):
                return v[0], v[1], name
        if cand:
            v = max(cand, key=lambda x: x[0])[1]
            return v[0], v[1], v[2]
    return None


def _merc(lat):
    lat = np.clip(lat, -80.0, 80.0)
    return np.degrees(np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)))


def _inside(polys: list, lon: float, lat: float) -> bool:
    for rings in polys:
        hit = False
        for r in rings:
            x, y = r[:, 0], r[:, 1]
            x2, y2 = np.roll(x, -1), np.roll(y, -1)
            cross = ((y > lat) != (y2 > lat))
            with np.errstate(divide="ignore", invalid="ignore"):
                xi = x + (lat - y) * (x2 - x) / (y2 - y)
            hit ^= bool(np.count_nonzero(cross & (lon < xi)) % 2)
        if hit:
            return True
    return False


def polity_at(lat: float, lon: float, year: int) -> int | None:
    """Индекс записи государства, которому в этот год принадлежала точка (самое мелкое из подходящих)."""
    L = _hist()
    idx = L.active(year, leaf=True)
    bb = L.bb[L.g[idx]]
    ok = idx[(bb[:, 0] <= lon) & (lon <= bb[:, 2]) & (bb[:, 1] <= lat) & (lat <= bb[:, 3])]
    best = None
    for i in sorted(ok, key=lambda j: L.area[j]):
        if _inside(L.geom(int(L.g[i])), lon, lat):
            best = int(i)
            break
    if best is None:                       # точка на самом берегу: ищем рядом
        for d in (0.08, 0.2):
            for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d)):
                for i in sorted(ok if d < 0.1 else idx, key=lambda j: L.area[j]):
                    g = L.bb[L.g[i]]
                    if g[0] <= lon + dx <= g[2] and g[1] <= lat + dy <= g[3] and _inside(L.geom(int(L.g[i])), lon + dx, lat + dy):
                        return int(i)
    return best


def polity_named(name: str, year: int) -> int | None:
    """Государство по названию (как в Cliopatria по-английски или по-русски из кэша Викидаты)."""
    k = _norm(name).strip("()")
    if not k:
        return None
    L = _hist()
    ru = {**_wd_cache(), **_names_ru_ne()}
    for i in L.active(year, leaf=None):
        r = L.recs[i]
        if _norm(r["n"]).strip("()") == k or (r.get("w") and _norm(ru.get(r["w"], "")) == k):
            return int(i)
    return None


def parent_of(i: int, year: int) -> int:
    """Вся империя целиком (метрополия с колониями), если государство в неё входит."""
    L = _hist()
    m = L.recs[i].get("m")
    if not m:
        return i
    for j in L.active(year, leaf=False):
        if L.recs[j]["n"] == m:
            return int(j)
    return i


def _resolve(item: dict, year: int) -> int | None:
    i = polity_at(item["lat"], item["lon"], year) if item.get("lat") is not None else polity_named(item.get("name", ""), year)
    if i is not None and item.get("whole"):
        i = parent_of(i, year)
    return i


def polity_label(i: int, lang: str = "ru", ru_names: dict | None = None) -> str:
    r = _hist().recs[i]
    name = r["n"].strip("()")
    if lang != "en":
        ru = (ru_names or {}).get(r.get("w") or "") or _names_ru_ne().get(r.get("w") or "")
        if ru:
            name = ru
    return name


# ---------- разбор сценария ----------

def _q(v, n=40) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _year(v) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = _norm(v)
    if s in ("сейчас", "сегодня", "now", "today", "современность"):
        return YEAR_MAX
    m = re.match(r"^(-?\d{1,4})\s*(до н\.? ?э\.?|bc|bce)?", s)
    if not m:
        return None
    y = int(m.group(1))
    return -abs(y) if m.group(2) else y


def normalize(raw, where: str, warn: list[str]) -> dict | None:
    """Проверяет поле map сцены и находит места. Ошибки - в warn; без подсветки и меток карта не ставится."""
    if not isinstance(raw, dict):
        warn.append(f"{where}: map должен быть объектом")
        return None
    if not available():
        warn.append(f"{where}: данные карт не установлены (core/mapdata), карта пропущена")
        return None
    yrs = raw.get("years")
    if yrs is None:
        yrs = [raw.get("year")] if raw.get("year") is not None else []
    if not isinstance(yrs, list):
        yrs = [yrs]
    years = []
    for v in yrs:
        y = _year(v)
        if y is None:
            warn.append(f"{where}: год карты «{v}» не понял, пропущен")
            continue
        if not YEAR_MIN <= y <= YEAR_MAX:
            y = max(YEAR_MIN, min(YEAR_MAX, y))
        if y not in years:
            years.append(y)
    modern = not years
    years = years[:MAX_YEARS] or [YEAR_MAX]

    def place(v, what):
        got = find_place(v)
        if not got:
            warn.append(f"{where}: {what} «{v}» не нашёл на карте (нужен современный город или страна)")
        return got

    items = []
    hl = raw.get("highlight") or []
    for it in (hl if isinstance(hl, list) else [hl])[:MAX_HL]:
        it = {"at": it} if isinstance(it, (str, list)) else (it if isinstance(it, dict) else {})
        at = it.get("at") or it.get("place") or it.get("city")
        rec = {"whole": bool(it.get("whole"))}
        if at:
            got = place(at, "место")
            if not got:
                continue
            rec.update(at=at if isinstance(at, str) else got[2], lat=got[0], lon=got[1])
        elif it.get("name"):
            rec["name"] = _q(it["name"], 60)
        else:
            continue
        lab = it.get("label")
        if isinstance(lab, dict):
            rec["label"] = {str(_year(k)): _q(v, 28) for k, v in lab.items() if _year(k) is not None and _q(v)}
        elif isinstance(lab, list):
            rec["label"] = {str(y): _q(v, 28) for y, v in zip(years, lab) if _q(v)}
        elif _q(lab):
            rec["label"] = _q(lab, 28)
        col = _norm(it.get("color"))
        col = RU_COLORS.get(col, col)
        rec["color"] = COLORS.get(col) or (col.upper() if re.match(r"^#[0-9a-f]{6}$", col) else COLORS[ORDER[len(items) % len(ORDER)]])
        found = [y for y in years if _resolve(rec, y) is not None]
        if not found:
            warn.append(f"{where}: на месте «{rec.get('at') or rec.get('name')}» в {', '.join(_fmt_year(y) for y in years)} "
                        "в данных нет государства, подсветка пропущена")
            continue
        for y in years:
            i = _resolve(rec, y)
            same = [o for o in items if i is not None and _resolve(o, y) == i]
            if same:
                warn.append(f"{where}: в {_fmt_year(y)} «{rec.get('at') or rec.get('name')}» и «{same[0].get('at') or same[0].get('name')}» - "
                            "одно государство, в этот год подсвечу только первое")
        items.append(rec)
    pins = []
    for p in (raw.get("pins") or [])[:MAX_PINS] if isinstance(raw.get("pins"), list) else []:
        at = p.get("at") if isinstance(p, dict) else p
        got = place(at, "метку")
        if got:
            label = _q(p.get("label"), 24) if isinstance(p, dict) and p.get("label") else (at if isinstance(at, str) else got[2])
            pins.append({"lat": got[0], "lon": got[1], "label": _q(label, 24)})
    arrows = []
    for a in (raw.get("arrows") or [])[:MAX_ARROWS] if isinstance(raw.get("arrows"), list) else []:
        if not isinstance(a, dict):
            continue
        p0, p1 = place(a.get("from"), "начало стрелки"), place(a.get("to"), "конец стрелки")
        if p0 and p1:
            arrows.append({"a": [p0[0], p0[1]], "b": [p1[0], p1[1]]})
    if not items and not pins:
        warn.append(f"{where}: на карте нечего показать (нет найденных подсветок и меток), карта пропущена")
        return None
    cues = [_q(c, 30) for c in (raw.get("cues") or []) if _q(c)] if isinstance(raw.get("cues"), list) else []
    return {"years": years, "modern": modern, "items": items, "pins": pins, "arrows": arrows,
            "title": _q(raw.get("title"), 32), "cues": cues[:len(years) - 1]}


def describe(spec: dict, lang: str = "ru") -> str:
    yrs = "сегодня" if spec.get("modern") else " → ".join(_fmt_year(y, lang) for y in spec["years"])
    names = []
    for it in spec["items"]:
        lab = it.get("label")
        names.append(lab if isinstance(lab, str) else (next(iter(lab.values())) if lab else (it.get("at") or it.get("name"))))
    return f"карта {yrs}" + (f": {', '.join(n for n in names if n)}" if names else "")


# ---------- рисование ----------

class _View:
    """Меркатор: x - долгота, y - «широта Меркатора» в градусах; k - пикселей подложки на градус."""

    def __init__(self, x0: float, y0: float, k: float):
        self.x0, self.y0, self.k = x0, y0, k

    def px(self, lon, lat):
        return (np.asarray(lon) - self.x0) * self.k, (self.y0 - _merc(np.asarray(lat))) * self.k

    def bounds(self):
        """Видимая область подложки: (долгота от, до, широта от, до)."""
        ym0, ym1 = self.y0 - IH / self.k, self.y0
        lat = lambda y: float(np.degrees(2 * np.arctan(np.exp(np.radians(y))) - np.pi / 2))  # noqa: E731
        return self.x0, self.x0 + IW / self.k, lat(ym0), lat(ym1)


def _focus_bbox(i: int, it: dict, wide: bool = True) -> list[float]:
    """Рамка той части государства, где точка подсветки (без далёких колоний и островов), не шире ~50° вокруг точки."""
    L = _hist()
    polys = L.geom(int(L.g[i]))
    lat, lon = it.get("lat"), it.get("lon")
    if lat is None or not polys:
        b = L.bb[L.g[i]]
        return [float(b[0]), float(b[1]), float(b[2]), float(b[3])]
    best, bd = None, 1e9
    for rings in polys:
        r = rings[0]
        x0, y0, x1, y1 = r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max()
        if _inside([rings], lon, lat):
            best = (x0, y0, x1, y1)
            break
        d = max(x0 - lon, lon - x1, 0) + max(y0 - lat, lat - y1, 0)
        if d < bd:
            best, bd = (x0, y0, x1, y1), d
    x0, y0, x1, y1 = best
    rx, ry = (40, 24) if wide else (12, 9)
    return [max(x0, lon - rx), max(y0, lat - ry), min(x1, lon + rx), min(y1, lat + ry)]


def _view_for(spec: dict) -> _View:
    _hist()
    xs, ys = [], []
    for n_, it in enumerate(spec["items"]):
        for y in spec["years"]:
            i = _resolve(it, y)
            if i is None:
                continue
            b = _focus_bbox(i, it, wide=n_ == 0 or bool(it.get("whole")))
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
    for p in spec["pins"]:
        xs += [p["lon"] - 2, p["lon"] + 2]
        ys += [p["lat"] - 2, p["lat"] + 2]
    for a in spec["arrows"]:
        for la, lo in (a["a"], a["b"]):
            xs += [lo - 2, lo + 2]
            ys += [la - 2, la + 2]
    if not xs:
        xs, ys = [-20, 60], [20, 60]
    x0, x1 = min(xs), max(xs)
    if x1 - x0 > 150:                                  # огромная империя: середина по точкам подсветки
        pts = [it["lon"] for it in spec["items"] if it.get("lon") is not None] + [p["lon"] for p in spec["pins"]]
        c = float(np.mean(pts)) if pts else (x0 + x1) / 2
        x0, x1 = c - 75, c + 75
    ym0, ym1 = float(_merc(max(min(ys), -78))), float(_merc(min(max(ys), 80)))
    cx, cy = (x0 + x1) / 2, (ym0 + ym1) / 2
    sx, sy = max(x1 - x0, 7.0) * 1.12, max(ym1 - ym0, 6.0) * 1.12
    rw, rh = (ROI[2] - ROI[0]) * S, (ROI[3] - ROI[1]) * S
    k = min(rw / sx, rh / sy)
    rcx, rcy = (ROI[0] + ROI[2]) / 2 * S, (ROI[1] + ROI[3]) / 2 * S
    return _View(cx - rcx / k, cy + rcy / k, k)


def _ring_px(view: _View, ring: np.ndarray) -> np.ndarray:
    x, y = view.px(ring[:, 0], ring[:, 1])
    return np.round(np.stack([x, y], axis=1) * 16).astype(np.int32)       # shift=4: субпиксельная точность


def _shade(name: str):
    return SHADES[sum(map(ord, name)) % len(SHADES)]


def _visible(layer: _Layer, idx, bounds) -> list[int]:
    lo0, lo1, la0, la1 = bounds
    bb = layer.bb[layer.g[idx]]
    m = (bb[:, 2] >= lo0) & (bb[:, 0] <= lo1) & (bb[:, 3] >= la0) & (bb[:, 1] <= la1)
    return [int(i) for i in np.asarray(idx)[m]]


def _polys_px(layer: _Layer, i: int, view: _View) -> list[np.ndarray]:
    return [_ring_px(view, r) for rings in layer.geom(int(layer.g[i])) for r in rings]


def _base(view: _View, year: int):
    """Подложка года: море с сеткой, суша, государства тонами с границами, озёра. -> (картинка BGR, контуры государств)"""
    import cv2
    t = np.linspace(0, 1, IH, dtype=np.float32)[:, None]
    img = np.empty((IH, IW, 3), np.uint8)
    for c in range(3):
        img[:, :, c] = (SEA_TOP[c] + (SEA_BOT[c] - SEA_TOP[c]) * t).astype(np.uint8)
    b = view.bounds()
    for lon in range(int(b[0] // 10 * 10), int(b[1]) + 11, 10):
        x = int(view.px(lon, 0)[0])
        cv2.line(img, (x, 0), (x, IH), GRID, 1, cv2.LINE_AA)
    for lat in range(-80, 81, 10):
        y = int(view.px(0, lat)[1])
        if 0 <= y < IH:
            cv2.line(img, (0, y), (IW, y), GRID, 1, cv2.LINE_AA)
    land = _land()
    for i in _visible(land, np.arange(len(land.recs)), b):
        cv2.fillPoly(img, _polys_px(land, i, view), LAND, cv2.LINE_AA, 4)
    L = _hist()
    vis = _visible(L, L.active(year, leaf=True), b)
    outlines = []
    for i in vis:
        pts = _polys_px(L, i, view)
        cv2.fillPoly(img, pts, _shade(L.recs[i]["n"]), cv2.LINE_AA, 4)
        outlines.append(pts)
    for pts in outlines:
        cv2.polylines(img, pts, True, BORDER, 2 * S, cv2.LINE_AA, 4)
    lakes = _lakes()
    for i in _visible(lakes, np.arange(len(lakes.recs)), b):
        cv2.fillPoly(img, _polys_px(lakes, i, view), SEA_TOP, cv2.LINE_AA, 4)
    return img


def _hex_bgr(h: str):
    h = h.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


def _highlight(base: np.ndarray, view: _View, regs: list[tuple[int, str]]) -> tuple[np.ndarray, list]:
    """Подсветка государств поверх подложки: заливка цветом, светящийся край. -> (картинка, маски)"""
    import cv2
    out = base.astype(np.float32)
    glow = np.zeros(base.shape[:2], np.float32)
    masks = []
    L = _hist()
    for i, color in regs:
        pts = _polys_px(L, i, view)
        m = np.zeros(base.shape[:2], np.uint8)
        cv2.fillPoly(m, pts, 255, cv2.LINE_AA, 4)
        masks.append(m)
        a = (m.astype(np.float32) / 255.0)[:, :, None] * 0.78
        col = np.array(_hex_bgr(color), np.float32)[None, None, :]
        out = out * (1 - a) + col * a
        edge = np.zeros(base.shape[:2], np.uint8)
        cv2.polylines(edge, pts, True, 255, 7 * S, cv2.LINE_AA, 4)
        glow = np.maximum(glow, edge.astype(np.float32))
    if regs:
        g = cv2.GaussianBlur(glow, (0, 0), 9 * S) / 255.0 * 0.55
        out = out + (255 - out) * g[:, :, None]
        for (i, color), m in zip(regs, masks):
            pts = _polys_px(L, i, view)
            light = tuple(min(255, int(c * 0.5 + 255 * 0.5)) for c in _hex_bgr(color))
            cv2.polylines(out, pts, True, light, 2 * S, cv2.LINE_AA, 4)
    return np.clip(out, 0, 255).astype(np.uint8), masks


def _shade_edges(img: np.ndarray) -> np.ndarray:
    """Затемнение сверху (под годом) и снизу (под субтитрами), лёгкая виньетка."""
    h = img.shape[0]
    y = np.linspace(0, 1, h, dtype=np.float32)
    top = np.clip(1 - y / 0.2, 0, 1) ** 1.6 * 0.55
    bot = np.clip((y - 0.62) / 0.38, 0, 1) ** 1.4 * 0.6
    k = (1 - np.maximum(top, bot))[:, None, None]
    return (img.astype(np.float32) * k).astype(np.uint8)


# ---------- подписи (ASS) ----------

def _out(px: float, py: float, z: float) -> tuple[float, float]:
    """Точка подложки -> точка кадра при наезде z (центр наезда - центр области региона)."""
    cx, cy = (ROI[0] + ROI[2]) / 2 * S, (ROI[1] + ROI[3]) / 2 * S
    r = 1 / S
    return (px - cx) * z * r + cx * r, (py - cy) * z * r + cy * r


def _z(t: float, length: float) -> float:
    return Z0 + (Z1 - Z0) * min(max(t / max(length, 0.1), 0), 1)


def _label_point(mask: np.ndarray, length: float, near: tuple[float, float] | None = None) -> tuple[float, float, float] | None:
    """Самая «просторная» точка области (дальше всего от краёв), видимая до конца наезда; near - точка подсветки в подложке:
    подпись ставится рядом с ней (у Британской Индии подпись «Бирма» - в Бирме, а не в центре Индии). -> (x, y, радиус)"""
    import cv2
    f = 4
    small = cv2.resize(mask, (IW // f, IH // f), interpolation=cv2.INTER_AREA)
    small = (small > 127).astype(np.uint8)
    if near is not None:
        rad = 300 * S / f
        yy, xx = np.ogrid[:small.shape[0], :small.shape[1]]
        around = ((xx - near[0] / f) ** 2 + (yy - near[1] / f) ** 2) <= rad ** 2
        if (small & around).any():
            d_full = cv2.distanceTransform(np.pad(small, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
            small = small & around
            d_full = np.where(small > 0, d_full, 0)
            # дальше всего от краёв, но с поправкой на расстояние до точки
            dist = np.sqrt((xx - near[0] / f) ** 2 + (yy - near[1] / f) ** 2)
            score = np.minimum(d_full, 60 * S / f) - dist * 0.15
            score = np.where(small > 0, score, -1e9)
            cx_, cy_ = (ROI[0] + ROI[2]) / 2 * S, (ROI[1] + ROI[3]) / 2 * S
            x0 = cx_ + (60 - cx_ / S) * S / Z1
            x1 = cx_ + (W - 60 - cx_ / S) * S / Z1
            y0 = cy_ + (380 - cy_ / S) * S / Z1
            y1 = cy_ + (1240 - cy_ / S) * S / Z1
            win = np.zeros(small.shape, bool)
            win[max(0, int(y0 / f)):int(y1 / f), max(0, int(x0 / f)):int(x1 / f)] = True
            score = np.where(win, score, -1e9)
            if score.max() > -1e8:
                yy_, xx_ = np.unravel_index(int(np.argmax(score)), score.shape)
                return xx_ * f + f / 2, yy_ * f + f / 2, float(d_full[yy_, xx_]) * f
    # видимая в конце сцены часть кадра без зоны года и субтитров
    cx, cy = (ROI[0] + ROI[2]) / 2 * S, (ROI[1] + ROI[3]) / 2 * S
    x0 = cx + (60 - cx / S) * S / Z1
    x1 = cx + (W - 60 - cx / S) * S / Z1
    y0 = cy + (380 - cy / S) * S / Z1
    y1 = cy + (1240 - cy / S) * S / Z1
    win = np.zeros_like(small)
    win[max(0, int(y0 / f)):int(y1 / f), max(0, int(x0 / f)):int(x1 / f)] = 1
    small &= win
    if not small.any():
        return None
    d = cv2.distanceTransform(np.pad(small, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    yy, xx = np.unravel_index(int(np.argmax(d)), d.shape)
    return xx * f + f / 2, yy * f + f / 2, float(d[yy, xx]) * f


def _move(x: float, y: float, a: float, b: float, length: float, scene_a: float = 0.0) -> str:
    """\\move для точки подложки на отрезке [a, b] сцены."""
    p0 = _out(x, y, _z(a, length))
    p1 = _out(x, y, _z(b, length))
    return f"\\move({p0[0]:.1f},{p0[1]:.1f},{p1[0]:.1f},{p1[1]:.1f},0,{int((b - a) * 1000)})"


def _arrow_shape(pts: np.ndarray, width: float, head: bool) -> str:
    """Полоса вдоль ломаной + наконечник; координаты от левого верхнего угла (an7)."""
    d = np.diff(pts, axis=0)
    n = np.stack([-d[:, 1], d[:, 0]], axis=1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-6)
    n = np.vstack([n, n[-1:]])
    left, right = pts + n * width / 2, pts - n * width / 2
    poly = [*left, *right[::-1]]
    shapes = [poly]
    if head and len(pts) >= 2:
        tip, back = pts[-1], pts[-1] - d[-1] / max(np.linalg.norm(d[-1]), 1e-6) * width * 3.2
        nn = n[-1] * width * 1.9
        shapes.append([tip + d[-1] / max(np.linalg.norm(d[-1]), 1e-6) * width * 1.2, back + nn, back - nn])
    allp = np.array([p for s in shapes for p in s])
    mn = allp.min(axis=0)
    path = " ".join("m " + " l ".join(f"{p[0] - mn[0]:.1f} {p[1] - mn[1]:.1f}" for p in s).replace(" l ", " l ", 1) for s in shapes)
    return path, mn


# ---------- сцена целиком ----------

def _segments(spec: dict, length: float, words: list[tuple[float, str]] | None) -> list[float]:
    """Моменты смены года: по словам-подсказкам (cues), иначе поровну."""
    n = len(spec["years"])
    if n == 1:
        return []
    even = [length * k / n for k in range(1, n)]
    out = []
    if words and spec.get("cues"):
        from core.statfx import _stem5
        stems = [(t, _stem5(w)) for t, w in words]
        for k, cue in enumerate(spec["cues"]):
            st = _stem5(cue.split()[0]) if cue.split() else ""
            hit = next((t for t, s in stems if s and s == st and t > (out[-1] if out else 0.5)), None)
            out.append(hit if hit is not None else even[k])
    out += even[len(out):]
    res = []
    for t in out:                                      # не раньше 1 с от начала и между сменами не меньше 1,2 с
        t = max(t, (res[-1] + 1.2) if res else 1.0)
        res.append(min(t, length - 0.8))
    return res


def render(work: Path, name: str, length: float, spec: dict, lang: str = "ru", words=None, accent: str = "#FBBF24") -> Path:
    """Видео сцены-карты длиной length: подложка по годам со сменой границ, наезд камеры, подписи. -> путь к mp4"""
    import cv2

    from common.media import run
    from core import fontpack, statfx, subtitles
    from core.progress import _bgr

    view = _view_for(spec)
    years = spec["years"]
    switches = _segments(spec, length, words)
    bounds_t = [0.0, *switches, length]
    L = _hist()
    ru = fetch_ru_names([L.recs[i].get("w") for y in years for it in spec["items"]
                         for i in [_resolve(it, y)] if i is not None]) if lang != "en" else {}
    imgs, labels = [], []                        # по годам: картинка и подписи [(текст, x, y, радиус, цвет)]
    first_base = None
    for k, y in enumerate(years):
        base = _base(view, y)
        if first_base is None:
            first_base = base
        regs, meta = [], []
        for it in spec["items"]:
            i = _resolve(it, y)
            if i is None or any(i == r_[0] for r_ in regs):     # то же государство уже подсвечено другим местом
                continue
            regs.append((i, it["color"]))
            lab = it.get("label")
            text = (lab.get(str(y)) or next((lab[str(p)] for p in reversed(years[:k]) if str(p) in lab), None)
                    if isinstance(lab, dict) else lab) or polity_label(i, lang, ru)
            meta.append((text, it))
        img, masks = _highlight(base, view, regs)
        imgs.append(_shade_edges(img))
        labs = []
        for (text, it), m in zip(meta, masks):
            near = tuple(float(v) for v in view.px(it["lon"], it["lat"])) if it.get("lat") is not None else None
            pt = _label_point(m, length, near)
            if pt:
                labs.append((text, pt[0], pt[1], pt[2], it["color"]))
        labels.append(labs)
    files = []
    plain = work / f"{name}_y0.png"
    cv2.imwrite(str(plain), _shade_edges(first_base))
    files.append(plain)
    for k, img in enumerate(imgs):
        f = work / f"{name}_y{k + 1}.png"
        cv2.imwrite(str(f), img)
        files.append(f)

    # подписи, метки, год
    ev: list[str] = []
    font = fontpack.head()
    lay = 2
    pin_boxes = []
    for p in spec["pins"]:
        ox, oy = _out(*view.px(p["lon"], p["lat"]), Z0)
        tw = statfx._w(p["label"], 36)
        if _out(*view.px(p["lon"], p["lat"]), Z1)[0] + 26 + tw > W - 30:
            pin_boxes.append((ox - 30 - tw, oy - 22, ox + 16, oy + 22))
        else:
            pin_boxes.append((ox - 16, oy - 22, ox + 30 + tw, oy + 22))
    for k, labs in enumerate(labels):
        a = HL_IN[0] + HL_IN[1] * 0.6 if k == 0 else bounds_t[k] + XF * 0.3
        b = bounds_t[k + 1] + (XF * 0.2 if k + 1 < len(years) else 0)
        boxes = list(pin_boxes)
        for text, x, y, rad, color in labs:
            za = _z(a, length)
            r_out = rad / S * za
            size = max(36, min(60, r_out * 0.45))
            maxw = max(260, min(880, r_out * 2.6))
            lines = statfx._balanced(text, size, maxw)
            while len(lines) > 2 and size > 34:
                size -= 4
                lines = statfx._balanced(text, size, maxw)
            tw = max(statfx._w(ln_, size) for ln_ in lines) + 16
            th = size * 1.15 * len(lines) + 8
            ox, oy = _out(x, y, za)
            for dx, dy in ((0, 0), (0, -th * 0.9), (0, th * 0.9), (0, -th * 1.7), (tw * 0.6, 0), (-tw * 0.6, 0)):
                bx = (ox + dx - tw / 2, oy + dy - th / 2, ox + dx + tw / 2, oy + dy + th / 2)
                if not any(_overlap(bx, o) for o in boxes):
                    break
            boxes.append(bx)
            x, y = x + dx * S / za, y + dy * S / za
            body = "\\N".join(statfx._esc(x_) for x_ in lines)
            tags = (f"\\an5{_move(x, y, a, b, length)}\\fn{font}\\fs{size:.0f}\\b0\\c&HFFFFFF&\\bord{3.4:.1f}\\3c&H000000&"
                    f"\\shad2\\4c&H000000&\\4a&H60&\\q2\\fad(260,220)")
            ev.append(f"Dialogue: {lay},{_tt(a)},{_tt(b)},Prog,,0,0,0,,{{{tags}}}{body}")
    for p in spec["pins"]:
        px_, py_ = view.px(p["lon"], p["lat"])
        a, b = 0.5, length
        dot = "m -10 0 b -10 -13.3 10 -13.3 10 0 b 10 13.3 -10 13.3 -10 0"
        ev.append(f"Dialogue: {lay + 1},{_tt(a)},{_tt(b)},Prog,,0,0,0,,{{\\an5{_move(px_, py_, a, b, length)}\\p1\\bord5"
                  f"\\3c{_bgr(accent)}\\c&HFFFFFF&\\shad0\\fad(200,0)\\fscx40\\fscy40\\t(0,260,\\fscx100\\fscy100)}}{dot}")
        tw = statfx._w(p["label"], 36)
        left = _out(px_, py_, Z1)[0] + 26 + tw > W - 30          # не помещается справа - подпись слева от точки
        lx, ly = px_ + (-22 if left else 22) * S, py_
        ev.append(f"Dialogue: {lay + 1},{_tt(a + 0.15)},{_tt(b)},Prog,,0,0,0,,{{\\an{6 if left else 4}{_move(lx, ly, a + 0.15, b, length)}\\fn{font}"
                  f"\\fs36\\b0\\c&HFFFFFF&\\bord3\\3c&H000000&\\shad0\\q2\\fad(220,0)}}{statfx._esc(p['label'])}")
    for ai, ar in enumerate(spec["arrows"]):
        (la0, lo0), (la1, lo1) = ar["a"], ar["b"]
        x0, y0 = view.px(lo0, la0)
        x1, y1 = view.px(lo1, la1)
        p0, p1 = np.array([x0, y0], float), np.array([x1, y1], float)
        d = p1 - p0
        ctrl = (p0 + p1) / 2 + np.array([-d[1], d[0]]) * 0.22
        tt = np.linspace(0, 1, 40)[:, None]
        curve = (1 - tt) ** 2 * p0 + 2 * (1 - tt) * tt * ctrl + tt ** 2 * p1
        a0 = 0.8 + ai * 0.5
        steps = 10
        for s in range(1, steps + 1):
            ta = a0 + (s - 1) * 0.07
            tb = a0 + s * 0.07 if s < steps else length
            part = curve[:max(2, int(len(curve) * s / steps))]
            z_a, z_b = _z(ta, length), _z(tb, length)
            pts = np.array([_out(x, y, z_a) for x, y in part])
            path, mn = _arrow_shape(pts, 9.0, s == steps)
            # левый верхний угол фигуры в координатах подложки, чтобы ехать вместе с камерой
            tl = np.array([(mn[0] - _out(0, 0, z_a)[0]) / (z_a / S), (mn[1] - _out(0, 0, z_a)[1]) / (z_a / S)])
            q0, q1 = _out(tl[0], tl[1], z_a), _out(tl[0], tl[1], z_b)
            sc = 100 * z_b / z_a
            ev.append(f"Dialogue: {lay},{_tt(ta)},{_tt(tb)},Prog,,0,0,0,,{{\\an7\\move({q0[0]:.1f},{q0[1]:.1f},{q1[0]:.1f},{q1[1]:.1f},0,"
                      f"{int((tb - ta) * 1000)})\\p1\\bord2\\3c&H000000&\\c{_bgr(accent)}\\shad0"
                      f"\\t(0,{int((tb - ta) * 1000)},\\fscx{sc:.1f}\\fscy{sc:.1f})}}{path}")
    # год: крупно сверху, смена - «прокруткой» чисел
    if not spec.get("modern"):
        for k, y in enumerate(years):
            a = 0.0 if k == 0 else switches[k - 1] + XF * 0.25
            b = switches[k] - XF * 0.25 if k + 1 < len(years) else length
            ev.append(_year_ev(a, b, y, font, lang, fade_in=k == 0))
            if k + 1 < len(years):
                y2, n_ = years[k + 1], 7
                for s in range(1, n_ + 1):
                    ta = b + (s - 1) * XF * 0.5 / n_
                    ev.append(_year_ev(ta, ta + XF * 0.5 / n_, round(y + (y2 - y) * s / (n_ + 1)), font, lang))
    title = spec.get("title") or ("Сегодня" if spec.get("modern") and lang != "en" else ("Today" if spec.get("modern") else ""))
    if title:
        yt = 300 if not spec.get("modern") else 220
        ev.append(f"Dialogue: {lay},{_tt(0.1)},{_tt(length)},Prog,,0,0,0,,{{\\an5\\pos(540,{yt})\\fn{font}\\fs46\\b0"
                  f"\\c{_bgr('#C9D3E3')}\\bord0\\shad3\\4c&H000000&\\4a&H50&\\q2\\fad(300,0)}}{statfx._esc(title)}")
    ass = work / f"{name}.ass"
    subtitles.write_ass([], ass, None, None, ev)

    # склейка годов и камера
    fps = 30
    offs = [0.0, HL_IN[0]] + [t - XF / 2 for t in switches]
    durs = [HL_IN[1]] + [XF] * len(switches)
    inputs = []
    for j, f in enumerate(files):
        end = (offs[j + 1] + durs[j]) if j + 1 < len(files) else length
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{end - offs[j] + 0.2:.3f}", "-i", f.name]
    chain, last = [], "[0:v]"
    for j in range(1, len(files)):
        lbl = f"[x{j}]"
        chain.append(f"{last}[{j}:v]xfade=transition=fade:duration={durs[j - 1]:.3f}:offset={offs[j]:.3f}{lbl}")
        last = lbl
    nfr = int(round(length * fps)) + 2
    cx, cy = (ROI[0] + ROI[2]) / 2 * S, (ROI[1] + ROI[3]) / 2 * S
    chain.append(f"{last}zoompan=z='{Z0}+{Z1 - Z0}*on/{nfr}':x='{cx}-{cx}/zoom':y='{cy}-{cy}/zoom':d=1:s={W}x{H}:fps={fps},"
                 f"subtitles={ass.name},setsar=1,format=yuv420p[v]")
    out = work / f"{name}.mp4"
    from core import compose
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chain), "-map", "[v]",
         "-t", f"{length:.3f}", "-an", "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", str(compose.PART_CRF),
         "-pix_fmt", "yuv420p", out.name], cwd=work)
    for f in files:
        f.unlink(missing_ok=True)
    return out


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _tt(sec: float) -> str:
    from core.progress import _t
    return _t(sec)


def _year_ev(a: float, b: float, y: int, font: str, lang: str, fade_in: bool = False) -> str:
    big = str(abs(y)) if y < 0 else str(y)
    tail = ("{\\fs54} " + ("до н. э." if lang != "en" else "BC")) if y < 0 else ""
    fade = "\\fad(300,0)" if fade_in else ""
    return (f"Dialogue: 3,{_tt(a)},{_tt(b)},Prog,,0,0,0,,{{\\an5\\pos(540,190)\\fn{font}\\fs124\\b0\\c&HFFFFFF&\\bord0\\shad4"
            f"\\4c&H000000&\\4a&H40&\\q2{fade}}}{big}{tail}")


def preview(work: Path, spec: dict, lang: str = "ru") -> list[Path]:
    """Кадры для проверки карты до сборки: по одному на каждый год (из короткой версии сцены)."""
    from common.media import run
    per = 2.6
    length = per * len(spec["years"])
    vid = render(work, "mapprev", length, {**spec, "cues": []}, lang)
    shots = []
    for k in range(len(spec["years"])):
        t = per * k + per * 0.75
        f = work / f"map_{k + 1}.jpg"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", vid.name, "-frames:v", "1", "-q:v", "3", f.name], cwd=work)
        shots.append(f)
    return shots
