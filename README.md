# Shorts Factory

[![CI](https://github.com/OWNER/shorts-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/shorts-factory/actions)
![Python](https://img.shields.io/badge/python-3.11-blue)
![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-orange)

A Telegram bot that turns a script, a raw talking-head recording or a link into a finished vertical video
for Shorts, Reels and TikTok: voice-over, word-synced subtitles, stock footage, maps, cards and cuts.
It runs as a single-VPS service (2 vCPU, 4 GB RAM) and ships as a **multi-user product** with credits,
Telegram Stars payments, quotas, abuse protection and an admin analytics panel.

> Русская версия: [README.ru.md](README.ru.md) · full Russian user guide: [docs/USER_GUIDE.ru.md](docs/USER_GUIDE.ru.md)

## What it does

| Mode | Input | Result |
|---|---|---|
| **Smart montage** | A topic. The bot writes a prompt, the user runs it in any LLM and sends the JSON back | Video with TTS voice-over, word-synced subtitles, a stock clip or image for every phrase, info cards, charts and historical maps |
| **ReelsEditor** | Raw video of a person talking to the camera | Dead air, filler words and false starts cut out, punch-in zooms, B-roll inserts, subtitles, call-to-action |
| **UGC ads** | Product description | Several ad variants with different hooks, built from one brief |
| **Reference analysis** | Link or file of someone else's video | Shot/cut structure, speech tempo, hook, CTA and a storyboard; "make a similar one" prompt |
| **Cutting** | Long video or link | Scene-based or "best moments" fragments (album or zip) |
| **Download** | Link (YouTube, TikTok, Instagram, VK ...) | The video in chat, re-encoded to fit Telegram limits |
| **Brand profile / formats** | Logo, colour, CTA, hashtags; 9:16, 4:5, 1:1, 16:9 | Applied to every render |

The "bring your own LLM" flow is deliberate: the product has no per-video model cost, and users keep their favourite assistant.

## Engineering highlights

- **Job queue on SQLite** with atomic claiming, retries, per-kind failure policy (network errors are not retried),
  a hard task timeout (`setitimer`) and crash recovery.
- **One global render lock** (`fcntl.flock`) so two workers (owner bot + commercial bot) never render at once on a 2-vCPU box;
  paid users are claimed first, queue position is computed priority-aware.
- **Billing ledger**: credits, free demo with watermark, Telegram Stars (XTR) invoices.
  Payments are idempotent by charge id (Telegram may redeliver), refunds and chargebacks are one-shot via unique ledger indexes,
  failed jobs are refunded automatically and do not count toward daily limits.
- **Abuse protection**: per-user flood guard, per-user and global queue caps, daily quotas per mode,
  SSRF-safe URL checks (private/loopback/link-local/metadata addresses rejected, non-http schemes rejected),
  stop-word list, optional channel subscription gate, ban/unban.
- **Process isolation**: systemd units with `ProtectSystem=strict`, `NoNewPrivileges`, write access only to the instance's data folder,
  secrets of the other bot (`.env`, rclone config, `~/.ssh`) made inaccessible, memory caps; automatic fallback if hardening fails.
- **Admin analytics inside the bot** (`/admin`): new/active users, funnel (arrived → clicked → ordered → received result → returned),
  task success rate per mode, most used sections, UTM-like source tags via `t.me/bot?start=tag`, per-user cards,
  error feed, push alerts and a daily report, CSV export. Events store actions only, never message text.
- **Privacy**: `/delete_me` removes settings, events and brand assets; logs of video titles expire after 30 days.
- **Content pipeline**: faster-whisper word timestamps, forced alignment of the script to speech, ASS subtitles rendered by libass,
  OpenCV maps/animations, ffmpeg filter graphs generated per scene, cliché detector for LLM-written text, Russian text-to-speech
  pronunciation normalisation (numbers, abbreviations, units).
- **Stock sourcing with licence tracking**: Pexels, Pixabay, Wikimedia Commons, Openverse (commercial-use and modification licences only), NASA.
  Required attribution is appended to the video description automatically.

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Stack

Python 3.11 · aiogram 3 · SQLite · faster-whisper · edge-tts · ffmpeg/libass · OpenCV · Pillow · PySceneDetect · yt-dlp · systemd · optional local Telegram Bot API server (Docker) for files up to 2 GB.

## Repository layout

```
common/      config, SQLite access, billing/limits (creator.py), analytics (adminstats.py), notifications, backups
core/        pipeline: worker, smart montage, ReelsEditor, TTS, subtitles, cutting, formats, brand, maps, effects
gateway/     Telegram UI: owner bot (bot.py) and commercial bot (creator_bot.py + creatorui, smartui, adminui)
transport/   delivery to Google Drive (owner bot), nightly cleanup
backgrounds/ background-clip bank refill (owner bot)
systemd/     unit templates; creator_setup.py generates hardened units for the commercial instance
tests/       pytest suite (billing, analytics, URL safety, formats, text checks)
docs/        architecture, Russian user guide
```

The same code base runs two instances: the owner's personal bot (`gateway/bot.py`, output to Google Drive)
and the commercial multi-user bot (`CREATOR_MODE=1`, `gateway/creator_bot.py`, output straight to the chat).

## Quick start

```bash
git clone https://github.com/OWNER/shorts-factory && cd shorts-factory
sudo apt-get install -y ffmpeg                       # plus fonts-dejavu-core
python3 -m venv venv && . venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                                      # 34 tests, no network, no Telegram token needed
```

Run the commercial bot locally:

```bash
cp .env.creator.example .env.creator                 # put a BotFather token and your Telegram ID
ENV_FILE=.env.creator python -m gateway.creator_bot &
ENV_FILE=.env.creator python -m core.worker
```

Production install on Ubuntu 24.04 (creates a second token, hardened systemd units, cron cleanup, backups, swap):

```bash
python3 creator_setup.py          # interactive
python3 creator_bigfiles.py       # optional: local Bot API server, uploads up to 2 GB
```

Configuration reference: `.env.example` (owner bot) and `.env.creator.example` (commercial bot).
Free API keys for Pexels and Pixabay are required for stock footage.

## Status

Working product deployed on a single VPS. Not included: autopublishing (stub), English UI (interface texts are Russian),
Mini App front-end (planned).

## Licence and credits

Source-available under the **PolyForm Noncommercial License 1.0.0** (see [LICENSE](LICENSE)): you may read, run and learn from the code
for non-commercial purposes. For commercial use, contact the author.
Third-party data and fonts are listed in [NOTICE.md](NOTICE.md).

Only download, cut or re-edit material you own or have the right to use.
