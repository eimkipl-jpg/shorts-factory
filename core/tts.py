"""Озвучка текста. Движок выбирается через TTS_ENGINE в .env.
Чтобы добавить свой движок - напишите функцию synthesize_<name>(text, out_path) и зарегистрируйте в ENGINES.
"""
import asyncio
import logging
import re
import subprocess
from pathlib import Path

from common import config

log = logging.getLogger("tts")

CYRILLIC_LANGS = ("ru", "uk", "bg", "be", "kk", "sr", "mk")


def _speakable(text: str) -> str:
    """Убирает то, на чём Edge-TTS отвечает «No audio was received»: невидимые символы, эмодзи, пустой текст."""
    import re
    t = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", text or "")
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not re.search(r"\w", t, re.UNICODE):
        raise RuntimeError(f"в тексте нечего озвучивать: «{(text or '')[:40]}»")
    return t


def _match_voice(text: str, voice: str) -> str:
    """Edge-TTS не озвучивает кириллицу не-кириллическим голосом (en-GB-RyanNeural и т.п.) и отвечает
    «No audio was received». Если текст в основном кириллический, берём русский голос."""
    letters = re.findall(r"[^\W\d_]", text, re.UNICODE)
    if not letters:
        return voice
    cyr = sum(1 for ch in letters if "\u0400" <= ch <= "\u04ff")
    if cyr / len(letters) < 0.5 or voice[:2].lower() in CYRILLIC_LANGS:
        return voice
    new = next((v for v in config.VOICES if v[:2].lower() == "ru"), "ru-RU-DmitryNeural")
    log.warning("текст на кириллице, а голос %s не русский: озвучиваю голосом %s", voice, new)
    return new


def synthesize_edge(text: str, out_path: Path, voice: str | None = None, rate: int | None = None) -> None:
    """rate - изменение скорости в процентах: -20 медленнее, +20 быстрее.
    «No audio was received» бывает из-за сбоя сервиса Microsoft, неверного имени голоса или странного текста,
    поэтому: 3 попытки, затем без изменения скорости, затем голос по умолчанию."""
    import time

    import edge_tts
    from edge_tts.exceptions import NoAudioReceived

    text = _speakable(text)
    voice = _match_voice(text, voice or config.TTS_VOICE)
    attempts = [(voice, rate)] * 3
    if rate:
        attempts.append((voice, None))
    if voice != config.TTS_VOICE:
        attempts.append((config.TTS_VOICE, None))

    last: Exception | None = None
    for i, (v, r) in enumerate(attempts):
        kwargs = {"rate": f"{int(r):+d}%"} if r else {}

        async def _run():
            await edge_tts.Communicate(text, v, **kwargs).save(str(out_path))

        try:
            asyncio.run(_run())
            if out_path.exists() and out_path.stat().st_size > 0:
                return
            last = RuntimeError("пустой файл озвучки")
        except NoAudioReceived as e:
            last = e
        except Exception as e:  # noqa: BLE001  - сеть, таймаут, 403 от сервиса
            last = e
        out_path.unlink(missing_ok=True)
        if i < 2:
            time.sleep(2 * (i + 1))
    raise RuntimeError(
        f"Edge-TTS не вернул звук (голос {voice}, скорость {rate or 0:+d}%, текст «{text[:40]}…»): {last}. "
        "Проверьте имя голоса, доступ сервера к интернету и повторите задачу."
    ) from last


def synthesize_piper(text: str, out_path: Path, voice: str | None = None, rate: int | None = None) -> None:
    """Локальный офлайн-движок. Нужен установленный `piper` и модель в TTS_VOICE (путь к .onnx)."""
    wav = out_path.with_suffix(".wav")
    subprocess.run(
        ["piper", "--model", voice or config.TTS_VOICE, "--output_file", str(wav),
         *(["--length_scale", f"{1 / (1 + int(rate) / 100):.2f}"] if rate else [])],
        input=text.encode("utf-8"), check=True, capture_output=True,
    )
    wav.rename(out_path) if wav != out_path else None


def synthesize_elevenlabs(text: str, out_path: Path, voice: str | None = None, rate: int | None = None) -> None:
    """ElevenLabs: голос задаётся как el:<voice_id> (свой клонированный голос диктора или голос из библиотеки).
    Ключ - ELEVENLABS_API_KEY в .env, модель - ELEVENLABS_MODEL (по умолчанию eleven_multilingual_v2).
    rate (%) переводится в скорость речи ElevenLabs 0.7-1.2."""
    import time

    import requests

    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ElevenLabs: не задан ELEVENLABS_API_KEY в .env")
    voice_id = (voice or "").removeprefix("el:").strip()
    if not voice_id:
        raise RuntimeError("ElevenLabs: не указан голос (el:<voice_id>)")
    body = {"text": text, "model_id": config.ELEVENLABS_MODEL,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True,
                               "speed": max(0.7, min(1.2, 1 + (rate or 0) / 100))}}
    last: Exception | None = None
    for i in range(3):
        try:
            r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", params={"output_format": "mp3_44100_128"},
                              headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"}, json=body, timeout=180)
            if r.status_code == 200 and r.content:
                out_path.write_bytes(r.content)
                return
            msg = r.text[:200].replace("\n", " ")
            last = RuntimeError(f"HTTP {r.status_code}: {msg}")
            if r.status_code in (400, 401, 402, 403, 404, 422):   # повтор не поможет: ключ, лимит символов, голос или текст
                break
        except requests.RequestException as e:
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"ElevenLabs не вернул звук (голос {voice_id}): {last}")


ENGINES = {"edge": synthesize_edge, "piper": synthesize_piper}

# Скорость речи по умолчанию (%), если у вызова своя не задана: воркер ставит её из профиля канала перед каждой задачей.
DEFAULT_RATE = 0


def synthesize(text: str, out_path: Path, voice: str | None = None, rate: int | None = None) -> Path:
    if (voice or "").startswith("el:"):   # ElevenLabs: словарь ударений Edge не применяем, модель читает сама
        synthesize_elevenlabs(_speakable(text), out_path, voice, rate if rate else (DEFAULT_RATE or None))
        return out_path
    engine = ENGINES.get(config.TTS_ENGINE)
    if engine is None:
        raise RuntimeError(f"Неизвестный TTS_ENGINE: {config.TTS_ENGINE}")
    from core import pronounce
    rate = rate if rate else (DEFAULT_RATE or None)
    engine(pronounce.prepare(text, voice or config.TTS_VOICE), out_path, voice, rate)   # словарь только для озвучки, субтитры не трогаем
    return out_path
