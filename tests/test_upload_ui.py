"""Локальная страница загрузки (`tools/upload_ui.py`): что она отдаёт и кого не пускает.

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

import upload  # noqa: E402
import upload_ui  # noqa: E402


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Поднятый сервер на свободном порту; сайт и стек — заглушками, конвейер не запускается."""
    monkeypatch.setattr(upload_ui, "FOLDERS", [tmp_path / "Downloads"])
    monkeypatch.setattr(upload, "HOME", tmp_path / "work")
    monkeypatch.setattr(upload, "stack_health", lambda: {"status": "ok"})
    monkeypatch.setattr(upload_ui, "site_state", lambda: {"site": "https://site.example.org", "logged": True,
                                                          "events": ["Доклады", "Встречи"]})
    downloads = tmp_path / "Downloads"
    (downloads / "Встречи").mkdir(parents=True)
    (downloads / "talk.mp4").write_bytes(b"\x00" * 2048)
    (downloads / "Встречи" / "deep.mov").write_bytes(b"\x00" * 1024)
    (downloads / "notes.txt").write_text("не видео")
    time.sleep(0.01)
    (downloads / "talk.mp4").touch()   # свежайшее — должно быть первым

    upload_ui.STATE.update({"stage": "idle", "id": "", "error": "", "url": ""})
    upload_ui.Handler.token = "tok"
    httpd = upload_ui.ThreadingHTTPServer(("127.0.0.1", 0), upload_ui.Handler)
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


def raw(url: str) -> tuple[int, bytes, str]:
    """Сырой ответ: статике нужен и тип содержимого, и то, что она не JSON."""
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


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
    # ⚠️ Состояние стека отдаётся ЦЕЛИКОМ, а не «да/нет»: в настройках видно, кто именно молчит.
    assert body["stack"].get("status") == "ok" and body["site"]["events"] == ["Доклады", "Встречи"]
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
    assert upload_ui.STATE["stage"] == "idle", "ни один отказ не оставил состояние «в работе»"


def test_start_runs_the_pipeline_once_and_reports(server, monkeypatch):
    base, tmp = server
    calls: list[dict] = []
    release = threading.Event()

    def fake_pipeline(video, **kw):
        calls.append({"video": str(video), **kw})
        release.wait(5)
        return "2026-03-12-kafka-bez-boli"

    monkeypatch.setattr(upload, "pipeline", fake_pipeline)
    monkeypatch.setattr(upload, "load_session", lambda site=None: ("https://site.example.org", {"c": "1"}))
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
        if upload_ui.STATE["stage"] == "done":
            break
        time.sleep(0.05)
    assert upload_ui.STATE["stage"] == "done" and len(calls) == 1


def test_failure_is_shown_not_swallowed(server, monkeypatch):
    base, tmp = server

    def boom(video, **kw):
        raise upload.Step("стек транскрибации не отвечает")

    monkeypatch.setattr(upload, "pipeline", boom)
    post(f"{base}/api/start?t=tok", {"video": str(tmp / "Downloads" / "talk.mp4"), "title": "Норм",
                                     "date": "2026-03-12", "event": "Доклады"})
    for _ in range(50):
        if upload_ui.STATE["stage"] == "error":
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
    monkeypatch.setattr(upload, "stack", lambda cmd: stack_calls.append(cmd))
    monkeypatch.setattr(upload, "stack_health", lambda: {"status": "ok"})
    monkeypatch.setattr(upload, "load_session",
                        lambda site: ("https://site.example.org", {"morag_session": "eyJzdWIi.c2lnbg"}))

    import httpx

    seen: dict = {}

    def fake(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = dict(request.headers)
        if request.url.path == "/api/upload/options":
            return httpx.Response(200, json={"events": [], "llm": {"via_site": True, "path": "/api/upload/llm",
                                                                   "model": "Instruct", "cookie": "morag_session"}})
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(upload, "TRANSPORT", httpx.MockTransport(fake))
    code, body = post(f"{base}/api/llm?t=tok", {})
    assert code == 200 and body == {"via_site": True, "checked": True}

    text = env_file.read_text(encoding="utf-8")
    assert "OR_KEY=eyJzdWIi.c2lnbg" in text, "удостоверение — сама сессия сайта"
    assert "ASR_LLM_BASE_URL=https://site.example.org/api/upload/llm" in text
    assert "ASR_LLM_MODEL=Instruct" in text
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert stack_calls == ["down"]
    assert seen["/api/upload/llm/models"]["authorization"] == "Bearer eyJzdWIi.c2lnbg", "проверка тем же удостоверением"
    assert upload_ui.llm_state() == {"ready": True, "via_site": True,
                                    "base": "https://site.example.org/api/upload/llm"}
    assert get(f"{base}/api/state?t=tok")[1]["llm"]["via_site"] is True



def test_rubric_is_asked_before_the_work_not_after(server, tmp_path):
    """⚠️ Живой случай 24.09: расшифровка и разбор экрана прошли, а сервер отверг манифест — в нём
    не было рубрики. Она решает ветку и год, без неё запись класть некуда; спрашиваем ДО работы."""
    base, tmp = server
    fields = {"video": str(tmp / "Downloads" / "talk.mp4"), "title": "Норм", "date": "2026-03-12"}
    code, body = post(f"{base}/api/start?t=tok", fields)
    assert code == 400 and "рубрик" in body["error"]
    assert upload_ui.STATE["stage"] == "idle", "работа не началась"


def test_reset_clears_the_finished_job_but_not_a_running_one(server):
    base, _ = server
    upload_ui.STATE.update({"stage": "done", "id": "rec", "url": "https://site/rec"})
    upload.LOG.append("строка")
    assert post(f"{base}/api/reset?t=tok", {})[0] == 200
    assert upload_ui.STATE["stage"] == "idle" and not upload.LOG
    upload_ui.STATE.update({"stage": "running"})
    assert post(f"{base}/api/reset?t=tok", {})[0] == 400, "идущую работу не сбрасываем"
    upload_ui.STATE.update({"stage": "idle"})


def test_page_has_the_drop_zone_and_the_native_hook(server):
    """Страница — одна на окно и браузер: зона перетаскивания, поиск файла по имени (браузер)
    и `window.dropVideo` (окно отдаёт настоящий путь).

    ⚠️ Разметка и логика с 24.09 разведены по файлам (`tools/ui/`), поэтому крючок окна ищем в
    модуле, а на странице — что она этот модуль подключает. Иначе проверка молча ослабнет.
    """
    base, _ = server
    body = get(f"{base}/")[1]
    assert "Перетащите сюда запись" in body
    assert '/ui/app.js' in body, "страница подключает свой модуль"
    app = (Path(upload_ui.UI) / "app.js").read_text(encoding="utf-8")
    assert "window.dropVideo" in app, "окно зовёт эту функцию с путём файла"
    assert "title_auto" in app, "страница говорит серверу, что название подставлено из имени файла"


def test_everything_the_page_links_to_exists_and_is_served(server):
    """⚠️ Окно ставится коллеге с зеркала и работает ОФЛАЙН: мёртвая ссылка на стиль или шрифт
    там не «некрасиво», а нечитаемая страница без единой ошибки в консоли, которую никто не
    увидит. Поэтому каждая ссылка страницы проверяется файлом И живым ответом сервера."""
    import re

    base, _ = server
    page = Path(upload_ui.PAGE).read_text(encoding="utf-8")
    links = re.findall(r'(?:href|src)="(/ui/[^"]+)"', page)
    assert links, "страница обязана ссылаться на свои стили и модуль"
    for link in links:
        assert (Path(upload_ui.UI) / link[4:]).is_file(), f"{link} — файла нет"
        code, body, ctype = raw(f"{base}{link}")
        assert code == 200 and body, f"{link} — сервер не отдал"
        assert ctype.split(";")[0] in ("text/css", "text/javascript"), ctype

    # Шрифты подключает не страница, а `fonts.css` — их проверяем отдельно, тем же правилом.
    css = (Path(upload_ui.UI) / "fonts.css").read_text(encoding="utf-8")
    fonts = re.findall(r'url\("\./([^"]+)"\)', css)
    assert len(set(fonts)) == 6, fonts
    for name in set(fonts):
        assert raw(f"{base}/ui/{name}")[0] == 200, name


def test_window_falls_back_to_the_browser_without_pyobjc(monkeypatch):
    """Нет PyObjC (не мак, урезанный питон) — не отказ, а прежняя страница в браузере."""
    import upload_app

    monkeypatch.setitem(sys.modules, "Cocoa", None)
    monkeypatch.setattr(upload_app, "available", lambda: False)
    called = {}

    def fake_serve(port, open_browser):
        called["port"] = port
        called["browser"] = open_browser
        return 0

    monkeypatch.setattr(upload_ui, "serve", fake_serve)
    sys.argv = ["upload.py", "app", "--port", "8123"]
    assert upload.main() == 0
    assert called == {"port": 8123, "browser": True}


def test_the_event_feed_is_cheap_and_cursored(server, monkeypatch):
    """⚠️ Эту ручку опрашивают раз в секунду весь прогон — она не имеет права ходить в сеть.

    Проверяем буквально: ломаем ВСЁ, что делает дорогой опрос состояния (стек, сайт, обход
    каталогов), и лента обязана отвечать как ни в чём не бывало.
    """
    base, tmp = server

    def boom(*a, **kw):
        raise AssertionError("лента событий полезла в сеть")

    monkeypatch.setattr(upload, "stack_health", boom)
    monkeypatch.setattr(upload_ui, "site_state", boom)
    monkeypatch.setattr(upload_ui, "videos", boom)

    upload.EVENTS.clear()
    upload._SEQ[0] = 0
    upload.emit("stage.start", stage="diarize")
    upload.emit("chunk.done", i=1, raw="привет")

    code, body = get(f"{base}/api/events?t=tok")
    assert code == 200
    assert [e["t"] for e in body["events"]] == ["stage.start", "chunk.done"]
    assert body["cursor"] == 2 and body["stage"] == "idle"

    assert get(f"{base}/api/events?since=1&t=tok")[1]["events"] == body["events"][1:]
    assert get(f"{base}/api/events?since=99&t=tok")[1] == {"events": [], "cursor": 99, "stage": "idle"}
    assert get(f"{base}/api/events")[0] == 403, "лента за токеном, как и остальное api"


def test_static_of_the_page_is_served_without_a_token_but_not_beyond_its_folder(server):
    """Стили и шрифты отдаём без токена — данных в них нет, как и в самой странице. А вот выйти
    из каталога нельзя: сервер слушает localhost, и постучаться может любая вкладка."""
    base, tmp = server
    ui = Path(upload_ui.UI)
    ui.mkdir(exist_ok=True)
    (ui / "probe.css").write_text(":root{--x:1}", encoding="utf-8")
    try:
        code, body, ctype = raw(f"{base}/ui/probe.css")
        assert code == 200 and b"--x" in body and ctype.startswith("text/css")
        assert raw(f"{base}/ui/../upload.py")[0] == 404
        assert raw(f"{base}/ui/nope.css")[0] == 404
        assert raw(f"{base}/ui/")[0] == 404
    finally:
        (ui / "probe.css").unlink(missing_ok=True)
