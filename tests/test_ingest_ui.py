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


# --- ключ шлюза, сброс и окно ---------------------------------------------------------

def test_key_is_saved_to_the_stack_env_with_owner_only_rights(server, tmp_path, monkeypatch):
    """Ключ вводят в окне, а читает его стек — значит писать надо в его env-файл, и только для
    себя (0600). ⚠️ Адаптер читает файл при старте, поэтому поднятый стек гасим: иначе новый
    ключ подхватится «когда-нибудь», и человек решит, что кнопка не работает."""
    base, tmp = server
    env_file = tmp / "asr.env"
    env_file.write_text("ASR_LLM_BASE_URL=https://llm.example.org/api\nOR_KEY=старый\n", encoding="utf-8")
    monkeypatch.setenv("ASR_STACK_ENV", str(env_file))
    monkeypatch.delenv("OR_KEY", raising=False)
    stack_calls: list[str] = []
    monkeypatch.setattr(ingest, "stack", lambda cmd: stack_calls.append(cmd))
    monkeypatch.setattr(ingest, "stack_health", lambda: {"status": "ok"})

    code, body = post(f"{base}/api/key?t=tok", {"key": "  новый-ключ  "})
    assert code == 200 and body["ok"] is True
    text = env_file.read_text(encoding="utf-8")
    assert "OR_KEY=новый-ключ" in text and "старый" not in text, "ключ заменён, а не дописан вторым"
    assert text.count("OR_KEY=") == 1
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert stack_calls == ["down"], "стек погашен, чтобы перечитать ключ при следующем подъёме"
    assert ingest_ui.gateway_key() == "новый-ключ"
    assert get(f"{base}/api/state?t=tok")[1]["key"] is True

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
