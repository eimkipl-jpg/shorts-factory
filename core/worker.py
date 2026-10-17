"""Ядро сборки: берёт задачи из очереди по одной и превращает их в final.mp4 на Google Drive.

Конвейер задачи: озвучка (если текст) -> замер аудио -> Whisper -> .ass -> фон -> ffmpeg -> rclone -> уборка.
"""
import fcntl
import json
import logging
import shutil
import signal
import subprocess
import time
from pathlib import Path

from common import config, db, notify
from common.media import audio_duration
from core import compose, cutter, mix, render, smart, story, subtitles, tts
from transport.upload import upload_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")

MAX_ATTEMPTS = 2
POLL_SECONDS = 3


def find_audio(task_dir: Path) -> Path:
    for f in task_dir.iterdir():
        if f.name.startswith("input_audio") or f.name == "voice.mp3":
            return f
    raise RuntimeError("в папке задачи нет аудио")


def process(task) -> None:
    task_id = task["id"]
    task_dir = config.TASKS_DIR / f"task_{task_id}"
    meta = json.loads((task_dir / "metadata.json").read_text("utf-8"))
    try:   # ускорение речи из профиля канала: действует, если у задачи своя скорость не выбрана
        tts.DEFAULT_RATE = max(-50, min(100, int(meta.get("speech_rate") or 0)))
    except (TypeError, ValueError):
        tts.DEFAULT_RATE = 0

    # Бот для креаторов: скачивание и нарезка - результат уходит человеку в чат
    if config.CREATOR and task["input_type"] in ("dl", "cut", "reels", "ref"):
        from core import creatortasks
        creatortasks.run(task, task_dir, meta)
        shutil.rmtree(task_dir, ignore_errors=True)
        db.set_status(task_id, "done")
        log.info("task_%s: %s отправлено пользователю", task_id, task["input_type"])
        return

    # Нарезчик: режет своё видео на фрагменты и кладёт их в библиотеку клипов (и на Диск)
    if task["input_type"] == "cut":
        _process_cut(task, task_dir, meta)
        return

    # Рецепт из мастера «Создать» (любое сочетание блоков) или быстрая нарезка /mix
    if task["input_type"] in ("mix", "recipe", "story", "smart"):
        builder = {"recipe": compose.build, "story": story.build, "smart": smart.build}.get(task["input_type"], mix.build_mix)
        out = builder(task_dir, meta)
        (task_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        db.set_status(task_id, "uploading")
        if config.CREATOR:
            _deliver(task, task_dir, meta, out)
            shutil.rmtree(task_dir, ignore_errors=True)
            db.set_status(task_id, "done")
            log.info("task_%s: ролик отправлен пользователю", task_id)
            return
        upload_task(task_dir, meta["channel"])
        shutil.rmtree(task_dir, ignore_errors=True)
        db.set_status(task_id, "done")
        log.info("task_%s: %s готово, выгружено на Drive", task_id, task["input_type"])
        return

    # 1. Озвучка, если пришёл текст
    if task["input_type"] == "text":
        script = (task_dir / "script.txt").read_text("utf-8")
        tts.synthesize(script, task_dir / "voice.mp3", meta.get("voice"))

    # 2. Инициализация и замер длительности
    audio = find_audio(task_dir)
    duration = audio_duration(audio)
    log.info("task_%s: аудио %.1f с", task_id, duration)

    # 3-4. Whisper -> .srt -> .ass
    # Язык субтитров: для озвучки из текста - по языку голоса (en-GB-... -> en), иначе из профиля
    lang = meta.get("voice", "")[:2] if task["input_type"] == "text" and meta.get("voice") else meta.get("lang")
    ass = subtitles.build_subtitles(audio, task_dir, lang) if meta.get("subs", True) else None

    # 5-6. Фон + музыка + рендер
    tags = list(meta.get("tags", []))
    if meta.get("bg") and meta["bg"] != "auto":
        tags.append("#" + meta["bg"])
    render.render(task_dir, audio, ass, tags, duration, meta.get("music", "off"), meta.get("music_vol", 12))

    # 7. Выгрузка на Google Drive (папка целиком, затем удаляется с диска VPS)
    db.set_status(task_id, "uploading")
    # Временные файлы не нужны на Диске: оставляем final.mp4 и metadata.json (+ srt для справки)
    for junk in list(task_dir.glob("input_audio*")) + [task_dir / "voice.mp3", task_dir / "script.txt"] + ([ass] if ass else []):
        junk.unlink(missing_ok=True)
    upload_task(task_dir, meta["channel"])
    shutil.rmtree(task_dir, ignore_errors=True)
    db.set_status(task_id, "done")
    log.info("task_%s: готово, выгружено на Drive", task_id)


def _fit_size(src: Path, max_mb: float | None = None) -> Path:
    """Telegram Bot API принимает файл до 50 МБ: если ролик тяжелее, пережимаем по битрейту."""
    max_mb = max_mb or (1900.0 if config.TELEGRAM_API_URL else 48.0)   # свой сервер Bot API - до 2 ГБ
    if src.stat().st_size <= max_mb * 1024 * 1024:
        return src
    dur = audio_duration(src) or 60.0
    kbps = max(500, int((max_mb * 8 * 1024 * 0.92) / dur) - 128)
    dst = src.with_name("fit.mp4")
    res = subprocess.run(["ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k",
                          "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k", "-c:a", "aac", "-b:a", "128k",
                          "-movflags", "+faststart", str(dst)], capture_output=True, text=True)
    if res.returncode != 0 or not dst.exists():
        raise RuntimeError("не удалось ужать ролик до лимита Telegram")
    return dst


def _deliver(task, task_dir: Path, meta: dict, out) -> None:
    """Экземпляр для креаторов: готовое видео уходит человеку в чат (на Диск владельца ничего не попадает)."""
    video = Path(out) if out else task_dir / "final.mp4"
    if not video.is_file():
        video = task_dir / "final.mp4"
    if meta.get("format") or meta.get("brand"):        # формат и бренд креатора: один проход ffmpeg
        from core import formats
        from common.media import video_duration
        video = formats.finalize(video, meta.get("format"), meta.get("fit", "blur"), meta.get("brand"),
                                 video_duration(video) or 0, task_dir / "fmt")
    video = _fit_size(video)
    title = (meta.get("title") or "").strip()
    ok, why = notify.send_video(task["chat_id"], video, f"🎬 {title}" if title else "🎬 Готово")
    if not ok:
        raise RuntimeError(f"не удалось отправить ролик: {why}")
    desc = (meta.get("description") or "").strip()
    tags = " ".join(meta.get("tags") or [])
    text = "\n\n".join(x for x in (f"📝 Описание для публикации:\n{desc}" if desc else "", tags) if x)
    warns = meta.get("warnings") or []
    if warns:
        text += "\n\n⚠️ " + "\n⚠️ ".join(warns[:5])
    if meta.get("watermark"):
        text += "\n\n🎁 Это демо-ролик с водяным знаком. Ролики без знака - после пополнения баланса: /buy"
    if text.strip():
        notify.send(task["chat_id"], text)


def _process_cut(task, task_dir: Path, meta: dict) -> None:
    c = meta["cut"]
    video = config.ROOT / c["file"]
    if not video.is_file():
        raise RuntimeError(f"исходное видео не найдено: {c['file']}")
    log.info("task_%s: нарезка %s, режим %s", task["id"], video.name, c["mode"])
    res = cutter.run(video, c["theme"], c["mode"], c["min"], c["max"], c["sens"], c["top"], c.get("title") or "")
    shutil.rmtree(task_dir, ignore_errors=True)
    db.set_status(task["id"], "done")
    drive = "скопировано на Диск" if res["drive_ok"] else f"⚠️ на Диск не скопировалось ({res['drive_msg'] or 'ошибка rclone'}): при синхронизации клипы могут пропасть"
    spans = "\n".join(f"• {int(s // 60)}:{int(s % 60):02}–{int(e // 60)}:{int(e % 60):02}" + (f" (оценка {sc})" if sc else "")
                      for _, s, e, sc in res["files"][:15])
    notify.send(task["chat_id"],
                f"✂️ Нарезка готова (task_{task['id']}): новых фрагментов {res['made']}, уже было {res['skipped']}, "
                f"переходов найдено {res['cuts']}. Тема «{res['theme']}», {drive}.\n{spans}\n\n"
                "Теперь их можно выбрать в 🎬 Создать → 1️⃣ Видеоряд → 🎯 Выбрать ролики.")
    log.info("task_%s: нарезка готова: %s фрагментов", task["id"], res["made"])


class TaskTimeout(BaseException):
    """BaseException, чтобы внутренние `except Exception` конвейера (повторы озвучки, поиска) не проглотили таймаут."""


def _on_timeout(signum, frame):
    raise TaskTimeout("превышено время сборки")


def _arm_timeout() -> None:
    """Предохранитель: задача не может занять рендер дольше TASK_TIMEOUT секунд (дочерние ffmpeg убивает subprocess.run)."""
    if config.TASK_TIMEOUT > 0:
        signal.signal(signal.SIGALRM, _on_timeout)
        signal.setitimer(signal.ITIMER_REAL, config.TASK_TIMEOUT)


def _claim_locked():
    """Общий замок рендера: ролики двух ботов собираются по очереди (2 vCPU не тянут два рендера сразу).
    Берём замок на время «взять задачу + собрать»; пока рендерит чужой воркер, ждём."""
    fh = open(config.RENDER_LOCK, "a+")
    fcntl.flock(fh, fcntl.LOCK_EX)
    task = db.claim_next()
    if task is None:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        return None, None
    return task, fh


def _fail_final(task, err: Exception) -> None:
    db.set_status(task["id"], "failed", str(err)[:500])
    if config.CREATOR:
        from common import creator
        from core import fetch
        kind = task["input_type"]
        refunded = creator.refund(task["id"])
        from common import adminstats
        adminstats.on_fail(task, err)
        why = str(err)[:300] if isinstance(err, (fetch.FetchError, RuntimeError)) else "технический сбой"
        if kind == "dl":
            notify.send(task["chat_id"], f"❌ Не удалось скачать: {why}. Попытка не засчитана в лимит.")
        elif kind == "cut":
            notify.send(task["chat_id"], f"❌ Нарезка не получилась: {why}. Попытка не засчитана в лимит.")
        elif kind == "ref":
            notify.send(task["chat_id"], f"❌ Разбор не получился: {why}. Попытка не засчитана в лимит.")
        elif kind == "reels":
            notify.send(task["chat_id"], f"❌ ReelsEditor не смонтировал видео: {why}. Попытка не засчитана в лимит.")
        elif refunded:
            notify.send(task["chat_id"], "😔 Ролик не получился - технический сбой. "
                        + ("Попытка не засчитана в лимит" if config.FREE_MODE else "Кредит (или бесплатное демо) возвращён")
                        + ", попробуйте ещё раз или упростите сценарий.")
        shutil.rmtree(config.TASKS_DIR / f"task_{task['id']}", ignore_errors=True)
        return
    if task["input_type"] == "cut":
        notify.send(task["chat_id"], f"❌ Нарезка task_{task['id']} не удалась: {str(err)[:300]}")


def main() -> None:
    db.init()
    if config.CREATOR:
        from common import creator
        creator.init()
    db.recover_interrupted()
    log.info("Worker started%s", " (creator)" if config.CREATOR else "")
    while True:
        task, lock = _claim_locked()
        if task is None:
            time.sleep(POLL_SECONDS)
            continue
        if config.CREATOR and task["input_type"] != "dl" and time.time() - task["created_at"] > 90 and task["attempts"] == 0:
            notify.send(task["chat_id"], "▶ Ваша очередь подошла - начинаю работу. Результат пришлю сюда.")
        try:
            _arm_timeout()
            process(task)
        except (Exception, TaskTimeout) as e:  # noqa: BLE001 - воркер не должен падать из-за одной задачи
            signal.setitimer(signal.ITIMER_REAL, 0)
            log.exception("task_%s: ошибка", task["id"])
            # таймаут повторять нет смысла (тяжёлый ролик зависнет снова), остальные ошибки - одна повторная попытка
            from core.fetch import FetchError
            if isinstance(e, TaskTimeout):
                _fail_final(task, RuntimeError("сборка заняла слишком много времени"))
            elif isinstance(e, FetchError):      # ссылка недоступна - повтор ничего не изменит
                _fail_final(task, e)
            elif task["attempts"] + 1 < MAX_ATTEMPTS:
                db.set_status(task["id"], "new", str(e)[:500])
            else:
                _fail_final(task, e)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


if __name__ == "__main__":
    main()
