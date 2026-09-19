"""Приём записи, транскрибированной на чужой машине (`app/content/ingest.py`, `app/api/ingest.py`).

Что здесь обязано быть закреплено, потому что цена ошибки — корпус:
  * номера голосов СДВИГАЮТСЯ в свой диапазон — иначе `names.json` подписал бы чужой `Speaker_3`
    чужим именем; сдвиг ровно один, и повторный приём после сбоя его не повторяет;
  * стейджинг вне `records/`, запись появляется там одним ходом, после сборки настоящим
    `make_record` (не заглушкой) — с метой из манифеста и `media:` внутри архива;
  * сервер принимает только перечисленные файлы, в пределах размера, только с правом;
  * индексация — отдельной задачей той же очереди и только после приёма.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from app.content import ingest as core  # noqa: E402
from test_turn_edits import whole_artifact  # noqa: E402


# --- чистые функции --------------------------------------------------------------------

def test_shift_moves_every_speaker_label_and_nothing_else():
    text = json.dumps({"markdown": "[Speaker_3] привет\n[Speaker_12] да", "speaker_map": {"SPEAKER_00": "Speaker_3"},
                       "turns": [{"speaker": "Speaker_3"}, {"speaker": "Пётр Ковалёв"}]}, ensure_ascii=False)
    out = core.shift_speakers(text, 100000)
    assert "Speaker_100003" in out and "Speaker_100012" in out
    assert "Speaker_3" not in out.replace("Speaker_100003", "") and '"Speaker_3"' not in out
    assert "Пётр Ковалёв" in out and "SPEAKER_00" in out, "метки диаризатора и имена — не трогаем"
    assert core.SPEAKER_RE.match("Speaker_100003"), "сдвинутый номер остаётся безымянным голосом для сайта"


def test_manifest_validation_and_id(tmp_path):
    fam = tmp_path
    m = core.validate({"title": "Kafka без боли", "date": "2026-03-12", "video": "talk.MP4",
                       "tags": ["kafka", " "], "speakers": ["Мария Кузнецова"], "summary": "x" * 3000}, fam)
    assert m.record_id == "2026-03-12-kafka-bez-boli" and m.video == "video.mp4"
    assert m.tags == ["kafka"] and len(m.summary) == 2000
    for bad in ({"title": "ab", "date": "2026-03-12", "video": "a.mp4"},
                {"title": "Норм", "date": "12.03.2026", "video": "a.mp4"},
                {"title": "Норм", "date": "2026-03-12", "video": "a.avi"},
                {"title": "!!!", "date": "2026-03-12", "video": "a.mp4"}):
        with pytest.raises(core.Refused):
            core.validate(bad, fam)


def test_events_come_from_routing_rules_with_a_branch(tmp_path):
    (tmp_path / "hub.yml").write_text(yaml.safe_dump({"routing": [
        {"match": {"tag": "Концерт", "event_prefix": "Концерт"}, "space": "t", "branch": "Встречи"},
        {"match": {"event": "Курс Python"}, "space": "t", "branch": "Курсы", "sub": "Python 2024"},
        {"match": {"event": "Черновик"}, "space": "t"},          # без ветки — раскладка не решена
        {"match": {"rest": True}, "space": "t", "branch": "Доклады"},
    ]}, allow_unicode=True), encoding="utf-8")
    assert core.events_of(tmp_path) == ["Концерт", "Курс Python"]
    with pytest.raises(core.Refused):
        core.validate({"title": "Норм", "date": "2026-03-12", "video": "a.mp4", "event": "Черновик"}, tmp_path)
    assert core.validate({"title": "Норм", "date": "2026-03-12", "video": "a.mp4", "event": "Концерт"}, tmp_path).event == "Концерт"


def test_only_listed_files_are_accepted():
    assert core.accept_name("artifact.json") and core.accept_name("video.webm") and core.accept_name("slides.zip")
    for bad in ("../x", "record.md", "video.exe", "artifact.json.bak", ""):
        assert not core.accept_name(bad)


# --- живое приложение на копии демо-корпуса --------------------------------------------

@pytest.fixture
def live(tmp_path, monkeypatch):
    """Сайт на копии демо-корпуса во временном каталоге: приём включён, вход выключен, петля не
    проверяется; сборка — настоящим `make_record`, индексация — командой, оставляющей след."""
    demo = tmp_path / "demo"
    shutil.copytree(REPO / "corpora" / "demo", demo)
    archive = tmp_path / "archive"
    marker = tmp_path / "indexed"
    monkeypatch.setenv("MORAG_WEB_CORPUS", str(demo))
    monkeypatch.setenv("MORAG_WEB_CONFIG", str(REPO / "app" / "config.example.yml"))
    from app.main import app
    with TestClient(app) as c:
        cfg = app.state.cfg.ingest
        cfg.enabled, cfg.archive, cfg.after = True, str(archive), []
        cfg.index = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('1')"]
        cfg.min_free_gb = 0
        app.state.cfg.editing.local_only = False
        try:
            yield c, demo, archive, marker
        finally:
            cfg.enabled = False


def manifest(**over) -> dict:
    return {"title": "Kafka без боли", "date": "2026-03-12", "video": "talk.mp4",
            "speakers": ["Мария Кузнецова"], "summary": "Про очереди.", **over}


def wait(c, rid, states=("done", "error"), timeout=30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = c.get(f"/api/ingest/{rid}").json()
        if body["state"] in states:
            return body
        time.sleep(0.2)
    raise AssertionError(f"не дождались {states}: {body}")


def upload_all(c, rid, video=b"\x00" * 1024) -> None:
    art = whole_artifact()
    assert c.put(f"/api/ingest/{rid}/files/artifact.json", content=json.dumps(art, ensure_ascii=False).encode()).status_code == 200
    refs = {"refs": [{"speaker": "Speaker_3", "at": 9.0, "quote": "А почему не Kafka?"}]}
    assert c.put(f"/api/ingest/{rid}/files/record.refs.json", content=json.dumps(refs).encode()).status_code == 200
    # кадры: маленький zip с одним jpg и одной попыткой выйти за каталог
    from io import BytesIO
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("s001.jpg", b"jpg")
        zf.writestr("evil/../../x.txt", b"no")
    assert c.put(f"/api/ingest/{rid}/files/slides.zip", content=buf.getvalue()).status_code == 200
    assert c.put(f"/api/ingest/{rid}/files/video.mp4", content=video).status_code == 200


def test_end_to_end_upload_builds_a_record_with_shifted_voices(live):
    c, demo, archive, marker = live
    r = c.post("/api/ingest", json=manifest())
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert rid == "2026-03-12-kafka-bez-boli" and r.json()["video"] == "video.mp4"
    assert c.get(f"/api/ingest/{rid}").json()["state"] == "uploading"
    assert c.post(f"/api/ingest/{rid}/finish").status_code == 400, "без файлов принимать нечего"

    upload_all(c, rid)
    r = c.post(f"/api/ingest/{rid}/finish")
    assert r.status_code == 200 and r.json()["state"] == "queued", r.text
    body = wait(c, rid)
    assert body["state"] == "done", body

    record = demo / "records" / rid
    _, head, text = (record / "record.md").read_text(encoding="utf-8").split("---\n", 2)
    meta = yaml.safe_load(head)
    assert meta["title"] == "Kafka без боли" and meta["date"] == "2026-03-12"
    assert meta["media"] == f"Uploads/{rid}.mp4" and (archive / "Uploads" / f"{rid}.mp4").is_file()
    assert "Speaker_100003" in text and "Speaker_3]" not in text, "номера голосов сдвинуты в свой диапазон"
    # У демо нет `hub.yml::roles` → модель ветки `meeting`, докладчиков в шапке не бывает; в ветке
    # с моделью `talk` названный автором докладчик встаёт в `speakers` (make_record.roles_of, `upload`).
    assert "speakers" not in meta and meta["summary"] == "Про очереди."
    record_meta = json.loads((record / "record.meta.json").read_text(encoding="utf-8"))
    assert record_meta["speakers"][0] == {"name": "Мария Кузнецова", "from": "upload"}
    assert "local" in record_meta["sources"]["upload"]["by"]
    refs = json.loads((record / "record.refs.json").read_text(encoding="utf-8"))
    assert refs["refs"][0]["speaker"] == "Speaker_100003", "сайдкар экрана сдвинут вместе с артефактом"
    assert (record / "slides" / "s001.jpg").is_file() and not list(record.glob("**/x.txt")), "из zip — только jpg по имени"
    assert (record / "record.json").is_file(), "сырец приехал — правка на сайте возможна"
    assert marker.is_file(), "индексация запущена после приёма"
    assert not (demo / "incoming" / rid / "video.mp4").exists() and not (demo / "incoming" / rid / "artifact.json").exists()

    ids = {r["id"] for r in c.get("/api/records", params={"slug": "demo"}).json()["records"]}
    assert rid in ids, "индекс записей увидел новую без рестарта"
    assert c.post("/api/ingest", json=manifest()).status_code == 409, "второй раз ту же — нельзя"


def test_second_record_gets_the_next_speaker_range(live):
    c, demo, archive, marker = live
    a = c.post("/api/ingest", json=manifest(title="Первая", date="2026-04-01")).json()["id"]
    upload_all(c, a)
    assert c.post(f"/api/ingest/{a}/finish").status_code == 200
    wait(c, a)
    b = c.post("/api/ingest", json=manifest(title="Вторая", date="2026-04-02")).json()["id"]
    upload_all(c, b)
    assert c.post(f"/api/ingest/{b}/finish").status_code == 200
    wait(c, b)
    ta = (demo / "records" / a / "record.md").read_text(encoding="utf-8")
    tb = (demo / "records" / b / "record.md").read_text(encoding="utf-8")
    assert "Speaker_100003" in ta and "Speaker_101003" in tb, "у каждой записи свой диапазон"


def test_guards_whitelist_and_limits(live):
    c, demo, archive, marker = live
    from app.main import app
    rid = c.post("/api/ingest", json=manifest(title="Лимиты")).json()["id"]
    r = c.put(f"/api/ingest/{rid}/files/record.md", content=b"x")
    assert r.status_code == 400 and "не из пакета" in r.json()["detail"]
    assert c.put(f"/api/ingest/{rid}/files/video.webm", content=b"x").status_code == 400, "видео названо в манифесте"
    app.state.cfg.ingest.max_gb = 1 / 1024 ** 2   # 1 КБ
    assert c.put(f"/api/ingest/{rid}/files/video.mp4", content=b"\x00" * 4096).status_code == 413
    assert not (demo / "incoming" / rid / "video.mp4").exists() and not list((demo / "incoming" / rid).glob("*.part"))
    app.state.cfg.ingest.max_gb = 12
    assert c.post("/api/ingest", json="не объект").status_code == 400
    assert c.get("/api/ingest/2026-01-01-net-takoy").status_code == 404
    with pytest.raises(core.Refused):
        app.state.ingest.dir("../../etc")   # id проверяется по форме до любого обращения к диску

    app.state.cfg.ingest.enabled = False
    assert c.get("/api/ingest/options").status_code == 403
    assert c.post("/api/ingest", json=manifest()).status_code == 403
    app.state.cfg.ingest.enabled = True
    app.state.cfg.editing.local_only = True
    assert c.post("/api/ingest", json=manifest(title="С чужой машины")).status_code == 403, "без входа — только петля"


def test_options_list_events_and_file_names(live):
    c, demo, *_ = live
    body = c.get("/api/ingest/options").json()
    assert body["events"] == [] and "artifact.json" in body["files"] and "mp4" in body["video_ext"]
