"""Единая конфигурация: читается из .env в корне проекта."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
# Второй экземпляр (бот для креаторов) читает свой файл настроек: ENV_FILE=.env.creator в юните systemd
ENV_FILE = os.getenv("ENV_FILE") or ".env"
load_dotenv(PROJECT_DIR / ENV_FILE if not os.path.isabs(ENV_FILE) else ENV_FILE)


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "")
ALLOWED_USER_IDS = {int(x) for x in _list("ALLOWED_USER_IDS") if x.lstrip("-").isdigit()}
CHANNELS = _list("CHANNELS", "channel1") or ["channel1"]

# --- бот для креаторов (патч 39) ---
CREATOR = os.getenv("CREATOR_MODE", "0") == "1"           # этот экземпляр обслуживает чужих пользователей
DRIVE_ENABLED = os.getenv("DRIVE_ENABLED", "0" if CREATOR else "1") == "1"   # у креаторов Google Drive владельца недоступен
ADMIN_IDS = {int(x) for x in _list("ADMIN_IDS") if x.lstrip("-").isdigit()} or set(ALLOWED_USER_IDS)
RENDER_LOCK = os.getenv("RENDER_LOCK", "/tmp/shorts_render.lock")   # общий замок: два воркера не рендерят одновременно
FREE_VIDEOS = int(os.getenv("FREE_VIDEOS", "3"))           # бесплатный старт (с водяным знаком)
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "10"))          # потолок роликов в сутки на человека
MAX_QUEUED = int(os.getenv("MAX_QUEUED", "2"))             # одновременно в очереди у одного человека
MAX_QUEUE_TOTAL = int(os.getenv("MAX_QUEUE_TOTAL", "30"))  # общая очередь: дальше - «подождите»
FREE_DAILY_TOTAL = int(os.getenv("FREE_DAILY_TOTAL", "12"))   # всего бесплатных демо в сутки на весь бот (защита от кучи аккаунтов)
FREE_QUEUE_MAX = int(os.getenv("FREE_QUEUE_MAX", "5"))        # бесплатных роликов одновременно в очереди; платные идут вперёд
CREATOR_MAX_SECONDS = int(os.getenv("CREATOR_MAX_SECONDS", "120"))   # длина ролика в боте для креаторов (в основном боте - smart.MAX_SECONDS)
TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", "1200" if CREATOR else "0"))   # секунд на задачу; 0 - без ограничения
REQUIRE_CHANNEL = os.getenv("REQUIRE_CHANNEL", "")           # @канал: подписка обязательна для бесплатного демо (бот должен быть в нём админом)
WATERMARK = os.getenv("WATERMARK", "")
FREE_MODE = os.getenv("CREATOR_FREE_MODE", "1" if CREATOR else "0") == "1"   # всё бесплатно: без кредитов и водяного знака, лимиты остаются
DL_DAILY = int(os.getenv("DL_DAILY", "20"))               # скачиваний в сутки на человека
CUT_DAILY = int(os.getenv("CUT_DAILY", "5"))              # нарезок в сутки на человека
DL_MAX_MINUTES = int(os.getenv("DL_MAX_MINUTES", "60"))   # самое длинное видео для скачивания
CUT_MAX_MINUTES = int(os.getenv("CUT_MAX_MINUTES", "30")) # самое длинное видео для нарезки
REELS_DAILY = int(os.getenv("REELS_DAILY", "5"))          # монтажей ReelsEditor в сутки на человека
REELS_MAX_MINUTES = int(os.getenv("REELS_MAX_MINUTES", "5"))   # самое длинное сырое видео для ReelsEditor
REF_DAILY = int(os.getenv("REF_DAILY", "5"))              # разборов референса в сутки на человека
REF_MAX_MINUTES = int(os.getenv("REF_MAX_MINUTES", "3"))  # самый длинный референс                      # текст водяного знака демо-роликов (например @mybot)
# пакеты «кредитов за Stars»: "10:150,30:400" = 10 роликов за 150 ⭐, 30 за 400 ⭐
CREATOR_PACKS = [tuple(int(v) for v in x.split(":")) for x in _list("CREATOR_PACKS", "10:150,30:400") if x.count(":") == 1]
REPORT_TZ = float(os.getenv("REPORT_TZ", "3"))          # часовой пояс админа для статистики и отчёта (3 = Москва)
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "10"))       # в котором часу присылать админу отчёт за вчера

ROOT = Path(os.getenv("FACTORY_ROOT", "/shorts_factory"))
CHANNELS_FILE = ROOT / "channels.json"   # каналы, созданные кнопкой в боте (в дополнение к CHANNELS из .env)


def _load_extra_channels() -> None:
    try:
        import json
        for name in json.loads(CHANNELS_FILE.read_text("utf-8")):
            if isinstance(name, str) and name and name not in CHANNELS:
                CHANNELS.append(name)
    except (OSError, ValueError):
        pass


def add_channel(name: str) -> bool:
    """Добавляет канал на лету (список общий для всех модулей) и запоминает его в channels.json. False - уже есть."""
    import json
    if name in CHANNELS:
        return False
    CHANNELS.append(name)
    try:
        extra = json.loads(CHANNELS_FILE.read_text("utf-8")) if CHANNELS_FILE.exists() else []
    except (OSError, ValueError):
        extra = []
    extra.append(name)
    CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHANNELS_FILE.write_text(json.dumps(extra, ensure_ascii=False), "utf-8")
    return True


def has_channel(name: str) -> bool:
    """Есть ли такой канал. Каналы, созданные кнопкой в боте, подхватываются из channels.json без перезапуска
    (воркер - отдельный процесс и сам список не обновляет)."""
    if name in CHANNELS:
        return True
    _load_extra_channels()
    return name in CHANNELS


_load_extra_channels()
BACKGROUNDS_DIR = ROOT / "backgrounds"
CLIPS_DIR = ROOT / "clips"
ASSETS_DIR = ROOT / "assets"          # логотипы и встроенные звуки (не зеркалятся с Диска)
IDEAS_DIR = ROOT / "ideas"           # банк идей: ideas/<канал>/*.txt (зеркало Диска, папка _ideas)
PROJECTS_DIR = ROOT / "projects"     # материалы по идеям: projects/<канал>/<идея>/
TASKS_DIR = ROOT / "tasks"
TMP_DIR = ROOT / "tmp"
LOGS_DIR = ROOT / "logs"
DB_PATH = ROOT / "factory.db"

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru")

TTS_ENGINE = os.getenv("TTS_ENGINE", "edge")
TTS_VOICE = os.getenv("TTS_VOICE", "ru-RU-DmitryNeural")
VOICES = _list("TTS_VOICES", "ru-RU-DmitryNeural,ru-RU-SvetlanaNeural,en-GB-RyanNeural,en-GB-SoniaNeural,en-US-GuyNeural,en-US-JennyNeural,"
    "de-DE-ConradNeural,de-DE-KatjaNeural,es-ES-AlvaroNeural,es-ES-ElviraNeural,fr-FR-HenriNeural,fr-FR-DeniseNeural")

MAX_AUDIO_SECONDS = float(os.getenv("MAX_AUDIO_SECONDS", "90"))
MIN_AUDIO_SECONDS = float(os.getenv("MIN_AUDIO_SECONDS", "3"))

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")   # необязательно: проверка кадров умного монтажа по смыслу (core/vision.py)
VISION_MODEL = os.getenv("VISION_MODEL", "claude-haiku-4-5-20251001")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
ELEVENLABS_VOICES = _list("ELEVENLABS_VOICES")       # «Название:voice_id,Название2:voice_id2»; в боте голос выбирается как el:voice_id
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
BG_SEGMENT_SECONDS = int(os.getenv("BG_SEGMENT_SECONDS", "60"))
BG_PER_THEME_TARGET = int(os.getenv("BG_PER_THEME_TARGET", "15"))
BG_MAX_GB = float(os.getenv("BG_MAX_GB", "15"))


def bg_themes() -> dict[str, str]:
    """BG_THEMES='abstract=abstract loop,gameplay=gaming neon' -> {tag: query}"""
    result = {}
    for item in _list("BG_THEMES", "abstract=abstract background"):
        tag, _, query = item.partition("=")
        result[tag.strip()] = (query or tag).strip()
    return result


RCLONE_REMOTE = os.getenv("RCLONE_REMOTE", "gdrive")
RCLONE_BASE_PATH = os.getenv("RCLONE_BASE_PATH", "ShortsFactory")
CLIPS_REMOTE_PATH = os.getenv("CLIPS_REMOTE_PATH", f"{RCLONE_BASE_PATH}/_clips")
BG_REMOTE_PATH = os.getenv("BG_REMOTE_PATH", f"{RCLONE_BASE_PATH}/_backgrounds")
IDEAS_REMOTE_PATH = os.getenv("IDEAS_REMOTE_PATH", f"{RCLONE_BASE_PATH}/_ideas")
PROJECTS_REMOTE_PATH = os.getenv("PROJECTS_REMOTE_PATH", f"{RCLONE_BASE_PATH}/_projects")
MIX_DEFAULT_CLIPS = int(os.getenv("MIX_DEFAULT_CLIPS", "6"))
MIX_DEFAULT_SECONDS = int(os.getenv("MIX_DEFAULT_SECONDS", "30"))
KEEP_FAILED_DAYS = int(os.getenv("KEEP_FAILED_DAYS", "7"))


def ensure_dirs() -> None:
    for d in (BACKGROUNDS_DIR, CLIPS_DIR, ASSETS_DIR, TASKS_DIR, TMP_DIR, LOGS_DIR, IDEAS_DIR, PROJECTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for tag in bg_themes():
        (BACKGROUNDS_DIR / tag).mkdir(parents=True, exist_ok=True)
