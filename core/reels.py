"""ReelsEditor: монтаж сырой «говорящей головы» без озвучки и аватаров.

Креатор присылает видео, где говорит в камеру. Бот:
  1. распознаёт речь Whisper'ом с таймингами слов (подсказка в промте заставляет его не выбрасывать «э-э»);
  2. вырезает паузы, слова-паразиты, повторы и фальстарты («прив… привет»);
  3. ставит зумы: на акцентах (самые громкие содержательные слова) и, в динамичном режиме, через склейку (jump cut);
  4. перекрывает куски вставками со стоков по ключевым словам фразы;
  5. добавляет динамические субтитры (стиль - из настроек умного монтажа) и выравнивает громкость голоса.

Как режется: каждый кусок кодируется отдельно (точный поиск + ровно N кадров при 30 к/с), куски склеиваются без перекодирования,
звук собирается из исходной дорожки по тем же границам с короткими затуханиями на склейках - картинка и звук не расходятся."""
import logging
import math
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from common import config
from common.media import probe

log = logging.getLogger("reels")

FPS = 30
W, H = 1080, 1920
SR = 48000
PAD_IN, PAD_OUT = 0.05, 0.09            # запас до и после слова, чтобы не съесть начало и хвост звука
FADE = 0.012                            # затухание звука на склейке (без щелчков)

DEFAULT = {"pauses": "mid", "fillers": "basic", "repeats": True, "zoom": "accents", "broll": "rare", "sound": True, "frame": "crop",
           "lang": "ru"}
OPTIONS = {
    "lang": ("🌐 Язык речи", [("ru", "русский"), ("en", "английский"), ("auto", "определить автоматически")]),
    "pauses": ("✂️ Паузы", [("mid", "средне: убрать всё длиннее 0,4 с"), ("hard", "жёстко: темп как у блогеров"),
                           ("soft", "мягко: только длинные паузы")]),
    "fillers": ("🗣 Слова-паразиты", [("basic", "убрать «э», «эм», «м-м»"), ("strict", "строго: ещё «типа», «короче», «как бы»"),
                                     ("off", "не трогать")]),
    "repeats": ("🔁 Повторы и оговорки", [(True, "убирать"), (False, "не трогать")]),
    "zoom": ("🔍 Зумы", [("accents", "на акцентах"), ("dynamic", "динамично: на акцентах и через склейку"), ("off", "без зумов")]),
    "broll": ("🎬 Вставки со стоков", [("rare", "редко: раз в 9-10 с"), ("often", "часто: раз в 5-6 с"), ("off", "без вставок")]),
    "sound": ("🔊 Звук", [(True, "улучшить: шумоподавление и громкость"), (False, "как есть")]),
    "frame": ("🖼 Горизонтальное видео", [("crop", "обрезать до 9:16 по центру"), ("blur", "целиком на размытом фоне")]),
}
GUIDE = {
    "lang": "На каком языке вы говорите в видео. С точным языком бот лучше слышит «э-э» и повторы; «автоматически» - если "
            "язык другой (паузы вырежутся, а паразиты - только те, что распознаются).",
    "pauses": "Сколько тишины оставлять между фразами. «Средне» - естественно и динамично; «жёстко» - плотный темп, как у "
              "популярных блогеров (иногда режет вдох); «мягко» - для спокойных экспертных роликов.",
    "fillers": "«Э», «эм», «м-м» убираются всегда безопасно. Строгий режим вырезает ещё «типа», «короче», «как бы», «это самое» - "
               "проверьте результат: иногда эти слова несут смысл.",
    "repeats": "Повтор подряд («я хотел я хотел сказать») и обрыв слова («прив… привет»): остаётся последний, удачный вариант.",
    "zoom": "Зум на акценте - камера резко приближается на самом громком содержательном слове: так делают в Reels, чтобы "
            "удержать внимание. «Динамично» - ещё и смена масштаба через склейку, кадр не «стоит на месте».",
    "broll": "Короткие вставки из Pexels и Pixabay по ключевым словам фразы: голос продолжает звучать, картинка меняется. "
             "Подбор автоматический - если вставка мимо, выключите или поставьте «редко».",
    "sound": "Убирает фоновый шум и гул, выравнивает громкость под площадки (−14 LUFS).",
    "frame": "Только для горизонтального видео: обрезать по центру (лицо крупно) или показать кадр целиком на размытом фоне.",
}
PAUSE_THR = {"soft": 0.7, "mid": 0.4, "hard": 0.22}
FILLER_RE = re.compile(r"^(э+м*|м{2,}|а{2,}|хм+|эх+|u+h+m*|u+m+|e+r+m*|a+h+|h+m+|m{2,})$")
STRICT_ONE = {"типа", "короче", "like", "basically", "literally"}
STRICT_SEQ = [("как", "бы"), ("это", "самое"), ("так", "сказать"), ("в", "общем"), ("you", "know"), ("i", "mean")]
ALLOW_DUP = {"да", "нет", "очень", "ну", "так", "вот", "ой", "no", "yes", "very", "so"}
PROMPT = {"ru": "Э-э, ну, м-м, вот. Эм, это самое, короче, как бы.", "en": "Um, uh, well, hmm. Like, you know, er."}
STOP = set("""это этот эта эти тот та те так там тут здесь сейчас потом когда тогда если чтобы потому поэтому который которая
которые очень просто можно нужно надо будет было были есть быть может могут только даже уже ещё еще всего всех свой своя свои
наш наша наши ваш ваша ваши меня тебя себя него неё нее него них нему ними чем что как где куда почему зачем какой какая какие
вообще всегда никогда сегодня завтра вчера каждый каждая много мало больше меньше самый самая самое просто между после перед
через около вокруг почти также тоже вместе другой другая другие например значит вот ведь разве хотя пусть давайте давай ребята
друзья привет спасибо пожалуйста думаю знаете знаю говорю сказать сделать делать хочу хотите хотел типа короче
about after again also another because before being between could doing during every first going have just know like
make many more most much other really should since some something than that their them then there these they thing think
this those through very want well what when where which while with would your you're""".split())


def opt(cfg: dict, key: str):
    return cfg.get(key, DEFAULT[key])


def norm(w: str) -> str:
    w = (w or "").lower().replace("ё", "е")
    w = re.sub(r"[^\w-]", "", w).strip("-")
    return w


def media_info(src: Path) -> dict:
    """Размер кадра с учётом поворота (телефоны пишут «вертикаль» как горизонталь + флаг поворота), длина, есть ли звук."""
    info = probe(src)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    if v is None:
        raise RuntimeError("в файле нет видео")
    if not any(s.get("codec_type") == "audio" for s in info["streams"]):
        raise RuntimeError("в видео нет звука - ReelsEditor монтирует по речи")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = 0
    try:
        rot = int(float((v.get("tags") or {}).get("rotate", 0)))
    except ValueError:
        pass
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if abs(rot) % 180 == 90:
        w, h = h, w
    return {"w": w, "h": h, "dur": float(info["format"].get("duration") or 0)}


# ---------- что вырезать ----------

def find_drops(words: list[tuple[float, float, str]], fillers: str, repeats: bool) -> tuple[set[int], dict]:
    """Индексы слов, которые уходят: паразиты, повторы подряд, фальстарты. И счётчики для отчёта."""
    toks = [norm(w) for _, _, w in words]
    drop: set[int] = set()
    stats = {"fillers": 0, "repeats": 0}
    if fillers != "off":
        for i, t in enumerate(toks):
            if t and FILLER_RE.match(t.replace("-", "")):
                drop.add(i)
                stats["fillers"] += 1
        if fillers == "strict":
            for i, t in enumerate(toks):
                if t in STRICT_ONE and i not in drop:
                    drop.add(i)
                    stats["fillers"] += 1
            for a, b in STRICT_SEQ:
                for i in range(len(toks) - 1):
                    if toks[i] == a and toks[i + 1] == b and i not in drop:
                        drop.update((i, i + 1))
                        stats["fillers"] += 1
    if repeats:
        live = [i for i in range(len(toks)) if i not in drop and toks[i]]
        lt = [toks[i] for i in live]
        gone: set[int] = set()
        for n in (4, 3, 2, 1):
            k = 0
            while k + 2 * n <= len(lt):
                if lt[k:k + n] == lt[k + n:k + 2 * n] and not any(live[j] in gone for j in range(k, k + 2 * n)):
                    if n == 1 and (lt[k] in ALLOW_DUP or len(lt[k]) < 3):
                        k += 1
                        continue
                    gone.update(live[j] for j in range(k, k + n))     # остаётся второй (удачный) вариант
                    stats["repeats"] += 1
                    k += n
                else:
                    k += 1
        for j in range(len(lt) - 1):          # фальстарт: «прив привет», «пятьд пятьдесят»
            a, b = lt[j], lt[j + 1]
            if live[j] in gone or live[j + 1] in gone:
                continue
            if 2 <= len(a) < len(b) and b.startswith(a) and words[live[j + 1]][0] - words[live[j]][1] < 1.5 and a not in ALLOW_DUP:
                gone.add(live[j])
                stats["repeats"] += 1
        drop |= gone
    return drop, stats


def plan_segments(words, drop: set[int], thr: float, dur: float) -> list[tuple[float, float]]:
    """Отрезки исходника, которые остаются: слова с небольшим запасом, паузы длиннее thr вырезаны, выкинутые слова - всегда разрез."""
    segs: list[list[float]] = []
    prev = None
    for i, (s, e, _) in enumerate(words):
        if i in drop:
            continue
        if prev is None:
            segs.append([max(0.0, s - PAD_IN), e])
            prev = i
            continue
        dropped = [j for j in range(prev + 1, i) if j in drop]
        pe = words[prev][1]
        if dropped or s - pe > thr:
            limit = words[dropped[0]][0] if dropped else s - PAD_IN
            segs[-1][1] = max(segs[-1][1], min(pe + PAD_OUT, max(pe, limit)))
            start = max(s - PAD_IN, words[dropped[-1]][1] if dropped else pe + PAD_OUT)
            segs.append([min(start, s), e])
        else:
            segs[-1][1] = e
        prev = i
    if not segs:
        return []
    segs[-1][1] = min(dur, segs[-1][1] + PAD_OUT)
    out: list[tuple[float, float]] = []
    for s, e in segs:                       # на сетку кадров: длительности картинки и звука совпадут точно
        s2, e2 = math.floor(s * FPS) / FPS, min(math.ceil(e * FPS) / FPS, math.floor(dur * FPS) / FPS)
        if out and s2 <= out[-1][1] + 1e-6:
            out[-1] = (out[-1][0], max(out[-1][1], e2))
        elif e2 - s2 >= 4 / FPS:
            out.append((s2, e2))
    return out


def remap(t: float, segs: list[tuple[float, float]], offs: list[float]) -> float | None:
    for (s, e), o in zip(segs, offs):
        if s - 1e-6 <= t <= e + 1e-6:
            return o + (t - s)
    return None


def accents(words, keep: list[int], audio16: np.ndarray, new_times: dict[int, float], total: float) -> list[int]:
    """Акценты: самые громкие содержательные слова (и слова с цифрами), не чаще раза в 3,5 с."""
    cand = []
    for i in keep:
        t = norm(words[i][2])
        if (len(t) < 4 or t in STOP) and not re.search(r"\d", t):
            continue
        a, b = int(words[i][0] * 16000), int(words[i][1] * 16000)
        seg = audio16[a:b]
        if len(seg) < 160:
            continue
        cand.append((float(np.sqrt(np.mean(seg ** 2))) + (0.02 if re.search(r"\d", t) else 0.0), i))
    if not cand:
        return []
    med = float(np.median([c for c, _ in cand])) or 1e-6
    cand = [(c / med, i) for c, i in cand if c / med > 1.08]
    cand.sort(reverse=True)
    pick: list[int] = []
    limit = max(1, int(total / 5))
    for _, i in cand:
        t = new_times[i]
        if t < 1.0 or t > total - 1.0:
            continue
        if all(abs(t - new_times[j]) >= 3.5 for j in pick):
            pick.append(i)
        if len(pick) >= limit:
            break
    return sorted(pick, key=lambda i: new_times[i])


def plan_pieces(segs, offs, acc_times: list[float], zoom_mode: str) -> list[dict]:
    """Куски для кодирования: отрезок исходника, масштаб, склейка ли перед ним (для звука).
    Зум на акценте держится до конца отрезка, но не дольше 1,8 с; в динамичном режиме масштаб меняется через склейку."""
    pieces = []
    for k, ((s, e), o) in enumerate(zip(segs, offs)):
        base = 1.0 if zoom_mode != "dynamic" else (1.0 if k % 2 == 0 else 1.08)
        bounds = [(s, base)]
        if zoom_mode != "off":
            for at in acc_times:
                if o <= at < o + (e - s) - 0.2:
                    a = s + (at - o)
                    b = min(e, a + 1.8)
                    bounds.append((math.floor(a * FPS) / FPS, base + 0.14))
                    if b < e - 0.15:
                        bounds.append((math.floor(b * FPS) / FPS, base))
        bounds.sort()
        clean = []
        for t, z in bounds:
            if clean and t - clean[-1][0] < 6 / FPS:      # слишком короткий кусок - объединяем
                clean[-1] = (clean[-1][0], z)
            else:
                clean.append((t, z))
        for j, (t, z) in enumerate(clean):
            end = clean[j + 1][0] if j + 1 < len(clean) else e
            n = int(round((end - t) * FPS))
            if n > 0:
                pieces.append({"s": t, "n": n, "z": z, "cut": j == 0})
    return pieces


# ---------- кодирование ----------

def _run(cmd: list[str], timeout: int = 900, cwd: Path | None = None) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg: " + res.stderr[-400:])


def _even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


def piece_filter(w: int, h: int, z: float, frame: str, W: int = W, H: int = H) -> str:
    """Кадр исходника -> кадр W x H (формат ролика) с масштабом z. blur - исходник целиком на размытом фоне,
    если он заметно шире целевого формата."""
    landscape = w / max(h, 1) > (W / H) * 1.25
    if frame == "blur" and landscape:
        fw = _even(W * z)
        return (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2[bg];"
                f"[b]scale={fw}:-2,crop={W}:ih[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}[v]")
    if w / h > W / H:
        ch, cw = h, h * W / H
    else:
        cw, ch = w, w * H / W
    cw, ch = cw / z, ch / z
    x = (w - cw) / 2
    y = max(0.0, min(h - ch, (h - ch) * 0.4))        # лицо обычно выше центра
    return f"[0:v]crop={_even(cw)}:{_even(ch)}:{int(x)}:{int(y)},scale={W}:{H}:flags=lanczos,setsar=1,fps={FPS}[v]"


def encode_pieces(src: Path, pieces: list[dict], info: dict, frame: str, work: Path, W: int = W, H: int = H) -> Path:
    lst = work / "pieces.txt"
    lines = []
    for k, p in enumerate(pieces):
        out = work / f"p{k:04d}.mp4"
        _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{p['s']:.4f}", "-i", str(src), "-filter_complex",
              piece_filter(info["w"], info["h"], p["z"], frame, W, H), "-map", "[v]", "-frames:v", str(p["n"]), "-an",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])
        lines.append(f"file '{out.name}'")
    lst.write_text("\n".join(lines) + "\n")
    video = work / "cut_video.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", video.name], cwd=work)
    return video


def cut_audio(wav_in: Path, pieces: list[dict], wav_out: Path) -> float:
    """Звук по тем же границам, что и картинка; на склейках - затухания по 12 мс."""
    with wave.open(str(wav_in)) as w:
        ch, sr = w.getnchannels(), w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, ch).astype(np.float32)
    parts = []
    f = int(FADE * sr)
    for k, p in enumerate(pieces):
        a = int(round(p["s"] * sr))
        n = int(round(p["n"] / FPS * sr))
        seg = data[a:a + n]
        if len(seg) < n:
            seg = np.vstack([seg, np.zeros((n - len(seg), ch), np.float32)])
        seg = seg.copy()
        if p["cut"] and f and len(seg) > 2 * f:
            seg[:f] *= np.linspace(0, 1, f)[:, None]
        nxt_cut = k + 1 >= len(pieces) or pieces[k + 1]["cut"]
        if nxt_cut and f and len(seg) > 2 * f:
            seg[-f:] *= np.linspace(1, 0, f)[:, None]
        parts.append(seg)
    out = np.vstack(parts) if parts else np.zeros((1, ch), np.float32)
    with wave.open(str(wav_out), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.clip(out, -32768, 32767).astype(np.int16).tobytes())
    return len(out) / sr


# ---------- вставки ----------

EN_STOP = set("""about above after again against also always another anything around away back because been before being below
between both bring came come could does doing done down during each even every first from going gonna good have having here into
just keep know last like little look made make many maybe more most much must need never next only other over people pretty
really reason right said same second should show since some something still such sure take tell than thank thanks that their them
then there these they thing things think this those though three through time today told together tomorrow under until very want
well were what when where which while will with without would year years your yours you're they're it's don't that's there's
important different simple great small large huge little main whole special possible usual common easy hard high long young
best better worse worst real true false actually basically probably exactly certainly absolutely already almost enough
everyone everything someone nothing anyone somebody nobody""".split())


# предметные слова, которые хорошо находятся на стоках: идут в запрос первыми
VISUAL = set("""money cash coins coin bank card wallet salary price shop shopping store market coffee food bread pizza kitchen
restaurant house home apartment room office desk computer laptop phone smartphone screen city street road car taxi bus train
plane airport travel beach ocean river forest mountain night morning sunrise sunset rain snow winter summer family child
children baby friends woman man couple students school university book books library doctor hospital medicine gym sport
running football music guitar party wedding dog cat garden flowers business meeting team chart graph stock investor
investment savings piggy calendar clock time watch tickets ticket camera film cinema movie studio actor actress director
village farm factory worker construction bridge ship boat church castle history war soldier army map flag crowd concert
sky stars space rocket robot technology code internet subscription taxes receipt gold diamond fire water ice""".split())


def en_queries(text: str) -> list[str]:
    """Кандидаты в запрос к стокам из английской фразы: по одному слову, сначала «предметные», потом самые длинные."""
    words = [w for w in re.findall(r"[a-z]{3,}", (text or "").lower())
             if w not in EN_STOP and not w.endswith("ly") and (len(w) >= 4 or w in VISUAL)]
    uniq = list(dict.fromkeys(words))
    stem = lambda w: w[:-1] if w.endswith("s") and w[:-1] in VISUAL else w  # noqa: E731
    uniq = list(dict.fromkeys(stem(w) for w in uniq))
    return sorted(uniq, key=lambda w: (w not in VISUAL, -len(w)))[:3]


def en_query(text: str) -> str:
    return " ".join(en_queries(text)[:2])


def broll_plan(items: list[tuple[float, str]], total: float, mode: str, acc_times: list[float],
               en: list[tuple[float, float, str]] | None = None) -> list[tuple[float, float, str, str]]:
    """Окна для вставок: (начало, конец, запрос, фраза). items - (время в новом ролике, слово);
    en - английский перевод фраз в новом времени: по нему запрос (как в умном монтаже); без перевода - русское ключевое слово."""
    if mode == "off" or total < 8:
        return []
    step, length = (9.0, 2.2) if mode == "rare" else (5.0, 1.8)
    out, t = [], 2.5
    while t < total - 3.0:
        idx = next((k for k, (wt, _) in enumerate(items) if wt >= t), None)
        if idx is None:
            break
        ws = items[idx][0]
        we = min(ws + length, total - 1.5)
        if we - ws < 1.0:
            break
        if any(ws - 0.3 <= a <= we for a in acc_times):        # на акценте лицо крупным планом важнее вставки
            t = ws + 1.0
            continue
        q, ctx = "", ""
        if en:
            seg = next((x for x in en if x[0] - 0.3 <= ws <= x[1] + 0.3), None) or min(en, key=lambda x: abs(x[0] - ws))
            if abs(seg[0] - ws) < 6:
                q, ctx = "|".join(en_queries(seg[2])), seg[2]
        if not q and not en:
            words = [norm(w) for wt, w in items if ws - 0.2 <= wt <= we + 1.2]
            kw = [w for w in words if len(w) >= 5 and w not in STOP and not re.search(r"\d", w)]
            q = max(kw, key=len) if kw else ""
        if q:
            out.append((round(ws, 3), round(we, 3), q, ctx))
            t = we + step
        else:
            t = ws + 1.0
    return out


def _phrases(ws: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Слова английской речи -> фразы (по знакам препинания), чтобы запрос строился из целой мысли."""
    out, cur = [], []
    for w in ws:
        cur.append(w)
        if w[2][-1:] in ".!?;," and len(cur) >= 3:
            out.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
            cur = []
    if cur:
        out.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
    return out


def fetch_broll(plan: list[tuple[float, float, str, str]], lang: str, report: list) -> list[tuple[float, float, Path]]:
    """Английский запрос - подбор умного монтажа (совпадение слов запроса с описанием ролика, лишнее отбрасывается);
    русский (перевод не получился) - поиск стоков на языке речи."""
    from core import smart, sources
    from common.media import video_duration
    got, used_paths, used_ids = [], set(), set()
    for a, b, q, ctx in plan:
        need = (b - a) + 0.1
        if re.fullmatch(r"[a-z|]+", q):
            # пробуем слова по очереди; берём только уверенное совпадение с описанием ролика - лучше без вставки, чем мимо
            for word in q.split("|"):
                try:
                    cands = smart.stock_candidates(word, used_ids, report, ctx or word)
                except Exception as e:  # noqa: BLE001
                    report.append(f"стоки: {str(e)[:100]}")
                    break
                pick = next((p for p, cov in cands if cov >= smart.MIN_COV and p not in used_paths
                             and (video_duration(p) or 0) >= need), None)
                if pick is not None:
                    used_paths.add(pick)
                    got.append((a, b, pick))
                    break
            continue
        try:
            items, errs = sources.search(q, per_page=6, lang=lang)
        except sources.NoKeys:
            report.append("ключи Pexels/Pixabay не заданы - вставки пропущены")
            return got
        except Exception as e:  # noqa: BLE001
            report.append(f"стоки: {str(e)[:100]}")
            continue
        for it in items:
            if (it.get("duration") or 0) < need + 0.2:
                continue
            try:
                path = sources.add_item(it, "reels")["path"]
            except Exception as e:  # noqa: BLE001
                report.append(f"вставка не скачалась: {str(e)[:80]}")
                continue
            if path not in used_paths and (video_duration(path) or 0) >= need:
                used_paths.add(path)
                got.append((a, b, path))
                break
    return got


# ---------- сборка ----------

def build(src: Path, task_dir: Path, cfg: dict, scfg: dict, lang_hint: str | None = None,
          fmt: str = "9:16", brand: dict | None = None) -> dict:
    """-> {"path", "title", "text", "stats"}; scfg - настройки умного монтажа (субтитры, музыка);
    fmt - формат ролика (9:16, 4:5, 1:1, 16:9); brand - бренд-профиль (цвет субтитров, логотип, призыв)."""
    from core import brand as brandmod, fontpack, formats, smart, statfx, subtitles
    fmt = formats.norm(fmt)
    W, H = formats.size(fmt)
    pry = formats.play_res_y(fmt)
    work = task_dir / "reels"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    info = media_info(src)
    if info["dur"] > config.REELS_MAX_MINUTES * 60 + 1:
        raise RuntimeError(f"видео длиннее {config.REELS_MAX_MINUTES} мин")
    wav = work / "voice48.wav"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(wav)])
    lang_hint = lang_hint or (None if opt(cfg, "lang") == "auto" else opt(cfg, "lang"))
    words, lang = subtitles.transcribe_words_lang(wav, lang_hint, prompt=PROMPT.get(lang_hint or ""), vad=False)
    words = [(float(s), float(e), t) for s, e, t in words if e > s]
    if len(words) < 3:
        raise RuntimeError("в видео не нашлось речи - ReelsEditor монтирует ролики, где человек говорит в камеру")
    drop, stats = find_drops(words, opt(cfg, "fillers"), bool(opt(cfg, "repeats")))
    segs = plan_segments(words, drop, PAUSE_THR[opt(cfg, "pauses")], info["dur"])
    if not segs:
        raise RuntimeError("после чистки ничего не осталось - выберите паузы «мягко»")
    offs, acc_t = [], 0.0
    for s, e in segs:
        offs.append(acc_t)
        acc_t += e - s
    keep = [i for i in range(len(words)) if i not in drop]
    new_t = {i: t for i in keep if (t := remap(words[i][0], segs, offs)) is not None}
    keep = [i for i in keep if i in new_t]
    total = acc_t
    audio16 = subtitles.load_audio(wav)
    acc = accents(words, keep, audio16, new_t, total) if opt(cfg, "zoom") != "off" else []
    acc_times = [new_t[i] for i in acc]
    pieces = plan_pieces(segs, offs, acc_times, opt(cfg, "zoom"))
    video = encode_pieces(src, pieces, info, opt(cfg, "frame"), work, W, H)
    voice = work / "voice_cut.wav"
    total = cut_audio(wav, pieces, voice)

    report: list[str] = []
    items = [(new_t[i], words[i][2]) for i in keep]
    broll = []
    if opt(cfg, "broll") != "off":
        en: list[tuple[float, float, str]] = []
        if (lang or "ru") != "en":
            try:       # перевод речи на английский: запросы к стокам как в умном монтаже
                for s0, e0, txt in subtitles.translate_segments(wav, lang or lang_hint):
                    ts = [new_t[i] for i in keep if s0 - 0.05 <= words[i][0] <= e0 + 0.05]
                    if ts:
                        en.append((min(ts), max(ts) + 0.4, txt))
            except Exception as e:  # noqa: BLE001
                log.warning("перевод для вставок не получился: %s", e)
        else:
            en = [(new_t[i], new_t[i] + 0.4, words[i][2]) for i in keep]
            en = _phrases(en)
        broll = fetch_broll(broll_plan(items, total, opt(cfg, "broll"), acc_times, en), lang or "ru", report)

    # субтитры - тем же оформлением, что в умном монтаже
    fontpack.ensure()
    statfx.set_theme(smart.opt(scfg, "theme"))
    events: list[str] = []
    if scfg.get("subs", True) and smart.opt(scfg, "sub_style") != "off":
        tokens = [words[i][2] for i in keep]
        times = [new_t[i] for i in keep]
        calm = smart.opt(scfg, "sub_style") == "calm"
        grp = smart.sub_groups(tokens, times, total, *((8, 44, False) if calm else (3, 22, True)))
        y = smart.SUB_POS[smart.opt(scfg, "sub_pos")] * pry / 1920
        sc = smart.SUB_SIZES[smart.opt(scfg, "sub_size")] * formats.SUB_SCALE[fmt]
        events = (statfx.calm_subtitle_events(grp, y=y, scale=sc) if calm else
                  statfx.subtitle_events(grp, upper=smart.opt(scfg, "sub_case") == "upper", y=y, scale=sc,
                                         style=smart.opt(scfg, "sub_style"), accent=brandmod.color_hex(brand)))
    cta = brandmod.cta_events(brand, total, fmt, y_frac=(smart.SUB_POS[smart.opt(scfg, "sub_pos")] / 1920))
    if cta:          # последние 3 с: плашка призыва на месте субтитров (лицо не закрываем)
        events = brandmod.clip_events(events, total - brandmod.CTA_SECONDS) + cta
    if events:
        brandmod.write_ass(events, fmt, work / "subs.ass")

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video.name, "-i", voice.name]
    filt, cur = [], "0:v"
    for j, (a, b, path) in enumerate(broll):
        cmd += ["-t", f"{b - a + 0.2:.3f}", "-i", str(path)]
        filt.append(f"[{j + 2}:v]setpts=PTS-STARTPTS+{a:.3f}/TB,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                    f"setsar=1,fps={FPS}[b{j}]")
        filt.append(f"[{cur}][b{j}]overlay=enable='between(t,{a:.3f},{b:.3f})':eof_action=pass[o{j}]")
        cur = f"o{j}"
    nin = len(broll) + 2
    logo = brandmod.logo_path(brand)
    if logo:
        cmd += ["-i", str(logo)]
        filt.append(brandmod.logo_filter(brand, fmt, cur, f"{nin}:v", "lgv"))
        cur, nin = "lgv", nin + 1
    filt.append(f"[{cur}]" + ("ass=subs.ass," if events else "") + "format=yuv420p[vout]")
    voice_f = "highpass=f=80,afftdn=nf=-25,loudnorm=I=-14:TP=-1.5:LRA=11" if opt(cfg, "sound") else "anull"
    music = None
    if scfg.get("music", "off") not in ("off", None):
        from core import mix
        music = mix._music(scfg["music"])
    if music:
        mi = nin
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        vol = max(0.02, min(0.5, float(smart.opt(scfg, "music_vol")) / 100))
        filt.append(f"[1:a]{voice_f}[vo];[{mi}:a]volume={vol:.2f},atrim=0:{total:.3f}[mu];"
                    "[vo][mu]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
    else:
        filt.append(f"[1:a]{voice_f}[aout]")
    out = task_dir / "final.mp4"
    cmd += ["-filter_complex", ";".join(filt), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-ar", str(SR),
            "-movflags", "+faststart", str(out)]
    _run(cmd, timeout=1800, cwd=work)
    text = " ".join(words[i][2] for i in keep)
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    stats.update({"before": info["dur"], "after": total, "cuts": len(segs) - 1, "accents": len(acc), "broll": len(broll),
                  "lang": lang, "report": report})
    shutil.rmtree(work, ignore_errors=True)
    return {"path": out, "title": first[:90], "text": text, "stats": stats}


def report_text(stats: dict) -> str:
    b, a = stats["before"], stats["after"]
    mm = lambda t: f"{int(t) // 60}:{int(t) % 60:02}"  # noqa: E731
    lines = [f"⏱ Было {mm(b)} → стало {mm(a)} (минус {max(0, b - a):.0f} с)",
             f"✂️ Склеек: {stats['cuts']}; слов-паразитов убрано: {stats['fillers']}; повторов и оговорок: {stats['repeats']}",
             f"🔍 Зумов на акцентах: {stats['accents']}; 🎬 вставок: {stats['broll']}"]
    lines += [f"⚠️ {r}" for r in dict.fromkeys(stats.get("report") or [])][:3]
    return "\n".join(lines)
