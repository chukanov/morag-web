"""Классификатор записей (`tools/classify.py`) — чистые части: таксономия, канонизация, промпт,
разбор ответа. LLM подменён; таксономия синтетическая — доменного в тестах нет.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import classify  # noqa: E402

TAX = {
    "categories": [
        {"name": "Очереди и потоки", "about": "Брокеры сообщений и стриминг.", "hints": ["Kafka"]},
        {"name": "Базы данных", "about": "Хранилища и индексы.", "hints": ["Postgres"]},
    ],
    "kinds": ["Доклад", "Воркшоп"],
    "topics": {
        "tech": [
            {"name": "Kafka", "aliases": ["kafka", "кафка"]},
            {"name": "PostgreSQL", "aliases": ["Postgres", "постгрес"]},
        ],
        "systems": [{"name": "Маяк", "aliases": ["Mayak", "маяк"]}],
    },
}


def test_canonize_maps_aliases_and_keeps_unknown_apart():
    index = classify.canon_index(TAX)
    known, unknown = classify.canonize(["кафка", "Postgres", "Mayak", "Redis", "Kafka"], index)
    assert known == ["Kafka", "PostgreSQL", "Маяк"]      # порядок ответа, без дублей
    assert unknown == ["Redis"]                            # не в словаре — в отчёт, не в шапку


def test_key_ignores_case_spacing_and_yo():
    assert classify._key("VS Code") == classify._key("vscode")
    assert classify._key("Ёлка") == classify._key("елка")


def test_prompt_lists_taxonomy_and_record_facts():
    index = classify.canon_index(TAX)
    inp = {"id": "r", "title": "Kafka без боли", "date": "2026-03-12", "branch": "Летучка", "sub": "2026",
           "event": "Летучка", "kind": [], "tags": ["kafka"], "summary": "", "calendar_topic": "",
           "speakers": ["Мария Кузнецова"], "doc_summary": "Про партиции.", "terms": ["Postgres", "zookeeper", "кафка"]}
    text = classify.prompt_for(inp, TAX, index)
    assert "Очереди и потоки — Брокеры сообщений" in text and "Формат (kind) в записи: пусто" in text
    assert "Сводка расшифровки: Про партиции." in text
    # термины из словаря идут первыми
    terms_line = next(l for l in text.splitlines() if l.startswith("Термины из глоссария"))
    assert terms_line.index("Postgres") < terms_line.index("zookeeper")


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    async def complete_json(self, messages, schema, schema_name, max_tokens):
        self.calls += 1
        assert schema["required"] and schema_name == "classify"
        return self.answer


def _run(answer, inp_kind=None):
    inp = {"id": "r1", "title": "t", "date": "2026-01-01", "branch": "Летучка", "sub": "2026", "event": "",
           "kind": inp_kind or [], "tags": [], "summary": "", "calendar_topic": "", "speakers": [],
           "doc_summary": "s", "terms": []}
    return asyncio.run(classify.classify_one(FakeLLM(answer), inp, TAX, classify.canon_index(TAX),
                                             classify.category_names(TAX)))


def test_answer_is_validated_against_taxonomy():
    res = _run({"category": "Базы данных", "category_alt": "Не категория", "confidence": 0.8,
                "topics": ["постгрес", "Redis"], "new_topics": ["ClickHouse"], "kind": "Воркшоп", "why": "ok"})
    assert res["category"] == "Базы данных" and res["category_alt"] is None
    assert res["topics"] == ["PostgreSQL"] and res["new_topics"] == ["ClickHouse", "Redis"]
    assert res["kind"] == "Воркшоп"


def test_category_typo_is_forgiven_but_not_invention():
    cats = ["Компьютерное зрение", "Базы данных"]
    assert classify.match_category("Комьютерное зрение", cats) == "Компьютерное зрение"
    assert classify.match_category("Базы  данных", cats) == "Базы данных"
    assert classify.match_category("Компьютеры", cats) == ""


def test_category_outside_list_is_empty_and_kind_not_overwritten():
    res = _run({"category": "Что-то своё", "category_alt": None, "confidence": 0.9, "topics": [],
                "new_topics": [], "kind": "Воркшоп", "why": ""}, inp_kind=["Доклад"])
    assert res["category"] == "" and res["category_raw"] == "Что-то своё"
    assert res["kind"] is None and res["had_kind"] is True


def test_llm_failure_is_reported_not_raised():
    class Boom:
        async def complete_json(self, *a, **k):
            raise RuntimeError("timeout")
    inp = {"id": "r1", "title": "t", "date": "", "branch": "", "sub": "", "event": "", "kind": [], "tags": [],
           "summary": "", "calendar_topic": "", "speakers": [], "doc_summary": "", "terms": []}
    res = asyncio.run(classify.classify_one(Boom(), inp, TAX, {}, []))
    assert res["error"].startswith("RuntimeError")


def test_write_meta_keeps_owner_labels(tmp_path):
    meta = tmp_path / "record.meta.json"
    meta.write_text(json.dumps({"id": "r1", "labels": {"category": "Базы данных"}}), encoding="utf-8")
    result = {"category": "Очереди и потоки", "topics": ["Kafka"], "confidence": 0.7, "why": "w"}
    stamp = {"model": "m", "prompt_sha": "abc", "when": "2026-09-12T10:00"}
    assert classify.write_meta(meta, result, stamp, dry=False)
    saved = json.loads(meta.read_text(encoding="utf-8"))
    assert saved["labels"] == {"category": "Базы данных"}
    assert saved["classification"]["category"] == "Очереди и потоки" and saved["classification"]["from"] == "llm"
    assert not classify.write_meta(meta, result, stamp, dry=False), "повтор без изменений — не пишем"
