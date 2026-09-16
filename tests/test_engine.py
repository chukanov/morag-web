"""Тесты SSE-парсера и нормализатора — на записанных фикстурах, без сети и трат."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import AsyncIterator

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.engine.chunk_html import extract, html_to_text  # noqa: E402
from app.engine.normalize import StreamNormalizer, _ThinkStripper  # noqa: E402
from app.engine.sse import SSEEvent, iter_events_from_bytes, iter_sse_events  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures"
H200 = FIXTURES / "engine_h200.sse"
CATALOG = FIXTURES / "engine_catalog.sse"


async def _chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


async def _lines(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


async def collect(data: bytes, chunk_size: int, resolver=None) -> list[dict]:
    norm = StreamNormalizer(resolver=resolver)
    out: list[dict] = []
    async for ev in iter_events_from_bytes(_chunks(data, chunk_size)):
        out.extend(norm.feed(ev))
        if norm.finished:
            break
    return out


def kinds(frames: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in frames:
        counts[f["type"]] = counts.get(f["type"], 0) + 1
    return counts


# --- парсер SSE ------------------------------------------------------------


async def test_done_without_trailing_newlines_is_not_lost():
    """Главная грабля движка: [DONE] приходит без \\n\\n и теряется наивным парсером."""
    raw = b'data: {"choices":[{"delta":{"content":"\xd0\xbf\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"}}]}\n\ndata: [DONE]'
    events = [ev async for ev in iter_events_from_bytes(_chunks(raw, 7))]
    assert events[-1].data == "[DONE]"


async def test_comments_and_unknown_fields_ignored():
    events = [
        ev
        async for ev in iter_sse_events(
            _lines([": hb", "id: 42", "retry: 3000", "data: {}", "", "data: [DONE]"])
        )
    ]
    assert [e.data for e in events] == ["{}", "[DONE]"]


async def test_multiline_data_is_joined():
    events = [ev async for ev in iter_sse_events(_lines(["data: a", "data: b", ""]))]
    assert events[0].data == "a\nb"


# --- фикстуры --------------------------------------------------------------


@pytest.mark.skipif(not H200.exists(), reason="нет фикстуры")
async def test_content_question_yields_tokens_and_citations():
    frames = await collect(H200.read_bytes(), 4096)
    k = kinds(frames)
    assert k["token"] > 100
    assert k["status"] >= 3
    assert k["citation"] == 14
    assert "error" not in k

    cits = [f for f in frames if f["type"] == "citation"]
    assert all(isinstance(c["n"], int) for c in cits)
    assert len({c["n"] for c in cits}) == len(cits), "номера цитат должны быть уникальны"
    assert all("#t=" in c["url"] for c in cits), "цитата должна вести на секунду записи"
    for c in cits:
        assert c["label"] and "·" in c["label"]
        assert c["text"] and "<" not in c["text"], "в тексте цитаты не должно быть тегов"
        assert "background" not in c["text"], "CSS из <style> не должен просачиваться"

    # ключевые слова движка — сырьё для смыслового ядра карточки
    with_keys = [c for c in cits if c["keywords"]]
    assert len(with_keys) >= len(cits) * 0.7, "движок подсвечивает почти каждый чанк"
    for c in with_keys:
        low = c["text"].lower()
        assert all(k in low for k in c["keywords"]), "ключевое слово обязано быть в тексте"


@pytest.mark.skipif(not CATALOG.exists(), reason="нет фикстуры")
async def test_catalog_question_has_no_citations():
    """Каталожный вопрос отвечает без цитат — это норма, а не ошибка."""
    frames = await collect(CATALOG.read_bytes(), 4096)
    k = kinds(frames)
    assert k.get("citation", 0) == 0
    assert k["token"] > 50
    assert "error" not in k


@pytest.mark.skipif(not H200.exists(), reason="нет фикстуры")
@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 13, 64, 997, 65536])
async def test_chunk_boundaries_do_not_change_result(size: int):
    """Разрезание потока в произвольном месте не должно менять ни один кадр."""
    data = H200.read_bytes()
    reference = await collect(data, 1 << 20)
    assert await collect(data, size) == reference


@pytest.mark.skipif(not H200.exists(), reason="нет фикстуры")
async def test_answer_text_is_assembled():
    data = H200.read_bytes()
    norm = StreamNormalizer()
    async for ev in iter_events_from_bytes(_chunks(data, 8192)):
        list(norm.feed(ev))
        if norm.finished:
            break
    assert norm.finished
    assert len(norm.answer_text) > 500
    assert norm.malformed == 0


@pytest.mark.skipif(not H200.exists(), reason="нет фикстуры")
async def test_resolver_fills_record_and_second():
    """⚠️ Фикстура записана на корпусе подкаста, и doc_id в ней подкастовые. Проверяем
    поэтому не попадание в наш корпус, а то, что нормализатор доносит поля резолвера до
    кадра и не падает на чужих путях: своё разрешение записи проверяет test_resolve.py."""
    from app.content.records import RecordIndex
    from app.content.resolve import make_resolver

    index = RecordIndex(REPO / "corpora" / "demo" / "records")
    frames = await collect(H200.read_bytes(), 4096, resolver=make_resolver(index, "demo"))
    cits = [f for f in frames if f["type"] == "citation"]

    assert cits, "цитат в фикстуре нет — проверять нечего"
    for c in cits:
        assert set(c) >= {"rec", "sec", "end", "url"}, f"кадр цитаты потерял поля: {c}"


# --- устойчивость ----------------------------------------------------------


async def test_malformed_frame_does_not_kill_stream():
    raw = (
        b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        b"data: {not json at all\n\n"
        b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
        b"data: [DONE]"
    )
    frames = await collect(raw, 16)
    assert [f["text"] for f in frames if f["type"] == "token"] == ["a", "b"]


async def test_finish_reason_frame_is_not_a_token():
    raw = b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]'
    assert kinds(await collect(raw, 16)).get("token", 0) == 0


async def test_unknown_event_type_is_ignored():
    raw = b'data: {"event":{"type":"weird_new_thing","data":{}}}\n\ndata: [DONE]'
    frames = await collect(raw, 16)
    assert frames == []


async def test_citation_number_missing_falls_back_to_counter():
    raw = (
        b'data: {"event":{"type":"citation","data":{"source":{"name":"A","url":"u#t=5"},'
        b'"metadata":[{"url":"u#t=5"}],"document":["<p>x</p>"]}}}\n\n'
        b"data: [DONE]"
    )
    cits = [f for f in await collect(raw, 32) if f["type"] == "citation"]
    assert cits[0]["n"] == 1


async def test_provenance_passes_through_known_fields_only():
    """Как чанк нашёлся — единственный честный ответ на «почему это здесь».

    Берём allowlist, а не «всё, что прислали»: движок общий, и однажды в этом
    поле окажется что-нибудь ещё — оно не должно уехать в браузер.
    """
    found = json.dumps(
        [
            {"tool": "search", "step": 1, "rank": 2, "query": "H200 обучение", "секрет": "нельзя"},
            {"tool": "get_doc", "step": 4, "rank": 1, "query": " " * 3},
            "мусор",
        ],
        ensure_ascii=False,
    )
    raw = (
        'data: {"event":{"type":"citation","data":{"source":{"name":"A","url":"u#t=5"},'
        f'"metadata":[{{"citation_number":1,"url":"u#t=5","found_by":{found}}}],'
        '"document":["<p>x</p>"]}}}\n\ndata: [DONE]'
    ).encode()
    cits = [f for f in await collect(raw, 256) if f["type"] == "citation"]
    assert cits[0]["found_by"] == [
        {"query": "H200 обучение", "tool": "search", "step": 1, "rank": 2},
        {"tool": "get_doc", "step": 4, "rank": 1},  # пустой запрос не поле
    ]


async def test_provenance_absent_is_not_an_error():
    """Движок постарше про провенанс не знает — цитата всё равно должна прийти."""
    raw = (
        'data: {"event":{"type":"citation","data":{"source":{"name":"A","url":"u"},'
        '"metadata":[{"citation_number":1,"url":"u"}],"document":["<p>x</p>"]}}}\n\n'
        "data: [DONE]"
    ).encode()
    cits = [f for f in await collect(raw, 64) if f["type"] == "citation"]
    assert cits[0]["found_by"] == []


async def test_duplicate_citation_numbers_are_deduped():
    frame = (
        'data: {"event":{"type":"citation","data":{"source":{"name":"A","url":"u"},'
        '"metadata":[{"citation_number":"3","url":"u"}],"document":["<p>x</p>"]}}}\n\n'
    )
    raw = (frame * 3 + "data: [DONE]").encode()
    cits = [f for f in await collect(raw, 64) if f["type"] == "citation"]
    assert len(cits) == 1 and cits[0]["n"] == 3


# --- вспомогательное -------------------------------------------------------


def test_think_stripper_handles_split_tags():
    s = _ThinkStripper()
    parts = ["ответ ", "<thi", "nk>", "мысли", "</think>", " дальше"]
    assert "".join(s.feed(p) for p in parts) + s.flush() == "ответ  дальше"


def test_think_stripper_passes_plain_text():
    s = _ThinkStripper()
    assert s.feed("обычный текст") + s.flush() == "обычный текст"


def test_html_to_text_drops_style_and_keeps_marks():
    # <meta> — void-тег: если считать его «открытым» блоком пропуска, съедается весь документ
    html = (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        "<style>body{background:#fff}</style></head>"
        "<body><p>[37:07] [Кто-то] текст с <mark>подсветкой</mark></p></body></html>"
    )
    text = html_to_text(html)
    assert "background" not in text
    assert "подсветкой" in text and "<" not in text
    assert text.startswith("[37:07]")


def test_extract_collects_marked_words():
    """`<mark>` — это движок говорит, чем чанк совпал с запросами агента."""
    html = (
        "<body><p>[37:07] [Кто-то] Вот DGX с <mark>H200</mark>, восемь "
        "<mark>видеокарт</mark>, на них учат <mark>модели</mark> и ещё раз "
        "<mark>модели</mark>. И <mark>я</mark> тоже.</p></body>"
    )
    chunk = extract(html)
    # порядок появления сохранён, повторы схлопнуты, текст не пострадал
    assert chunk.keywords == ("h200", "видеокарт", "модели")  # «я» короче порога
    assert "видеокарт" in chunk.text and "<mark>" not in chunk.text


def test_extract_without_marks_gives_no_keywords():
    assert extract("<body><p>обычный чанк</p></body>").keywords == ()


def test_html_to_text_survives_unclosed_head():
    text = html_to_text("<html><head><meta charset='utf-8'><body><p>текст</p></body></html>")
    assert "текст" in text
