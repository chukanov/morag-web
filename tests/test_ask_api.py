"""`POST /api/ask` целиком: от кадра движка до строчки в журнале.

Дыра, которую этот файл закрывает, стоила владельцу журнала. Кадр цитаты собирает
`engine/frames.py` (поле записи там `rec`), а обработчик ответа читал `ep` — подкастовый номер
выпуска. Тесты на кадры были, тест на журнал был, а прохода «движок ответил цитатой → журнал
получил запись» не было НИ ОДНОГО, и `KeyError` жил в `finally` уже после отданного ответа:
снаружи всё выглядело исправным, а в журнале не оставалось ровно тех ответов, ради разбора
которых он и заведён.

Движок здесь — заглушка: настоящий требует Qdrant, эмбеддер и LLM, а проверяем мы свой стык.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.main import app  # noqa: E402

# Ответ движка: статус, цитата и два токена. Форма — как у живого morag pipelines.
# ⚠️ `url` у цитаты НЕ пуст: у корпуса без поля `url` в шапке движок синтезирует `file://`
# со своим внутренним путём. Раньше он перебивал наш адрес видео.
CITATION = {
    "event": {
        "type": "citation",
        "data": {
            "source": {"name": "Тестовая запись · 4:07 · Голос"},
            "document": ["[Голос] строка одна\n[Голос] строка два"],
            "metadata": [{
                "source": "local:demo:2024-01-15-kafka-osnovy/record.md#247",
                "url": "file:///app/space/records/2024-01-15-kafka-osnovy/record.md#t=247",
                "citation_number": 1,
                "found_by": [{"tool": "search", "step": 1, "rank": 1, "query": "тест"}],
            }],
        },
    }
}
TOKENS = [{"choices": [{"delta": {"content": piece}}]} for piece in ("Ответ ", "по существу [1].")]


class _FakeResponse:
    status_code = 200

    async def aiter_bytes(self):
        for event in (CITATION, *TOKENS):
            yield b"data: " + json.dumps(event).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"

    async def aread(self):
        return b""


class _FakeEngine:
    def __init__(self) -> None:
        # ⚠️ Запоминаем сообщения: иначе не видно, доехал ли до движка ОБОГАЩЁННЫЙ вопрос —
        # снаружи ответ выглядит одинаково и с контекстом, и без него.
        self.seen: list[dict] = []

    @asynccontextmanager
    async def stream_chat(self, messages):
        self.seen = list(messages)
        yield _FakeResponse()

    async def aclose(self):
        """Приложение закрывает движки на выключении — заглушка обязана это уметь."""


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(app) as c:
        slug = next(iter(app.state.corpora))
        real = app.state.engines[slug]
        fake = _FakeEngine()
        app.state.engines[slug] = fake
        # Журнал — во временный файл: настоящий это личные данные владельца.
        app.state.journal.path = tmp_path / "journal.jsonl"
        app.state.topic.enabled = False   # тема ходит в LLM, здесь она не при чём
        try:
            yield c, slug, tmp_path / "journal.jsonl"
        finally:
            app.state.engines[slug] = real   # не оставляем заглушку соседним тестам


@pytest.fixture
def engine():
    """Заглушка движка текущего клиента — чтобы посмотреть, ЧТО ему отправили."""
    slug = next(iter(app.state.corpora))
    return lambda: app.state.engines[slug]


def _frames(response) -> list[dict]:
    out = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def test_ответ_с_цитатой_доезжает_до_журнала(client):
    """Главное: цитата в ответе не роняет запись в журнал."""
    c, slug, journal = client
    response = c.post("/api/ask", json={"question": "что рассказывали?", "corpus": slug})
    assert response.status_code == 200
    frames = _frames(response)
    assert [f for f in frames if f["type"] == "citation"], "цитата не доехала до браузера"

    text = journal.read_text(encoding="utf-8").strip()
    assert text, "ответ с цитатой не попал в журнал — падение в finally его съело"
    record = json.loads(text.splitlines()[-1])
    assert record["status"] == "ok"
    assert record["citations"], "цитаты в журнале пусты — разбирать провал будет не по чему"
    # Поле называется как в кадре. Разъедется — журнал снова начнёт падать молча.
    assert "rec" in record["citations"][0]


def test_вопрос_про_запись_целиком_уходит_обогащённым(client, engine):
    """Канала «искать в подмножестве» между сайтом и движком нет: ограничение выражается ТОЛЬКО
    текстом вопроса. Значит проверять надо то, что реально отправлено движку."""
    c, slug, journal = client
    corpus = app.state.corpora[slug]
    record = next(iter(corpus.index.all()))
    # У демо-корпуса движкового конфига рядом нет, поэтому источник задаём прямо: проверяем
    # СБОРКУ идентификатора (префикс + относительный путь), а не чтение чужого файла.
    corpus._doc_prefix = "local:demo:"
    try:
        response = c.post("/api/ask", json={
            "question": "о чём эта запись?", "corpus": slug,
            "context": {"record_id": record.id, "scope": "record"},
        })
    finally:
        corpus._doc_prefix = None
    assert response.status_code == 200
    sent = engine().seen[-1]["content"]
    assert record.title in sent, "агенту не назвали запись"
    assert "о чём эта запись?" in sent, "вопрос человека потерялся в обогащении"
    assert f"local:demo:{record.path}" in sent, "идентификатор записи в базе движка не подставлен"

    written = json.loads(journal.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert written["context"] == {"record_id": record.id, "scope": "record"}
    assert written["question"] == "о чём эта запись?", "в журнале СЫРОЙ вопрос, а не промпт"


def test_без_движкового_конфига_запись_названа_словами(client, engine):
    """Источник неизвестен — идентификатора нет, и агенту велено найти запись в каталоге.
    Пустая подстановка вида `get_doc("")` в промпт не уходит."""
    c, slug, _ = client
    record = next(iter(app.state.corpora[slug].index.all()))
    c.post("/api/ask", json={
        "question": "о чём эта запись?", "corpus": slug,
        "context": {"record_id": record.id, "scope": "record"},
    })
    sent = engine().seen[-1]["content"]
    assert record.title in sent and "каталоге" in sent
    assert 'get_doc("")' not in sent and "local:" not in sent


def test_неизвестная_запись_в_контексте_не_роняет_вопрос(client, engine):
    c, slug, _ = client
    response = c.post("/api/ask", json={
        "question": "о чём эта запись?", "corpus": slug,
        "context": {"record_id": "нет-такой-записи", "scope": "record"},
    })
    assert response.status_code == 200
    assert engine().seen[-1]["content"] == "о чём эта запись?", "вопрос должен уйти голым"


def test_вопрос_про_запись_идёт_во_вторую_дверь_если_она_есть(client, engine):
    """Режим «одна запись» — свой процесс движка на том же индексе (`engines.<слаг>.record`,
    15.09): вопрос со scope=record уходит в него, обычный — в общий; в журнале видно, кто отвечал.
    Второй двери нет — всё как раньше (проверяют соседние тесты)."""
    c, slug, journal = client
    record = next(iter(app.state.corpora[slug].index.all()))
    second = _FakeEngine()
    app.state.engines[f"{slug}:record"] = second
    try:
        c.post("/api/ask", json={
            "question": "о чём эта запись?", "corpus": slug,
            "context": {"record_id": record.id, "scope": "record"},
        })
        assert second.seen, "вопрос к записи не дошёл до второй двери"
        assert not engine().seen, "вопрос к записи ушёл ещё и в общий движок"
        written = json.loads(journal.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert written["engine"] == "record"

        c.post("/api/ask", json={"question": "что рассказывали?", "corpus": slug})
        assert engine().seen, "обычный вопрос обязан идти в общий движок"
        written = json.loads(journal.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert written["engine"] == "corpus"
    finally:
        del app.state.engines[f"{slug}:record"]


def test_свой_адрес_видео_перебивает_file_от_движка(client):
    """`file://` — внутренний путь контейнера: включить его нельзя, показывать браузеру незачем."""
    c, slug, _ = client
    frames = _frames(c.post("/api/ask", json={"question": "что рассказывали?", "corpus": slug}))
    citation = next(f for f in frames if f["type"] == "citation")
    assert not citation["url"].startswith("file://"), "адрес движка перебил наш"
    assert citation["rec"], "цитата не разрешилась в запись демо-корпуса"


class _DeadEngine:
    """Движок, который не отвечает вовсе — как при упавшем контейнере или лёгшем LLM за ним."""

    @asynccontextmanager
    async def stream_chat(self, messages):
        raise httpx.ConnectError("connection refused")
        yield  # pragma: no cover — до сюда не доходит, но делает функцию генератором

    async def aclose(self):
        pass


@pytest.mark.parametrize("llm_alive, expect", [(True, "Движок"), (False, "LLM-шлюз")])
def test_молчащий_движок_отличает_себя_от_молчащего_LLM(client, monkeypatch, llm_alive, expect):
    """⚠️ Снаружи «упал поиск» и «лёг LLM-шлюз» — одно и то же «не получилось», и различать их
    приходилось руками: два инцидента 17–18.09 ушли на это целиком. Теперь на пути ОШИБКИ (и
    только там) сайт спрашивает сам LLM-эндпоинт и называет причину."""
    c, slug, _ = client
    app.state.engines[slug] = _DeadEngine()

    async def reachable(timeout: float = 5.0):
        return llm_alive

    monkeypatch.setattr(app.state.topic, "reachable", reachable)
    frames = _frames(c.post("/api/ask", json={"question": "что там?", "corpus": slug}))
    errors = [f for f in frames if f.get("type") == "error"]
    assert errors, "молчащий движок обязан сказать об этом кадром ошибки"
    assert expect in errors[0]["message"], errors[0]["message"]


def test_если_спросить_LLM_не_у_кого_ответ_прежний(client, monkeypatch):
    """Диагностика не имеет права заменить собой ошибку: не настроен ключ (`reachable` → None)
    или сама проба упала — человек видит обычное «движок недоступен», а не молчание."""
    c, slug, _ = client
    app.state.engines[slug] = _DeadEngine()

    async def broken(timeout: float = 5.0):
        raise RuntimeError("проба сама сломалась")

    monkeypatch.setattr(app.state.topic, "reachable", broken)
    frames = _frames(c.post("/api/ask", json={"question": "что там?", "corpus": slug}))
    assert any("Движок" in f.get("message", "") for f in frames if f.get("type") == "error")
