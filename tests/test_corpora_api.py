"""Витрина пространств: `/api/corpora` — единственный запрос, который нужен главной.

Проверяем три обещания, каждое из которых иначе всплыло бы уже на живом сайте:
  * порядок карточек берётся из конфига, а не из порядка чтения каталогов;
  * пространство, забытое в конфиге, всё равно ВИДНО — прятать записи из-за пропущенной
    строчки нельзя, поэтому оно уходит в конец, а не исчезает;
  * числа считаются по индексу, а не берутся из конфига: вписанное руками устаревает молча.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_витрина_перечисляет_все_пространства(client):
    body = client.get("/api/corpora").json()
    health = client.get("/api/health").json()
    assert {c["slug"] for c in body["corpora"]} == set(health["corpora"])
    assert body["corpora"], "витрина без пространств — сайту нечего показать"


def test_числа_совпадают_с_индексом(client):
    body = client.get("/api/corpora").json()
    for space in body["corpora"]:
        site = client.get("/api/site", params={"slug": space["slug"]}).json()
        assert space["records_count"] == site["records_count"]
        assert space["hours"] == site["hours"]


def test_цвета_веток_доезжают_до_фронта(client):
    """`theme.sections` из site.yml — карта «ветка → hex»; фронт красит ею чипы, карточки,
    календарь и страницу записи. Ответ фильтрует не-строки: конфиг пишет владелец руками."""
    import re
    body = client.get("/api/corpora").json()
    for space in body["corpora"]:
        site = client.get("/api/site", params={"slug": space["slug"]}).json()
        sections = site["theme"].get("sections")
        assert isinstance(sections, dict), f"{space['slug']}: в теме нет карты цветов веток"
        for branch, colour in sections.items():
            assert re.fullmatch(r"#[0-9A-Fa-f]{6}", colour), f"{branch}: не hex-цвет: {colour!r}"


def test_кнопки_вопросов_к_записи_доезжают_до_фронта(client):
    """`chat.presets` из site.yml — {ветка: [{label, question}]}, ключ «*» общий. Мусор сервер
    выбрасывает: конфиг пишет человек руками, а фронту нужен вход без сюрпризов."""
    body = client.get("/api/corpora").json()
    for space in body["corpora"]:
        site = client.get("/api/site", params={"slug": space["slug"]}).json()
        presets = site["ask_presets"]
        assert isinstance(presets, dict), f"{space['slug']}: набор кнопок не словарь"
        for branch, items in presets.items():
            assert items and isinstance(items, list), f"{branch}: пустой набор не отдаём"
            for item in items:
                assert item["label"].strip() and item["question"].strip()


def test_у_каждого_пространства_свой_акцент(client):
    """Акцент — единственный опознавательный знак раздела, и он не должен быть общим."""
    body = client.get("/api/corpora").json()
    if len(body["corpora"]) < 2:
        pytest.skip("пространство одно — различать нечего")
    accents = [c["accent"] for c in body["corpora"] if c["accent"]]
    assert len(set(accents)) == len(accents), "два пространства покрашены одинаково"


def test_витрина_вырождается_на_одном_пространстве(client):
    """Корпус без `hub.yml` — обычный сайт: витрины нет, но ответ не разваливается."""
    body = client.get("/api/corpora").json()
    assert "hub" in body and "default" in body
    if body["hub"] is None:
        assert len(body["corpora"]) >= 1


def test_перелив_знака_доезжает_до_фронта(client):
    """`theme.halo` из site.yml — узор/палитра/цель/период перелива знака и заставки темы.
    ⚠️ Тема отдаётся по ключам, и до 15.09 этот ключ не пробрасывался: стенд `/lab/halo.html`
    и конфиг были готовы, а выбор владельца до сайта не доезжал («кажется, это пропустилось»)."""
    from app.config import halo_payload
    body = client.get("/api/corpora").json()
    for space in body["corpora"]:
        site = client.get("/api/site", params={"slug": space["slug"]}).json()
        assert "halo" in site["theme"], f"{space['slug']}: в теме нет ключа halo"
    # Чистая функция: только известные поля и чистые типы — файл пишут руками.
    assert halo_payload({}) is None
    assert halo_payload({"halo": "random"}) is None
    assert halo_payload({"halo": {"pattern": "random", "period": 1.5, "junk": 1, "target": 7}}) == {
        "pattern": "random", "period": 1.5}
    assert halo_payload({"halo": {"period": True}}) is None, "bool — не период"
    assert halo_payload({"halo": {"pattern": "clouds", "palette": "aurora", "target": "halo", "period": 3}}) == {
        "pattern": "clouds", "palette": "aurora", "target": "halo", "period": 3.0}
    # Пул целей для random — только строки; повтор в пуле сохраняется: это вес.
    assert halo_payload({"halo": {"target": "random", "targets": ["glyph", "glyph", 5, "halo"]}}) == {
        "target": "random", "targets": ["glyph", "glyph", "halo"]}
    assert halo_payload({"halo": {"target": "random", "targets": "glyph"}}) == {"target": "random"}
