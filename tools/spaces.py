#!/usr/bin/env python3
"""Раскладка корпуса по пространствам — ОДИН модуль на весь репозиторий.

Пространство = каталог со своим `site.yml` и своим `records/`. У каждого свой поиск, поэтому
каталог пространства — это ровно `sources[].path` его индексатора, и запись обязана лежать
в своём каталоге физически, а не по признаку в конфиге.

    from spaces import space_of, records_dirs, find_record, route

⚠️ Правила живут в ОДНОМ месте — `hub.yml` семьи корпуса, ключ `routing`, — и отвечают ровно на
один вопрос: КУДА КЛАСТЬ НОВУЮ ЗАПИСЬ. Что показывает сайт, они больше не решают: раздел списка —
это каталог на диске, и читает его сайт сам (`app/content/records.py`). Пока решений было два,
рядом жили две копии знания; теперь копия одна, а ошибка в правиле видна сразу — каталогом.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
RECORD_FILE = "record.md"
CORPUS_ENV = "MORAG_WEB_CORPUS"

_family: Path | None = None


def family_dir() -> Path:
    """Каталог семьи пространств: здесь общее хозяйство корпуса (словари, профиль, inbox, `ops.env`)
    и витрина; записи лежат глубже, по пространствам.

    Не константа: код инструментов generic и живёт отдельно от корпуса. Откуда: `$MORAG_WEB_CORPUS`
    (тот же ключ, что у сайта), иначе — `corpora[0].dir` действующего конфига приложения
    (`$MORAG_WEB_CONFIG` → `app/config.yml` → пример с демо-корпусом). Сайт и инструменты смотрят в
    один корпус по построению, а не по договорённости.
    """
    global _family
    if _family is None:
        env = os.environ.get(CORPUS_ENV)
        if env:
            _family = Path(env).expanduser().resolve()
        else:
            sys.path.insert(0, str(REPO))
            from app.config import family_dir as _app_family, load_config  # noqa: PLC0415
            _family = _app_family(load_config())
    return _family


def ops_env() -> dict[str, str]:
    """Операционные настройки корпуса для инструментов — `ops.env` в каталоге семьи (KEY=VALUE),
    поверх — переменные окружения. Здесь то, что описывает МАШИНЫ и хозяйство конкретного корпуса
    (сервер с видео, файл стека транскрибации, порядок веток), а не качество и не домен."""
    values: dict[str, str] = {}
    path = family_dir() / "ops.env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.replace("export ", "").strip()] = val.strip().strip('"').strip("'")
    for key in list(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def match_rule(rule: dict, *, group: str, tags: list[str]) -> bool:
    """Подходит ли запись под правило раскладки.

    Словарь `match` понимает `event` (рубрика целиком), `event_prefix` (начало рубрики), `tag`
    (метка) и `rest` (остаток). ⚠️ Ключи соединяются ИЛИ: одна строка ловит и метку «Концерт» у
    записи из «Докладов», и рубрики «Концерт» / «Концерт 2024» / любую будущую «Концерт-…».

    ⚠️ Раскладку НЕЛЬЗЯ вывести из одного поля. Замерено на живом корпусе: концерт лежит с
    рубрикой «Доклады», и в свою ветку его относит только МЕТКА.
    """
    match = rule.get("match") or {}
    return bool(
        match.get("rest")
        or (match.get("event") and group == match["event"])
        or (match.get("event_prefix") and group.startswith(match["event_prefix"]))
        or (match.get("tag") and match["tag"] in tags)
    )


def _rule_for(event: str, tags: list[str] | None, family: Path | None = None) -> dict | None:
    """Первое совпавшее правило `routing` — один проход на все три ответа (пространство, ветка,
    второй уровень). Раньше проход был свой у каждого, и правило читалось трижды."""
    for rule in _hub(family).get("routing") or []:
        if match_rule(rule, group=event or "", tags=list(tags or [])):
            return rule
    return None


def _hub(family: Path | None = None) -> dict:
    """Витрина семьи. Нет файла — корпус состоит из одного пространства (обычный сайт)."""
    path = (family or family_dir()) / "hub.yml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def slugs(family: Path | None = None) -> list[str]:
    """Слаги пространств в порядке витрины. У корпуса без витрины пространство одно."""
    spaces_ = _hub(family).get("spaces") or []
    return [str(s.get("slug")) for s in spaces_] if spaces_ else [""]


def space_dir(slug: str, family: Path | None = None) -> Path:
    base = family or family_dir()
    for space in _hub(family).get("spaces") or []:
        if str(space.get("slug")) == slug:
            return base / str(space.get("dir"))
    if not slug:
        return base
    # Корпус из одного пространства: его слаг — в `site.yml` (или имя каталога), каталог — сам base.
    site = base / "site.yml"
    if site.is_file():
        own = str((yaml.safe_load(site.read_text(encoding="utf-8")) or {}).get("slug") or base.name)
        if own == slug:
            return base
    raise KeyError(f"нет такого пространства: {slug}")


def records_dir(slug: str, family: Path | None = None) -> Path:
    return space_dir(slug, family) / "records"


def records_dirs(family: Path | None = None) -> list[Path]:
    """Все каталоги записей. Тем, кто ищет запись, обязаны быть известны ВСЕ.

    ⚠️ Инструмент, знающий про один каталог из шести, не падает — он молча считает запись
    неперенесённой и тащит её заново через конвейер. Ровно это уже ловили на двух записях.
    """
    return [records_dir(s, family) for s in slugs(family)]


def space_of(event: str, tags: list[str] | None = None, family: Path | None = None) -> str:
    """Слаг пространства для записи. Первое совпавшее правило выигрывает.

    У корпуса без витрины правил нет и выбирать не из чего — пространство одно.
    """
    if not (_hub(family).get("routing") or []):
        return ""
    rule = _rule_for(event, tags, family)
    if rule is None:
        raise ValueError(f"ни одно правило не подошло (рубрика {event!r}) — проверьте hub.yml")
    return str(rule.get("space"))


def branch_of(event: str, tags: list[str] | None = None, family: Path | None = None) -> str:
    """Ветка — первый уровень раскладки (`records/<ветка>/<второй уровень>/<id>`).

    Берётся из того же правила `routing`, что и пространство: ветку НЕЛЬЗЯ вывести из одного поля
    шапки. Замерено: записи концертов лежат с рубрикой «Доклады», и в свою ветку их относит МЕТКА.

    ⚠️ Ветка — это ещё и РАЗДЕЛ СПИСКА на сайте: имя каталога показывается как есть. Поэтому
    ветки названы по-человечески и кириллицей, а не слагами, — их же читает движок (путь входит
    в dense-вектор чанка и в корни Карты знаний).

    Пусто (в правиле нет `branch`) — раскладка плоская, как было.
    """
    rule = _rule_for(event, tags, family) or {}
    return str(rule.get("branch") or "")


def sub_of(event: str, tags: list[str] | None = None, date: str = "",
           family: Path | None = None) -> str:
    """Второй уровень раскладки — подраздел списка. ГОД по умолчанию, но не всегда год.

    ⚠️ У курса год бесполезен и вдобавок врёт: 25 лекций «Python 2023» выложены в 2024-м одним
    днём. Поэтому правило может назвать второй уровень буквально (`sub: "Python 2023"`), и тогда
    берётся он. Общего поля шапки под это нет и заводить его нельзя: шапка уезжает в payload
    КАЖДОГО чанка, а природа второго уровня у веток разная.
    """
    rule = _rule_for(event, tags, family) or {}
    named = str(rule.get("sub") or "").strip()
    if named:
        return named
    year = str(date or "")[:4]
    return year if year.isdigit() else ""


def roles_policy(branch: str, family: Path | None = None) -> dict:
    """Модель ролей ветки из `hub.yml::roles`: `{model}`. Ветка без правила — `meeting`:
    никого не назначать докладчиком наугад безопаснее, чем назначить не того."""
    rules = _hub(family).get("roles") or {}
    rule = dict(rules.get(branch) or {})
    rule.setdefault("model", "meeting")
    return rule


def find_record(record_id: str, family: Path | None = None) -> Path | None:
    """Каталог записи, в каком бы пространстве и на какой бы глубине она ни лежала.

    ⚠️ Ищем рекурсивно: записи разложены по веткам и годам. Инструмент, который смотрит только на
    `records/<id>`, не падает — он молча считает запись неперенесённой и тащит её заново через
    конвейер. Ровно это уже ловили на двух записях одной ветки, когда корней стало шесть.
    """
    for root in records_dirs(family):
        if not root.is_dir():
            continue
        hit = next(root.glob(f"**/{record_id}/{RECORD_FILE}"), None)
        if hit is not None:
            return hit.parent
        # Запись без `record.md` — она в сборке: каталог уже есть, тела ещё нет.
        for candidate in root.glob(f"**/{record_id}"):
            if candidate.is_dir():
                return candidate
    return None


def record_dirs(marker: str = RECORD_FILE, family: Path | None = None) -> list[Path]:
    """Каталоги записей по всему корпусу, на любой глубине.

    Записью считается каталог, в котором лежит `marker` (по умолчанию `record.md`; сборщику нужен
    `record.json`, разбору слайдов — колода). Один помощник на всех: четыре инструмента раньше
    обходили корни через `iterdir()`, то есть видели ВЕТКИ вместо записей и отдавали пустой список,
    не падая. Пустой результат от такого обхода неотличим от «корпус пуст».
    """
    out: list[Path] = []
    for root in records_dirs(family):
        if not root.is_dir():
            continue
        out.extend(sorted(hit.parent for hit in root.glob(f"**/{marker}")))
    return out


def route(record_id: str, *, event: str, tags: list[str] | None = None,
          date: str = "", family: Path | None = None) -> Path:
    """Куда класть НОВУЮ запись. Существующую не двигаем: у неё уже есть место.

    Раскладка — `records/<ветка>/<второй уровень>/<id>`, если у совпавшего правила есть `branch`
    и второй уровень известен (год из `date` либо названный правилом). Ветки нет в правиле —
    кладём плоско, как раньше: «ветка не решена» и «ветка пустая» обязаны различаться.
    """
    found = find_record(record_id, family)
    if found is not None:
        return found
    base = records_dir(space_of(event, tags, family), family)
    branch, sub = branch_of(event, tags, family), sub_of(event, tags, date, family)
    return base / branch / sub / record_id if branch and sub else base / record_id


def corpus_root(path: Path) -> Path:
    """Каталог с общим хозяйством (словари правок, реестр имён) для пути внутри пространства.

    ⚠️ Раньше это было `record_dir.parent.parent`, и после раскладки такой подъём упирался бы
    в каталог пространства — словари нашлись бы пустыми, а правки молча перестали применяться.
    Ищем по признаку: вверх до каталога, где лежит `names.json`.
    """
    for parent in [path, *path.parents]:
        if (parent / "names.json").is_file():
            return parent
    return family_dir()
