"""Реестр голосов на сервере (`app/content/registry.py`): кто это говорит.

Цена ошибки здесь необратима — номер голоса уезжает в текст записи, в шапку, в payload каждого
чанка и в словарь имён. Поэтому закрепляем не «работает», а ровно те решения, которые уже
приняты по корпусу и замерены (порог 0.7, пятнадцать секунд эфира) и которые движок принимает у
себя: иначе одна и та же запись получит разные номера на ноутбуке и на сервере.
"""

from __future__ import annotations

import json
import math
import random
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.content import registry  # noqa: E402


def vec(seed: float, dim: int = registry.DIM) -> list[float]:
    """Единичный вектор, детерминированный по сиду.

    ⚠️ Случайные направления, а не синус от сида: у синуса соседние сиды дают ПОЧТИ ОДИН И ТОТ ЖЕ
    вектор (замерено: cos(vec(1), vec(20)) = 0.989), и «незнакомый голос» в тесте оказывался
    знакомым. В 192 измерениях два случайных направления почти ортогональны — это и есть «разные
    люди».
    """
    rng = random.Random(seed)
    raw = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def mix(a: list[float], b: list[float], k: float) -> list[float]:
    """Вектор между двумя: так строится «тот же человек, другой микрофон»."""
    raw = [x * (1 - k) + y * k for x, y in zip(a, b)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def voices(**kw) -> dict:
    return {label: {"centroid": c, "air_sec": air} for label, (c, air) in kw.items()}


def test_empty_registry_hands_out_numbers_from_zero(tmp_path):
    path = tmp_path / "reg.json"
    mapping, report = registry.identify(path, voices(Speaker_0=(vec(1), 600), Speaker_1=(vec(9), 300)),
                                        episode="rec-1")
    assert mapping == {"Speaker_0": "Speaker_0", "Speaker_1": "Speaker_1"}
    assert [r["how"] for r in report] == ["new", "new"]
    reg = json.loads(path.read_text(encoding="utf-8"))
    assert reg["next_id"] == 2 and len(reg["speakers"]) == 2
    assert len(reg["speakers"]["0"]["centroids"][0]) == registry.DIM


def test_numbers_are_handed_out_by_air_not_by_label(tmp_path):
    """⚠️ Порядок — по эфиру убыванием, как в движке: иначе та же запись, разобранная дважды,
    даст разные номера, и сравнить корпус с самим собой станет невозможно."""
    path = tmp_path / "reg.json"
    mapping, _ = registry.identify(path, voices(Speaker_7=(vec(2), 120), Speaker_3=(vec(8), 900)))
    assert mapping["Speaker_3"] == "Speaker_0", "дольше говорил — получил номер первым"
    assert mapping["Speaker_7"] == "Speaker_1"


def test_the_same_voice_is_recognised_and_enriches_the_registry(tmp_path):
    path = tmp_path / "reg.json"
    registry.identify(path, voices(Speaker_0=(vec(1), 600)), episode="rec-1")
    same = mix(vec(1), vec(2), 0.15)        # тот же человек, другой микрофон
    mapping, report = registry.identify(path, voices(Speaker_4=(same, 500)), episode="rec-2")
    assert mapping == {"Speaker_4": "Speaker_0"}
    assert report[0]["how"] == "matched" and report[0]["cos"] >= 0.7
    reg = json.loads(path.read_text(encoding="utf-8"))
    assert len(reg["speakers"]) == 1, "знакомый голос не заводит второй номер"
    assert len(reg["speakers"]["0"]["centroids"]) == 2, "голос обогатился вторым центроидом"
    assert len(reg["speakers"]["0"]["provenance"]) == 2


def test_centroid_cap_holds_but_provenance_keeps_growing(tmp_path):
    path = tmp_path / "reg.json"
    base = vec(1)
    for n in range(5):
        registry.identify(path, voices(Speaker_0=(mix(base, vec(2), 0.02 * n), 600)),
                          episode=f"rec-{n}", max_centroids=3)
    reg = json.loads(path.read_text(encoding="utf-8"))
    assert len(reg["speakers"]) == 1
    assert len(reg["speakers"]["0"]["centroids"]) == 3, "потолок центроидов держится"
    assert len(reg["speakers"]["0"]["provenance"]) == 5, "а где звучал — помним всё"


def test_a_short_stranger_gets_his_own_number_and_a_mark(tmp_path):
    """⚠️⚠️ Решение владельца 24.09: короткого незнакомца БОЛЬШЕ НЕ ПРИКЛЕИВАЕМ к ближайшему.
    Приписать человеку чужие двенадцать секунд — ошибка неисправимая и невидимая; лишний
    безымянный номер — строчка в словаре. Помечаем его `short`, чтобы верстак не зарастал."""
    path = tmp_path / "reg.json"
    registry.identify(path, voices(Speaker_0=(vec(1), 900)), episode="rec-1")
    mapping, report = registry.identify(path, voices(Speaker_2=(vec(20), 5)), episode="rec-2")
    assert mapping == {"Speaker_2": "Speaker_1"} and report[0]["how"] == "new"
    reg = json.loads(path.read_text(encoding="utf-8"))
    assert reg["speakers"]["1"]["short"] is True
    assert "short" not in reg["speakers"]["0"], "долгий голос коротким не помечается"


def test_a_doubtful_match_becomes_a_new_voice_with_a_hint(tmp_path):
    """Полоса сомнения (0.65…0.75): заводим новый голос, но говорим, на кого он похож. Так
    дешёвая ошибка (лишний номер) отделена от дорогой (склейка), а человеку есть что
    подтвердить — замерено на корпусе: 0.617 это разные люди, 0.807 — один."""
    path = tmp_path / "reg.json"
    registry.identify(path, voices(Speaker_0=(vec(1), 900)), episode="rec-1")
    doubtful = mix(vec(1), vec(2), 0.52)      # похож, но не уверенно
    cos = registry.cosine(vec(1), doubtful)
    assert 0.65 <= cos < 0.75, f"проба должна попасть в полосу сомнения, а вышло {cos:.3f}"
    mapping, report = registry.identify(path, voices(Speaker_9=(doubtful, 400)), episode="rec-2")
    assert mapping == {"Speaker_9": "Speaker_1"} and report[0]["how"] == "new"
    assert report[0]["similar_to"] == "Speaker_0"
    reg = json.loads(path.read_text(encoding="utf-8"))
    assert reg["speakers"]["1"]["similar_to"]["voice"] == "Speaker_0"
    assert reg["speakers"]["0"]["centroids"] == [vec(1)], "сомнительный центроид в чужой голос не дописан"


def test_a_long_stranger_becomes_a_new_voice(tmp_path):
    path = tmp_path / "reg.json"
    registry.identify(path, voices(Speaker_0=(vec(1), 900)), episode="rec-1")
    mapping, report = registry.identify(path, voices(Speaker_2=(vec(20), 600)), episode="rec-2")
    assert mapping == {"Speaker_2": "Speaker_1"} and report[0]["how"] == "new"
    assert "similar_to" not in report[0], "ни на кого не похож — и подсказки нет"
    assert "short" not in json.loads(path.read_text(encoding="utf-8"))["speakers"]["1"]


def test_a_preview_changes_nothing_at_all(tmp_path):
    """⚠️ Предпросмотр не смеет занимать номера. Ловилось на живой перенумерации 24.09: «покажи
    карту» зарегистрировало четыре голоса записи, которую ещё не решили трогать, и следующий
    настоящий прогон выдал бы уже другие номера."""
    path = tmp_path / "reg.json"
    registry.identify(path, voices(Speaker_0=(vec(1), 900)), episode="rec-1")
    before = path.read_text(encoding="utf-8")

    mapping, report = registry.identify(path, voices(Speaker_7=(vec(1), 800), Speaker_8=(vec(20), 500)),
                                        episode="rec-2", dry=True)
    assert mapping == {"Speaker_7": "Speaker_0", "Speaker_8": "Speaker_1"}
    assert [r["how"] for r in report] == ["matched", "new"], "решения те же, что были бы всерьёз"
    assert path.read_text(encoding="utf-8") == before, "файл реестра не тронут ни байтом"

    # и номер не съеден: настоящий прогон выдаёт ровно то, что обещал предпросмотр
    real, _ = registry.identify(path, voices(Speaker_7=(vec(1), 800), Speaker_8=(vec(20), 500)),
                                episode="rec-2")
    assert real == mapping


def test_a_broken_registry_refuses_instead_of_starting_over(tmp_path):
    """⚠️⚠️ Главное свойство. В движке битый JSON молча превращается в пустой реестр и
    перезаписывается — здесь это означало бы новую нумерацию для всего корпуса и чужие имена."""
    path = tmp_path / "reg.json"
    path.write_text("{это не json", encoding="utf-8")
    with pytest.raises(registry.RegistryError, match="не читается"):
        registry.identify(path, voices(Speaker_0=(vec(1), 600)))
    assert path.read_text(encoding="utf-8") == "{это не json", "файл не тронут"


def test_a_foreign_vector_is_refused(tmp_path):
    path = tmp_path / "reg.json"
    with pytest.raises(registry.RegistryError, match="192"):
        registry.identify(path, {"Speaker_0": {"centroid": [0.1, 0.2], "air_sec": 600}})


def test_saving_keeps_a_copy_and_replaces_atomically(tmp_path):
    path = tmp_path / "reg.json"
    registry.identify(path, voices(Speaker_0=(vec(1), 600)))
    registry.identify(path, voices(Speaker_0=(vec(30), 600)))
    assert (tmp_path / "reg.json.bak").is_file(), "прошлая версия рядом"
    assert not (tmp_path / "reg.json.tmp").exists(), "временный файл не остаётся"


def test_parallel_callers_do_not_share_a_number(tmp_path):
    """Два приёма разом (у сайта очередь на один воркер, но замок обязан держать и без неё)."""
    path = tmp_path / "reg.json"
    got: list[dict] = []

    def work(seed: int) -> None:
        mapping, _ = registry.identify(path, voices(Speaker_0=(vec(seed * 7 + 3), 600)),
                                       episode=f"rec-{seed}")
        got.append(mapping)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    numbers = {m["Speaker_0"] for m in got}
    assert len(numbers) == 4, f"каждый получил свой номер: {numbers}"
    assert json.loads(path.read_text(encoding="utf-8"))["next_id"] == 4


def test_stats_tells_what_is_inside(tmp_path):
    path = tmp_path / "reg.json"
    assert registry.stats(path)["exists"] is False
    registry.identify(path, voices(Speaker_0=(vec(1), 600)))
    reg = json.loads(path.read_text(encoding="utf-8"))
    reg["speakers"]["0"]["name"] = "Мария Кузнецова"
    path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    out = registry.stats(path)
    assert out == {"exists": True, "voices": 1, "next_id": 1, "named": 1, "at": out["at"]}


# --- ручка сайта ------------------------------------------------------------------------

def test_the_route_answers_over_http(tmp_path, monkeypatch):
    """⚠️ Ручку надо звать ПО HTTP, а не проверять модуль импортом: 24.09 в неё уехал
    необъявленный `registry` — модуль импортировался, тесты были зелёными, а живой сервер отвечал
    500 на первом же запросе. Ошибку такого рода ловит только настоящий вызов."""
    import shutil

    from fastapi.testclient import TestClient

    demo = tmp_path / "demo"
    shutil.copytree(REPO / "corpora" / "demo", demo)
    monkeypatch.setenv("MORAG_WEB_CORPUS", str(demo))
    monkeypatch.setenv("MORAG_WEB_CONFIG", str(REPO / "app" / "config.example.yml"))
    from app.main import app

    with TestClient(app) as c:
        app.state.cfg.ingest.enabled = True
        app.state.cfg.editing.local_only = False
        assert c.get("/api/voices/registry").status_code == 404, "реестр не настроен — так и говорим"

        path = tmp_path / "reg.json"
        app.state.cfg.voices.registry = str(path)
        body = {"episode": "rec-1", "voices": {"Speaker_0": {"centroid": vec(3), "air_sec": 700}}}
        answer = c.post("/api/voices/identify", json=body)
        assert answer.status_code == 200, answer.text
        assert answer.json()["map"] == {"Speaker_0": "Speaker_0"}
        assert answer.json()["report"][0]["how"] == "new"

        stats = c.get("/api/voices/registry").json()
        assert stats == {"exists": True, "voices": 1, "next_id": 1, "named": 0, "at": stats["at"]}

        preview = c.post("/api/voices/identify", json={**body, "dry": True,
                                                       "voices": {"Speaker_9": {"centroid": vec(40), "air_sec": 400}}})
        assert preview.json()["map"] == {"Speaker_9": "Speaker_1"} and preview.json()["dry"] is True
        assert c.get("/api/voices/registry").json()["next_id"] == 1, "предпросмотр номер не занял"

        assert c.post("/api/voices/identify", json={"voices": {}}).status_code == 400
        path.write_text("{битый", encoding="utf-8")
        assert c.post("/api/voices/identify", json=body).status_code == 503, "битый реестр — 503, не 500"
