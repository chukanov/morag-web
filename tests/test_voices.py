"""Правка имён голосов: обратимость, охват и три рубежа доступа.

Голос — это идентификатор ИЗ РЕЕСТРА, общий на весь корпус, а не метка внутри записи. Отсюда всё
остальное: имя, названное один раз, относится ко всем записям, где голос звучит, и снятое имя
возвращает запись к исходной метке, потому что сайдкар остался сырым.

⚠️⚠️ Про доступ здесь два теста, и они не дублируют друг друга. Авторизации нет, правка пишет в
корпус, и рубежа два: выключенный флаг и петлевой адрес. Тест на каждый — потому что снять их
случайно легко, а заметить пропажу нечем.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.content.voices import Voices  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def family(tmp_path: Path) -> Path:
    """Каталог семьи пространств: словарь имён и снимок голосов."""
    (tmp_path / "names.json").write_text(
        json.dumps({"speakers": {"Speaker_7": "Кузнецова"}, "records": {}, "_why": {}},
                   ensure_ascii=False), encoding="utf-8")
    (tmp_path / "voices.json").write_text(json.dumps({
        "vocabulary": ["Кузнецова", "Ковалёв"],
        "record_space": {"зап-1": "первое", "зап-2": "второе"},
        "voices": [
            {"id": "Speaker_7", "sec": 900.0, "records": ["зап-1", "зап-2"],
             "spaces": ["первое", "второе"], "name": "устарело", "solo": False,
             "intros": [], "candidates": [{"name": "Ковалёв", "votes": 2, "from": "tag"}]},
            {"id": "Speaker_9", "sec": 30.0, "records": ["зап-1"], "spaces": ["первое"],
             "name": "", "solo": True, "intros": [], "candidates": []},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def names_of(family: Path) -> dict:
    return json.loads((family / "names.json").read_text(encoding="utf-8"))


def test_имя_относится_ко_всем_записям_голоса(family):
    """Ради этого всё и делается: у самого частого голоса корпуса 47 записей."""
    touched = Voices(family).rename("Speaker_7", "Ковалёв", "проба")
    assert touched == ["зап-1", "зап-2"], "правка обязана накрыть все записи голоса"
    assert names_of(family)["speakers"]["Speaker_7"] == "Ковалёв"


def test_снятое_имя_возвращает_запись_к_метке(family):
    """Обратимость — операция, а не обещание: словарь снова молчит, текст выводится из словаря."""
    store = Voices(family)
    store.rename("Speaker_7", "Ковалёв", "проба")
    store.rename("Speaker_7", "", "")
    assert "Speaker_7" not in names_of(family)["speakers"]
    assert "Speaker_7" not in names_of(family)["_why"]


def test_адресная_правка_не_трогает_остальные_записи(family):
    """Реестр иногда склеивает двух людей. Тогда имя нужно ровно в одной записи."""
    touched = Voices(family).rename("Speaker_7", "Ковалёв", "склейка", record="зап-2")
    assert touched == ["зап-2"]
    data = names_of(family)
    assert data["records"]["зап-2"]["Speaker_7"] == "Ковалёв"
    assert data["speakers"]["Speaker_7"] == "Кузнецова", "общее имя трогать было нельзя"


def test_снятая_адресная_правка_не_оставляет_мусора(family):
    """Пустая секция записи в словаре мешает: его читают глазами."""
    store = Voices(family)
    store.rename("Speaker_7", "Ковалёв", "", record="зап-2")
    store.rename("Speaker_7", "", "", record="зап-2")
    assert names_of(family)["records"] == {}


def test_чужой_идентификатор_отвергается(family):
    """Ключ словаря — идентификатор реестра. Всё прочее там означало бы подписывание не тех."""
    with pytest.raises(ValueError):
        Voices(family).rename("Ковалёв", "кто-то", "")


def test_имя_берётся_из_словаря_а_не_из_снимка(family):
    """⚠️ Снимок собирают руками, словарь меняется на каждой правке.

    Показывать имя из снимка значило бы врать сразу после первой же правки — до следующего скана.
    """
    store = Voices(family)
    store.rename("Speaker_7", "Ковалёв", "проба")
    shown = {v["id"]: v["name"] for v in store.public()["voices"]}
    assert shown["Speaker_7"] == "Ковалёв", "в снимке лежит «устарело» — верить надо словарю"
    assert shown["Speaker_9"] == ""


def test_один_голос_ищется_по_идентификатору_реестра(family):
    """Карточка в читалке просит ОДИН голос, а не весь снимок: 281 голос ради одной метки
    гонять в браузер незачем."""
    one = Voices(family).one("Speaker_7", record="зап-2")
    assert one["known"] is True
    assert one["name"] == "Кузнецова", "имя обязано браться из словаря, а не из снимка"
    assert one["records"] == ["зап-1", "зап-2"]
    assert one["vocabulary"], "без словаря имён подсказывать нечего"


def test_кандидаты_своей_записи_идут_первыми(family):
    """Мета называет докладчика ИМЕННО этой встречи — это самая близкая догадка из возможных."""
    data = json.loads((family / "voices.json").read_text(encoding="utf-8"))
    data["voices"][0]["candidates"] = [
        {"name": "Чужой", "votes": 9, "from": "зап-9"},
        {"name": "Свой", "votes": 1, "from": "зап-2"},
    ]
    (family / "voices.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    имена = [c["name"] for c in Voices(family).one("Speaker_7", record="зап-2")["candidates"]]
    assert имена[0] == "Свой", "кандидат из своей записи обязан быть первым"


def test_незнакомый_голос_не_ошибка(family):
    """Снимок собирают руками и он стареет; читалка обязана открыть карточку и для нового id."""
    one = Voices(family).one("Speaker_999")
    assert one["known"] is False and one["name"] == ""


def test_снимка_нет_это_не_ошибка(tmp_path):
    """Снимок производный и собирается инструментом; сайт обязан открыться и без него."""
    data = Voices(tmp_path).public()
    assert data["ready"] is False and data["voices"] == []


# --- рубежи доступа --------------------------------------------------------

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_правка_выключена_по_умолчанию(client):
    """⚠️ Первый рубеж: в примере конфига (он в git) правка выключена."""
    app.state.cfg.editing.enabled = False
    r = client.post("/api/voices/Speaker_1", json={"name": "Кто-то"})
    assert r.status_code == 403 and "выключена" in r.json()["detail"]


def test_правка_только_с_этой_машины(client):
    """⚠️ Второй рубеж: флаг, случайно уехавший на сервер, сам по себе дыры не даёт.

    У TestClient адрес не петлевой — именно поэтому он сюда и годится.
    """
    app.state.cfg.editing.enabled = True
    app.state.cfg.editing.local_only = True
    try:
        r = client.post("/api/voices/Speaker_1", json={"name": "Кто-то"})
        assert r.status_code == 403 and "машины" in r.json()["detail"]
    finally:
        app.state.cfg.editing.enabled = False


def test_очередь_не_съедается_маршрутом_одного_голоса(client):
    """⚠️ `/api/voices/queue` объявлен РАНЬШЕ `/api/voices/{voice}`: маршруты разбираются по
    порядку, и иначе «queue» уехал бы в параметр — верстак перестал бы видеть очередь."""
    body = client.get("/api/voices/queue").json()
    assert set(body) >= {"pending", "current", "done"}, body


def test_один_голос_читается_и_при_выключенной_правке(client):
    app.state.cfg.editing.enabled = False
    body = client.get("/api/voices/Speaker_1?record=что-нибудь").json()
    assert body["id"] == "Speaker_1"
    assert body["editing"] is False, "читалка по этому флагу решает, показывать ли поле ввода"


def test_снимок_читается_даже_когда_правка_выключена(client):
    """Смотреть, кто сколько говорил, — не правка."""
    app.state.cfg.editing.enabled = False
    body = client.get("/api/voices").json()
    assert body["editing"] is False and "voices" in body


def test_the_tool_can_name_a_file_that_lies_outside_the_platform(tmp_path):
    """⚠️ Снимок собирается в КОРПУС, а корпус лежит отдельно от платформы — так устроено
    разделение. Печать имени записанного файла делалась через `relative_to` каталога платформы
    и роняла инструмент на последней строке: снимок собран, а человек видит трассировку и не
    знает, собран он или нет."""
    sys.path.insert(0, str(REPO / "tools"))
    import voices as tool

    outside = tmp_path / "corpora" / "mesto" / "voices.json"
    assert tool.short(outside).endswith("voices.json"), "путь вне платформы печатается, а не падает"
    assert tool.short(REPO / "tools" / "voices.py") == "tools/voices.py" or \
           tool.short(REPO / "tools" / "voices.py").endswith("tools/voices.py")
