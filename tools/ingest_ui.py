"""Страница вместо командной строки: локальный сервер на 127.0.0.1 и браузер поверх `ingest.py`.

Зачем. Конвейер тот же (`ingest.pipeline`), но человеку, который раз в месяц выкладывает свой
доклад, командная строка — барьер: флаги, кавычки, путь к файлу. Страница спрашивает то же
самое полями, показывает ход работы и ссылку в конце.

Что здесь есть и чего нет:
  * видео НЕ загружается в браузер — страница показывает список видеофайлов из ваших папок
    (Загрузки, Рабочий стол, Movies) и путь можно вписать руками. Копировать гигабайты через
    localhost незачем: конвейер читает файл с диска сам;
  * работа идёт В ПОТОКЕ, страница опрашивает состояние — закрыли вкладку, работа продолжается;
  * ⚠️ сервер слушает ТОЛЬКО петлю и требует токен, выданный при старте (он в адресе). Иначе
    любая открытая в том же браузере страница могла бы постучаться на наш порт и запустить
    загрузку чужого файла на сайт: localhost от чужих вкладок сам по себе не защищён.

Запуск — `ingest.py ui` (или `morag-ingest ui`), установщик делает ярлык для Finder.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ingest

HERE = Path(__file__).resolve().parent
PAGE = HERE / "ingest-ui.html"
# Где искать видео: обычные папки Mac плюс то, что укажут переменной. Глубина — два уровня:
# «Загрузки/Встречи/доклад.mp4» встречается, а сканировать весь диск незачем.
FOLDERS = [Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Movies"]
DEPTH = 2
LIMIT = 60

STATE: dict = {"stage": "idle", "id": "", "error": "", "url": "", "started": 0.0, "finished": 0.0}
LOCK = threading.Lock()


def videos() -> list[dict]:
    """Видеофайлы из обычных папок, свежие сверху: их и выбирают глазами."""
    out: list[dict] = []
    seen: set[Path] = set()
    for folder in FOLDERS:
        if not folder.is_dir():
            continue
        for path in folder.glob("*"):
            paths = [path] if path.is_file() else (list(path.glob("*")) if DEPTH > 1 and path.is_dir() else [])
            for f in paths:
                if not f.is_file() or f.suffix.lower().lstrip(".") not in ingest.VIDEO_EXT or f in seen:
                    continue
                seen.add(f)
                stat = f.stat()
                out.append({"path": str(f), "name": f.name, "folder": folder.name,
                            "size": stat.st_size, "mtime": stat.st_mtime})
    out.sort(key=lambda v: -v["mtime"])
    return out[:LIMIT]


def site_state() -> dict:
    """Сайт, сессия и его подсказки (рубрики). Нет сессии — страница покажет вход."""
    try:
        site, cookies = ingest.load_session(None)
    except ingest.Step:
        return {"site": "", "logged": False, "events": []}
    out = {"site": site, "logged": bool(cookies), "events": []}
    try:
        with ingest.client(site, cookies, timeout=10) as c:
            me = c.get("/api/auth/me")
            out["logged"] = me.status_code == 200 or not cookies
            if me.status_code == 200:
                out["who"] = me.json().get("name") or me.json().get("login")
            options = c.get("/api/ingest/options")
            if options.status_code == 200:
                out["events"] = options.json().get("events") or []
            elif options.status_code in (401, 403):
                out["logged"] = options.status_code != 401
                out["note"] = "загрузка на сайте выключена" if options.status_code == 403 else ""
    except Exception as error:  # сайт недоступен — страница об этом скажет, а не промолчит
        out["error"] = str(error)[:200]
    return out


def gateway_key() -> str:
    """Ключ шлюза, как его видят стек и инструменты: окружение, иначе файл стека."""
    if os.environ.get("OR_KEY"):
        return os.environ["OR_KEY"]
    path = Path(os.environ.get("ASR_STACK_ENV") or (Path.home() / ".asr-stack.env")).expanduser()
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, value = line.strip().removeprefix("export ").partition("=")
        if name.strip() == "OR_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def save_key(key: str) -> dict:
    """Записать ключ в файл стека (0600) и проверить его одним коротким запросом к шлюзу.

    ⚠️ Файл читает АДАПТЕР при старте: ключ, вписанный на ходу, подхватится только после
    перезапуска стека — поэтому гасим его здесь же, следующий прогон поднимет заново.
    """
    key = key.strip()
    if not key:
        raise ingest.Step("пустой ключ")
    path = Path(os.environ.get("ASR_STACK_ENV") or (Path.home() / ".asr-stack.env")).expanduser()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for i, line in enumerate(lines):
        if line.strip().removeprefix("export ").startswith("OR_KEY="):
            lines[i] = f"OR_KEY={key}"
            break
    else:
        lines.append(f"OR_KEY={key}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    os.environ["OR_KEY"] = key
    checked = False
    base = os.environ.get("ASR_LLM_BASE_URL") or ""
    if not base and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.strip().removeprefix("export ").partition("=")
            if name.strip() == "ASR_LLM_BASE_URL":
                base = value.strip().strip('"').strip("'")
    if base:
        try:
            with ingest.client(base, {}, timeout=15) as c:
                checked = c.get("/models", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        except Exception:  # noqa: BLE001 — шлюз недоступен: ключ всё равно сохранён
            checked = False
    if ingest.stack_health():
        ingest.stack("down")   # чтобы адаптер перечитал ключ при следующем подъёме
    return {"ok": True, "checked": checked}


def start(fields: dict) -> dict:
    """Запустить конвейер в потоке. Второй запуск, пока идёт первый, отклоняется: одна машина —
    одна расшифровка (стек всё равно последователен)."""
    with LOCK:
        if STATE["stage"] not in ("idle", "done", "error"):
            raise ingest.Step("одна запись уже в работе — дождитесь конца")
        video = Path(fields.get("video") or "").expanduser()
        checked = ingest.check_fields(video, fields.get("title") or "", fields.get("date") or "",
                                      fields.get("slides") or None)
        STATE.update({"stage": "running", "id": checked["id"], "error": "", "url": "",
                      "started": time.time(), "finished": 0.0})
        ingest.LOG.clear()

    def work() -> None:
        try:
            ingest.pipeline(
                video,
                title=fields["title"], date=fields["date"], event=fields.get("event") or "",
                speakers=[s.strip() for s in (fields.get("speakers") or "").split(",") if s.strip()],
                tags=[t.strip() for t in (fields.get("tags") or "").split(",") if t.strip()],
                summary=fields.get("summary") or "", slides=fields.get("slides") or None,
                with_stack=bool(fields.get("stack", True)), with_screen=not fields.get("no_screen"),
                wait=True, title_auto=bool(fields.get("title_auto")))
            site, _ = ingest.load_session(None)
            STATE.update({"stage": "done", "finished": time.time(),
                          "url": f"{site}/{fields.get('slug', '')}".rstrip("/")})
        except ingest.Step as error:
            STATE.update({"stage": "error", "error": str(error), "finished": time.time()})
            ingest.say(f"⚠️ {error}")
        except Exception as error:
            STATE.update({"stage": "error", "error": str(error)[:400], "finished": time.time()})
            ingest.say(f"⚠️ {type(error).__name__}: {error}")

    threading.Thread(target=work, daemon=True, name="ingest").start()
    return {"id": STATE["id"]}


class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, *args) -> None:  # тишина: свой лог ведёт `ingest.say`
        pass

    # --- служебное -----------------------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _guard(self, query: dict) -> bool:
        """Токен из адреса + запрет чужого Origin: страница со стороннего сайта не должна уметь
        дёргать наш порт (браузер шлёт Origin на POST, и он будет чужим)."""
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            self._json({"error": "чужой origin"}, 403)
            return False
        given = (query.get("t") or [""])[0] or self.headers.get("X-Token", "")
        if given != self.token:
            self._json({"error": "нужен токен из адреса, который напечатал запуск"}, 403)
            return False
        return True

    # --- маршруты ------------------------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            return
        if not self._guard(query):
            return
        if url.path == "/api/state":
            self._json({"job": dict(STATE), "log": ingest.LOG[-200:], "stack": bool(ingest.stack_health()),
                        "site": site_state(), "videos": videos(), "key": bool(gateway_key()),
                        "home": str(ingest.HOME), "ext": list(ingest.VIDEO_EXT)})
            return
        self._json({"error": "нет такого"}, 404)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._guard(query):
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"error": "не JSON"}, 400)
            return
        try:
            if url.path == "/api/login":
                site = (body.get("site") or "").rstrip("/")
                with ingest.client(site, {}) as c:
                    r = c.post("/api/auth/login", json={"login": body.get("login"), "password": body.get("password")})
                    if r.status_code != 200:
                        self._json({"error": r.json().get("detail", f"вход не удался ({r.status_code})")}, 400)
                        return
                    ingest.save_session(site, {k: v for k, v in c.cookies.items()})
                self._json({"ok": True})
                return
            if url.path == "/api/start":
                self._json(start(body))
                return
            if url.path == "/api/stack":
                ingest.stack("up" if body.get("up") else "down")
                self._json({"ok": True})
                return
            if url.path == "/api/key":
                self._json(save_key(str(body.get("key") or "")))
                return
            if url.path == "/api/reset":
                if STATE["stage"] == "running":
                    self._json({"error": "идёт работа"}, 400)
                    return
                STATE.update({"stage": "idle", "id": "", "error": "", "url": ""})
                ingest.LOG.clear()
                self._json({"ok": True})
                return
        except ingest.Step as error:
            self._json({"error": str(error)}, 400)
            return
        except Exception as error:
            self._json({"error": f"{type(error).__name__}: {error}"[:300]}, 500)
            return
        self._json({"error": "нет такого"}, 404)


def start_server(port: int = 8099) -> tuple[ThreadingHTTPServer, str]:
    """Поднять локальный сервер в отдельном потоке и вернуть его и адрес страницы с токеном.
    Порт занят — берём любой свободный: второе окно не должно падать на первом."""
    Handler.token = secrets.token_urlsafe(16)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="ingest-ui").start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/?t={Handler.token}"


def serve(port: int = 8099, open_browser: bool = True) -> int:
    """Страница в браузере (запасной путь; основной — окно, `ingest_app.py`)."""
    server, url = start_server(port)
    # ⚠️ `flush`: вывод в файл (запуск из `.command`, ярлыка, launchd) буферизуется, а процесс
    # потом спит часами — адрес со своим токеном не появлялся бы нигде вовсе.
    print(f"страница загрузки записи: {url}\n(закрыть — Ctrl+C; работа продолжается, пока открыто это окно)",
          flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print()
    finally:
        server.shutdown()
        server.server_close()
    return 0
