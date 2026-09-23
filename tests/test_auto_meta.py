"""Доразметка записи на сервере (`tools/auto_meta.py`, `tools/classify.py` по HTTP).

Загруженная через сайт запись описана двумя полями; остальное знает корпус. Что здесь закреплено:
  * категория и темы берутся ТОЛЬКО из таксономии — незнакомое уходит в `new_topics`, а не в шапку;
  * название придумывается лишь тогда, когда своего нет (или оно служебное — имя файла, «видео»);
  * авторская аннотация не перебивается сочинённой;
  * молчащий шлюз не роняет приём: запись остаётся, просто без категории.

Шлюз — заглушкой (`httpx.MockTransport`), ключи в тестах не нужны.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))

import auto_meta  # noqa: E402
import classify  # noqa: E402
from test_turn_edits import whole_artifact  # noqa: E402

TAXONOMY = {
    "categories": [{"name": "Инфраструктура", "about": "железо, сети, кластеры"},
                   {"name": "Разработка", "about": "код и практики"}],
    "kinds": ["Доклад", "Демо"],
    "topics": {"tech": [{"name": "Kafka", "aliases": ["кафка"]}, {"name": "Postgres"}]},
}


def corpus(tmp_path: Path, *, head_extra: str = "") -> tuple[Path, Path]:
    """Корпус из одного пространства с одной собранной записью (как после приёма)."""
    family = tmp_path / "corp"
    records = family / "records"
    records.mkdir(parents=True)
    (family / "names.json").write_text(json.dumps({"speakers": {}, "records": {}}), encoding="utf-8")
    (family / "text_fixes.json").write_text(json.dumps({"global": [], "records": {}}), encoding="utf-8")
    (family / "turn_fixes.json").write_text(json.dumps({"records": {}}), encoding="utf-8")
    (family / "site.yml").write_text("slug: demo\n", encoding="utf-8")
    (family / "taxonomy.yml").write_text(yaml.safe_dump(TAXONOMY, allow_unicode=True), encoding="utf-8")

    record = records / "2026-03-12-talk"
    record.mkdir()
    artifact = whole_artifact()
    artifact["x_enriched"]["doc_summary"] = "Про очереди Kafka и как их чинить."
    artifact["x_enriched"]["glossary"] = [{"heard": "кафка", "canonicals": ["Kafka"]}]
    (record / "record.json").write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    (record / "record.md").write_text(
        f'---\ntitle: "talk"\ndate: "2026-03-12"\n{head_extra}---\n\n'
        "[Пётр Ковалёв] <!-- t:1.0 --> Мы ставим Grafanna в прод.\n", encoding="utf-8")
    (record / "record.meta.json").write_text(json.dumps({"speakers": []}), encoding="utf-8")
    return family, record


def answer(**over) -> dict:
    return {"category": "Инфраструктура", "category_alt": None, "confidence": 0.9,
            "topics": ["Kafka", "кафка", "Неведомая штука"], "new_topics": [], "kind": "Доклад",
            "title": "Очереди Kafka без боли", "why": "про очереди", **over}


def run_classify(family: Path, rid: str) -> subprocess.CompletedProcess:
    """Классификатор как его зовёт сервер — отдельным процессом, но со шлюзом-заглушкой внутри."""
    stub = f'''
import json, sys, httpx
ANSWER = {json.dumps(answer(), ensure_ascii=False)!r}
real = httpx.AsyncClient
def handle(request):
    body = request.read().decode()
    content = ANSWER if "json_schema" in body else "Сводка."
    return httpx.Response(200, json={{"choices": [{{"message": {{"content": content}}}}], "usage": {{}}}})
httpx.AsyncClient = lambda *a, **kw: real(*a, **{{**kw, "transport": httpx.MockTransport(handle)}})
sys.argv = ["classify.py", "--all", {rid!r}]
sys.path.insert(0, {str(REPO / "tools")!r})
import classify; sys.exit(classify.main())
'''
    env = {**os.environ, "MORAG_WEB_CORPUS": str(family), "ASR_LLM_BASE_URL": "https://llm.example.org/api",
           "ASR_LLM_MODEL": "Instruct", "OR_KEY": "test-key"}
    return subprocess.run([sys.executable, "-c", stub], capture_output=True, text=True, env=env, cwd=str(REPO))


# --- классификатор ---------------------------------------------------------------------

def test_classify_writes_only_taxonomy_values(tmp_path):
    family, record = corpus(tmp_path)
    done = run_classify(family, record.name)
    assert done.returncode == 0, done.stdout + done.stderr
    block = json.loads((record / "record.meta.json").read_text(encoding="utf-8"))["classification"]
    assert block["category"] == "Инфраструктура"
    assert block["topics"] == ["Kafka"], "алиас свёрнут к канону, дубль убран, незнакомое — не в темы"
    assert "Неведомая штука" in block["new_topics"], "незнакомая тема уходит в отчёт, а не в шапку"
    assert block["title_auto"] == "Очереди Kafka без боли"
    assert block["from"] == "llm" and block["model"] == "Instruct"


def test_classify_talks_http_without_the_engine_package(tmp_path):
    """⚠️ Классификатор не должен тянуть `openai` и пакет движка: на сервере их нет (venv собран
    из `app/requirements.txt`), и до 23.09 шаг разметки падал там молча."""
    family, record = corpus(tmp_path)
    assert run_classify(family, record.name).returncode == 0
    probe = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(REPO / 'tools')!r}); import classify;"
         " import sys as s; bad=[m for m in ('openai','morag','morag.llm.client') if m in s.modules];"
         " print(','.join(bad))"],
        capture_output=True, text=True, cwd=str(REPO))
    assert probe.returncode == 0 and not probe.stdout.strip(), f"подтянулось лишнее: {probe.stdout}"


# --- оркестратор -----------------------------------------------------------------------

def test_title_is_invented_only_when_missing(tmp_path):
    family, record = corpus(tmp_path)
    assert auto_meta.needs_title({"title": "talk"}, "2026-03-12-talk") is False
    assert auto_meta.needs_title({}, "rec") is True
    assert auto_meta.needs_title({"title": "видео"}, "rec") is True
    assert auto_meta.needs_title({"title": "rec"}, "rec") is True
    assert auto_meta.needs_title({"title": "Kafka без боли"}, "rec") is False


def test_enrich_fills_the_head_and_keeps_the_authors_summary(tmp_path, monkeypatch):
    family, record = corpus(tmp_path, head_extra='summary: "Автор написал сам."\n')
    monkeypatch.setenv("MORAG_WEB_CORPUS", str(family))
    seen: list[list[str]] = []

    def fake_run(argv, dry):
        seen.append(argv)
        name = Path(argv[1]).name
        if name == "classify.py":
            meta = json.loads((record / "record.meta.json").read_text(encoding="utf-8"))
            meta["classification"] = {"category": "Инфраструктура", "topics": ["Kafka"],
                                      "kind": "Доклад", "title_auto": "Очереди Kafka без боли"}
            (record / "record.meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if name == "make_record.py":
            head = (record / "record.md").read_text(encoding="utf-8")
            extra = 'category: "Инфраструктура"\ntopics: ["Kafka"]\n'
            (record / "record.md").write_text(head.replace("date:", extra + "date:", 1), encoding="utf-8")
        return True, ""

    monkeypatch.setattr(auto_meta, "run", fake_run)
    out = auto_meta.enrich(record)
    names = [Path(a[1]).name for a in seen]
    assert names == ["classify.py", "make_record.py"], "аннотация не сочиняется поверх авторской"
    assert out["titled"] == "", "заголовок у записи был — не трогаем"
    head = auto_meta.head_of(record)
    assert head["category"] == "Инфраструктура" and head["topics"] == ["Kafka"]


def test_enrich_passes_the_invented_title_to_the_rebuild(tmp_path, monkeypatch):
    family, record = corpus(tmp_path)
    (record / "record.md").write_text('---\ntitle: "2026-03-12-talk"\ndate: "2026-03-12"\n---\n\nтекст\n',
                                      encoding="utf-8")
    monkeypatch.setenv("MORAG_WEB_CORPUS", str(family))
    seen: list[list[str]] = []

    def fake_run(argv, dry):
        seen.append(argv)
        if Path(argv[1]).name == "classify.py":
            meta = json.loads((record / "record.meta.json").read_text(encoding="utf-8"))
            meta["classification"] = {"category": "Разработка", "topics": [], "title_auto": "Очереди Kafka"}
            (record / "record.meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return True, ""

    monkeypatch.setattr(auto_meta, "run", fake_run)
    out = auto_meta.enrich(record)
    rebuild = next(a for a in seen if Path(a[1]).name == "make_record.py")
    assert "--title" in rebuild and rebuild[rebuild.index("--title") + 1] == "Очереди Kafka"
    assert out["titled"] == "Очереди Kafka"
    assert [Path(a[1]).name for a in seen][-1] == "make_blurb.py", "аннотации не было — сочиняем"


def test_a_dead_gateway_does_not_break_the_record(tmp_path, monkeypatch):
    family, record = corpus(tmp_path)
    monkeypatch.setenv("MORAG_WEB_CORPUS", str(family))
    monkeypatch.setattr(auto_meta, "run", lambda argv, dry: (False, "шлюз лёг"))
    out = auto_meta.enrich(record)
    assert out["classified"] is False
    assert (record / "record.md").is_file(), "запись на месте, просто без категории"
    assert not auto_meta.head_of(record).get("category")
