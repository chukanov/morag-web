"""Приём записи, транскрибированной на чужой машине: стейджинг → сборка → индексация.

Кто и зачем. Пока у корпуса нет сервера с моделями, докладчик гонит запись через стек
транскрибации на своём Mac (`tools/ingest.py`) и загружает сюда пакет: сырой артефакт адаптера,
сайдкары экрана, видео, поля. Дальше всё делает сервер — ТЕМ ЖЕ `make_record`, что пересобирает
правки с сайта, с настоящими словарями и раскладкой по веткам. Так запись, сделанная на чужом
ноутбуке, ничем не отличается от собранной у владельца корпуса, кроме одного — номеров голосов.

⚠️ Номера голосов СДВИГАЮТСЯ (`shift_speakers`). Реестр голосов — биометрия, на чужие машины
не едет; у чужой машины реестр свой, пустой, и её `Speaker_3` совпал бы по номеру с корпусным —
`names.json` подписал бы его чужим именем. Сдвиг в отдельный диапазон (`ingest.speaker_base` +
`speaker_step` на запись) оставляет голоса безымянными (сайт узнаёт их по `^Speaker_\\d+$`), а
коллизий нет. Сквозное узнавание таких голосов — дело центрального сервера, когда он появится.

⚠️ Стейджинг (`incoming/<id>/`) — ВНЕ `records/`: индексатор берёт каталог, и полуготовая запись в
базу не попадёт никак. Готовая запись появляется в `records/…` одним ходом, после сборки.

Что здесь чистое (под тесты без сервера): манифест и его проверка (`Manifest`, `validate`,
`record_id`), сдвиг номеров (`shift_speakers`), белый список файлов (`accept_name`). Что с
диском и подпроцессами — `Staging` и `accept`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import spaces  # noqa: E402
from make_record import slugify  # noqa: E402

SPEAKER_RE = re.compile(r"\bSpeaker_(\d+)\b")
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]{1,120}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VIDEO_EXT = ("mp4", "webm", "mov", "mkv")
# Что принимаем в стейджинг — и ничего другого: имя файла приходит из URL, а пишем мы на диск.
FILES = ("artifact.json", "record.slides.json", "record.refs.json", "record.annotations.json",
         "slides.zip", "slides.pdf") + tuple(f"video.{ext}" for ext in VIDEO_EXT)
# Сайдкары экрана — переезжают в каталог записи как есть; в `refs` есть метки голосов — их тоже сдвигаем.
SIDECARS = ("record.slides.json", "record.refs.json", "record.annotations.json")


class Refused(Exception):
    """Отказ с причиной для человека (400/409/507 решает ручка)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Manifest:
    title: str
    date: str
    event: str = ""
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    speakers: list[str] = field(default_factory=list)
    video: str = ""            # имя файла видео в стейджинге (`video.mp4`)
    uploader: str = ""         # логин/имя из сессии
    record_id: str = ""

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def record_id(date: str, title: str) -> str:
    """id = адрес страницы: дата и транслит названия, как у `make_record --id` по умолчанию."""
    return f"{date}-{slugify(title)}"[:130].rstrip("-")


def events_of(family: Path) -> list[str]:
    """Рубрики, которые правила раскладки (`hub.yml::routing`) умеют положить на место: значения
    `event` и `event_prefix` правил с веткой. Пусто — раскладки нет, рубрика свободная."""
    out: list[str] = []
    for rule in (spaces._hub(family).get("routing") or []):
        match = rule.get("match") or {}
        value = match.get("event") or match.get("event_prefix")
        if value and rule.get("branch") and value not in out:
            out.append(str(value))
    return out


def validate(raw: dict, family: Path) -> Manifest:
    """Проверить манифест и вывести id. Что не сходится — `Refused` с причиной, а не 500."""
    title = str(raw.get("title") or "").strip()
    date = str(raw.get("date") or "").strip()
    if not 3 <= len(title) <= 200:
        raise Refused("название — от 3 до 200 знаков")
    if not DATE_RE.match(date):
        raise Refused("дата — в виде YYYY-MM-DD")
    event = str(raw.get("event") or "").strip()
    allowed = events_of(family)
    if allowed and event not in allowed:
        raise Refused(f"рубрика «{event or '—'}» не разложится по веткам; допустимые: {', '.join(allowed)}")
    tags = [str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()][:20]
    speakers = [str(t).strip() for t in (raw.get("speakers") or []) if str(t).strip()][:20]
    summary = str(raw.get("summary") or "").strip()[:2000]
    video = str(raw.get("video") or "").strip()
    ext = video.rsplit(".", 1)[-1].lower() if "." in video else ""
    if ext not in VIDEO_EXT:
        raise Refused(f"видео — файл с расширением {', '.join(VIDEO_EXT)}")
    rid = record_id(date, title)
    if not ID_RE.match(rid):
        raise Refused("из названия не вышло адреса записи — напишите его латиницей или кириллицей")
    return Manifest(title=title, date=date, event=event, tags=tags, summary=summary,
                    speakers=speakers, video=f"video.{ext}", record_id=rid)


def accept_name(name: str) -> bool:
    return name in FILES


def shift_speakers(text: str, base: int) -> str:
    """`Speaker_N` → `Speaker_{base+N}` во всём тексте (артефакт и сайдкары — JSON-строки).
    Регэксп по тексту, а не обход структуры: метка живёт в markdown, `speaker_map`, `turns`,
    `words` и `speaker_names` разом, и структурный обход пропустил бы новое место молча."""
    return SPEAKER_RE.sub(lambda m: f"Speaker_{base + int(m.group(1))}", text)


class Staging:
    """Стейджинг загрузок семьи: `incoming/<id>/` с манифестом, файлами и статусом."""

    def __init__(self, root: Path, family: Path, base: int = 100000, step: int = 1000) -> None:
        self.root = Path(root)
        self.family = Path(family)
        self.base = base
        self.step = step

    def dir(self, rid: str) -> Path:
        if not ID_RE.match(rid):
            raise Refused("кривой id записи")
        return self.root / rid

    def create(self, manifest: Manifest) -> Path:
        d = self.dir(manifest.record_id)
        if (d / "done.json").is_file():
            raise Refused(f"запись {manifest.record_id} уже принята", 409)
        if spaces.find_record(manifest.record_id, self.family) is not None:
            raise Refused(f"запись {manifest.record_id} уже есть в корпусе — другое название или дата", 409)
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps(manifest.to_json(), ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        self.set_status(manifest.record_id, "uploading")
        return d

    def manifest(self, rid: str) -> Manifest:
        d = self.dir(rid)
        path = d / "manifest.json"
        if not path.is_file():
            raise Refused(f"загрузки {rid} нет — сначала манифест", 404)
        return Manifest(**json.loads(path.read_text(encoding="utf-8")))

    def status(self, rid: str) -> dict:
        d = self.dir(rid)
        path = d / "status.json"
        if not path.is_file():
            raise Refused(f"загрузки {rid} нет", 404)
        out = json.loads(path.read_text(encoding="utf-8"))
        out["files"] = sorted(p.name for p in d.iterdir() if p.is_file() and accept_name(p.name))
        return out

    def set_status(self, rid: str, state: str, **extra) -> None:
        d = self.dir(rid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps({"id": rid, "state": state, "at": time.time(), **extra},
                                                  ensure_ascii=False), encoding="utf-8")

    def free_bytes(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.root).free

    def complete(self, rid: str) -> list[str]:
        """Чего не хватает для приёма. Пусто — можно принимать."""
        d, m = self.dir(rid), self.manifest(rid)
        missing = []
        art = d / "artifact.json"
        if not art.is_file() and (d / f"{rid}.json").is_file():
            art = d / f"{rid}.json"   # повторный приём: артефакт уже переименован
        if not art.is_file():
            missing.append("artifact.json")
        else:
            try:
                x = json.loads(art.read_text(encoding="utf-8")).get("x_enriched") or {}
                if not x.get("markdown") or not (x.get("words") or {}).get("turns"):
                    missing.append("artifact.json: нет x_enriched.markdown или words.turns — это не артефакт адаптера")
            except (ValueError, AttributeError):
                missing.append("artifact.json: не JSON")
        if not (d / m.video).is_file() and not (d / "shift.json").is_file():
            missing.append(m.video)   # после первого приёма видео уже в архиве — не требуем
        return missing

    def next_base(self) -> int:
        """Диапазон номеров голосов для следующей принятой записи — счётчик в стейджинге."""
        counter = self.root / ".speaker_base.json"
        current = self.base
        if counter.is_file():
            current = int(json.loads(counter.read_text(encoding="utf-8")).get("next", self.base))
        counter.write_text(json.dumps({"next": current + self.step}), encoding="utf-8")
        return current


async def _run(argv: list[str], cwd: Path, subst: dict[str, str]) -> str:
    cmd = [a.format(**subst) for a in argv]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    text = out.decode("utf-8", "replace")
    if proc.returncode:
        raise RuntimeError(f"{Path(cmd[0]).name} {' '.join(cmd[1:3])}…: код {proc.returncode}: {text[-600:]}")
    return text


async def accept(staging: Staging, rid: str, *, family: Path, cfg, root: Path) -> Path:
    """Стейджинг → запись в корпусе. Порядок важен и записан:

    1. номера голосов — в свой диапазон (артефакт и `refs`);
    2. видео — в архив (`<archive>/<video_dir>/<id>.<ext>`), в шапке — путь внутри архива,
       как у всех записей (плеер играет через `media_base`);
    3. мета из манифеста (докладчики `from: upload`, кто загрузил) — рядом с артефактом, как
       `<id>.meta.json` в inbox: `make_record` заберёт её в `record.meta.json`;
    4. сборка `make_record` с настоящими словарями и раскладкой — запись появляется в
       `records/…` одним ходом;
    5. сайдкары экрана и кадры (`slides.zip` → `slides/`), `slides.pdf` — в каталог записи;
    6. команды `after` (обложка, снимок голосов).
    Индексация — отдельной задачей той же очереди (см. `api/ingest.py`): она длинная.
    """
    d = staging.dir(rid)
    m = staging.manifest(rid)
    staging.set_status(rid, "building")
    try:
        # Сдвиг номеров — ровно один раз: повторный приём после сбоя (сеть, диск) не должен
        # сдвинуть их второй раз и развести артефакт с `refs`. Диапазон запоминается рядом.
        shifted = d / "shift.json"
        src = d / f"{rid}.json"   # артефакт под именем записи: `make_record` возьмёт мету из `<stem>.meta.json`
        if shifted.is_file():
            base = int(json.loads(shifted.read_text(encoding="utf-8"))["base"])
        else:
            base = staging.next_base()
            art = d / "artifact.json"
            art.write_text(shift_speakers(art.read_text(encoding="utf-8"), base), encoding="utf-8")
            refs = d / "record.refs.json"
            if refs.is_file():
                refs.write_text(shift_speakers(refs.read_text(encoding="utf-8"), base), encoding="utf-8")
            art.rename(src)
            shifted.write_text(json.dumps({"base": base}), encoding="utf-8")

        media = ""
        archive = Path(cfg.archive) if cfg.archive else None
        if archive:
            ext = m.video.rsplit(".", 1)[-1]
            target = archive / cfg.video_dir / f"{rid}.{ext}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if (d / m.video).is_file():
                shutil.move(str(d / m.video), str(target))
            elif not target.is_file():
                raise RuntimeError(f"видео {m.video} нет ни в стейджинге, ни в архиве")
            media = f"{cfg.video_dir}/{rid}.{ext}"

        meta = {
            "speakers": [{"name": n, "from": "upload"} for n in m.speakers],
            "summary": m.summary,
            "links": {},
            "sources": {"upload": {"by": m.uploader, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}},
        }
        (d / f"{rid}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        argv = [*cfg.build, "{artifact}", "--id", rid, "--title", m.title, "--date", m.date]
        if m.event:
            argv += ["--event", m.event]
        if m.tags:
            argv += ["--tags", ", ".join(m.tags)]
        if m.summary:
            argv += ["--summary", m.summary]
        argv += ["--media", media] if media else ["--no-media"]
        subst = {"artifact": str(src), "family": str(family), "record_dir": ""}
        out = await _run(argv, root, subst)
        record_dir = spaces.find_record(rid, family)
        if record_dir is None:
            raise RuntimeError(f"сборка прошла, а записи {rid} в корпусе нет: {out[-400:]}")

        for name in SIDECARS:
            if (d / name).is_file():
                shutil.move(str(d / name), str(record_dir / name))
        if (d / "slides.pdf").is_file():
            shutil.move(str(d / "slides.pdf"), str(record_dir / "slides.pdf"))
        if (d / "slides.zip").is_file():
            frames = record_dir / "slides"
            frames.mkdir(exist_ok=True)
            with zipfile.ZipFile(d / "slides.zip") as z:
                for info in z.infolist():
                    name = Path(info.filename).name
                    if name.endswith(".jpg") and not info.is_dir():
                        with z.open(info) as fin, (frames / name).open("wb") as fout:
                            shutil.copyfileobj(fin, fout)
            (d / "slides.zip").unlink()

        subst["record_dir"] = str(record_dir)
        for after in cfg.after:
            try:
                await _run(after, root, subst)
            except RuntimeError as error:
                log.warning("приём %s: шаг после сборки не удался: %s", rid, error)

        (d / "done.json").write_text(json.dumps({"record_dir": str(record_dir), "media": media,
                                                 "speaker_base": base, "at": time.time()}), encoding="utf-8")
        for leftover in d.iterdir():
            if leftover.name not in ("manifest.json", "status.json", "done.json", "shift.json"):
                leftover.unlink()
        staging.set_status(rid, "indexing" if cfg.index else "done", record=str(record_dir.name))
        log.info("принята запись %s → %s (голоса от Speaker_%d)", rid, record_dir, base)
        return record_dir
    except Exception as error:
        staging.set_status(rid, "error", error=str(error)[-800:])
        log.error("приём %s не удался: %s", rid, error)
        raise


async def index(staging: Staging, rid: str, *, cfg, root: Path, family: Path) -> None:
    """Индексация после приёма — командой из конфига; статус записи — по итогу."""
    if not cfg.index:
        return
    try:
        await _run(cfg.index, root, {"family": str(family), "record_dir": "", "artifact": ""})
        staging.set_status(rid, "done", record=rid)
    except Exception as error:
        staging.set_status(rid, "error", error=f"индексация: {str(error)[-800:]}", record=rid)
        log.error("индексация после приёма %s не удалась: %s", rid, error)
        raise
