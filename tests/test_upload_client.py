"""Клиент загрузки (`tools/upload.py`) против фейкового адаптера и фейкового сайта.

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

import upload  # noqa: E402
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
        if p == "/api/upload" and request.method == "POST":
            self.manifests.append(json.loads(request.read()))
            assert request.headers.get("cookie", "").startswith("morag_session=")
            return httpx.Response(200, json={"id": "2026-03-12-kafka-bez-boli", "video": "video.mp4"})
        if p.startswith("/api/upload/2026-03-12-kafka-bez-boli/files/") and request.method == "PUT":
            self.uploads.append((p.rsplit("/", 1)[-1], len(request.read())))
            return httpx.Response(200, json={"bytes": self.uploads[-1][1]})
        if p.endswith("/finish"):
            self.finishes += 1
            return httpx.Response(200, json={"state": "queued"})
        if p == "/api/upload/2026-03-12-kafka-bez-boli":
            self.status_calls += 1
            state = "building" if self.status_calls < 2 else "done"
            return httpx.Response(200, json={"state": state, "url": "/demo/rec/2026-03-12-kafka-bez-boli"})
        return httpx.Response(404, json={"detail": p})


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(upload, "TRANSPORT", httpx.MockTransport(fake.handle))
    monkeypatch.setattr(upload, "HOME", tmp_path / "work")
    monkeypatch.setattr(upload, "SESSION", tmp_path / "session.json")
    monkeypatch.setattr(upload, "POLL_SEC", 0)
    monkeypatch.setattr(upload.time, "sleep", lambda *_: None)
    monkeypatch.setattr(upload, "ffmpeg_audio", lambda video, out: out.write_bytes(b"mp3" * 100))
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"\x00" * 3000)
    return fake, video, tmp_path


def run(video: Path, *extra: str) -> int:
    sys.argv = ["upload.py", "run", str(video), "--title", "Kafka без боли", "--date", "2026-03-12",
                "--speakers", "Мария Кузнецова", "--no-screen", *extra]
    return upload.main()


def test_login_stores_the_session_with_owner_only_rights(env, monkeypatch):
    fake, video, tmp = env
    monkeypatch.setattr("builtins.input", lambda *_: "kuznetsova")
    monkeypatch.setattr(upload.getpass, "getpass", lambda *_: "pw")
    sys.argv = ["upload.py", "login", "--site", "https://site.example.org/"]
    assert upload.main() == 0
    data = json.loads(upload.SESSION.read_text())
    assert data["site"] == "https://site.example.org" and data["cookies"]["morag_session"] == "abc"
    assert oct(upload.SESSION.stat().st_mode & 0o777) == "0o600"


def test_run_transcribes_uploads_in_order_and_waits(env, monkeypatch):
    fake, video, tmp = env
    upload.save_session("https://site.example.org", {"morag_session": "abc"})
    assert run(video) == 0
    assert fake.transcriptions == 1 and fake.polls >= 2
    work = upload.HOME / "2026-03-12-kafka-bez-boli"
    assert (work / "artifact.json").is_file() and (work / "transcript.md").is_file()
    assert not (work / "audio.mp3").exists(), "пакет принят — звук больше не нужен, удалён в конце"
    assert fake.manifests[0]["speakers"] == ["Мария Кузнецова"] and fake.manifests[0]["video"] == "video.mp4"
    assert [n for n, _ in fake.uploads] == ["artifact.json", "video.mp4"], "видео — последним"
    assert dict(fake.uploads)["video.mp4"] == 3000
    assert fake.finishes == 1 and fake.status_calls >= 2


def test_second_run_resumes_without_redoing(env):
    fake, video, tmp = env
    upload.save_session("https://site.example.org", {"morag_session": "abc"})
    assert run(video) == 0
    assert run(video) == 0
    assert fake.transcriptions == 1, "артефакт уже есть — адаптер не трогаем"
    assert len(fake.uploads) == 2, "отданные файлы второй раз не льём"
    assert len(fake.manifests) == 1, "манифест не заводится заново — id уже известен"
    assert fake.finishes == 2, "finish повторяем: он идемпотентен"


def test_refusals_are_messages_not_tracebacks(env, capsys):
    fake, video, tmp = env
    upload.save_session("https://site.example.org", {"morag_session": "abc"})
    sys.argv = ["upload.py", "run", str(tmp / "нет.mp4"), "--title", "x", "--date", "2026-03-12"]
    assert upload.main() == 1 and "нет файла" in capsys.readouterr().out
    sys.argv = ["upload.py", "run", str(video), "--title", "Норм", "--date", "12.03.2026"]
    assert upload.main() == 1 and "ГГГГ-ММ-ДД" in capsys.readouterr().out
    upload.SESSION.unlink()
    sys.argv = ["upload.py", "run", str(video), "--title", "Норм", "--date", "2026-03-12", "--no-screen"]
    assert upload.main() == 1 and "login" in capsys.readouterr().out


# --- стек: уборка не врёт, а ошибка называет причину ------------------------------------

def test_shutting_the_stack_down_never_masks_the_real_error(tmp_path, monkeypatch):
    """⚠️ Живой случай 24.09: расшифровка упала, а человек увидел «CalledProcessError: stack.sh
    down». Гасим мы в `finally`, и исключение из УБОРКИ заменяет настоящую ошибку; сам `down`
    вдобавок возвращает 1, когда последний порт уже свободен (баг в `stack.sh` движка)."""
    script = tmp_path / "morag" / "services" / "asr-adaptor" / "deploy" / "mac" / "stack.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(upload, "MORAG_REPO", tmp_path / "morag")

    with pytest.raises(Exception):
        upload.stack("up")                      # обычный вызов по-прежнему кричит
    upload.stack("down", check=False)           # а уборка молчит и не роняет


def test_a_dead_stack_says_why_and_not_just_that(tmp_path, monkeypatch):
    """«Стек не отвечает» человеку ничего не говорит. Настоящая причина лежит в логах бэкендов —
    в живом случае это было «Missing credentials» у адаптера (пустой ключ шлюза)."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "adaptor.log").write_text(
        "INFO: старт\nTraceback (most recent call last):\n"
        "openai.OpenAIError: Missing credentials. Please pass an `api_key`\n", encoding="utf-8")
    (logs / "whisper.log").write_text("INFO:     Application startup complete.\n", encoding="utf-8")
    monkeypatch.setenv("ASR_STACK_HOME", str(tmp_path))
    why = upload.stack_trouble()
    assert "adaptor" in why and "Missing credentials" in why
    assert "whisper" not in why, "у здорового бэкенда жаловаться не на что"


def test_gateway_is_configured_before_work_not_only_at_login(tmp_path, monkeypatch):
    """⚠️ Вход мог случиться ДО обновления приложения — тогда шлюз остался ненастроенным, а
    адаптер без ключа не стартует вовсе (строит клиента на импорте). Проверяем у самой работы."""
    env_file = tmp_path / "asr.env"
    env_file.write_text("OR_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("ASR_STACK_ENV", str(env_file))
    monkeypatch.delenv("OR_KEY", raising=False)
    called: list[str] = []

    def fake_use_site_llm():
        called.append("да")
        upload.set_stack_env(OR_KEY="сессия-сайта")
        return {"via_site": True, "checked": True}

    monkeypatch.setattr(upload, "use_site_llm", fake_use_site_llm)
    upload.ensure_gateway()
    assert called == ["да"] and upload.stack_env_value("OR_KEY") == "сессия-сайта"

    called.clear()
    upload.ensure_gateway()
    assert called == [], "уже настроено — второй раз к сайту не ходим"

    monkeypatch.setattr(upload, "use_site_llm", lambda: {"via_site": False})
    monkeypatch.setenv("ASR_STACK_ENV", str(tmp_path / "пусто.env"))
    monkeypatch.delenv("OR_KEY", raising=False)
    with pytest.raises(upload.Step, match="шлюз"):
        upload.ensure_gateway()


def test_the_audio_outlives_transcription_so_voiceprints_can_be_taken(env, monkeypatch):
    """⚠️⚠️ Регрессия, из-за которой узнавание голосов не работало НИ РАЗУ.

    `transcribe()` удалял `audio.mp3` последним действием, а отпечатки считаются СЛЕДУЮЩИМ шагом
    из того же файла — и без него шаг молча возвращал `None`. Пакет уезжал без `voices.json`,
    сервер честно откатывался на «незнакомцы под номерами», и снаружи всё выглядело исправным.
    Прежний тест это пропускал: он проверял, что звука нет, — то есть закреплял сам дефект.

    Держим ИНВАРИАНТ, а не файл: в момент, когда считаются отпечатки, звук обязан быть на диске.
    """
    fake, video, tmp = env
    upload.save_session("https://site.example.org", {"morag_session": "abc"})

    sys.path.insert(0, str(REPO / "tools"))
    import voiceprints

    seen: dict = {}

    def fake_prints(artifact, audio, **kw):
        seen["audio"] = Path(audio)
        seen["existed"] = Path(audio).is_file() and Path(audio).stat().st_size > 0
        return {"Speaker_0": {"centroid": [0.0] * 192, "air_sec": 700.0, "cluster": "SPEAKER_00"}}

    monkeypatch.setattr(voiceprints, "fingerprints", fake_prints)
    assert run(video) == 0

    assert seen.get("existed"), "звук должен быть на диске, когда считаются отпечатки"
    work = upload.HOME / "2026-03-12-kafka-bez-boli"
    assert (work / "voices.json").is_file()
    assert "voices.json" in [n for n, _ in fake.uploads], "отпечатки обязаны уехать в пакете"
