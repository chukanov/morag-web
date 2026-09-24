"""Отдача страниц: то, что видит браузер и бот мессенджера.

Тесты на чистые функции (`test_meta.py`) не ловят ошибок СБОРКИ приложения —
а именно они самые дорогие: опечатка в обработчике 404 роняет пятисоткой каждый
адрес сайта разом, и снаружи это выглядит как «всё пропало». Так и случилось при
удалении лишнего модуля: осталась ссылка на него, все пути стали отдавать 500.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.main import app  # noqa: E402

# Запись — из ДЕМО-корпуса (`tools/make_demo.py`), и это принципиально: тест про сборку
# приложения не должен зависеть от того, лежит ли рядом корпус компании. Фикстурой это
# подменить всё равно нельзя — проверяем ровно то, что отдаётся браузеру.
SLUG = "demo"
RECORD = "2024-01-15-kafka-osnovy"
# Секунда внутри записи: тайм-код обязан доехать до мета-тега превью.
AT = 74
MMSS = "1:14"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_кадр_экрана_отдаётся_только_из_slides_и_только_jpg(client):
    """Обложка карточки — `slides/sNNN.jpg` из каталога записи. Белый список: путь вне `slides/`,
    подкаталог или не-jpg — 404, даже если файл существует (`record.md`). Кэш — с перепроверкой."""
    import shutil
    base = f"/api/records/{RECORD}/frame"
    for bad in ("record.md", "slides/x/s1.jpg", "slides/s1.png", "slides/s001.jpg"):
        assert client.get(f"{base}/{bad}?slug={SLUG}").status_code == 404, bad
    frames = REPO / "corpora" / "demo" / "records" / RECORD / "slides"
    frames.mkdir(exist_ok=True)
    try:
        (frames / "s001.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\0" * 16)
        r = client.get(f"{base}/slides/s001.jpg?slug={SLUG}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg")
        assert "must-revalidate" in r.headers.get("cache-control", "")
        again = client.get(f"{base}/slides/s001.jpg?slug={SLUG}", headers={"If-None-Match": r.headers["etag"]})
        assert again.status_code == 304
    finally:
        shutil.rmtree(frames, ignore_errors=True)


def test_кадры_записи_для_слайдшоу_без_людей_и_с_рамкой_обложки(client):
    """`/frames` отдаёт кадры слайдов по времени без кадров с людьми и без несуществующих файлов,
    плюс рамку обрезки обложки; без шкалы — пустой список, а не 404 (карточка без слайдшоу)."""
    import json
    import shutil
    rec = REPO / "corpora" / "demo" / "records" / RECORD
    assert client.get(f"/api/records/{RECORD}/frames?slug={SLUG}").json() == {"frames": [], "crop": None}
    frames = rec / "slides"
    frames.mkdir(exist_ok=True)
    try:
        for n in (1, 2):
            (frames / f"s{n:03d}.jpg").write_bytes(b"\xff\xd8\xff")
        (rec / "record.slides.json").write_text(json.dumps({"slides": [
            {"n": 1, "t0": 5.0, "frame": "slides/s001.jpg", "desc": {"title": "Титул"}},
            {"n": 2, "t0": 60.0, "frame": "slides/s002.jpg", "people": True, "desc": {"title": "Люди"}},
            {"n": 3, "t0": 90.0, "frame": "slides/s003.jpg", "desc": {"title": "Нет файла"}},
            {"n": 4, "t0": 120.0, "frame": "../record.md", "desc": {"title": "Чужой путь"}},
        ], "cover": {"frame": "slides/cover.jpg", "crop": {"box": [0, 0, 0.86, 0.94]}}}), encoding="utf-8")
        out = client.get(f"/api/records/{RECORD}/frames?slug={SLUG}").json()
        assert [f["frame"] for f in out["frames"]] == ["slides/s001.jpg"] and out["frames"][0]["title"] == "Титул"
        assert out["crop"] == [0, 0, 0.86, 0.94]
    finally:
        shutil.rmtree(frames, ignore_errors=True)
        (rec / "record.slides.json").unlink(missing_ok=True)


def test_главная_отдаётся(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<html" in response.text.lower()


def test_красивый_путь_отдаёт_приложение(client):
    """Маршрутизация на клиенте: сервер обязан отдать то же приложение."""
    response = client.get(f"/{SLUG}/rec/{RECORD}/{AT}")
    assert response.status_code == 200
    assert "/js/main.js" in response.text


def test_ссылка_на_момент_несёт_превью_с_тайм_кодом(client):
    """Бот мессенджера JS не выполняет — тайм-код обязан быть в мета-теге."""
    response = client.get(f"/{SLUG}/rec/{RECORD}/{AT}")
    assert 'property="og:title"' in response.text
    assert MMSS in response.text
    assert response.text.count("<title>") == 1


def test_несуществующая_запись_не_ломает_страницу(client):
    """Ссылку могли обрезать при пересылке — показываем сайт, а не ошибку."""
    response = client.get(f"/{SLUG}/rec/нет-такой/5")
    assert response.status_code == 200
    assert 'property="og:title"' in response.text


def test_чужой_слаг_отдаёт_корпус_по_умолчанию(client):
    """Корпусов может стать несколько; неизвестный слаг — не повод показывать пустоту."""
    assert client.get(f"/какой-то-другой/rec/{RECORD}").status_code == 200


def test_файлы_не_подменяются_страницей(client):
    """Иначе браузер, попросив картинку, получает HTML с кодом 200 и молча
    считает это ответом: `/favicon.ico` так оставлял вкладку без значка."""
    assert client.get("/нет-такого.ico").status_code == 404


def test_шире_даёт_соседние_реплики(client):
    """«Шире» на карточке-моменте: тот же чанк плюс разговор вокруг него."""
    narrow = client.get(f"/api/records/{RECORD}/words", params={"start": 70, "end": 80}).json()
    wide = client.get(f"/api/records/{RECORD}/words", params={"start": 70, "end": 80, "pad": 2}).json()
    if not narrow["aligned"]:
        pytest.skip("запись ещё не выровнена")
    assert len(wide["turns"]) > len(narrow["turns"])
    assert wide["turns"][0]["start"] < narrow["turns"][0]["start"]


def test_шире_ограничено_сверху(client):
    """Иначе «шире» становится способом выкачать запись по кусочкам."""
    huge = client.get(f"/api/records/{RECORD}/words", params={"start": 70, "end": 80, "pad": 999}).json()
    whole = client.get(f"/api/records/{RECORD}/words").json()
    if not whole["aligned"]:
        pytest.skip("запись ещё не выровнена")
    assert len(huge["turns"]) < len(whole["turns"])


def test_вход_в_чат_есть_на_любой_странице(client):
    """Ссылки на моменты ведут в читалку, а спрашивать — главное, ради чего сайт.

    Без кнопки в шапке пришедший по ссылке видел только расшифровку, и путь к
    чату начинался с догадки «нажми К записям».
    """
    # Адрес диалога (`/chat/<id>`) обязан отдавать приложение, а не 404: SPA-фолбэк.
    for path in (f"/{SLUG}/rec/{RECORD}/{AT}", f"/{SLUG}/records", f"/{SLUG}", f"/{SLUG}/chat/some-session-id"):
        assert 'id="go-ask"' in client.get(path).text, path


def test_календарь_без_файла_будущего_пуст_но_отвечает(client):
    """Будущее пространства — необязательный `calendar.json` в каталоге корпуса; у демо его
    нет, и это не ошибка: прошлое страница календаря берёт из самих записей."""
    body = client.get(f"/api/calendar?slug={SLUG}").json()
    assert body["corpus"] == SLUG
    assert body["upcoming"] == [] and body["ideas"] == []
    assert 'id="view-calendar"' in client.get(f"/{SLUG}/calendar").text


def test_роли_людей_доезжают_до_api(client):
    """Демо-запись про индексы — с открывшей встречу и участником: два списка и счётчик
    голосов в ответе."""
    records = client.get(f"/api/records?slug={SLUG}").json()["records"]
    rec = next(r for r in records if r["id"] == "2024-02-20-postgres-indeksy")
    assert rec["speakers"] == ["Пётр Ковалёв"]
    assert rec["participants"] == ["Мария Кузнецова", "Олег Соколов"]
    assert rec["voices"] == 3 and rec["kind"] == ["Технологии"] and rec["award"] is True
    assert rec["category"] == "Базы данных" and rec["topics"] == ["PostgreSQL", "индексы"] and rec["year"] == "2024"


def test_api_живо(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["records"][SLUG] > 0


def test_records_say_whether_they_reached_the_search(client, tmp_path, monkeypatch):
    """С 24.09 индексация плановая, а не на каждую загрузку: запись читается сразу, а ищется
    после ближайшего прогона. Список обязан это показывать — иначе «почему не находится»
    выглядит поломкой поиска. Нет отметки о прогоне — не обещаем ничего и поля не шлём."""
    import json as _json
    import time as _time

    from app.config import engine_for

    body = client.get("/api/records").json()
    assert all("indexed" not in r for r in body["records"]), "без отметки про индекс молчим"
    assert body["indexed_at"] == 0

    stamp = tmp_path / "indexed.json"
    stamp.write_text(_json.dumps({"at": _time.time() - 3600}), encoding="utf-8")
    engine_for(client.app.state.cfg, body["corpus"]).index_stamp = str(stamp)
    body = client.get("/api/records").json()
    assert body["indexed_at"] > 0
    assert all(r["indexed"] is True for r in body["records"]), "прогон был позже записей"

    stamp.write_text(_json.dumps({"at": 1}), encoding="utf-8")   # прогон был до всего
    body = client.get("/api/records").json()
    assert all(r["indexed"] is False for r in body["records"])
