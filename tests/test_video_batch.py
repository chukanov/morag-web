"""`tools/video_batch.py`: план прогона — порядок веток, пропуск готовых, фильтры, пути на сервере.

Сервер, архив и порядок веток у инструмента — из `ops.env` корпуса; здесь они подставляются
синтетическими: тест не зависит от того, есть ли рядом чей-то корпус.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import video_batch as vb  # noqa: E402

vb.HOST, vb.ARCHIVE, vb.WEB_ROOT = "user@video.example.org", "/srv/archive", "/srv/www/site"
vb.ORDER = ["Доклады", "Встречи", "Прочее", "Курсы"]
TALKS, MEETINGS, COURSES = vb.ORDER[0], vb.ORDER[1], vb.ORDER[3]


def _rec(root: Path, branch: str, sub: str, rid: str, media: str | None, done: bool = False) -> Path:
    d = root / branch / sub / rid
    d.mkdir(parents=True)
    head = f'---\ntitle: "{rid}"\ndate: "2024-01-01"\n' + (f'media: "{media}"\n' if media else "") + "---\n\n[A] <!-- t:0.0 --> Текст.\n"
    (d / "record.md").write_text(head, encoding="utf-8")
    if done:
        (d / "record.annotations.json").write_text('{"items": []}', encoding="utf-8")
        (d / "record.refs.json").write_text('{"refs": []}', encoding="utf-8")
    return d


def test_plan_orders_branches_and_skips_done_and_media_less(tmp_path):
    _rec(tmp_path, COURSES, "Python 2023", "2024-07-31-lecture-1", "Share/lec1.mp4")
    _rec(tmp_path, TALKS, "2024", "2024-02-16-b", "Share/Old Talks/b.mp4")
    _rec(tmp_path, TALKS, "2022", "2022-08-19-a", "Share/a.mp4", done=True)
    _rec(tmp_path, MEETINGS, "2025", "2025-05-20-c", "Share/c.webm")
    _rec(tmp_path, TALKS, "2026", "2026-01-01-anons", None)          # без видео — анонс
    got = [(rec.name, media) for rec, media in vb.plan(tmp_path)]
    assert got == [("2024-02-16-b", "Share/Old Talks/b.mp4"), ("2025-05-20-c", "Share/c.webm"),
                   ("2024-07-31-lecture-1", "Share/lec1.mp4")]
    assert [rec.name for rec, _ in vb.plan(tmp_path, redo=True)][0] == "2022-08-19-a"
    assert [rec.name for rec, _ in vb.plan(tmp_path, only=[MEETINGS])] == ["2025-05-20-c"]


def test_remote_path_with_spaces_is_quoted_for_the_remote_shell(monkeypatch, tmp_path):
    seen = {}

    def fake_run(name, cmd, rec_id):
        seen["cmd"] = cmd
        return True, 0.0

    monkeypatch.setattr(vb, "run", fake_run)
    vb.fetch("Share/Old Talks/2022.08.19_pytest.mp4", tmp_path / "x.mp4")
    assert seen["cmd"][-2] == f"{vb.HOST}:'{vb.ARCHIVE}/Share/Old Talks/2022.08.19_pytest.mp4'"
    assert "--partial-dir=.rsync-partial" in seen["cmd"]   # прерванная закачка продолжается, обрубок не под именем готового


def test_second_video_root_is_a_configured_prefix_not_a_hardcoded_path(monkeypatch):
    """Файлы, чей `media:` начинается с VIDEO_WEB_PREFIX, лежат во втором корне (VIDEO_WEB_ROOT), а не в
    архиве — правило конфига корпуса (ops.env), не код (ловилось прогоном 13.09: вложения прежнего
    сайта в архиве отсутствовали). Без префикса всё идёт из архива."""
    monkeypatch.setattr(vb, "WEB_PREFIX", "uploads/")
    assert vb.server_path("uploads/2024/05/demo.mp4") == f"{vb.WEB_ROOT}/uploads/2024/05/demo.mp4"
    assert vb.remote_path("uploads/2024/05/demo.mp4") == f"{vb.HOST}:{vb.WEB_ROOT}/uploads/2024/05/demo.mp4"
    assert vb.remote_path("new/a b.mp4") == f"{vb.HOST}:'{vb.ARCHIVE}/new/a b.mp4'"
    monkeypatch.setattr(vb, "WEB_PREFIX", "")
    assert vb.server_path("uploads/2024/05/demo.mp4") == f"{vb.ARCHIVE}/uploads/2024/05/demo.mp4"
