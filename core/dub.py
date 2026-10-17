"""Дубляж: речь из видео -> Whisper -> перевод (CTranslate2, opus-mt) -> озвучка -> голосовая дорожка + субтитры.

Модели перевода лежат в <ROOT>/models/opus-<src>-<tgt>/ (конвертируются один раз, см. README).
Расшифровки клипов кэшируются в <ROOT>/cache/transcripts/, поэтому повторное использование клипа Whisper не грузит.
"""
import gc
import hashlib
import json
import logging
import random
from pathlib import Path

from common import config
from common.media import audio_duration, run

log = logging.getLogger("dub")

MAX_TEMPO = 1.45  # максимальное ускорение озвучки, чтобы уложиться в тайминг оригинала (и догнать его после длинной фразы)
# срез тишины по краям озвучки: у Edge-TTS это 0.1-0.4 с с каждой стороны, из-за неё фразы опаздывали и сдвигали следующие
_TRIM = "silenceremove=start_periods=1:start_threshold=-42dB:start_duration=0.02:start_silence=0.03"

DEFAULT_VOICES = {
    "en": "en-GB-RyanNeural", "ru": "ru-RU-DmitryNeural", "de": "de-DE-ConradNeural",
    "es": "es-ES-AlvaroNeural", "fr": "fr-FR-HenriNeural",
}


# ---------- 1. Распознавание с кэшем ----------

def _cache_path(clip: Path) -> Path:
    st = clip.stat()
    key = hashlib.sha1(f"{clip}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:20]
    return config.ROOT / "cache" / "transcripts" / f"{key}.json"


def cached(clip: Path) -> dict | None:
    """Результат analyze, если клип уже разбирался (без запуска Whisper), иначе None."""
    cp = _cache_path(clip)
    if not cp.exists():
        return None
    try:
        return json.loads(cp.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def order_for_dub(pool: list[Path], target: str) -> list[Path]:
    """Порядок проверки клипов для перевода: сначала уже разобранные, в которых есть речь на другом языке,
    потом ещё не разобранные; разобранные без речи (или уже на нужном языке) пропускаются."""
    import random
    ready, unknown = [], []
    for c in pool:
        info = cached(c)
        if info is None:
            unknown.append(c)
        elif info.get("segments") and info.get("lang") != target:
            ready.append(c)
    random.shuffle(ready)
    random.shuffle(unknown)
    return ready + unknown


def analyze(clip: Path) -> dict:
    """{'lang': 'ru', 'segments': [[start, end, text], ...]} для всей речи в видео."""
    cp = _cache_path(clip)
    if cp.exists():
        return json.loads(cp.read_text("utf-8"))
    from core import subtitles

    try:
        audio = subtitles.load_audio(clip)
    except RuntimeError:  # нет звуковой дорожки
        audio = None
    result = {"lang": "", "segments": []}
    if audio is not None and len(audio) > 16000:
        segs, info = subtitles._get_model().transcribe(audio, language=None, vad_filter=True)
        result["lang"] = info.language
        result["segments"] = [[s.start, s.end, s.text.strip()] for s in segs if s.text.strip()]
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    return result


def pick_window(segments: list, target: float) -> list:
    """Непрерывная группа фраз длиной около target секунд, начинающаяся с начала фразы."""
    i = random.randrange(len(segments))
    chosen = [segments[i]]
    for seg in segments[i + 1:]:
        if seg[1] - chosen[0][0] > target * 1.2:
            break
        chosen.append(seg)
    return chosen


# ---------- 2. Перевод ----------

_translators: dict = {}


def available_pairs() -> list[str]:
    d = config.ROOT / "models"
    return sorted(p.name.removeprefix("opus-") for p in d.glob("opus-*")) if d.exists() else []


def translate(texts: list[str], src: str, tgt: str) -> list[str]:
    model_dir = config.ROOT / "models" / f"opus-{src}-{tgt}"
    if not model_dir.exists():
        raise RuntimeError(
            f"нет модели перевода {src}->{tgt} ({model_dir}). Установлены: {', '.join(available_pairs()) or 'ничего'}"
        )
    import ctranslate2
    import transformers

    key = f"{src}-{tgt}"
    if key not in _translators:
        _translators[key] = (
            ctranslate2.Translator(str(model_dir), device="cpu", compute_type="int8", intra_threads=2),
            transformers.AutoTokenizer.from_pretrained(f"Helsinki-NLP/opus-mt-{src}-{tgt}"),
        )
    translator, tok = _translators[key]
    tokens = [tok.convert_ids_to_tokens(tok.encode(t)) for t in texts]
    results = translator.translate_batch(tokens, beam_size=4)
    return [tok.decode(tok.convert_tokens_to_ids(r.hypotheses[0]), skip_special_tokens=True).strip() for r in results]


def unload_translators() -> None:
    _translators.clear()
    gc.collect()


# ---------- 3. Озвучка с подгонкой по времени ----------

def pick_voice(target: str, preferred: str | None) -> str:
    if preferred and preferred[:2] == target:
        return preferred
    if target in DEFAULT_VOICES:
        return DEFAULT_VOICES[target]
    raise RuntimeError(f"нет голоса для языка «{target}» (есть: {', '.join(DEFAULT_VOICES)})")


def synth_fit(text: str, voice: str, slot: float, workdir: Path, name: str, rate: int | None = None) -> tuple[Path, float]:
    """Озвучивает фразу и ускоряет её (не более MAX_TEMPO), если она длиннее слота оригинала."""
    from core import tts

    raw = workdir / f"{name}.mp3"
    tts.synthesize(text, raw, voice, rate)
    trimmed = workdir / f"{name}_t.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", f"{_TRIM},areverse,{_TRIM},areverse",
         "-ar", "44100", "-ac", "1", str(trimmed)])
    src = trimmed if trimmed.exists() and audio_duration(trimmed) >= 0.2 else raw
    dur = audio_duration(src)
    tempo = min(dur / slot, MAX_TEMPO) if slot > 0.3 and dur > slot else 1.0
    wav = workdir / f"{name}.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter:a", f"atempo={tempo:.3f}",
         "-ar", "44100", "-ac", "1", str(wav)])
    return wav, dur / tempo


def build_voice(segs: list, texts: list[str], voice: str, workdir: Path, tag: str,
                rate: int | None = None) -> tuple[list, float]:
    """segs - фразы относительно начала окна. Возвращает [(старт, wav, длина)] и конец последней фразы."""
    placed, cursor = [], 0.0
    for k, (seg, text) in enumerate(zip(segs, texts)):
        nxt = segs[k + 1][0] if k + 1 < len(segs) else seg[1] + 1.0
        start = max(seg[0], cursor)  # фразы не наезжают друг на друга
        # слот считается от реального старта: если предыдущая фраза затянулась, эта читается быстрее и догоняет оригинал
        slot = max(nxt - start, 0.5)
        wav, length = synth_fit(text, voice, slot, workdir, f"{tag}_s{k:02}", rate)
        placed.append((start, wav, length))
        cursor = start + length + 0.05
    return placed, cursor


def mix_voice_track(placed: list, total: float, out: Path) -> None:
    """Собирает дорожку заданной длины: каждая фраза ставится на свой тайм-код."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for _, wav, _ in placed:
        cmd += ["-i", str(wav)]
    parts = [f"[{i}:a]adelay={int(s * 1000)}:all=1[a{i}]" for i, (s, _, _) in enumerate(placed)]
    labels = "".join(f"[a{i}]" for i in range(len(placed)))
    n = max(int(round(total * 44100)), 1)   # длина по числу сэмплов, чтобы дорожка совпадала с куском видео
    fc = (";".join(parts) + f";{labels}amix=inputs={len(placed)}:duration=longest:dropout_transition=0:normalize=0,"
          f"aresample=44100,apad=whole_len={n},atrim=end_sample={n}[a]")
    cmd += ["-filter_complex", fc, "-map", "[a]", "-ar", "44100", "-ac", "1", str(out)]
    run(cmd)


# ---------- 4. Субтитры из перевода ----------

def subtitle_groups(placed: list, texts: list[str], offset: float, limit: float, max_words: int = 3) -> list:
    """Слова фразы равномерно раскладываются по времени озвучки, по 2-3 слова в титре."""
    groups = []
    for (start, _, length), text in zip(placed, texts):
        words = text.split()
        if not words:
            continue
        per = length / len(words)
        for i in range(0, len(words), max_words):
            chunk = words[i:i + max_words]
            a = offset + start + i * per
            b = min(offset + start + (i + len(chunk)) * per, offset + limit)
            if b > a:
                groups.append((a, b, " ".join(chunk)))
    return groups
