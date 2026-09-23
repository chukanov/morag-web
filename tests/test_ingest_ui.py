"""Локальная страница загрузки (`tools/ingest_ui.py`): что она отдаёт и кого не пускает.

Главное, что здесь закреплено: **токен и чужой origin**. Сервер слушает петлю, но петля от
чужих вкладок не защищает: страница любого сайта, открытая в том же браузере, может постучаться
на `127.0.0.1:8099`. Без токена и проверки origin это значило бы «чужая вкладка запускает
загрузку файла с вашего диска на сайт».
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import ingest  # noqa: E402
import ingest_ui  # noqa: E402


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Поднятый сервер на свободном порту; сайт и стек — заглушками, конвейер не запускается."""
    monkeypatch.setattr(ingest_ui, "FOLDERS", [tmp_path / "Downloads"])
    monkeypatch.setattr(ingest, "HOME", tmp_path / "work")
    monkeypatch.setattr(ingest, "stack_health", lambda: {"status": "ok"})
    monkeypatch.setattr(ingest_ui, "site_state", lambda: {"site": "https://site.example.org", "logged": True,
                                                          "events": ["Доклады", "Встречи"]})
    downloads = tmp_path / "Downloads"
    (downloads / "Встречи").mkdir(parents=True)
    (downloads / "talk.mp4").write_bytes(b"\x00" * 2048)
    (downloads / "Встречи" / "deep.mov").write_bytes(b"\x00" * 1024)
    (downloads / "notes.txt").write_text("не видео")
    time.sleep(0.01)
    (downloads / "talk.mp4").touch()   # свежайшее — должно быть первым

    ingest_ui.STATE.update({"stage": "idle", "id": "", "error": "", "url": ""})
    ingest_ui.Handler.token = "tok"
    httpd = ingest_ui.ThreadingHTTPServer(("127.0.0.1", 0), ingest_ui.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base, tmp_path
    finally:
        httpd.shutdown(); httpd.server_close()


def get(url: str, headers: dict | None = None) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if r.headers.get_content_type() == "application/json" else body)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def post(url: str, data: dict, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_page_opens_without_token_but_api_does_not(server):
    base, _ = server
    code, body = get(f"{base}/")
    assert code == 200 and "Загрузить запись" in body, "сама страница — без токена, токен она берёт из адреса"
    assert get(f"{base}/api/state")[0] == 403, "состояние — только с токеном"
    assert post(f"{base}/api/start", {"video": "/tmp/x.mp4"})[0] == 403
    code, body = post(f"{base}/api/start?t=tok", {"video": "/tmp/x.mp4", "title": "Норм", "date": "2026-03-12"},
                      headers={"Origin": "https://evil.example.org"})
    assert code == 403 and "origin" in body["error"], "чужая вкладка не запускает загрузку"


def test_state_lists_videos_newest_first_and_skips_others(server):
    base, tmp = server
    code, body = get(f"{base}/api/state?t=tok")
    assert code == 200
    names = [v["name"] for v in body["videos"]]
    assert names[0] == "talk.mp4" and "deep.mov" in names, "видео из подпапки тоже видно"
    assert "notes.txt" not in names
    assert body["stack"] is True and body["site"]["events"] == ["Доклады", "Встречи"]
    assert body["job"]["stage"] == "idle" and body["home"].endswith("work")


def test_start_checks_fields_before_doing_anything(server):
    base, tmp = server
    bad = [({"video": str(tmp / "нет.mp4"), "title": "Норм", "date": "2026-03-12"}, "нет файла"),
           ({"video": str(tmp / "Downloads" / "notes.txt"), "title": "Норм", "date": "2026-03-12"}, "видео"),
           ({"video": str(tmp / "Downloads" / "talk.mp4"), "title": "Норм", "date": "12.03.2026"}, "ГГГГ-ММ-ДД"),
           ({"video": str(tmp / "Downloads" / "talk.mp4"), "title": "ok", "date": "2026-03-12"}, "трёх знаков")]
    for fields, expect in bad:
        code, body = post(f"{base}/api/start?t=tok", fields)
        assert code == 400 and expect in body["error"], (fields, body)
    assert ingest_ui.STATE["stage"] == "idle", "ни один отказ не оставил состояние «в работе»"


def test_start_runs_the_pipeline_once_and_reports(server, monkeypatch):
    base, tmp = server
    calls: list[dict] = []
    release = threading.Event()

    def fake_pipeline(video, **kw):
        calls.append({"video": str(video), **kw})
        release.wait(5)
        return "2026-03-12-kafka-bez-boli"

    monkeypatch.setattr(ingest, "pipeline", fake_pipeline)
    monkeypatch.setattr(ingest, "load_session", lambda site=None: ("https://site.example.org", {"c": "1"}))
    fields = {"video": str(tmp / "Downloads" / "talk.mp4"), "title": "Kafka без боли", "date": "2026-03-12",
              "event": "Доклады", "speakers": "Мария Кузнецова, ", "tags": "kafka", "summary": "о чём",
              "no_screen": True}
    code, body = post(f"{base}/api/start?t=tok", fields)
    assert code == 200 and body["id"] == "2026-03-12-kafka-bez-boli"

    for _ in range(50):
        if calls:
            break
        time.sleep(0.05)
    assert calls and calls[0]["title"] == "Kafka без боли" and calls[0]["speakers"] == ["Мария Кузнецова"]
    assert calls[0]["with_screen"] is False and calls[0]["event"] == "Доклады"
    assert get(f"{base}/api/state?t=tok")[1]["job"]["stage"] == "running"
    assert post(f"{base}/api/start?t=tok", fields)[0] == 400, "вторую запись в работу не берём"

    release.set()
    for _ in range(50):
        if ingest_ui.STATE["stage"] == "done":
            break
        time.sleep(0.05)
    assert ingest_ui.STATE["stage"] == "done" and len(calls) == 1


def test_failure_is_shown_not_swallowed(server, monkeypatch):
    base, tmp = server

    def boom(video, **kw):
        raise ingest.Step("стек транскрибации не отвечает")

    monkeypatch.setattr(ingest, "pipeline", boom)
    post(f"{base}/api/start?t=tok", {"video": str(tmp / "Downloads" / "talk.mp4"), "title": "Норм",
                                     "date": "2026-03-12"})
    for _ in range(50):
        if ingest_ui.STATE["stage"] == "error":
            break
        time.sleep(0.05)
    job = get(f"{base}/api/state?t=tok")[1]["job"]
    assert job["stage"] == "error" and "не отвечает" in job["error"]


# --- чем ходим в шлюз, сброс и окно ----------------------------------------------------

def test_site_session_becomes_the_gateway_credential(server, tmp_path, monkeypatch):
    """Ключ у человека не спрашиваем вовсе: вошёл на сайт — стадии с ИИ идут ЧЕРЕЗ сайт, а
    удостоверением служит та же сессия. В файл стека уезжают адрес ручки, модель и строка
    сессии; поднятый стек гасим — адаптер читает файл при старте."""
    base, tmp = server
    env_file = tmp / "asr.env"
    env_file.write_text("ASR_LLM_BASE_URL=https://llm.example.org/api\nOR_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("ASR_STACK_ENV", str(env_file))
    for name in ("OR_KEY", "ASR_LLM_BASE_URL", "ASR_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    stack_calls: list[str] = []
    monkeypatch.setattr(ingest, "stack", lambda cmd: stack_calls.append(cmd))
    monkeypatch.setattr(ingest, "stack_health", lambda: {"status": "ok"})
    monkeypatch.setattr(ingest, "load_session",
                        lambda site: ("https://site.example.org", {"morag_session": "eyJzdWIi.c2lnbg"}))

    import httpx

    seen: dict = {}

    def fake(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = dict(request.headers)
        if request.url.path == "/api/ingest/options":
            return httpx.Response(200, json={"events": [], "llm": {"via_site": True, "path": "/api/ingest/llm",
                                                                   "model": "Instruct", "cookie": "morag_session"}})
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(ingest, "TRANSPORT", httpx.MockTransport(fake))
    code, body = post(f"{base}/api/llm?t=tok", {})
    assert code == 200 and body == {"via_site": True, "checked": True}

    text = env_file.read_text(encoding="utf-8")
    assert "OR_KEY=eyJzdWIi.c2lnbg" in text, "удостоверение — сама сессия сайта"
    assert "ASR_LLM_BASE_URL=https://site.example.org/api/ingest/llm" in text
    assert "ASR_LLM_MODEL=Instruct" in text
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert stack_calls == ["down"]
    assert seen["/api/ingest/llm/models"]["authorization"] == "Bearer eyJzdWIi.c2lnbg", "проверка тем же удостоверением"
    assert ingest_ui.llm_state() == {"ready": True, "via_site": True,
                                    "base": "https://site.example.org/api/ingest/llm"}
    assert get(f"{base}/api/state?t=tok")[1]["llm"]["via_site"] is True


def test_own_key_stays_as_a_fallback_and_restores_the_direct_address(server, tmp_path, monkeypatch):
    """Запасной ход для тех, кто гоняет стек без сайта. ⚠️ Вместе с ключом возвращаем и адрес
    САМОГО шлюза: если до этого ходили через сайт, чужой ключ к нашей ручке не подошёл бы, и
    вышло бы «ключ вписан, а ничего не работает»."""
    base, tmp = server
    env_file = tmp / "asr.env"
    env_file.write_text("ASR_LLM_BASE_URL=https://site.example.org/api/ingest/llm\nOR_KEY=eyJzdWIi.c2lnbg\n",
                        encoding="utf-8")
    monkeypatch.setenv("ASR_STACK_ENV", str(env_file))
    for name in ("OR_KEY", "ASR_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    home = tmp / "morag-ingest"
    home.mkdir()
    (home / "gateway.env").write_text("ASR_LLM_BASE_URL=https://llm.example.org/api\n", encoding="utf-8")
    monkeypatch.setenv("MORAG_INGEST_HOME", str(home))
    monkeypatch.setattr(ingest, "stack", lambda cmd: None)
    monkeypatch.setattr(ingest, "stack_health", lambda: None)

    code, body = post(f"{base}/api/key?t=tok", {"key": "  свой-ключ  "})
    assert code == 200 and body["ok"] is True
    text = env_file.read_text(encoding="utf-8")
    assert "OR_KEY=свой-ключ" in text and "eyJzdWIi" not in text
    assert "ASR_LLM_BASE_URL=https://llm.example.org/api" in text, "адрес вернулся на сам шлюз"
    assert text.count("OR_KEY=") == 1 and text.count("ASR_LLM_BASE_URL=") == 1
    assert ingest_ui.llm_state()["via_site"] is False
    assert post(f"{base}/api/key?t=tok", {"key": "   "})[0] == 400, "пустой ключ не принимаем"


def test_reset_clears_the_finished_job_but_not_a_running_one(server):
    base, _ = server
    ingest_ui.STATE.update({"stage": "done", "id": "rec", "url": "https://site/rec"})
    ingest.LOG.append("строка")
    assert post(f"{base}/api/reset?t=tok", {})[0] == 200
    assert ingest_ui.STATE["stage"] == "idle" and not ingest.LOG
    ingest_ui.STATE.update({"stage": "running"})
    assert post(f"{base}/api/reset?t=tok", {})[0] == 400, "идущую работу не сбрасываем"
    ingest_ui.STATE.update({"stage": "idle"})


def test_page_has_the_drop_zone_and_the_native_hook(server):
    """Страница — одна на окно и браузер: зона перетаскивания, поиск файла по имени (браузер)
    и `window.dropVideo` (окно отдаёт настоящий путь)."""
    base, _ = server
    body = get(f"{base}/")[1]
    assert "Перетащите сюда запись" in body
    assert "window.dropVideo" in body, "окно зовёт эту функцию с путём файла"
    assert "title_auto" in body, "страница говорит серверу, что название подставлено из имени файла"


def test_window_falls_back_to_the_browser_without_pyobjc(monkeypatch):
    """Нет PyObjC (не мак, урезанный питон) — не отказ, а прежняя страница в браузере."""
    import ingest_app

    monkeypatch.setitem(sys.modules, "Cocoa", None)
    monkeypatch.setattr(ingest_app, "available", lambda: False)
    called = {}

    def fake_serve(port, open_browser):
        called["port"] = port
        called["browser"] = open_browser
        return 0

    monkeypatch.setattr(ingest_ui, "serve", fake_serve)
    sys.argv = ["ingest.py", "app", "--port", "8123"]
    assert ingest.main() == 0
    assert called == {"port": 8123, "browser": True}
