#!/usr/bin/env python3
"""Конвейер экрана по всему корпусу, по одной записи за раз (13.09, после этапа D ADR-0027).

Для каждой записи с `media:` в шапке и без готовых сайдкаров:
  1. rsync видео из архива сервера → ~/asr-stack/video/<id>.<ext>   (тот же канал, что у fetch_audio)
  2. tools/slides_from_video.py   — шкала слайдов и кадры (record.slides.json, slides/)
  3. tools/describe_slides.py     — Vision по кадрам (кадры с людьми удаляются сами)
  4. tools/screen_refs.py --resolve --video — обращения «вот здесь» и их разрешение по кадру
  5. tools/make_annotations.py    — record.annotations.json для движка
  6. tools/make_slides_md.py      — slides.md (индексировать ли — решение владельца)
  7. видео удаляется (--keep-video — оставить): все 210 часов разом на диск не влезают (~95 ГБ).

Скачивание — узкое место (замерено 13.09: ~3 МБ/с на поток, ~8 ч на весь корпус одним потоком),
поэтому видео качаются ПАРАЛЛЕЛЬНО (`--workers`, по умолчанию 3) и ВПЕРЁД обработки (`--ahead`,
не больше 5 скачанных видео на диске, ~5 ГБ); обработка идёт в порядке плана. Диск: пока свободно
меньше MIN_FREE_GB (10), новые загрузки ждут, обработка продолжает освобождать место.

Возобновляемо: готовая запись (есть record.annotations.json и record.refs.json) пропускается,
каждая стадия доделывает только недостающее. Сбой одной записи не останавливает прогон — строка
в журнале и дальше. Журнал: ~/asr-stack/video-batch.log (сводка) и .stages.log (вывод стадий).

  python3 tools/video_batch.py --dry            # план: что и в каком порядке
  nohup ~/asr-stack/video-venv/bin/python tools/video_batch.py > /dev/null 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaces  # noqa: E402
from make_slides_md import read_header  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
# Сервер с видео и раскладка архива — хозяйство корпуса, не код: `ops.env` в каталоге семьи
# (`spaces.ops_env()`), поверх — переменные окружения. Пусто — видео только локальные файлы.
_OPS = spaces.ops_env()
HOST = _OPS.get("VIDEO_HOST", "")
ARCHIVE = _OPS.get("VIDEO_ARCHIVE", "")     # `media:` без префикса — путь внутри архива
WEB_ROOT = _OPS.get("VIDEO_WEB_ROOT", "")   # второй корень: файлы, чей `media:` начинается с WEB_PREFIX
# Каким префиксом `media:` помечены файлы второго корня (например, вложения прежнего сайта,
# лежащие в его каталоге, а не в архиве). Пусто — всё из архива. Правило — конфиг корпуса, не код.
WEB_PREFIX = _OPS.get("VIDEO_WEB_PREFIX", "")

VIDEO_DIR = Path.home() / "asr-stack" / "video"
# Звуковая дорожка каждой записи (16 кГц моно flac, как у транскрибации) остаётся на диске: ~60 МБ на час,
# ~13 ГБ на корпус. Нужна, чтобы пересобрать реестр голосов (13.09 обнаружено, что стек и реестр с
# ноутбука исчезли) и чтобы переслушивать/перетранскрибировать, не качая 95 ГБ видео второй раз.
AUDIO_DIR = Path.home() / "asr-stack" / "audio"
PY = Path.home() / "asr-stack" / "video-venv" / "bin" / "python"
LOG = Path.home() / "asr-stack" / "video-batch.log"
STAGES_LOG = Path.home() / "asr-stack" / "video-batch.stages.log"
# Порядок веток в прогоне (`VIDEO_ORDER`, через запятую): сначала те, где слайды, — там экран
# даёт больше всего; ветки вне списка — в конце, по имени.
ORDER = [x.strip() for x in _OPS.get("VIDEO_ORDER", "").split(",") if x.strip()]
MIN_FREE_GB = 10.0   # ниже — загрузки ждут (обработка продолжает удалять видео); совсем мало — стоп
TIMEOUT = {"fetch": 3 * 3600, "slides": 3600, "describe": 3 * 3600, "refs": 3 * 3600, "make": 600}


def is_done(rec: Path) -> bool:
    return (rec / "record.annotations.json").is_file() and (rec / "record.refs.json").is_file()


def plan(root: Path, only: list[str] | None = None, redo: bool = False) -> list[tuple[Path, str]]:
    """[(каталог записи, путь видео в архиве)] в порядке веток ORDER, внутри ветки — по id (дата)."""
    out: list[tuple[int, str, Path, str]] = []
    for md in root.glob("**/record.md"):
        rec = md.parent
        media = read_header(md).get("media", "").strip().strip('"')
        if not media:
            continue
        if only and not any(o in str(rec) for o in only):
            continue
        if is_done(rec) and not redo:
            continue
        branch = rec.relative_to(root).parts[0] if rec.relative_to(root).parts else ""
        out.append((ORDER.index(branch) if branch in ORDER else len(ORDER), rec.name, rec, media))
    out.sort()
    return [(rec, media) for _, _, rec, media in out]


def log(line: str) -> None:
    stamp = datetime.now().strftime("%d.%m %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}  {line}\n")
    print(f"{stamp}  {line}", flush=True)


def run(name: str, cmd: list[str], rec_id: str) -> tuple[bool, float]:
    """Стадия: вывод — в .stages.log, в сводку — только исход и время."""
    t0 = time.time()
    with STAGES_LOG.open("a", encoding="utf-8") as f:
        f.write(f"\n===== {rec_id} · {name} · {datetime.now():%d.%m %H:%M:%S}\n$ {' '.join(cmd)}\n")
        f.flush()
        try:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=TIMEOUT.get(name, 3600),
                               cwd=str(REPO))
            ok = r.returncode == 0
            if not ok:
                f.write(f"--- код возврата {r.returncode}\n")
        except subprocess.TimeoutExpired:
            f.write(f"--- ТАЙМАУТ {TIMEOUT.get(name)} с\n")
            ok = False
    return ok, time.time() - t0


def fetch(media: str, dst: Path) -> tuple[bool, float]:
    if dst.is_file() and dst.stat().st_size > 1024:
        return True, 0.0
    # ⚠️ пробелы и кириллица в пути: удалённый путь квотится ДЛЯ удалённой оболочки (shlex), иначе
    # rsync режет его по пробелу — грабли MVP 12.09
    # ⚠️ `--partial-dir`: прерванная закачка (перезапуск батча) продолжается с места обрыва, а не с
    # нуля — видео бывает 2+ ГБ, при ~3 МБ/с это 15-35 минут (замерено 13.09: fetch 2119 с). Именно
    # подкаталог, а не `--partial`: тот оставил бы недокачанный файл под ИМЕНЕМ готового, и проверка
    # «файл есть — не качаем» выше приняла бы обрубок за видео.
    return run("fetch", ["rsync", "-a", "--partial-dir=.rsync-partial", remote_path(media), str(dst)], dst.stem)


def server_path(media: str) -> str:
    """Путь видео на сервере: архив или, для файлов с префиксом WEB_PREFIX, второй корень."""
    if WEB_PREFIX and WEB_ROOT and media.startswith(WEB_PREFIX):
        return f"{WEB_ROOT}/{media}"
    return f"{ARCHIVE}/{media}"


def remote_path(media: str) -> str:
    return f"{HOST}:{shlex.quote(server_path(media))}"


def counts(rec: Path) -> str:
    try:
        sl = json.loads((rec / "record.slides.json").read_text(encoding="utf-8"))
        s = sl.get("summary", {})
        refs = json.loads((rec / "record.refs.json").read_text(encoding="utf-8")).get("refs", []) \
            if (rec / "record.refs.json").is_file() else []
        return (f"слайдов {s.get('slides', '?')}, описано {s.get('described', '?')}, "
                f"обращений {len(refs)} (разрешено {sum(1 for r in refs if r.get('resolved'))})")
    except (OSError, ValueError):
        return "сайдкаров нет"


def video_path(rec: Path, media: str) -> Path:
    return VIDEO_DIR / f"{rec.name}{Path(media).suffix.lower()}"


def free_gb() -> float:
    return shutil.disk_usage(VIDEO_DIR).free / 2**30


class Downloads:
    """Пул загрузок: `workers` потоков rsync, вперёд обработки не больше `ahead` видео на диске
    (слот занимает загрузчик, освобождает обработчик после удаления видео), при нехватке места
    загрузки ждут. Порядок плана сохраняется: очередь пула — FIFO."""

    def __init__(self, todo: list[tuple[Path, str]], workers: int, ahead: int) -> None:
        self._slots = threading.Semaphore(max(1, ahead))
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers))
        self.futures = [self._pool.submit(self._fetch, rec, media) for rec, media in todo]

    def _fetch(self, rec: Path, media: str) -> tuple[bool, float]:
        self._slots.acquire()
        waited = False
        while free_gb() < MIN_FREE_GB:
            if not waited:
                log(f"загрузки ждут: на диске {free_gb():.1f} ГБ < {MIN_FREE_GB}")
                waited = True
            time.sleep(30)
        return fetch(media, video_path(rec, media))

    def release(self) -> None:
        self._slots.release()

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def process(rec: Path, media: str, keep_video: bool, fetched: tuple[bool, float] | None = None,
            remote: bool = False) -> bool:
    rec_id = rec.name
    if free_gb() < MIN_FREE_GB / 3:
        log(f"СТОП: на диске {free_gb():.1f} ГБ — освободите место и перезапустите")
        return False
    times: dict[str, float] = {}
    if remote:
        # видео остаётся на сервере: детектор и обращения режут кадры по ssh, звук вынимает
        # серверный ffmpeg (fetch_audio) — сюда едут поток кадров, jpeg-и и flac
        video_arg, extra = server_path(media), ["--remote", HOST]
    else:
        video = video_path(rec, media)
        ok, times["fetch"] = fetched if fetched is not None else fetch(media, video)
        if not ok or not video.is_file():
            log(f"{rec_id}: СБОЙ скачивания ({media})")
            return True
        video_arg, extra = str(video), []
    steps = [
        ("slides", [str(PY), "tools/slides_from_video.py", video_arg, *extra, "--record", str(rec)],
         lambda: (rec / "record.slides.json").is_file()),
        # заставка — первый видимый кадр, кандидат на обложку (владелец, 14.09); шкалу не трогает
        ("intro", [str(PY), "tools/intro_frame.py", video_arg, *extra, "--record", str(rec)], lambda: False),
        ("describe", [str(PY), "tools/describe_slides.py", str(rec)], lambda: False),
        ("refs", [str(PY), "tools/screen_refs.py", str(rec), "--resolve", "--video", video_arg, *extra], lambda: False),
        ("make", ["python3", "tools/make_annotations.py", str(rec)], lambda: False),
        # ⚠️ `slides.md` больше НЕ пишем (владелец, 13.09 вечер): индексатор берёт все `*.md` каталога
        # и делал из него второй документ «… · экран» рядом с полем `screen` — дубль экрана в выдаче
        # и +30 % прогона. Экран едет в индекс только через `record.annotations.json`.
    ]
    failed = None
    for name, cmd, skip in steps:
        if skip():
            continue
        ok, dt = run(name, cmd, rec_id)
        times[name] = times.get(name, 0.0) + dt
        if not ok:
            failed = name
            break
    audio = AUDIO_DIR / f"{rec_id}.flac"
    if remote:
        pass   # звук — отдельным циклом `fetch_audio.py --missing` параллельно батчу: в очереди
               # он стоил 40-260 с на запись (rsync flac делит сеть с потоком кадров), а нужен не
               # конвейеру экрана, а пересборке реестра — после батча
    elif not audio.is_file():
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        ok, times["audio"] = run("audio", ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(video),
                                           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", str(audio)], rec_id)
        if not ok:
            audio.unlink(missing_ok=True)
            log(f"{rec_id}: звук не извлёкся — видео оставлено для повтора")
            keep_video = True
    if not remote and not keep_video:
        video.unlink(missing_ok=True)
    spent = " ".join(f"{k} {v:.0f}с" for k, v in times.items())
    log(f"{rec_id}: {'СБОЙ на стадии ' + failed if failed else 'готово'} — {counts(rec)} — {spent}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=None, help="каталог записей (по умолчанию — каталог записей первого пространства корпуса)")
    ap.add_argument("--only", action="append", help="подстрока пути записи (можно несколько)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redo", action="store_true", help="не пропускать готовые записи")
    ap.add_argument("--keep-video", action="store_true")
    ap.add_argument("--workers", type=int, default=3, help="параллельных загрузок")
    ap.add_argument("--ahead", type=int, default=5, help="скачанных видео впереди обработки (≈ ГБ на диске)")
    ap.add_argument("--remote", action="store_true",
                    help="видео не качать: ffmpeg на сервере по ssh, сюда — поток кадров, jpeg-и и звук (в 20-50 раз меньше трафика)")
    ap.add_argument("--dry", action="store_true", help="только план")
    a = ap.parse_args()
    if a.root is None:
        import spaces  # noqa: PLC0415
        a.root = spaces.records_dirs()[0]
    todo = plan(a.root, a.only, a.redo)
    if a.limit:
        todo = todo[:a.limit]
    if a.dry:
        for i, (rec, media) in enumerate(todo, 1):
            print(f"{i:3d}. {rec.relative_to(a.root)}  ←  {media}")
        print(f"итого {len(todo)} записей")
        return 0
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    done = 0
    if a.remote:
        log(f"=== прогон: {len(todo)} записей, видео остаются на сервере ({HOST}), кадры и звук по ssh")
        try:
            for rec, media in todo:
                if not process(rec, media, a.keep_video, remote=True):
                    break
                done += 1
        except KeyboardInterrupt:
            log("прервано вручную")
        log(f"=== конец: {done} из {len(todo)} записей за {(time.time() - started) / 3600:.1f} ч")
        return 0
    log(f"=== прогон: {len(todo)} записей, видео в {VIDEO_DIR}, {'видео остаются' if a.keep_video else 'видео удаляются'}, "
        f"загрузок {a.workers} параллельно, вперёд {a.ahead}")
    downloads = Downloads(todo, a.workers, a.ahead)
    pending = dict(enumerate(todo))
    try:
        while pending:
            # ⚠️ Обрабатываем то, что УЖЕ скачалось, а не следующее по плану. Большое видео (2 ГБ при
            # ~3 МБ/с — полчаса) держало конвейер, пока рядом лежали три готовых: замерено 13.09 —
            # 25 минут без единой готовой записи. Готового нет — ждём первое завершившееся; из
            # готовых берём самое раннее по плану, чтобы порядок веток в целом сохранялся.
            wait([downloads.futures[i] for i in pending], return_when=FIRST_COMPLETED)
            i = min(j for j in pending if downloads.futures[j].done())
            rec, media = pending.pop(i)
            fetched = downloads.futures[i].result()
            try:
                ok = process(rec, media, a.keep_video, fetched)
            finally:
                downloads.release()          # видео удалено — слот свободен для следующей загрузки
            if not ok:
                break
            done += 1
    except KeyboardInterrupt:
        log("прервано вручную")
    finally:
        downloads.close()
    log(f"=== конец: {done} из {len(todo)} записей за {(time.time() - started) / 3600:.1f} ч")
    return 0


if __name__ == "__main__":
    sys.exit(main())
