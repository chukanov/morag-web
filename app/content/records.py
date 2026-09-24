"""Индекс записей: строится из шапок `records/<id>/record.md`, обновляется по mtime.

Единица — доклад, а не митап: человек ищет «кто рассказывал про Kafka» и хочет доклад.
**`id` — имя каталога** (`2026-03-12-kafka-bez-boli`), оно же адрес страницы. Не `season-episode`:
такая пара делает поля номера обязательными, зашивает двухуровневую иерархию в ответ API, а
запись, загруженная задним числом, ломает нумерацию. Каталог уникален по построению ФС.

Из заголовка здесь НИЧЕГО не вычитывается. У подкаста заголовок кодировал номер и темы
(`Название №23: тема · тема`) и разбирался регуряркой из конфига; у доклада заголовок — просто
заголовок, а группировка вынесена в обычное строковое поле шапки (`event`, имя поля — `group_by`
в `site.yml`).

**Раздел — это КАТАЛОГ**, а не вычисленный признак: `records/<раздел>/<подраздел>/<id>/record.md`.
Путь читается в поля `section` и `subgroup` записи, и другого источника правды нет. Так сделано
после того, как выяснилось, что структуру считали два разных механизма: список — правилами по
шапке, диск — правилами раскладки. Матчер у них был общий, поэтому они не разъехались, но копий
знания было две.

ⓘ Дерево «раздел → подраздел → записи» здесь БОЛЬШЕ НЕ СОБИРАЕТСЯ. Список на сайте плоский, по
убыванию даты, а раздел, подраздел, год, метка и спикер — это ФИЛЬТРЫ, и считает их фронт из тех
же полей записи (весь корпус — 156 КБ, он и так целиком на клиенте). Серверное дерево, которым
никто не пользуется, — это ровно та вторая копия знания, от которой мы избавлялись.

Что это даёт кроме одного источника правды: то же дерево видит движок — путь входит в dense-вектор
чанка (`path + text + context`), а каталоги верхнего уровня становятся корнями Карты знаний.
Поэтому каталоги стоит называть по-человечески, словами языка корпуса, а не слагами.

⚠️ **Ось второго уровня у каждого раздела СВОЯ.** У одного это год, у другого — курс или релиз.
Замерено на живом корпусе: лекции учебного курса выложены ОДНИМ днём, и год их не различает
вовсе. Общим полем шапки это не выразить, а заводить поле под каждый случай нельзя — шапка
уезжает в payload КАЖДОГО чанка. Каталог выражает это даром.

⚠️ **Цена решения:** перекроить список, не двигая файлы, больше нельзя, а переезд записи ПОСЛЕ
индексации означает её переиндексацию (относительный путь входит в `doc_id`). Это сознательный
размен: раскладка меняется раз в полгода, а расхождение списка с диском ищут неделю.

ⓘ Раскладку НОВОЙ записи по-прежнему решают правила (`hub.yml::routing`, `tools/spaces.py`) —
но только её: разложили один раз, дальше истина на диске.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import read_frontmatter

log = logging.getLogger(__name__)

RECORD_FILE = "record.md"


@dataclass(frozen=True)
class RecordMeta:
    id: str
    title: str
    date: str
    group: str
    # Люди — двумя списками: выступавшие и остальные названные (кто есть кто — решает сборка
    # корпуса). `voices` — голосов всего, включая безымянные: мера неполноты списков.
    speakers: list[str]
    participants: list[str]
    voices: int
    duration_sec: int
    # Аннотация из поста — авторский текст, а не пересказ расшифровки. В списке из полутора
    # сотен заголовков она единственное, по чему видно, о чём запись. Есть у 81 из 167.
    summary: str
    media: str
    slides: str
    # Ссылка на исходный пост: под ним обсуждение, которого в расшифровке нет.
    post: str
    # Ссылка на обсуждение записи в мессенджере корпуса; пусто — ссылки не было.
    discussion: str
    tags: list[str]
    # Формат записи по таксономии корпуса («Технологии», «Воркшоп»…) и отметка «лучшее» из
    # того же источника. Смысл задаёт корпус, сайт лишь показывает.
    kind: list[str]
    award: bool
    # Предмет: одна категория и список тем из словаря корпуса; год — ось фильтра (у курсов он не
    # совпадает с датой выкладки, поэтому это отдельное поле, а не срез даты).
    category: str
    topics: list[str]
    year: str
    # Каталоги над записью: `records/<section>/<subgroup>/<id>`. В шапке их нет и быть не должно.
    # Пустые — раскладка плоская, список работает одним уровнем по `group`.
    section: str
    subgroup: str
    # Обложка — кадр слайда из каталога записи (`slides/sNNN.jpg`, выбор в `record.slides.json`
    # → `cover.frame`, пишет `tools/make_cover.py`); пусто — картинки нет. Сводка для карточки —
    # `record.meta.json` → `blurb.text` (`tools/make_blurb.py`), только у записей без авторской
    # аннотации: где `summary` есть, она лучше, и фронт показывает её.
    cover: str = ""
    blurb: str = ""
    # `path` — ОТНОСИТЕЛЬНЫЙ (`<id>/record.md`): так запись названа в doc_id движка, и ключ
    # обязан остаться прежним. `file` — абсолютный: корней у индекса теперь может быть несколько.
    path: str = field(default="", compare=False)
    file: str = field(default="", compare=False)
    # Когда запись в последний раз менялась — по этому же признаку решает индексатор («изменился
    # ли документ» он смотрит по mtime). Нужен, чтобы показать в списке, доехала ли запись до
    # поиска: с 24.09 индексация идёт плановым прогоном, а не на каждую загрузку.
    mtime: float = field(default=0.0, compare=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "date": self.date,
            "group": self.group,
            "speakers": self.speakers,
            "participants": self.participants,
            "voices": self.voices,
            "duration_sec": self.duration_sec,
            "summary": self.summary,
            "post": self.post,
            "discussion": self.discussion,
            "kind": self.kind,
            "award": self.award,
            "category": self.category,
            "topics": self.topics,
            "year": self.year,
            # имя файла, а не ссылка: адрес зависит от того, кто раздаёт медиа
            # (в проде Caddy, в разработке — сам BFF), и знает его фронт
            "media": self.media,
            "slides": self.slides,
            "tags": self.tags,
            "section": self.section,
            "subgroup": self.subgroup,
            # имя кадра внутри каталога записи; адрес строит фронт (`coverUrl`), как у медиа
            "cover": self.cover,
            "blurb": self.blurb,
        }


def axis_value(axis: str, date: str, fm: dict) -> str:
    """Значение оси группировки: `date:year` — год из даты, иначе поле шапки.

    Год отдельным полем не хранится и храниться не должен: он уже есть в `date`, а дубль был
    бы вторым источником правды и лишним ключом в payload КАЖДОГО чанка.
    """
    if axis == "date:year":
        return date[:4]
    return str(fm.get(axis) or "").strip() if axis else ""


def natural_key(text: str) -> tuple:
    """Ключ «человеческой» сортировки: числа сравниваются как числа.

    ⚠️ Не украшение. У курса все лекции выложены ОДНИМ днём, поэтому дату они делят, и порядок
    решает id. По строке «…-10-» встаёт раньше «…-2-», и курс из 25 лекций читается как шум.
    """
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text))


class RecordIndex:
    """Все записи корпуса. Тела расшифровок сюда не грузим — только шапки."""

    def __init__(self, records_dir: Path | list[Path], *, group_by: str = "event",
                 order: str = "desc", section_order: dict | None = None) -> None:
        # Корней может быть несколько: раздел вправе жить в своём каталоге, чтобы НЕ ПОПАСТЬ в
        # индекс движка (`sources[].path` смотрит только на первый). Первый корень — основной.
        dirs = records_dir if isinstance(records_dir, (list, tuple)) else [records_dir]
        self.dirs = [Path(d) for d in dirs]
        self.dir = self.dirs[0]
        self.group_by = group_by
        # ⚠️ Порядок списка — доменное решение, а не вкус. Митапам нужен свежий сверху, курсу —
        # лекция 1 сверху: курс читают подряд, и «свежее сверху» разворачивает его задом наперёд.
        self.order = order
        # ⚠️ Единственное, чего НЕ ВИДНО в каталоге, — направление чтения. Ключ здесь — имя
        # каталога раздела, значение `asc`/`desc`; чего нет — читается общим `order`.
        # Уезжает на фронт как есть: отфильтровав список курсом, он обязан показать лекцию 1
        # первой, а не последней, и вывести это из данных нельзя — только из решения владельца.
        self.section_order = {str(k): str(v) for k, v in (section_order or {}).items()}
        # Суммарная длительность корпуса: нужна витрине пространств, считается тут же даром.
        self.total_sec: int = 0
        self._items: list[RecordMeta] = []
        self._by_id: dict[str, RecordMeta] = {}
        self._by_path: dict[str, RecordMeta] = {}
        self._stamp: float = -1.0
        self.build()

    # --- построение -------------------------------------------------------

    def _files(self) -> list[Path]:
        # ⚠️ `**`, а не `*`: записи разложены по веткам и годам
        # (`records/<ветка>/<год>/<id>/record.md`) — путь и есть иерархия, которую видит
        # движок. Шаблон на один уровень нашёл бы НОЛЬ записей, и отказ был бы ТИХИМ:
        # в логе «0 шт.», на сайте пусто, ошибок нет.
        return sorted(f for d in self.dirs for f in d.glob(f"**/{RECORD_FILE}"))

    # Сайдкары, из которых берутся обложка и сводка. Их правят инструменты без касания
    # `record.md`, поэтому сторож свежести смотрит и на них — иначе новая обложка ждала бы рестарта.
    SIDECARS = ("record.slides.json", "record.meta.json")

    def _mtime_stamp(self, files: list[Path]) -> float:
        stamps = [f.stat().st_mtime for f in files]
        for f in files:
            for name in self.SIDECARS:
                side = f.parent / name
                if side.is_file():
                    stamps.append(side.stat().st_mtime)
        return max(stamps, default=0.0)

    @staticmethod
    def _sidecars(rec: Path) -> tuple[str, str]:
        """(кадр обложки, текст сводки) из сайдкаров записи; битый JSON или чужой путь — пусто.
        Кадр — только `slides/*.jpg` внутри каталога: имя уезжает во фронт и вернётся запросом."""
        cover = blurb = ""
        try:
            data = json.loads((rec / "record.slides.json").read_text(encoding="utf-8"))
            frame = str((data.get("cover") or {}).get("frame") or "")
            if frame.startswith("slides/") and frame.endswith(".jpg") and "/" not in frame[len("slides/"):] \
                    and (rec / frame).is_file():
                cover = frame
        except (OSError, ValueError, AttributeError):
            pass
        try:
            data = json.loads((rec / "record.meta.json").read_text(encoding="utf-8"))
            blurb = str((data.get("blurb") or {}).get("text") or "").strip()
        except (OSError, ValueError, AttributeError):
            pass
        return cover, blurb

    def build(self) -> None:
        files = self._files()
        items: list[RecordMeta] = []
        for path in files:
            try:
                meta = self._parse(path)
            except Exception:  # одна битая запись не должна ронять весь раздел
                log.exception("не разобрал запись %s — пропускаю", path)
                continue
            if meta:
                items.append(meta)
        self._items = items = self._sorted(items, self.order)
        self.total_sec = sum(r.duration_sec for r in items)
        self._by_id = {r.id: r for r in items}
        self._by_path = {r.path: r for r in items}
        self._stamp = self._mtime_stamp(files)
        log.info("индекс записей: %d шт. из %s", len(items),
                 ", ".join(str(d) for d in self.dirs))
        # ⚠️ Один id в двух корнях — беда: побеждает первый (индексируемый), но пара «показываем
        # одно, ищем другое» разъедется молча.
        if len(self._by_id) != len(items):
            log.warning("повторяющиеся id записей между каталогами — часть скрыта")

    def _root_of(self, path: Path) -> Path:
        """Корень, которому принадлежит файл. Относительный путь считается от него, иначе
        `by_path` перестал бы совпадать с doc_id движка."""
        for root in self.dirs:
            try:
                path.relative_to(root)
                return root
            except ValueError:
                continue
        return self.dir

    def _section_of(self, path: Path, root: Path) -> tuple[str, str]:
        """Раздел и подраздел — это каталоги НАД записью, как они лежат на диске.

        `records/<раздел>/<подраздел>/<id>/record.md` → («раздел», «подраздел»).
        `records/<id>/record.md` → («», «») — плоская раскладка, список идёт одним уровнем.

        ⓘ Хвост глубже второго уровня не отбрасываем, а склеиваем в подраздел: тихо потерять
        каталог хуже, чем показать «2026/октябрь». Такой раскладки сейчас нет ни у кого, и
        увидеть её появление лучше глазами, чем не увидеть вовсе.
        """
        tail = path.parent.relative_to(root).parts[:-1]  # последняя часть — сам каталог записи
        if not tail:
            return "", ""
        return tail[0], "/".join(tail[1:])

    def _order_of(self, section: str) -> str:
        return self.section_order.get(section, self.order)

    @staticmethod
    def _sorted(items: list[RecordMeta], order: str) -> list[RecordMeta]:
        """Свежие сверху (или самые ранние сверху при `asc`), а НИЧЬЯ — по id по возрастанию.

        ⚠️ Два прохода, а не один кортеж с `reverse`: перевёрнутый кортеж переворачивает и id,
        и лекции одного дня встают «занятие-10, занятие-9, …». Замерено на живом корпусе: у курса
        все 33 занятия выложены ОДНИМ днём, то есть ничья там не редкий случай, а весь курс.
        Сортировка устойчива, поэтому первый проход (id) переживает второй (дата).
        """
        out = sorted(items, key=lambda r: natural_key(r.id))
        out.sort(key=lambda r: r.date, reverse=(order != "asc"))
        return out

    def _parse(self, path: Path) -> RecordMeta | None:
        fm = read_frontmatter(path)
        title = str(fm.get("title") or "").strip()
        date = str(fm.get("date") or "").strip()
        # Обязательны ровно два поля. Остальное опционально принципиально: запись без видео
        # (перенесённый с прежнего сайта анонс) обязана показываться, а не выпадать из списка.
        if not title or not date:
            log.warning("нет title/date в %s — пропускаю", path)
            return None

        # Ось первого уровня понимает тот же словарь, что и разделы: `date:year` даёт список
        # по годам БЕЗ единого правила в конфиге — ровно то, что нужно большинству пространств.
        group = axis_value(self.group_by, date, fm)
        tags = [str(t) for t in (fm.get("tags") or [])]
        root = self._root_of(path)
        section, subgroup = self._section_of(path, root)
        cover, blurb = self._sidecars(path.parent)
        # ⚠️ Берём максимум по тем же файлам, по которым решает индексатор: сама расшифровка и
        # аннотации экрана (`updated_at` документа = max их mtime). Обложка и сводка живут в
        # других сайдкарах и поиска не меняют — включать их значило бы врать «не в индексе».
        annotations = path.parent / "record.annotations.json"
        mtime = max(path.stat().st_mtime,
                    annotations.stat().st_mtime if annotations.is_file() else 0.0)
        return RecordMeta(
            mtime=mtime,
            cover=cover,
            blurb=blurb,
            id=path.parent.name,
            title=title,
            date=date,
            group=group,
            speakers=[str(s) for s in (fm.get("speakers") or [])],
            participants=[str(s) for s in (fm.get("participants") or [])],
            voices=int(fm.get("voices") or 0),
            duration_sec=int(fm.get("duration_sec") or 0),
            summary=str(fm.get("summary") or ""),
            media=str(fm.get("media") or ""),
            post=str(fm.get("post") or ""),
            discussion=str(fm.get("discussion") or ""),
            slides=str(fm.get("slides") or ""),
            tags=tags,
            kind=[str(k) for k in (fm.get("kind") or [])],
            award=bool(fm.get("award")),
            category=str(fm.get("category") or ""),
            topics=[str(t) for t in (fm.get("topics") or [])],
            year=str(fm.get("year") or date[:4]),
            section=section,
            subgroup=subgroup,
            path=str(path.relative_to(root)),
            file=str(path),
        )

    def refresh_if_stale(self) -> None:
        """Записи добавляются операциями (rsync с ноутбука) — ловим без рестарта BFF."""
        files = self._files()
        if len(files) != len(self._items) or self._mtime_stamp(files) > self._stamp:
            self.build()

    # --- доступ -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[RecordMeta]:
        return list(self._items)

    def by_id(self, record_id: str) -> RecordMeta | None:
        return self._by_id.get(record_id)

    def by_path(self, rel_path: str) -> RecordMeta | None:
        """Путь вида `2026-03-12-kafka/record.md` — так запись названа в doc_id движка.

        `…/slides.md` — документ экрана той же записи (что было на экране, по секундам; собирает
        `tools/make_slides_md.py`): его цитата обязана открывать ту же читалку на той же секунде,
        иначе карточка-момент с экрана молча остаётся без видео. PDF сюда не относится — у него
        нет секунды, и его цитата честно читается текстом."""
        key = rel_path.strip().lstrip("/")
        meta = self._by_path.get(key)
        if meta is None and key.endswith("/slides.md"):
            meta = self._by_path.get(key[: -len("slides.md")] + "record.md")
        return meta

    def path_of(self, meta: RecordMeta) -> Path:
        return Path(meta.file)

    def file_of(self, meta: RecordMeta, name: str) -> Path | None:
        """Файл в каталоге записи (слайды). Имя приходит из шапки, то есть из данных, поэтому
        проверяем итоговый путь, а не строку: символическая ссылка обманула бы проверку строки."""
        base = self.path_of(meta).parent
        try:
            path = (base / name).resolve()
            path.relative_to(base.resolve())
        except (ValueError, OSError):
            return None
        return path if path.is_file() else None
