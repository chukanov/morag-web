#!/usr/bin/env python3
"""Собрать демо-корпус `corpora/demo` — три записи без единого данного компании.

    python3 tools/make_demo.py            # собрать, если ещё нет
    python3 tools/make_demo.py --force    # пересобрать заново

Зачем он нужен. Фронт этого репозитория рано или поздно переедет в чистую репу, которую будут
наполнять другие люди и другими корпусами. Универсальность, которую никто не проверяет, тихо
исчезает: тест, прибитый к живой записи, «работает», даже когда в коде поселилось доменное.
Демо-корпус — это то, на чём приложение обязано подниматься БЕЗ рабочего корпуса, и на нём же
гоняются тесты страниц. Он же — заготовка содержимого будущей репы.

Что внутри и почему именно так:
  * **синтетические фамилии и публичные технологии** — правило CLAUDE.md про примеры: форму
    сохраняем, утечки нет;
  * **запись со слайдами** и **запись без медиа** (перенесённый анонс обязан показываться,
    а не выпадать из списка) — оба инварианта живут в тестах и должны иметь на чём стоять;
  * **настоящая речь**, а не тон: `say -v Milena` синтезирует каждую реплику ОТДЕЛЬНО, поэтому
    начало реплики известно точно, а не на глаз. Внутри реплики слова раскладываются по длине —
    для караоке этого достаточно, и такой файл честно помечен `aligned_by: proportional`.

⚠️ Времена внутри реплики — ОЦЕНКА, а не выравнивание по звуку. В живом корпусе их считает
MMS_FA в конвейере; здесь звук синтетический и выравнивать нечего. Формат тот же
(`morag-words-v1`), поэтому читалка и караоке не отличают демо от настоящей записи.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "corpora" / "demo"

# Голос и битрейт: разборчиво и мелко. Демо коммитится в git, поэтому вес имеет значение.
VOICE = "Milena"
AUDIO_KBPS = 48
# Картинка нужна только чтобы это было ВИДЕО: сайт про видеозаписи, и демо не должно
# притворяться подкастом. Один кадр в секунду на сплошном фоне весит десятки килобайт.
VIDEO_SIZE = "640x360"
VIDEO_COLOR = "0x16212b"
GAP = 0.6  # пауза между репликами, чтобы стык не звучал склейкой


DEMO_RECORDS = [
    {
        "id": "2024-01-15-kafka-osnovy",
        "title": "Кафка без боли: основы",
        "date": "2024-01-15",
        "event": "Демо-встреча",
        "speakers": ["Мария Кузнецова"],
        "tags": ["kafka", "стриминг"],
        "category": "Очереди и потоки",
        "topics": ["Kafka", "партиции"],
        "summary": "Зачем очередь сообщений, что такое партиции и почему порядок гарантируется "
                   "только внутри партиции.",
        # ⚠️ Реплик намеренно много (16). Потолок «шире» на карточке-моменте (`_MAX_PAD`)
        # виден только на записи длиннее тринадцати реплик: на короткой «шире до упора» и
        # «вся запись» совпадают, и тест проходит вхолостую, ничего не проверив.
        "turns": [
            ("Мария Кузнецова",
             "Всем привет. Сегодня разберём, зачем нужна Кафка и чем она отличается от обычной "
             "очереди сообщений."),
            ("Мария Кузнецова",
             "Начну с того, чем она не является. Кафка — это не база данных и не замена "
             "очереди задач, хотя её регулярно пытаются использовать и так, и так."),
            ("Мария Кузнецова",
             "Кафка — это журнал. Сообщения не удаляются после чтения, а лежат в логе, и каждый "
             "потребитель сам помнит, до какого места он дочитал."),
            ("Мария Кузнецова",
             "Отсюда первое следствие. Двум разным сервисам не нужно договариваться: каждый "
             "читает один и тот же поток со своей скоростью и со своей позиции."),
            ("Мария Кузнецова",
             "Второе следствие важнее. Если сервис упал и сутки не читал, данные его дождутся — "
             "ровно до тех пор, пока не истечёт срок хранения темы."),
            ("Мария Кузнецова",
             "Тема делится на партиции. Порядок сообщений гарантируется только внутри одной "
             "партиции, и это самая частая ошибка в проектировании."),
            ("Мария Кузнецова",
             "Ключ сообщения решает, в какую партицию оно попадёт. Одинаковый ключ — одна "
             "партиция, значит порядок для этого ключа сохранится."),
            ("Мария Кузнецова",
             "Поэтому ключ выбирают по сущности, а не по удобству. Ключ по идентификатору "
             "заказа даёт порядок событий заказа, ключ по дате не даёт ничего."),
            ("Мария Кузнецова",
             "Число партиций задаёт потолок параллелизма. Больше читателей в группе, чем "
             "партиций, — лишние просто простаивают."),
            ("Мария Кузнецова",
             "Уменьшить число партиций нельзя, только увеличить. И увеличение перемешивает "
             "распределение ключей, то есть ломает порядок на границе изменения."),
            ("Мария Кузнецова",
             "Теперь про надёжность. Реплики решают, переживёт ли тема потерю сервера, а "
             "подтверждение записи решает, переживёт ли её отдельное сообщение."),
            ("Мария Кузнецова",
             "Подтверждение от всех реплик — самый медленный и самый честный режим. "
             "Подтверждение от одной быстрее, но при падении лидера сообщение исчезнет."),
            ("Мария Кузнецова",
             "Про доставку ровно один раз спрашивают всегда. Внутри Кафки она есть, между "
             "Кафкой и вашей базой — нет, и это нужно закрывать идемпотентностью."),
            ("Мария Кузнецова",
             "Идемпотентность обычно означает ключ операции в базе. Повторная запись с тем же "
             "ключом ничего не меняет, и повтор доставки перестаёт быть проблемой."),
            ("Мария Кузнецова",
             "Отставание потребителя — главная метрика, за которой стоит следить. Оно растёт "
             "заранее и предупреждает раньше, чем начнут жаловаться люди."),
            ("Мария Кузнецова",
             "Коротко: журнал вместо очереди, порядок внутри партиции, ключ по сущности и "
             "идемпотентность на выходе. Всё остальное — детали настройки."),
        ],
    },
    {
        "id": "2024-02-20-postgres-indeksy",
        "title": "Postgres: индексы, которые работают",
        "date": "2024-02-20",
        "event": "Демо-встреча",
        # Люди по ролям: выступавший и участники (открывшая встречу и вопрос из зала) — на
        # этом стоят тесты страницы записи и фильтра «человек в любой роли». `kind` и `award` —
        # то, что у живого корпуса приходит из его календаря; здесь просто значения.
        "speakers": ["Пётр Ковалёв"],
        "participants": ["Мария Кузнецова", "Олег Соколов"],
        "kind": ["Технологии"],
        "award": True,
        "category": "Базы данных",
        "topics": ["PostgreSQL", "индексы"],
        "tags": ["postgres", "базы данных"],
        "summary": "Почему индекс иногда не используется, чем B-tree отличается от GIN и как "
                   "читать план запроса.",
        "slides": "slides.pdf",
        "turns": [
            ("Мария Кузнецова",
             "Начинаем. Сегодня Пётр расскажет про индексы в Постгресе."),
            ("Пётр Ковалёв",
             "Индекс — это не волшебство, а структура данных. Планировщик выберет его только "
             "тогда, когда посчитает, что так дешевле."),
            ("Пётр Ковалёв",
             "Если запрос возвращает половину таблицы, последовательное чтение выиграет у "
             "индекса, и это правильное решение, а не ошибка."),
            ("Пётр Ковалёв",
             "B-tree отвечает на сравнения и диапазоны. Для поиска по массивам и по документам "
             "нужен GIN, и стоимость его обновления заметно выше."),
            ("Пётр Ковалёв",
             "Начинайте с плана запроса. Explain analyze показывает не догадку, а то, что "
             "случилось на самом деле."),
            ("Олег Соколов",
             "А вопрос: индекс по выражению планировщик тоже учитывает?"),
            ("Пётр Ковалёв",
             "Да, если выражение в запросе совпадает с индексным дословно."),
        ],
    },
    {
        # ⚠️ Запись БЕЗ медиа и без пословных времён. Такие приезжают из переноса: анонс встречи,
        # у которого видео так и не выложили. Она обязана показываться в списке и открываться
        # в читалке — на этом инварианте стоит отдельный тест.
        "id": "2024-03-05-anons-kvartala",
        "title": "Анонс: планы на квартал",
        "date": "2024-03-05",
        "event": "Демо-анонс",
        "speakers": [],
        # Анонс читает ведущий: голос назван, значит обязан стоять в одном из списков людей —
        # инвариант шапки (`tests/test_content.py::test_named_speakers_match_frontmatter`).
        "participants": ["Ведущий"],
        "tags": ["анонс"],
        "summary": "Короткий анонс без записи: что планируем обсудить в ближайшие месяцы.",
        "media": None,
        "turns": [
            ("", "В ближайшем квартале планируем три встречи: очереди сообщений, индексы и "
                 "наблюдаемость."),
            ("", "Записи выкладываем в этот же раздел. Если тема интересна — приходите с "
                 "вопросами заранее."),
        ],
    },
]


SITE_YML = """\
# Демо-корпус: одно пространство, ноль данных компании.
#
# Он существует ради двух вещей. Первая — приложение обязано подниматься БЕЗ рабочего корпуса,
# и на нём же гоняются тесты страниц: иначе проверки молча зависят от данных компании и в чистой
# репе не работают вовсе. Вторая — это ВЫРОЖДЕННЫЙ случай конфигурации: у корпуса нет ключа
# `spaces`, значит он сам и есть пространство. Так будет устроен любой заказ с одним корпусом.
slug: demo

brand:
  title: "Демо-записи"
  # {записей} / {записей-род} — фронт подставит живое число со склонением. Руками не вписывать.
  tagline: "Три синтетические записи — чтобы посмотреть, как всё устроено, на {записей-род}"
  about: |
    <p>Демонстрационный корпус: три записи, собранные скриптом
    <code>tools/make_demo.py</code>. Речь синтезирована, люди и события вымышлены.</p>
    <p>Он нужен, чтобы приложение можно было запустить и проверить без настоящего корпуса.</p>
  hosts: []
  links: []
  examples:
    - "Чем Кафка отличается от очереди?"
    - "Почему индекс не используется?"

theme:
  mood: instrument
  tokens:
    accent: "#6E8F6B"
    accent-fill: "#6E8F6B"
    sonar: "#57B4A9"
  # Цвет ветки (каталога первого уровня под `records/`): фронт маркирует им записи везде.
  # У демо-корпуса веток нет — карта показывает форму и проверяется тестом API.
  sections:
    Основы: "#6E8F6B"

content:
  records: records
  group_by: event
  media: media

# Вопрос к одной записи: кнопки над расшифровкой и шаблон, которым сервер сообщает агенту,
# что искать надо только внутри неё. Тексты синтетические, как и весь демо-корпус.
chat:
  enabled: true
  presets:
    "*":
      - label: "Краткое содержание"
        question: "Перескажи эту запись: о чём она и что в ней главное."
      - label: "Главные тезисы"
        question: "Назови главные тезисы этой записи по порядку, с моментами."
    Основы:
      - label: "Что нужно знать заранее"
        question: "Что нужно знать заранее, чтобы понять эту запись?"
"""


README_MD = """\
# Демо-корпус

Три синтетические записи. **Данных компании здесь нет и быть не может** — люди вымышлены,
темы взяты из публичных технологий, речь синтезирована.

Зачем он нужен:

* приложение обязано подниматься **без** рабочего корпуса — на этом стоит будущая чистая репа
  веб-морды, которую будут наполнять другие люди и другими записями;
* на нём гоняются тесты страниц: прибитые к живой записи, они «работают» даже когда в код
  просочилось доменное, и универсальность тихо исчезает;
* это вырожденный случай конфигурации — у корпуса нет ключа `spaces`, значит он сам и есть
  пространство. Так будет устроен любой заказ с одним корпусом.

Пересобрать (macOS, нужны `say`, `ffmpeg`, `ffprobe`):

```bash
python3 tools/make_demo.py --force
```

Что внутри и почему именно так:

| запись | чем важна |
|---|---|
| `2024-01-15-kafka-osnovy` | обычная: видео, пословные времена, метки |
| `2024-02-20-postgres-indeksy` | со **слайдами** — проверяет отдачу PDF |
| `2024-03-05-anons-kvartala` | **без медиа**: перенесённый анонс обязан показываться, а не выпадать |

⚠️ Времена ВНУТРИ реплики — оценка по длине слова, а не выравнивание по звуку: звук
синтетический, выравнивать нечего. Границы самих реплик точные — каждая синтезируется
отдельным файлом, поэтому ошибка не накапливается от начала записи. В файле это помечено
честно: `"aligned_by": "proportional"`. Формат тот же (`morag-words-v1`), и караоке не
отличает демо от настоящей записи.

⚠️ Имена проверяются против запретного списка (`tools/leak_check.py`), который растёт из
рабочего корпуса. Совпадений нет и быть не должно — на это есть тест: иначе фамилия
нового коллеги молча сделает демо-корпус незаконным.
"""


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def words_of(text: str, start: float, end: float) -> list[list]:
    """Слова реплики с временами: доля слова пропорциональна его длине.

    Внутри реплики это оценка (см. шапку модуля), но границы самой реплики точные — каждая
    синтезирована отдельным файлом. Для караоке важнее второе: ошибка не накапливается от
    начала записи, а гасится на каждой реплике.
    """
    tokens = [w for w in re.split(r"\s+", text.strip()) if w]
    if not tokens:
        return []
    weights = [len(w) + 1 for w in tokens]
    total = sum(weights)
    span = max(end - start, 0.1)
    words: list[list] = []
    at = start
    for token, weight in zip(tokens, weights):
        share = span * weight / total
        # Слово занимает 92% своей доли: остаток — зазор, иначе подсветка идёт впритык
        # и на стыке выглядит как одно длинное слово.
        words.append([token, round(at, 2), round(at + share * 0.92, 2)])
        at += share
    return words


def synthesize(record: dict, media_path: Path) -> list[tuple[float, float]]:
    """Озвучить реплики и собрать из них одно видео. Возвращает границы реплик."""
    tmp = media_path.parent / f".{record['id']}.tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        pieces, spans, at = [], [], 0.0
        for number, (_, text) in enumerate(record["turns"]):
            aiff = tmp / f"{number:02d}.aiff"
            _run(["say", "-v", VOICE, "-o", str(aiff), text])
            length = _duration(aiff)
            spans.append((round(at, 2), round(at + length, 2)))
            at += length + GAP
            pieces.append(aiff)

        # Склейка через фильтр, а не через concat-демультиплексор: паузу между репликами
        # проще задать здесь, чем городить файлы тишины.
        inputs: list[str] = []
        for piece in pieces:
            inputs += ["-i", str(piece)]
        # ⚠️ Нулевой вход — картинка от lavfi, поэтому звук начинается с первого.
        delays = "".join(
            f"[{i + 1}:a]adelay={int(spans[i][0] * 1000)}|{int(spans[i][0] * 1000)}[a{i}];"
            for i in range(len(pieces))
        )
        mix = "".join(f"[a{i}]" for i in range(len(pieces)))
        graph = f"{delays}{mix}amix=inputs={len(pieces)}:normalize=0[out]"
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={VIDEO_COLOR}:s={VIDEO_SIZE}:r=1",
            *inputs,
            "-filter_complex", graph, "-map", "[out]", "-map", "0:v",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "1",
            "-shortest", "-t", f"{at:.2f}", str(media_path),
        ])
        return spans
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# Минимальный валидный PDF на две страницы. Библиотеки ради демо не тянем, а LibreOffice
# на машине нет вовсе — проверено. Текст латиницей: base14-шрифты кириллицу не несут.
def write_pdf(path: Path) -> None:
    pages = ["Postgres indexes", "B-tree, GIN, and the query plan"]
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for index, title in enumerate(pages):
        content = f"BT /F1 28 Tf 60 500 Td ({title}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> "
            f"/Contents {4 + index * 2} 0 R >>".encode()
        )
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def yaml_value(value) -> str:
    """Как пишет шапку `make_record.py`: строки в кавычках, числа без.

    ⚠️ Не мелочь: `duration_sec` в кавычках приезжает в payload чанка строкой, и
    сравнение «длиннее часа» в инструменте `catalog` молча перестаёт работать.
    """
    if isinstance(value, list):
        return "[" + ", ".join(f'"{v}"' for v in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


def build(force: bool) -> int:
    if DEMO.exists() and not force:
        print(f"{DEMO} уже собран — нужен --force, чтобы пересобрать")
        return 0
    if force and DEMO.exists():
        shutil.rmtree(DEMO)

    (DEMO / "records").mkdir(parents=True)
    (DEMO / "media").mkdir(parents=True)
    (DEMO / "site.yml").write_text(SITE_YML, encoding="utf-8")
    (DEMO / "README.md").write_text(README_MD, encoding="utf-8")

    for record in DEMO_RECORDS:
        directory = DEMO / "records" / record["id"]
        directory.mkdir()
        has_media = record.get("media", "") is not None

        if has_media:
            media_name = f"{record['id']}.mp4"
            spans = synthesize(record, DEMO / "media" / media_name)
        else:
            # Без звука реплики всё равно нужны времена: читалка показывает тайм-коды,
            # а список — длительность. Считаем по скорости обычной речи.
            media_name, spans, at = None, [], 0.0
            for _, text in record["turns"]:
                length = max(2.0, len(text) / 14.0)
                spans.append((round(at, 2), round(at + length, 2)))
                at += length + GAP

        head = {
            "title": record["title"],
            "date": record["date"],
            "year": record["date"][:4],
            "event": record["event"],
            "kind": record.get("kind") or [],
            "category": record.get("category") or "",
            "topics": record.get("topics") or [],
            "speakers": record["speakers"],
            "participants": record.get("participants") or [],
            # голосов всего — сколько разных подписей в репликах, как считает живая сборка
            "voices": len({speaker for speaker, _ in record["turns"]}),
            "duration_sec": int(spans[-1][1]) + 1,
            "summary": record["summary"],
        }
        if media_name:
            head["media"] = media_name
        if record.get("slides"):
            head["slides"] = record["slides"]
            write_pdf(directory / record["slides"])
        head["tags"] = record["tags"]
        if record.get("award"):
            head["award"] = True

        lines = [f"{key}: {yaml_value(value)}" for key, value in head.items() if value not in (None, [], "")]
        body = "\n\n".join(
            f"[{speaker or 'Ведущий'}] <!-- t:{spans[i][0]} --> {text}"
            for i, (speaker, text) in enumerate(record["turns"])
        )
        (directory / "record.md").write_text(
            "---\n" + "\n".join(lines) + "\n---\n\n" + body + "\n", encoding="utf-8"
        )

        if has_media:
            turns = [
                {
                    "start": spans[i][0],
                    "end": spans[i][1],
                    "speaker": speaker or "Ведущий",
                    "words": words_of(text, spans[i][0], spans[i][1]),
                }
                for i, (speaker, text) in enumerate(record["turns"])
            ]
            (directory / "record.words.json").write_text(
                json.dumps(
                    {
                        "format": "morag-words-v1",
                        "episode": record["id"],
                        "duration_sec": spans[-1][1],
                        "words_total": sum(len(t["words"]) for t in turns),
                        "reordered": 0,
                        # ⚠️ Честная пометка: внутри реплики времена оценены по длине слова,
                        # а не выровнены по звуку. Границы реплик при этом точные.
                        "aligned_by": "proportional",
                        "turns": turns,
                    },
                    ensure_ascii=False, indent=1,
                ) + "\n",
                encoding="utf-8",
            )
        print(f"собрано: {record['id']}"
              f"{'' if has_media else '  (без медиа — намеренно)'}")

    print(f"\nдемо-корпус готов: {DEMO.relative_to(REPO)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="пересобрать, затерев существующий")
    args = parser.parse_args()
    for tool in ("say", "ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"нет {tool} — демо-корпус собирается на macOS с ffmpeg", file=sys.stderr)
            return 2
    return build(args.force)


if __name__ == "__main__":
    raise SystemExit(main())
