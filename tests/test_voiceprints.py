"""Отпечатки голосов на стороне машины, где идёт расшифровка (`tools/voiceprints.py`).

Реестр голосов коллеге не отдают — это биометрия корпуса. Вместо него в пакет едут отпечатки
ЭТОЙ записи, и узнавание делает сервер. Здесь закреплено то, от чего зависит, узнает ли он:
  * спаны берутся по СЕГМЕНТАМ реплик, а не по их границам (иначе центроид вбирает соседа);
  * названные голоса не отпечатываются — узнавать их незачем;
  * шаг не роняет загрузку: CAM++ молчит — запись всё равно уезжает.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import ingest  # noqa: E402
import voiceprints  # noqa: E402


def artifact(tmp_path: Path) -> Path:
    data = {"x_enriched": {"turns": [
        {"speaker_id": "Speaker_3", "start": 0.0, "end": 30.0,
         "segments": [{"start": 0.0, "end": 5.0}, {"start": 20.0, "end": 26.0}]},
        {"speaker_id": "Мария Кузнецова", "start": 31.0, "end": 60.0},
        {"speaker_id": "Speaker_4", "start": 61.0, "end": 62.0},   # только границы, коротко
    ]}}
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_spans_come_from_segments_and_named_voices_are_skipped(tmp_path):
    """⚠️ По границам реплики считать нельзя: между сегментами бывают пауза и чужая речь, и
    отпечаток вберёт соседа. Так же считает `rebuild_registry.py`, которым пересобирался реестр
    корпуса, — расходиться с ним значит получать другие решения на тех же людях."""
    out = voiceprints.spans_of(json.loads(artifact(tmp_path).read_text(encoding="utf-8")))
    assert set(out) == {"Speaker_3", "Speaker_4"}, "названный голос узнавать незачем"
    assert out["Speaker_3"]["spans"] == [(0.0, 5.0), (20.0, 26.0)], "сегменты, а не 0–30"
    assert out["Speaker_3"]["air_sec"] == 11.0 and out["Speaker_3"]["longest"] == 6.0
    assert out["Speaker_4"]["spans"] == [(61.0, 62.0)], "нет сегментов — берём границы реплики"


def test_short_voices_do_not_reach_the_service(tmp_path, monkeypatch):
    """CAM++ и так не берёт куски короче двух секунд — не гоняем их по сети."""
    seen: dict = {}
    monkeypatch.setattr(voiceprints, "wav16k", lambda src, out: out)
    monkeypatch.setattr(voiceprints, "embed",
                        lambda wav, spans, url, key="", timeout=600: seen.update(spans=spans) or
                        {"centroids": {"Speaker_3": [0.1] * 192}, "air": {"Speaker_3": 11.0}})
    prints = voiceprints.fingerprints(artifact(tmp_path), tmp_path / "audio.mp3", work=tmp_path)
    assert [s["speaker"] for s in seen["spans"]] == ["Speaker_3", "Speaker_3"], "Speaker_4 короче двух секунд"
    assert set(prints) == {"Speaker_3"} and len(prints["Speaker_3"]["centroid"]) == 192
    assert prints["Speaker_3"]["air_sec"] == 11.0


def test_the_step_never_costs_the_upload(tmp_path, monkeypatch):
    """⚠️ Потерять запись из-за неузнанных голосов было бы хуже, чем принять её с безымянными."""
    work = tmp_path
    (work / "audio.mp3").write_bytes(b"mp3")
    said: list[str] = []
    monkeypatch.setattr(ingest, "say", lambda text: said.append(text))
    monkeypatch.setattr(voiceprints, "fingerprints",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("CAM++ не отвечает")))
    assert ingest.voiceprint(work, artifact(tmp_path)) is None
    assert not (work / "voices.json").exists()
    assert any("безымянными" in line for line in said), "человеку сказали, что случилось"


def test_the_step_is_resumable(tmp_path, monkeypatch):
    (tmp_path / "audio.mp3").write_bytes(b"mp3")
    (tmp_path / "voices.json").write_text('{"Speaker_3": {}}', encoding="utf-8")
    monkeypatch.setattr(ingest, "say", lambda text: None)
    monkeypatch.setattr(voiceprints, "fingerprints",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("не должно считаться заново")))
    assert ingest.voiceprint(tmp_path, artifact(tmp_path)) == tmp_path / "voices.json"


# --- перенумерация уже собранной записи (`tools/voices_assign.py`) ----------------------

def test_names_move_together_with_the_numbers(tmp_path):
    """⚠️⚠️ Главная ловушка перенумерации: `make_record` ищет СТАРУЮ метку в тексте, не находит
    и молча идёт дальше (`hits == 0 → continue`). Имя осиротеет, и человек увидит безымянный
    голос там, где вчера было имя. Поэтому словарь правится вместе с сайдкаром."""
    import voices_assign

    names = tmp_path / "names.json"
    names.write_text(json.dumps({
        "speakers": {"Speaker_100001": "Мария Кузнецова", "Speaker_7": "Пётр Ковалёв"},
        "_why": {"Speaker_100001": "Имя — это я: kuznetsova, 24.09, с сайта."},
        "records": {"rec-1": {"Speaker_100002": "Гость"}},
    }, ensure_ascii=False), encoding="utf-8")
    moved = voices_assign.move_names(names, {"Speaker_100001": "Speaker_19",
                                             "Speaker_100002": "Speaker_20"}, dry=False)
    data = json.loads(names.read_text(encoding="utf-8"))
    assert data["speakers"] == {"Speaker_19": "Мария Кузнецова", "Speaker_7": "Пётр Ковалёв"}
    assert list(data["_why"]) == ["Speaker_19"]
    assert data["records"]["rec-1"] == {"Speaker_20": "Гость"}
    assert len(moved) == 3


def test_remap_survives_a_swap(tmp_path):
    import voices_assign

    out = voices_assign.remap('{"a": "Speaker_0", "b": "Speaker_3"}',
                              {"Speaker_0": "Speaker_3", "Speaker_3": "Speaker_0"})
    assert out == '{"a": "Speaker_3", "b": "Speaker_0"}'


def test_a_name_that_lives_in_other_records_is_never_taken_away(tmp_path):
    """⚠️⚠️ Обратная сторона той же ловушки, и она дороже: метка приехавшей записи может
    СОВПАСТЬ с корпусным номером случайно — у чужой машины свой счёт голосов, её `Speaker_3` не
    наш. Если у нашего `Speaker_3` уже стоит имя, слепой перенос увёл бы имя живого человека с
    его сорока записей на одного незнакомца, и молча. Такой номер не трогаем вовсе.
    """
    import voices_assign

    # корпус: <корень>/<ветка>/<год>/<id>/record.json — обход идёт от каталога записи вверх
    root = tmp_path / "records" / "Ветка" / "2026"
    mine = root / "rec-new"
    other = root / "rec-old"
    for d in (mine, other):
        d.mkdir(parents=True)
    mine.joinpath("record.json").write_text('{"speaker_map": {"SPEAKER_00": "Speaker_3"}}', encoding="utf-8")
    other.joinpath("record.json").write_text('{"speaker_map": {"SPEAKER_00": "Speaker_3"}}', encoding="utf-8")

    names = tmp_path / "names.json"
    names.write_text(json.dumps({"speakers": {"Speaker_3": "Пётр Ковалёв"}}, ensure_ascii=False),
                     encoding="utf-8")

    safe = voices_assign.drop_conflicts(mine, names, {"Speaker_3": "Speaker_250",
                                                      "Speaker_9": "Speaker_251"})
    assert safe == {"Speaker_9": "Speaker_251"}, "названный номер из других записей — не трогаем"
    assert json.loads(names.read_text(encoding="utf-8"))["speakers"] == {"Speaker_3": "Пётр Ковалёв"}

    # а голос, который звучит ТОЛЬКО здесь, переименовать можно: имя уезжает вместе с ним
    other.joinpath("record.json").write_text('{"speaker_map": {"SPEAKER_00": "Speaker_77"}}', encoding="utf-8")
    assert voices_assign.drop_conflicts(mine, names, {"Speaker_3": "Speaker_250"}) == {"Speaker_3": "Speaker_250"}
