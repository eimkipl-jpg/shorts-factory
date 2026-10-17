"""Встроенные звуковые эффекты: синтезируются самим ffmpeg, поэтому без лицензий и без скачивания.

Файлы лежат в <ROOT>/assets/sfx/ (эту папку rclone не трогает). Имя файла до «_» = тег:
whoosh_1.wav -> тег «whoosh». Свои звуки кладите на Диск в ShortsFactory/_clips/_sfx/<тег>/.
"""
import logging
import subprocess
from pathlib import Path

from common import config

log = logging.getLogger("sfxgen")

SFX_DIR = config.ASSETS_DIR / "sfx"

# имя -> lavfi-источник и дополнительные фильтры
BUILTIN = {
    "whoosh_1": ("anoisesrc=d=0.8:c=pink:r=44100:a=0.7",
                 "highpass=f=250,lowpass=f=7000,afade=t=in:st=0:d=0.4,afade=t=out:st=0.4:d=0.4,volume=3.2"),
    "whoosh_2": ("anoisesrc=d=0.55:c=white:r=44100:a=0.6",
                 "bandpass=f=1800:width_type=h:w=2500,afade=t=in:st=0:d=0.25,afade=t=out:st=0.25:d=0.3,volume=1.6"),
    "hit_1": ("aevalsrc='sin(2*PI*(90-70*t)*t)*exp(-7*t)':d=0.6:s=44100", "volume=0.9"),
    "hit_2": ("aevalsrc='(sin(2*PI*(70-50*t)*t)+0.4*(random(0)*2-1)*exp(-40*t))*exp(-6*t)':d=0.7:s=44100", "volume=0.7"),
    "pop_1": ("aevalsrc='sin(2*PI*(600+900*t)*t)*exp(-28*t)':d=0.18:s=44100", "volume=0.9"),
    "pop_2": ("aevalsrc='sin(2*PI*(420+600*t)*t)*exp(-22*t)':d=0.22:s=44100", "volume=0.9"),
    "click_1": ("aevalsrc='(random(0)*2-1)*exp(-160*t)':d=0.08:s=44100", "highpass=f=800,volume=0.9"),
}


def ensure_builtin() -> Path:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    for name, (src, flt) in BUILTIN.items():
        out = SFX_DIR / f"{name}.wav"
        if out.exists() and out.stat().st_size > 0:
            continue
        res = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, "-af", flt,
             "-ar", "44100", "-ac", "1", str(out)],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            log.warning("не удалось создать звук %s: %s", name, res.stderr[-200:])
            out.unlink(missing_ok=True)
    return SFX_DIR
