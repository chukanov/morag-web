"""Сборка вопроса для движка: контекст чтения и вопрос про запись целиком.

⚠️ Шаблоны приходят ИЗ КОНФИГА, который пишет человек руками. Поэтому тут проверяется не только
подстановка, но и то, что кривой шаблон не отнимает у посетителя возможность спросить: одна
лишняя `{` в site.yml иначе роняла бы КАЖДЫЙ вопрос пятисоткой.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chat.prompt import (  # noqa: E402
    build_reading_context, compose_about_record, compose_question, safe_format,
)
from app.content.records import RecordMeta  # noqa: E402
from app.content.transcript import Utterance  # noqa: E402

def record(**over) -> RecordMeta:
    """Запись-заготовка: у `RecordMeta` два десятка обязательных полей, а вопросу нужны пять."""
    fields = dict(
        id="2024-01-15-kafka-osnovy", title="Очереди Kafka: основы", date="2024-01-15",
        group="Летучка", speakers=["Мария Кузнецова"], participants=[], voices=2,
        duration_sec=600, summary="", media="", slides="", post="", discussion="", tags=[],
        kind=[], award=False, category="", topics=[], year="2024",
        section="Основы", subgroup="2024",
        path="Основы/2024/2024-01-15-kafka-osnovy/record.md",
    )
    return RecordMeta(**{**fields, **over})


RECORD = record()
LINES = [
    Utterance(sec=0.0, end_sec=11.0, speaker="Мария Кузнецова", text="Начнём с того, зачем нужны очереди."),
    Utterance(sec=12.0, end_sec=23.0, speaker="Мария Кузнецова", text="Партиции делят поток на части."),
    Utterance(sec=24.0, end_sec=30.0, speaker="Пётр Ковалёв", text="А как считается отставание?"),
]


def test_запись_целиком_несёт_заголовок_дату_и_идентификатор():
    out = compose_about_record("О чём запись?", record=RECORD, doc_id="local:demo:" + RECORD.path)
    assert "Очереди Kafka: основы" in out
    assert "2024-01-15" in out
    assert "local:demo:" + RECORD.path in out
    assert "get_doc" in out and "doc_ids" in out, "агенту названы инструменты сужения"
    assert out.rstrip().endswith("О чём запись?"), "вопрос человека стоит последним и целиком"


def test_без_идентификатора_остаётся_запасной_ход_через_каталог():
    """Движкового конфига рядом нет — ограничение держится словами, а запись ищется по названию."""
    out = compose_about_record("О чём запись?", record=RECORD)
    assert "каталоге" in out and "Очереди Kafka: основы" in out
    assert "local:" not in out, "пустую подстановку идентификатора в промпт не пускаем"
    assert 'get_doc("")' not in out


def test_шаблон_берётся_из_настроек_и_видит_поля_записи():
    settings = {"template": "{section} · {speakers} · {group}\n{question}"}
    out = compose_about_record("Вопрос?", record=RECORD, settings=settings)
    assert out == "Основы · Мария Кузнецова · Летучка\nВопрос?"


def test_кривой_шаблон_не_роняет_вопрос_а_отдаёт_его_голым():
    for broken in ("{title} {", "{нет_такого_поля}", "{0}"):
        assert compose_about_record("Вопрос?", record=RECORD, settings={"template": broken}) == "Вопрос?"
        assert safe_format(broken, title="x") == ""


def test_вопрос_от_реплики_подставляет_окно_и_переживает_пустой_митап():
    bare = record(group="")
    out = compose_question(
        "Что это значит?", record=bare, utterances=LINES, sec=12.0,
        settings={"template": "«{title}» ({group}, {date}) на {mmss}\n{quote}\n{context}\n{question}"},
    )
    assert "«Очереди Kafka: основы» (, 2024-01-15) на 0:12" in out
    assert "Партиции делят поток на части." in out
    assert out.rstrip().endswith("Что это значит?")


def test_без_реплик_вопрос_уходит_голым():
    assert compose_question("Вопрос?", record=RECORD, utterances=[], sec=0) == "Вопрос?"
    assert compose_question("Вопрос?") == "Вопрос?"


def test_окно_реплик_ставит_центральную_последней():
    context, center = build_reading_context(LINES, 24.0, radius=1, max_chars=2000)
    assert center is not None and center.speaker == "Пётр Ковалёв"
    assert context.strip().splitlines()[-1].endswith("А как считается отставание?")
