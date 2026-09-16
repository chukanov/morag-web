"""Правка реплики со стороны сайта: что записывается в словарь и кто вообще имеет право писать.

⚠️⚠️ Про доступ здесь два теста, и они не дублируют друг друга. Авторизации нет, правка пишет в
корпус, и рубежа два: выключенный флаг и петлевой адрес. Снять их случайно легко, а заметить
пропажу нечем — поэтому на каждый свой тест.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.content.edits import Edits, Refused, Stale  # noqa: E402
from app.main import app  # noqa: E402
from test_turn_edits import build, make_corpus  # noqa: E402

RECORD = "2026-03-12-grafana"


def prepared(tmp_path: Path, fixes: dict | None = None) -> tuple[Path, Path]:
    """Собранный корпус из одной записи: правки идут по УЖЕ собранной записи, как на сайте."""
    root = make_corpus(tmp_path, [], fixes=fixes)
    return root, build(root)


def stored(root: Path) -> list[dict]:
    return json.loads((root / "turn_fixes.json").read_text(encoding="utf-8")) \
        .get("records", {}).get(RECORD, [])


def test_edit_lands_in_the_dictionary_and_in_the_record(tmp_path):
    root, record = prepared(tmp_path)
    out = Edits(root).save(record, [{"turn": 0, "was": "Мы ставим Grafanna в прод.",
                                     "now": "Мы ставим Grafanna в продакшн."}])
    assert len(out["saved"]) == 1
    assert stored(root)[0]["now"].endswith("в продакшн.")


def test_untouched_words_keep_their_original_spelling(tmp_path):
    """⚠️ Главное свойство: правка НЕ вмораживает в сырец действующие правила словаря.

    Человек видит текст после гарблов, а правка ложится в сырец. Перепиши мы реплику целиком —
    поправленное правилом написание застыло бы в сырце навсегда, и снятие правила эту реплику
    уже не чинило бы. Поэтому переносим только изменённые куски.
    """
    root, record = prepared(tmp_path, fixes={"global": [{"was": "Grafanna", "now": "Grafana"}],
                                             "records": {}})
    shown = json.loads((record / "record.words.json").read_text(encoding="utf-8"))
    seen = " ".join(w[0] for w in shown["turns"][0]["words"])
    assert seen == "Мы ставим Grafana в прод.", "правило словаря не доехало до читалки"

    Edits(root).save(record, [{"turn": 0, "was": seen, "now": "Мы ставим Grafana в продакшн."}])
    edit = stored(root)[0]
    assert "Grafanna" in edit["was"] and "Grafanna" in edit["now"], (
        "правка переписала слово, которого человек не касался — правило вморожено в сырец")
    assert edit["now"].endswith("в продакшн.")


def test_stale_paragraph_is_refused(tmp_path):
    """Текст на сервере уже другой — правим не то, что человек видел. Это 409, а не тихая правка."""
    root, record = prepared(tmp_path)
    with pytest.raises(Stale):
        Edits(root).save(record, [{"turn": 0, "was": "что-то совсем другое", "now": "неважно"}])
    assert not (root / "turn_fixes.json").read_text(encoding="utf-8").count("неважно")


def test_empty_paragraph_is_refused(tmp_path):
    root, record = prepared(tmp_path)
    with pytest.raises(Refused):
        Edits(root).save(record, [{"turn": 0, "was": "Мы ставим Grafanna в прод.", "now": "   "}])


def test_record_without_raw_sidecar_is_refused_with_a_reason(tmp_path):
    """Сырец `record.json` вне git: запись, собранная на другой машине, приезжает без него.
    Ловилось 14.09: правка отвечала 500 без слов, а человек видел «не сохраняется»."""
    root, record = prepared(tmp_path)
    (record / "record.json").unlink()
    with pytest.raises(Refused, match="сырого сайдкара"):
        Edits(root).save(record, [{"turn": 0, "was": "Мы ставим Grafanna в прод.",
                                   "now": "Мы ставим Grafana в прод."}])


def test_nothing_changed_writes_nothing(tmp_path):
    """Открыл поле, закрыл, ничего не тронул — словарь трогать не за что."""
    root, record = prepared(tmp_path)
    same = "Мы ставим Grafanna в прод."
    assert Edits(root).save(record, [{"turn": 0, "was": same, "now": same}])["saved"] == []
    assert stored(root) == []


def test_single_word_replacement_is_offered_for_the_whole_corpus(tmp_path):
    """«Починить везде» предлагается только там, где правка выразима правилом.

    Удаление и вставку словарю не выразить: у него нет для этого формы.
    """
    root, record = prepared(tmp_path)
    store = Edits(root)
    out = store.save(record, [{"turn": 0, "was": "Мы ставим Grafanna в прод.",
                               "now": "Мы ставим Grafana в прод."}])
    assert out["suggest"] == [{"was": "Grafanna", "now": "Grafana"}]

    dropped = store.save(record, [{"turn": 0, "was": "Мы ставим Grafana в прод.",
                                   "now": "Мы ставим в прод."}])
    assert dropped["suggest"] == [], "удаление слова правилом не выразить"


def test_promotion_replaces_the_local_edit(tmp_path):
    """⚠️ Правило и поместная правка не должны чинить одно и то же.

    Оставь мы оба — снятое потом правило не вернуло бы реплике исходный текст: её держала бы
    ещё и поместная правка, и «обратимо» перестало бы быть правдой.
    """
    root, record = prepared(tmp_path)
    store = Edits(root)
    store.save(record, [{"turn": 0, "was": "Мы ставим Grafanna в прод.",
                         "now": "Мы ставим Grafana в прод."}])
    assert len(stored(root)) == 1
    out = store.promote(RECORD, "Grafanna", "Grafana")
    assert out["dropped"] == 1 and stored(root) == []
    rules = json.loads((root / "text_fixes.json").read_text(encoding="utf-8"))["global"]
    assert {"was": "Grafanna", "now": "Grafana"}.items() <= rules[0].items()


def test_promotion_keeps_an_edit_that_is_more_than_the_rule(tmp_path):
    """Правка была шире одной замены — правило её не заменяет, и снимать её нельзя."""
    root, record = prepared(tmp_path)
    store = Edits(root)
    store.save(record, [{"turn": 0, "was": "Мы ставим Grafanna в прод.",
                         "now": "Мы ставим Grafana в продакшн."}])
    assert store.promote(RECORD, "Grafanna", "Grafana")["dropped"] == 0
    assert len(stored(root)) == 1


@pytest.mark.parametrize("was,now", [("Grafanna", "Grafana Metrics"), ("Grafanna", "Grafanna"),
                                     ("", "Grafana")])
def test_rule_must_be_one_word_to_another(tmp_path, was, now):
    """Пробел в замене создаёт «слово» с пробелом в пословных временах, и разрез на абзацы
    молча отключается. Проверка на СЕРВЕРЕ, а не в поле ввода."""
    root, _ = prepared(tmp_path)
    with pytest.raises(Refused):
        Edits(root).promote(RECORD, was, now)


# --- рубежи доступа --------------------------------------------------------

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_правка_текста_выключена_по_умолчанию(client):
    """⚠️ Первый рубеж: в примере конфига (он в git) правка выключена."""
    app.state.cfg.editing.enabled = False
    r = client.post("/api/records/чего-нибудь/edits", json={"edits": []})
    assert r.status_code == 403 and "выключена" in r.json()["detail"]


def test_правка_текста_только_с_этой_машины(client):
    """⚠️ Второй рубеж: флаг, случайно уехавший на сервер, сам по себе дыры не даёт."""
    app.state.cfg.editing.enabled = True
    app.state.cfg.editing.local_only = True
    try:
        r = client.post("/api/fixes", json={"was": "а", "now": "б"})
        assert r.status_code == 403 and "машины" in r.json()["detail"]
    finally:
        app.state.cfg.editing.enabled = False


def test_поиск_написания_читается_и_при_выключенной_правке(client):
    """Посмотреть, где ещё встречается слово, — это чтение корпуса, который и так открыт."""
    app.state.cfg.editing.enabled = False
    r = client.get("/api/tokens/расшифровка")
    assert r.status_code == 200
    assert set(r.json()) >= {"word", "records", "total", "variants"}
