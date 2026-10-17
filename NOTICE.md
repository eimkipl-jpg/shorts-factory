# Third-party materials

| Component | Where | License |
|---|---|---|
| **Cliopatria** historical borders (Seshat Global History Databank), simplified | `core/mapdata/hist.bin` | CC BY 4.0, https://github.com/Seshat-Global-History-Databank/cliopatria. The bot adds the credit line to the video description automatically (`core/histmap.py`, `CREDIT`). |
| **Natural Earth** land, lakes, cities, country names | `core/mapdata/land.bin`, `lakes.bin`, `places.json`, `names_ru.json` | Public domain, https://www.naturalearthdata.com |
| **Fira Sans** (Black, SemiBold), subset | embedded in `core/fontpack.py` | SIL Open Font License 1.1, see `core/FONTS_OFL.txt` |
| **Phosphor Icons** (Fill), subset | embedded in `core/iconpack.py` | MIT, see `core/ICONS_LICENSE.txt` |

Stock footage and images are **not** shipped. At run time the bot fetches them from Pexels, Pixabay, Wikimedia Commons,
Openverse and NASA through their public APIs and records the source and licence of every asset.
You need your own API keys and must follow each provider's terms.

Python dependencies keep their own licences (aiogram, faster-whisper, edge-tts, OpenCV, Pillow, yt-dlp, ...).

`yt-dlp` is used only for links a user submits. Download, cut and re-edit only material you own or have the right to use.
