"""Страница вместо командной строки: локальный сервер на 127.0.0.1 и браузер поверх `upload.py`.

Зачем. Конвейер тот же (`upload.pipeline`), но человеку, который раз в месяц выкладывает свой
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

Запуск — `upload.py ui` (или `morag-upload ui`), установщик делает ярлык для Finder.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import upload

# Признак «ходим в шлюз через сайт» — путь его ручки (`app/api/llm.py`).
SITE_LLM_PATH = upload.SITE_LLM_PATH

HERE = Path(__file__).resolve().parent
PAGE = HERE / "upload-ui.html"
UI = HERE / "ui"                       # стили, шрифты и сцены страницы
TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml",
         ".html": "text/html; charset=utf-8", ".jsonl": "application/x-ndjson; charset=utf-8"}

# ⚠️ Опрос состояния делает ДОРОГИЕ вещи: ходит в локальный стек, дважды в корпоративный сайт и
# обходит три каталога с видео. На двадцатиминутном прогоне при опросе раз в две секунды это
# шестьсот походов в сеть. Ответы кэшируем, а список видео во время работы не собираем вовсе —
# посреди прогона файл не выбирают.
_CACHE: dict[str, tuple[float, object]] = {}


def cached(key: str, ttl: float, fn):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _CACHE[key] = (now, value)
    return value
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
                if not f.is_file() or f.suffix.lower().lstrip(".") not in upload.VIDEO_EXT or f in seen:
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
        site, cookies = upload.load_session(None)
    except upload.Step:
        return {"site": "", "logged": False, "events": []}
    out = {"site": site, "logged": bool(cookies), "events": []}
    try:
        with upload.client(site, cookies, timeout=10) as c:
            me = c.get("/api/auth/me")
            out["logged"] = me.status_code == 200 or not cookies
            if me.status_code == 200:
                out["who"] = me.json().get("name") or me.json().get("login")
            options = c.get("/api/upload/options")
            if options.status_code == 200:
                out["events"] = options.json().get("events") or []
            elif options.status_code in (401, 403):
                out["logged"] = options.status_code != 401
                out["note"] = "загрузка на сайте выключена" if options.status_code == 403 else ""
    except Exception as error:  # сайт недоступен — страница об этом скажет, а не промолчит
        out["error"] = str(error)[:200]
    return out


def llm_state() -> dict:
    """Чем стек ходит в шлюз: через сайт (сессией), своим ключом — или ничем.

    ⚠️ «Через сайт» узнаём по адресу, а не по флагу в своём файле: флаг разъехался бы с тем,
    что на самом деле написано в окружении стека, и приложение врало бы про готовность.
    """
    base = upload.stack_env_value("ASR_LLM_BASE_URL")
    key = upload.stack_env_value("OR_KEY")
    return {"ready": bool(base and key), "via_site": base.endswith(SITE_LLM_PATH), "base": base}


def save_key(key: str) -> dict:
    """Запасной ход: свой ключ к шлюзу (кто гоняет стек без сайта или хочет свой расход).

    ⚠️ Вместе с ключом возвращаем и АДРЕС самого шлюза: если до этого стек ходил через сайт,
    в окружении лежит наш путь, и чужой ключ к нему не подойдёт — вышло бы «ключ вписан, а
    ничего не работает». Адрес берём из настроек корпуса, привезённых установщиком.
    """
    key = key.strip()
    if not key:
        raise upload.Step("пустой ключ")
    values = {"OR_KEY": key}
    base = upload.stack_env_value("ASR_LLM_BASE_URL")
    direct = gateway_from_mirror()
    if direct and base.endswith(SITE_LLM_PATH):
        values["ASR_LLM_BASE_URL"] = direct
    upload.set_stack_env(**values)
    checked = False
    base = values.get("ASR_LLM_BASE_URL", base)
    if base:
        try:
            with upload.client(base, {}, timeout=15) as c:
                checked = c.get("/models", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        except Exception:  # noqa: BLE001 — шлюз недоступен: ключ всё равно сохранён
            checked = False
    if upload.stack_health():
        upload.stack("down")
    return {"ok": True, "checked": checked}


def gateway_from_mirror() -> str:
    """Адрес корпоративного шлюза, привезённый установщиком (`~/morag-upload/gateway.env`).
    Нужен только запасному ходу «у меня свой ключ»."""
    path = Path(os.environ.get("MORAG_UPLOAD_HOME") or (Path.home() / "morag-upload")) / "gateway.env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, value = line.strip().removeprefix("export ").partition("=")
        if name.strip() == "ASR_LLM_BASE_URL":
            return value.strip().strip('"').strip("'")
    return ""


def choose() -> dict:
    """Системный диалог выбора файла — единственный способ узнать ПУТЬ из браузера.

    ⚠️ `<input type=file>` здесь бесполезен: браузер отдаёт содержимое и имя, но НЕ путь, а
    конвейеру нужен файл на диске. Диалог показывает СЕРВЕР (он и так на этой машине) —
    `osascript`, штатный выбор файла macOS.
    """
    if sys.platform != "darwin":
        raise upload.Step("системный диалог есть только на macOS — вставьте путь в поле")
    # ⚠️ `tell me to activate`, а НЕ через "System Events": обращение к чужому приложению
    # macOS спрашивает отдельным разрешением на автоматизацию, и без него диалог не откроется вовсе.
    script = ('tell me to activate\n'
              'set f to choose file with prompt "Выберите видеозапись" of type {"public.movie"}\n'
              'POSIX path of f')
    try:
        done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise upload.Step(f"диалог не открылся: {error}") from error
    if done.returncode != 0:
        # Отказ человека — не ошибка: страница просто остаётся как была.
        if "-128" in done.stderr or "canceled" in done.stderr.lower():
            return {"cancelled": True}
        raise upload.Step(done.stderr.strip()[:200] or "диалог не вернул файл")
    return by_path(done.stdout.strip())


def by_path(raw: str) -> dict:
    """Файл по ПУТИ, вписанному руками или брошенному в поле.

    ⚠️ Без этого страница в БРАУЗЕРЕ была тупиком для всякого, чьё видео лежит не в
    Загрузках, не на Рабочем столе и не в Movies: брошенный файл браузер отдаёт БЕЗ ПУТИ
    (только имя и размер), и сопоставить его было не с чем. В нативном окне путь есть
    (`window.dropVideo`), в браузере — нет, и это не лечится ничем, кроме поля.
    """
    raw = raw.strip().strip('"').strip("'")
    if raw.startswith("file://"):
        from urllib.parse import unquote, urlparse as _u
        raw = unquote(_u(raw).path)
    if not raw:
        raise upload.Step("путь пустой")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise upload.Step("нужен полный путь, от корня")
    # ⚠️ «Нет файла» и «не дают смотреть» — разные беды, а `is_file()` обе отдаёт как False. macOS
    # закрывает Загрузки, Рабочий стол и Документы от программ, которым человек этого не разрешал,
    # и тогда совет «проверьте путь» уводит совсем не туда.
    try:
        ok = path.is_file()
    except PermissionError:
        ok = False
    if not ok:
        try:
            path.stat()
        except PermissionError:
            raise upload.Step(
                f"macOS не даёт читать {path.parent} — разрешите доступ тому, откуда запущено "
                f"приложение (Настройки → Конфиденциальность → Файлы и папки), либо положите видео в другую папку") from None
        except FileNotFoundError:
            pass
        raise upload.Step(f"нет такого файла: {path}")
    if path.suffix.lower().lstrip(".") not in upload.VIDEO_EXT:
        raise upload.Step(f"не видео: нужен {', '.join(sorted(upload.VIDEO_EXT))}")
    st = path.stat()
    return {"path": str(path), "name": path.name, "size": st.st_size, "mtime": st.st_mtime,
            "folder": path.parent.name}


def start(fields: dict) -> dict:
    """Запустить конвейер в потоке. Второй запуск, пока идёт первый, отклоняется: одна машина —
    одна расшифровка (стек всё равно последователен)."""
    with LOCK:
        if STATE["stage"] not in ("idle", "done", "error"):
            raise upload.Step("одна запись уже в работе — дождитесь конца")
        video = Path(fields.get("video") or "").expanduser()
        checked = upload.check_fields(video, fields.get("title") or "", fields.get("date") or "",
                                      fields.get("slides") or None)
        # ⚠️ Рубрику спрашиваем ЗДЕСЬ, до двадцати минут расшифровки: сервер без неё запись не
        # примет (она решает ветку и год), и узнавать об этом в самом конце — обидно.
        # Ловилось на первой живой загрузке 24.09. Порядок проверок — от файла к полям: человек
        # только что бросил видео, и про него он думает первым.
        if (site_state().get("events") or []) and not (fields.get("event") or "").strip():
            raise upload.Step("выберите рубрику — она решает, в какую ветку и год ляжет запись")
        STATE.update({"stage": "running", "id": checked["id"], "error": "", "url": "",
                      "started": time.time(), "finished": 0.0})
        upload.LOG.clear()

    def work() -> None:
        try:
            upload.pipeline(
                video,
                title=fields["title"], date=fields["date"], event=fields.get("event") or "",
                speakers=[s.strip() for s in (fields.get("speakers") or "").split(",") if s.strip()],
                tags=[t.strip() for t in (fields.get("tags") or "").split(",") if t.strip()],
                summary=fields.get("summary") or "", slides=fields.get("slides") or None,
                with_stack=bool(fields.get("stack", True)), with_screen=not fields.get("no_screen"),
                wait=True, title_auto=bool(fields.get("title_auto")))
            site, _ = upload.load_session(None)
            STATE.update({"stage": "done", "finished": time.time(), "search": upload.LAST_SEARCH,
                          "url": f"{site}/{fields.get('slug', '')}".rstrip("/")})
        except upload.Step as error:
            STATE.update({"stage": "error", "error": str(error), "finished": time.time()})
            upload.say(f"⚠️ {error}")
        except Exception as error:
            STATE.update({"stage": "error", "error": str(error)[:400], "finished": time.time()})
            upload.say(f"⚠️ {type(error).__name__}: {error}")

    threading.Thread(target=work, daemon=True, name="upload").start()
    return {"id": STATE["id"]}


class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, *args) -> None:  # тишина: свой лог ведёт `upload.say`
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

    def _static(self, name: str) -> None:
        """Файл из `tools/ui/`. ⚠️ Белый список расширений и проверка, что путь НЕ вышел из
        каталога: `..` в адресе иначе отдаёт что угодно с диска, а сервер слушает localhost, куда
        может постучаться любая вкладка. Токен здесь не спрашиваем — в стилях и шрифтах данных
        нет, как и в самой странице."""
        if not name or ".." in name:
            self._json({"error": "нет такого"}, 404)
            return
        path = (UI / name).resolve()
        if UI.resolve() not in path.parents or path.suffix not in TYPES or not path.is_file():
            self._json({"error": "нет такого"}, 404)
            return
        self._send(200, path.read_bytes(), TYPES[path.suffix])

    # --- маршруты ------------------------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            return
        if url.path.startswith("/ui/"):
            self._static(url.path[4:])
            return
        if not self._guard(query):
            return
        if url.path == "/api/state":
            running = STATE.get("stage") == "running"
            self._json({"job": dict(STATE), "log": upload.LOG[-200:],
                        "stack": bool(cached("stack", 5.0, upload.stack_health)),
                        "site": cached("site", 30.0, site_state),
                        "videos": [] if running else cached("videos", 5.0, videos),
                        "llm": cached("llm", 30.0, llm_state),
                        "home": str(upload.HOME), "ext": list(upload.VIDEO_EXT)})
            return
        if url.path == "/api/events":
            # Только память: эту ручку опрашивают часто, и она не имеет права ходить в сеть.
            since = int((query.get("since") or ["0"])[0] or 0)
            items, cursor = upload.events_since(since)
            self._json({"events": items, "cursor": cursor, "stage": STATE.get("stage", "idle")})
            return
        self._json({"error": "нет такого"}, 404)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._guard(query):
            return
        # ⚠️ Любое действие страницы делает кэш сводок неправдой: человек нажал «войти» или
        # «свой ключ» и в следующий же опрос ждёт увидеть НОВОЕ состояние, а не тридцатисекундной
        # давности. Кэш тут ради частого опроса, а не ради экономии на действиях.
        _CACHE.clear()
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"error": "не JSON"}, 400)
            return
        try:
            if url.path == "/api/login":
                site = (body.get("site") or "").rstrip("/")
                with upload.client(site, {}) as c:
                    r = c.post("/api/auth/login", json={"login": body.get("login"), "password": body.get("password")})
                    if r.status_code != 200:
                        self._json({"error": r.json().get("detail", f"вход не удался ({r.status_code})")}, 400)
                        return
                    upload.save_session(site, {k: v for k, v in c.cookies.items()})
                # Вошли — значит ключ больше не нужен: стадии с LLM пойдут через сайт этой же
                # сессией. Не получилось (сайт так не умеет) — не беда, скажем в настройках.
                try:
                    self._json({"ok": True, "llm": upload.use_site_llm()})
                except upload.Step as error:
                    self._json({"ok": True, "llm": {"via_site": False, "error": str(error)}})
                return
            if url.path == "/api/pick":
                self._json(choose())
                return
            if url.path == "/api/file":
                self._json(by_path(str(body.get("path") or "")))
                return
            if url.path == "/api/start":
                self._json(start(body))
                return
            if url.path == "/api/stack":
                upload.stack("up" if body.get("up") else "down")
                self._json({"ok": True})
                return
            if url.path == "/api/llm":
                self._json(upload.use_site_llm())
                return
            if url.path == "/api/key":
                self._json(save_key(str(body.get("key") or "")))
                return
            if url.path == "/api/reset":
                if STATE["stage"] == "running":
                    self._json({"error": "идёт работа"}, 400)
                    return
                STATE.update({"stage": "idle", "id": "", "error": "", "url": ""})
                upload.LOG.clear()
                self._json({"ok": True})
                return
        except upload.Step as error:
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
    threading.Thread(target=server.serve_forever, daemon=True, name="upload-ui").start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/?t={Handler.token}"


def serve(port: int = 8099, open_browser: bool = True) -> int:
    """Страница в браузере (запасной путь; основной — окно, `upload_app.py`)."""
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
