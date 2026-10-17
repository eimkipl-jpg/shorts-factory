"""Перевод субтитров отдельно от речи: речь остаётся как есть, а титры показываются на другом языке.

Титры (группы по 2-3 слова с таймингами) склеиваются обратно в фразы, фразы переводятся целиком (так перевод осмысленный),
затем перевод снова режется на короткие титры и раскладывается по времени исходной фразы пропорционально длине слов.
Модели перевода те же, что у дубляжа (<ROOT>/models/opus-<src>-<tgt>); если прямой пары нет, перевод идёт через английский.
"""
import logging
import re

from core import dub

log = logging.getLogger("subtrans")

LANGS = ("en", "ru", "de", "es", "fr")
MAX_WORDS = 14     # самая длинная фраза для перевода, слов
GAP = 0.7          # пауза между титрами, после которой начинается новая фраза, с


def _sentences(groups: list[tuple[float, float, str]]) -> list[list[tuple[float, float, str]]]:
    out, cur, words = [], [], 0
    for g in groups:
        if cur and (g[0] - cur[-1][1] > GAP or words >= MAX_WORDS):
            out.append(cur)
            cur, words = [], 0
        cur.append(g)
        words += len(g[2].split())
        if re.search(r"[.!?…]+[»\"')]*$", g[2].strip()):
            out.append(cur)
            cur, words = [], 0
    if cur:
        out.append(cur)
    return out


def _route(src: str, dst: str) -> list[tuple[str, str]] | None:
    pairs = set(dub.available_pairs())
    if f"{src}-{dst}" in pairs:
        return [(src, dst)]
    if src != "en" and dst != "en" and f"{src}-en" in pairs and f"en-{dst}" in pairs:
        return [(src, "en"), ("en", dst)]
    return None


def _split(text: str, a: float, b: float, max_words: int = 3) -> list[tuple[float, float, str]]:
    words = text.split()
    if not words or b <= a:
        return []
    chunks = [words[i:i + max_words] for i in range(0, len(words), max_words)]
    weights = [sum(len(w) + 1 for w in c) for c in chunks]
    total = float(sum(weights))
    out, t = [], a
    for c, w in zip(chunks, weights):
        t2 = t + (b - a) * w / total
        out.append((t, t2, " ".join(c)))
        t = t2
    return out


def translate_groups(groups: list[tuple[float, float, str]], src: str | None, dst: str | None,
                     warnings: list | None = None) -> list[tuple[float, float, str]]:
    """Титры на языке dst. Если перевести нельзя (язык не определён, нет модели), возвращает титры как есть с замечанием."""
    if not groups or not dst or dst not in LANGS:
        return groups
    src = (src or "").lower()[:2]
    if src == dst:
        return groups
    note = warnings.append if warnings is not None else (lambda _m: None)
    if src not in LANGS:
        note(f"субтитры не переведены: язык речи не определён ({src or '?'})")
        return groups
    route = _route(src, dst)
    if not route:
        have = ", ".join(dub.available_pairs()) or "нет ни одной"
        note(f"субтитры не переведены: нет модели перевода {src}→{dst} (установлены: {have})")
        return groups
    sents = _sentences(groups)
    texts = [" ".join(g[2] for g in s) for s in sents]
    try:
        for a, b in route:
            texts = dub.translate(texts, a, b)
    except Exception as e:  # noqa: BLE001 - без перевода ролик всё равно собирается
        log.warning("перевод субтитров %s→%s не удался: %s", src, dst, e)
        note(f"субтитры не переведены ({str(e)[:120]})")
        return groups
    finally:
        dub.unload_translators()
    out = []
    for s, t in zip(sents, texts):
        out += _split(t, s[0][0], s[-1][1])
    log.info("субтитры переведены %s→%s: фраз %d", src, dst, len(sents))
    return out
