"""Клиент загрузки (`tools/ingest.py`) против фейкового адаптера и фейкового сайта.

Что закреплено: протокол адаптера (multipart + поллинг), пакет и порядок загрузки (видео —
последним), возобновление (повторный запуск не гонит транскрибацию заново и не льёт уже
отданные файлы), сессия сайта на диске с правами 0600, отказ без трейсбека.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))

import ingest  # noqa: E402
from test_turn_edits import whole_artifact  # noqa: E402


class Fake:
    """Адаптер (:8082) и сайт в одном транспорте — по базовому адресу запроса."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, int]] = []
        self.manifests: list[dict] = []
        self.finishes = 0
        self.transcriptions = 0
        self.polls = 0
        self.status_calls = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if request.url.port == 8082:
            if p == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if p == "/v1/audio/transcriptions":
                self.transcriptions += 1
                body = request.read()
                assert b'name="mode"' in body and b"async" in body and b'name="hints"' in body
                return httpx.Response(200, json={"job_id": "j1"})
            if p == "/v1/jobs/j1":
                self.polls += 1
                if self.polls < 2:
                    return httpx.Response(200, json={"status": "running", "progress": "whisper"})
                return httpx.Response(200, json={"status": "done", "result": whole_artifact()})
        if p == "/api/auth/state":
            return httpx.Response(200, json={"enabled": True})
        if p == "/api/auth/login":
            return httpx.Response(200, json={"name": "Мария", "role": "editor"}, headers={"set-cookie": "morag_session=abc; Path=/"})
        if p == "/api/ingest" and request.method == "POST":
            self.manifests.append(json.loads(request.read()))
            assert request.headers.get("cookie", "").startswith("morag_session=")
            return httpx.Response(200, json={"id": "2026-03-12-kafka-bez-boli", "video": "video.mp4"})
        if p.startswith("/api/ingest/2026-03-12-kafka-bez-boli/files/") and request.method == "PUT":
            self.uploads.append((p.rsplit("/", 1)[-1], len(request.read())))
            return httpx.Response(200, json={"bytes": self.uploads[-1][1]})
        if p.endswith("/finish"):
            self.finishes += 1
            return httpx.Response(200, json={"state": "queued"})
        if p == "/api/ingest/2026-03-12-kafka-bez-boli":
            self.status_calls += 1
            state = "building" if self.status_calls < 2 else "done"
            return httpx.Response(200, json={"state": state, "url": "/demo/rec/2026-03-12-kafka-bez-boli"})
        return httpx.Response(404, json={"detail": p})


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(ingest, "TRANSPORT", httpx.MockTransport(fake.handle))
    monkeypatch.setattr(ingest, "HOME", tmp_path / "work")
    monkeypatch.setattr(ingest, "SESSION", tmp_path / "session.json")
    monkeypatch.setattr(ingest, "POLL_SEC", 0)
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ingest, "ffmpeg_audio", lambda video, out: out.write_bytes(b"mp3" * 100))
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"\x00" * 3000)
    return fake, video, tmp_path


def run(video: Path, *extra: str) -> int:
    sys.argv = ["ingest.py", "run", str(video), "--title", "Kafka без боли", "--date", "2026-03-12",
                "--speakers", "Мария Кузнецова", "--no-screen", *extra]
    return ingest.main()


def test_login_stores_the_session_with_owner_only_rights(env, monkeypatch):
    fake, video, tmp = env
    monkeypatch.setattr("builtins.input", lambda *_: "kuznetsova")
    monkeypatch.setattr(ingest.getpass, "getpass", lambda *_: "pw")
    sys.argv = ["ingest.py", "login", "--site", "https://site.example.org/"]
    assert ingest.main() == 0
    data = json.loads(ingest.SESSION.read_text())
    assert data["site"] == "https://site.example.org" and data["cookies"]["morag_session"] == "abc"
    assert oct(ingest.SESSION.stat().st_mode & 0o777) == "0o600"


def test_run_transcribes_uploads_in_order_and_waits(env, monkeypatch):
    fake, video, tmp = env
    ingest.save_session("https://site.example.org", {"morag_session": "abc"})
    assert run(video) == 0
    assert fake.transcriptions == 1 and fake.polls >= 2
    work = ingest.HOME / "2026-03-12-kafka-bez-boli"
    assert (work / "artifact.json").is_file() and (work / "transcript.md").is_file()
    assert not (work / "audio.mp3").exists(), "звук после расшифровки не нужен — удалён"
    assert fake.manifests[0]["speakers"] == ["Мария Кузнецова"] and fake.manifests[0]["video"] == "video.mp4"
    assert [n for n, _ in fake.uploads] == ["artifact.json", "video.mp4"], "видео — последним"
    assert dict(fake.uploads)["video.mp4"] == 3000
    assert fake.finishes == 1 and fake.status_calls >= 2


def test_second_run_resumes_without_redoing(env):
    fake, video, tmp = env
    ingest.save_session("https://site.example.org", {"morag_session": "abc"})
    assert run(video) == 0
    assert run(video) == 0
    assert fake.transcriptions == 1, "артефакт уже есть — адаптер не трогаем"
    assert len(fake.uploads) == 2, "отданные файлы второй раз не льём"
    assert len(fake.manifests) == 1, "манифест не заводится заново — id уже известен"
    assert fake.finishes == 2, "finish повторяем: он идемпотентен"


def test_refusals_are_messages_not_tracebacks(env, capsys):
    fake, video, tmp = env
    ingest.save_session("https://site.example.org", {"morag_session": "abc"})
    sys.argv = ["ingest.py", "run", str(tmp / "нет.mp4"), "--title", "x", "--date", "2026-03-12"]
    assert ingest.main() == 1 and "нет файла" in capsys.readouterr().out
    sys.argv = ["ingest.py", "run", str(video), "--title", "Норм", "--date", "12.03.2026"]
    assert ingest.main() == 1 and "ГГГГ-ММ-ДД" in capsys.readouterr().out
    ingest.SESSION.unlink()
    sys.argv = ["ingest.py", "run", str(video), "--title", "Норм", "--date", "2026-03-12", "--no-screen"]
    assert ingest.main() == 1 and "login" in capsys.readouterr().out
