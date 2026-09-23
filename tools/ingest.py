#!/usr/bin/env python3
"""Своя запись — на сайт: транскрибация и экран у себя на Mac, сборка и индексация на сервере.

    python3 tools/ingest.py login --site https://site.example.org
    python3 tools/ingest.py run talk.mp4 --title "Kafka без боли" --date 2026-03-12 \\
        [--event "Доклады"] [--speakers "Мария Кузнецова"] [--tags kafka,streams] \\
        [--summary "О чём доклад"] [--slides deck.pdf] [--no-screen] [--stack] [--no-wait]
    python3 tools/ingest.py status 2026-03-12-kafka-bez-boli

Что происходит (`run`), по шагам, каждый — с возобновлением: упало на третьем — второй раз
начнётся с третьего (состояние — `~/morag-ingest/<id>/state.json`):

  1. стек транскрибации у вас на машине (`morag/services/asr-adaptor/deploy/mac/stack.sh`,
     ставится `tools/ingest-install.sh`): звук вынимает ffmpeg, адаптер гонит диаризацию,
     whisper и LLM-стадии через ВАШ ключ (`~/.asr-stack.env`) → `artifact.json`;
  2. экран из видео (`--no-screen` пропускает): шкала слайдов, заставка, описания кадров
     Vision-моделью, обращения «вот здесь», аннотации — инструменты `tools/*.py` под
     `~/asr-stack/video-venv`; для них запись собирается ВО ВРЕМЕННОЙ семье с пустыми словарями
     (`~/morag-ingest/family`) — на сайт эта сборка не едет, едет сырой артефакт;
  3. пакет уезжает на сайт под вашей учёткой (`/api/ingest`): манифест, артефакт, сайдкары
     экрана, кадры, слайды, видео (последним, с прогрессом); сервер собирает запись с
     настоящими словарями и раскладкой, индексирует и отвечает адресом.

Голоса в записи приедут безымянными (`Speaker_N`): у вашей машины свой реестр голосов, и на
сайте их называют потом — «Это я» у своего голоса, карточка голоса у остальных. Категория и
темы — позже, обычной разметкой корпуса.

Зависимости: python3.10+, ffmpeg, httpx (есть в `video-venv`). Стек — `ingest-install.sh`.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover — подсказка вместо трейсбека
    sys.exit("нужен httpx: ~/asr-stack/video-venv/bin/python tools/ingest.py … (или pip install httpx)")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from make_record import slugify  # noqa: E402

HOME = Path(os.environ.get("MORAG_INGEST_HOME") or (Path.home() / "morag-ingest"))
SESSION = Path.home() / ".morag-ingest" / "session.json"
ASR_BASE = os.environ.get("ASR_BASE", "http://127.0.0.1:8082")
STACK_HOME = Path(os.environ.get("ASR_STACK_HOME") or (Path.home() / "asr-stack"))
STACK_ENV = Path(os.environ.get("ASR_STACK_ENV") or (Path.home() / ".asr-stack.env"))
VIDEO_PY = STACK_HOME / "video-venv" / "bin" / "python"
MORAG_REPO = Path(os.environ.get("MORAG_REPO") or (REPO.parent / "morag"))
VIDEO_EXT = ("mp4", "webm", "mov", "mkv")
SIDECARS = ("record.slides.json", "record.refs.json", "record.annotations.json")
POLL_SEC = 15


class Step(Exception):
    """Шаг не прошёл: причина для человека, без трейсбека."""


# Хвост сообщений — для страницы (`ingest_ui.py`): те же строки, что в терминале. Кольцо, а не
# файл: страница показывает ход работы, а разбор потом — в терминале.
LOG: list[str] = []


def size_of(n: int) -> str:
    """Человеческий размер: файл на 900 КБ не должен печататься как «0 МБ»."""
    return f"{n / 1e9:.1f} ГБ" if n >= 1e9 else (f"{round(n / 1e6)} МБ" if n >= 1e6 else f"{max(1, round(n / 1e3))} КБ")


def say(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.append(line)
    del LOG[:-400]


# --- файл стека ----------------------------------------------------------------------------

# Путь ручки сайта, через которую стадии с LLM ходят в корпоративный шлюз (`app/api/llm.py`):
# сервер называет его сам в `/api/ingest/options`, здесь — запасное значение.
SITE_LLM_PATH = "/api/ingest/llm"

def stack_env_path() -> Path:
    """Файл стека — ПО ЗОВУ, а не по импорту: окружение приложению задаёт запускатор, а тесты
    и терминал подменяют его на ходу; константа, прочитанная при импорте, писала бы не туда."""
    return Path(os.environ.get("ASR_STACK_ENV") or STACK_ENV).expanduser()


def set_stack_env(**values: str) -> Path:
    """Вписать значения в файл стека (0600), заполняя СУЩЕСТВУЮЩИЕ строки, а не дописывая.

    ⚠️ Два присваивания одного имени в одном файле — классическая тихая беда: побеждает
    последнее, и правка верхней строки ни на что не влияет (ловилось в установщике морага).
    ⚠️ Файл читает АДАПТЕР при старте: вписанное на ходу подхватится только после перезапуска
    стека — гасить его должен зовущий.
    """
    path = stack_env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for name, value in values.items():
        for i, line in enumerate(lines):
            if line.strip().removeprefix("export ").split("=", 1)[0].strip() == name:
                lines[i] = f"{name}={value}"
                break
        else:
            lines.append(f"{name}={value}")
        os.environ[name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def stack_env_value(name: str) -> str:
    """Значение из окружения, иначе из файла стека."""
    if os.environ.get(name):
        return os.environ[name]
    path = stack_env_path()
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().removeprefix("export ").partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


# --- сессия сайта --------------------------------------------------------------------------

def load_session(site: str | None) -> tuple[str, dict[str, str]]:
    """Адрес сайта и cookie. Порядок: сохранённая сессия → аргумент → `MORAG_SITE` из окружения.

    ⚠️ Последнее — не мелочь. Установка с сайта (`install-mac.sh`) знает его адрес и кладёт в
    окружение; без этого шага человек, поставивший приложение по ссылке с сайта, должен был бы
    ВПИСАТЬ адрес этого же сайта руками. Ловилось на чистой установке.
    """
    if SESSION.is_file():
        data = json.loads(SESSION.read_text(encoding="utf-8"))
        if not site or site == data.get("site"):
            return data["site"], data.get("cookies") or {}
    site = site or os.environ.get("MORAG_SITE") or ""
    if not site:
        raise Step("не знаю адрес сайта: сначала `ingest.py login --site …`")
    return site.rstrip("/"), {}


def save_session(site: str, cookies: dict[str, str]) -> None:
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps({"site": site, "cookies": cookies}), encoding="utf-8")
    SESSION.chmod(0o600)


TRANSPORT: httpx.BaseTransport | None = None   # тесты подменяют сайт и адаптер


def client(site: str, cookies: dict[str, str], timeout=60.0) -> httpx.Client:
    return httpx.Client(base_url=site, cookies=cookies, timeout=timeout, follow_redirects=False, transport=TRANSPORT)


def cmd_login(args: argparse.Namespace) -> int:
    site = (args.site or load_session(None)[0]).rstrip("/")
    with client(site, {}) as c:
        state = c.get("/api/auth/state").json()
        if not state.get("enabled"):
            save_session(site, {})
            say(f"{site}: вход выключен — сессия не нужна")
            return 0
        login = args.login or input("логин: ").strip()
        password = getpass.getpass("пароль: ")
        r = c.post("/api/auth/login", json={"login": login, "password": password})
        if r.status_code != 200:
            raise Step(f"вход не удался ({r.status_code}): {r.json().get('detail', r.text)}")
        cookies = {k: v for k, v in c.cookies.items()}
        me = r.json()
    save_session(site, cookies)
    say(f"вошли как {me.get('name') or login} ({me.get('role')}), сессия — {SESSION}")
    # Ключ к шлюзу спрашивать не надо: сайт умеет ходить туда за нас этой же сессией.
    try:
        out = use_site_llm()
        say("стадии с ИИ пойдут через сайт — ключ не нужен" if out.get("via_site")
            else "сайт не ходит в шлюз за вас: впишите свой ключ в OR_KEY файла стека")
    except Step as error:
        say(f"шлюз через сайт не настроился ({error}) — можно вписать свой ключ в OR_KEY")
    return 0


def use_site_llm() -> dict:
    """Ключ не спрашиваем: пусть стадии с LLM ходят в шлюз ЧЕРЕЗ САЙТ, а удостоверением служит
    та же сессия, которой человек только что вошёл.

    Что кладём в файл стека: адрес ручки сайта, модель (её называет сервер) и строку сессии как
    `OR_KEY` — стек умеет только `Authorization: Bearer <строка>`, и этого достаточно. Ключ
    корпоративного шлюза остаётся на сервере; здесь его нет вовсе.
    """
    site, cookies = load_session(None)
    with client(site, cookies, timeout=20) as c:
        r = c.get("/api/ingest/options")
        if r.status_code != 200:
            raise Step(f"сайт не ответил про загрузку ({r.status_code}) — войдите заново")
        llm = (r.json() or {}).get("llm") or {}
        if not llm.get("via_site"):
            return {"via_site": False}
        name = llm.get("cookie") or ""
        token = cookies.get(name) or (next(iter(cookies.values())) if len(cookies) == 1 else "")
        if not token:
            raise Step("не нашёл сессию сайта — войдите заново")
        set_stack_env(ASR_LLM_BASE_URL=site + llm.get("path", SITE_LLM_PATH),
                      ASR_LLM_MODEL=llm.get("model") or "", OR_KEY=token)
        checked = c.get(f"{llm.get('path', SITE_LLM_PATH)}/models",
                        headers={"Authorization": f"Bearer {token}"}).status_code == 200
    if stack_health():
        stack("down")   # адаптер читает файл при старте
    return {"via_site": True, "checked": checked}


# --- шаг 1: транскрибация ------------------------------------------------------------------

def ffmpeg_audio(video: Path, out: Path) -> None:
    if out.is_file() and out.stat().st_size:
        return
    if not shutil.which("ffmpeg"):
        raise Step("нет ffmpeg: brew install ffmpeg")
    say(f"звук из видео → {out.name}")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                    "-vn", "-acodec", "libmp3lame", "-q:a", "4", str(out)], check=True)


def stack_health() -> dict:
    try:
        with client(ASR_BASE, {}, timeout=20) as c:
            r = c.get("/health")
        return r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {}


def stack(command: str) -> None:
    script = MORAG_REPO / "services" / "asr-adaptor" / "deploy" / "mac" / "stack.sh"
    if not script.is_file():
        raise Step(f"нет {script}: чекаут morag ожидается рядом (MORAG_REPO) — см. ingest-install.sh")
    env = {**os.environ, "ASR_STACK_ENV": str(STACK_ENV)}
    say(f"стек: {command}")
    subprocess.run([str(script), command], check=True, env=env)


def transcribe(work: Path, video: Path, rid: str, title: str, speakers: list[str]) -> Path:
    artifact = work / "artifact.json"
    if artifact.is_file():
        say("транскрибация уже есть — пропускаю")
        return artifact
    if not stack_health():
        raise Step(f"стек транскрибации не отвечает на {ASR_BASE}/health — поднимите `stack.sh up` или запустите с --stack")
    audio = work / "audio.mp3"
    ffmpeg_audio(video, audio)
    hints = json.dumps({"about": title, "names": speakers, "terms": []}, ensure_ascii=False)
    say(f"отправляю звук в адаптер ({size_of(audio.stat().st_size)})…")
    with client(ASR_BASE, {}, timeout=900) as c:
        with audio.open("rb") as fh:
            r = c.post("/v1/audio/transcriptions",
                       files={"file": (audio.name, fh, "audio/mpeg")},
                       data={"mode": "async", "episode": rid, "title": title, "url": str(video), "hints": hints})
        r.raise_for_status()
        job = r.json()["job_id"]
        say(f"задача {job}; жду (диаризация + whisper + LLM-стадии — на час записи ~10–15 минут)")
        polls = 0
        while True:
            time.sleep(POLL_SEC)
            try:
                s = c.get(f"/v1/jobs/{job}", timeout=60).json()
            except (httpx.HTTPError, ValueError):
                continue
            status = s.get("status")
            if status == "done":
                break
            if status == "error":
                raise Step(f"адаптер вернул ошибку: {json.dumps(s, ensure_ascii=False)[:600]}")
            polls += 1
            if polls % 4 == 0:
                say(f"  …{s.get('progress', status)} (~{polls * POLL_SEC} с)")
    result = s["result"]
    x = result.get("x_enriched") or {}
    if not x.get("markdown") or not (x.get("words") or {}).get("turns"):
        raise Step("в ответе адаптера нет x_enriched.markdown/words — это не тот адаптер или прогон без выравнивания")
    artifact.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    (work / "transcript.md").write_text(x["markdown"], encoding="utf-8")
    audio.unlink(missing_ok=True)
    say(f"расшифровка готова: {work / 'transcript.md'}")
    return artifact


# --- шаг 2: экран из видео -----------------------------------------------------------------

def temp_family() -> Path:
    """Семья с пустыми словарями — только чтобы собрать `record.words.json` для обращений к экрану."""
    fam = HOME / "family"
    if not (fam / "site.yml").is_file():
        fam.mkdir(parents=True, exist_ok=True)
        (fam / "site.yml").write_text('slug: local\nbrand: {title: "Локальная сборка"}\n', encoding="utf-8")
        for name, body in (("names.json", {"speakers": {}, "records": {}}),
                           ("text_fixes.json", {"global": [], "records": {}}),
                           ("turn_fixes.json", {"records": {}})):
            (fam / name).write_text(json.dumps(body), encoding="utf-8")
        (fam / "records").mkdir(exist_ok=True)
        (fam / "inbox").mkdir(exist_ok=True)
    return fam


def local_record(work: Path, artifact: Path, rid: str, title: str, date: str) -> Path:
    fam = temp_family()
    record = fam / "records" / rid
    if (record / "record.words.json").is_file():
        return record
    src = fam / "inbox" / f"{rid}.json"
    shutil.copy2(artifact, src)
    env = {**os.environ, "MORAG_WEB_CORPUS": str(fam)}
    say("локальная сборка записи (для привязки обращений к экрану)")
    subprocess.run([sys.executable, str(HERE / "make_record.py"), str(src), "--id", rid, "--title", title,
                    "--date", date, "--no-media"], check=True, env=env, cwd=str(REPO))
    if not (record / "record.words.json").is_file():
        raise Step("локальная сборка не дала record.words.json")
    return record


def screen(work: Path, video: Path, record: Path) -> None:
    done = work / "screen.done"
    if done.is_file():
        say("экран уже снят — пропускаю")
        return
    py = VIDEO_PY if VIDEO_PY.is_file() else Path(sys.executable)
    env = {**os.environ, "ASR_STACK_ENV": str(STACK_ENV), "MORAG_WEB_CORPUS": str(temp_family())}
    steps = [
        ("шкала слайдов и кадры", ["slides_from_video.py", str(video), "--record", str(record)]),
        ("заставка", ["intro_frame.py", str(video), "--record", str(record)]),
        ("описания кадров (Vision)", ["describe_slides.py", str(record)]),
        ("обращения к экрану", ["screen_refs.py", str(record), "--resolve", "--video", str(video)]),
        ("аннотации", ["make_annotations.py", str(record)]),
    ]
    for label, argv in steps:
        say(f"экран: {label}")
        proc = subprocess.run([str(py), str(HERE / argv[0]), *argv[1:]], env=env, cwd=str(REPO))
        if proc.returncode:
            raise Step(f"экран: «{label}» не прошёл (код {proc.returncode}); повторный запуск продолжит отсюда")
    for name in SIDECARS:
        if (record / name).is_file():
            shutil.copy2(record / name, work / name)
    frames = sorted((record / "slides").glob("*.jpg")) if (record / "slides").is_dir() else []
    if frames:
        with zipfile.ZipFile(work / "slides.zip", "w", zipfile.ZIP_STORED) as z:
            for f in frames:
                z.write(f, f.name)
    done.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    say(f"экран готов: кадров {len(frames)}")


# --- шаг 3: загрузка ------------------------------------------------------------------------

class Progress:
    """Файл, который считает отданные байты: у видео это единственный способ увидеть, что идёт."""

    def __init__(self, path: Path, label: str) -> None:
        self.fh = path.open("rb")
        self.total = path.stat().st_size
        self.sent = 0
        self.label = label
        self.last = 0.0

    def read(self, n: int = -1) -> bytes:
        chunk = self.fh.read(n if n > 0 else 1 << 20)
        self.sent += len(chunk)
        if self.total > 50_000_000 and time.monotonic() - self.last > 5:
            self.last = time.monotonic()
            say(f"  {self.label}: {self.sent * 100 // max(1, self.total)} % ({size_of(self.sent)} из {size_of(self.total)})")
        return chunk

    def __iter__(self):
        while True:
            chunk = self.read(1 << 20)
            if not chunk:
                self.fh.close()
                return
            yield chunk


def upload(work: Path, site: str, cookies: dict[str, str], manifest: dict, files: list[tuple[str, Path]],
           video: Path, wait: bool) -> str:
    state_path = work / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    with client(site, cookies, timeout=httpx.Timeout(600.0, connect=30.0)) as c:
        rid = state.get("server_id")
        if not rid:
            r = c.post("/api/ingest", json=manifest)
            if r.status_code == 401:
                raise Step("сессия сайта протухла: `ingest.py login`")
            if r.status_code != 200:
                raise Step(f"сайт отверг манифест ({r.status_code}): {r.json().get('detail', r.text)}")
            rid = r.json()["id"]
            state["server_id"] = rid
            state_path.write_text(json.dumps(state), encoding="utf-8")
        sent = set(state.get("sent") or [])
        for name, path in files + [(manifest["video"], video)]:
            if name in sent or not path.is_file():
                continue
            say(f"загружаю {name} ({size_of(path.stat().st_size)})")
            body = Progress(path, name)
            r = c.put(f"/api/ingest/{rid}/files/{name}", content=body,
                      headers={"Content-Length": str(body.total), "Content-Type": "application/octet-stream"})
            if r.status_code != 200:
                raise Step(f"{name}: сайт ответил {r.status_code}: {r.text[:300]}")
            sent.add(name)
            state["sent"] = sorted(sent)
            state_path.write_text(json.dumps(state), encoding="utf-8")
        r = c.post(f"/api/ingest/{rid}/finish")
        if r.status_code != 200:
            raise Step(f"приём не запустился ({r.status_code}): {r.json().get('detail', r.text)}")
        say(f"пакет принят, сервер собирает запись {rid}" + (" и индексирует" if wait else ""))
        if not wait:
            return rid
        seen = ""
        while True:
            time.sleep(5)
            s = c.get(f"/api/ingest/{rid}").json()
            if s.get("state") != seen:
                seen = s.get("state")
                say(f"  сервер: {seen}")
            if seen == "done":
                say(f"готово: {site}{s.get('url') or ''}")
                return rid
            if seen == "error":
                raise Step(f"сервер не принял запись: {s.get('error')}")


# --- run ---------------------------------------------------------------------------------------

def check_fields(video: Path, title: str, date: str, slides: str | None) -> dict:
    """Проверить то, что ввёл человек, ДО долгой работы: час расшифровки и отказ на загрузке
    из-за кривой даты — худшее, что можно сделать с его временем. Возвращает разобранные поля."""
    if not video.is_file():
        raise Step(f"нет файла {video}")
    ext = video.suffix.lower().lstrip(".")
    if ext not in VIDEO_EXT:
        raise Step(f"видео — {', '.join(VIDEO_EXT)}; у вас .{ext}")
    if len(date) != 10 or date[4] != "-" or date[7] != "-" or not date.replace("-", "").isdigit():
        raise Step("дата — в виде ГГГГ-ММ-ДД")
    if len(title.strip()) < 3:
        raise Step("название — от трёх знаков")
    rid = f"{date}-{slugify(title)}"
    if not rid.rsplit("-", 1)[-1]:
        raise Step("из названия не вышло адреса записи — напишите его словами")
    slides_pdf = Path(slides).expanduser().resolve() if slides else None
    if slides_pdf and not slides_pdf.is_file():
        raise Step(f"нет слайдов {slides_pdf}")
    return {"id": rid, "ext": ext, "slides": slides_pdf}


def pipeline(video: Path, *, title: str, date: str, event: str = "", speakers: list[str] | None = None,
             tags: list[str] | None = None, summary: str = "", slides: str | None = None,
             site: str | None = None, with_stack: bool = False, with_screen: bool = True,
             wait: bool = True, title_auto: bool = False) -> str:
    """Весь путь записи: расшифровка → экран → пакет на сайт. Общий для командной строки и для
    страницы (`ingest_ui.py`) — шаги, возобновление и сообщения обязаны быть одни и те же."""
    video = Path(video).expanduser().resolve()
    fields = check_fields(video, title, date, slides)
    rid, ext, slides_pdf = fields["id"], fields["ext"], fields["slides"]
    speakers = [s for s in (speakers or []) if s]
    tags = [t for t in (tags or []) if t]
    site_url, cookies = load_session(site)
    work = HOME / rid
    work.mkdir(parents=True, exist_ok=True)
    say(f"запись {rid} — рабочий каталог {work}")

    stack_started = False
    try:
        if with_stack and not (work / "artifact.json").is_file() and not stack_health():
            stack("up")
            stack_started = True
        artifact = transcribe(work, video, rid, title, speakers)
        if with_screen:
            record = local_record(work, artifact, rid, title, date)
            screen(work, video, record)
    finally:
        if stack_started:
            stack("down")

    manifest = {"title": title, "date": date, "event": event or "", "tags": tags,
                "summary": summary or "", "speakers": speakers, "video": f"video.{ext}",
                # «название подставилось само» — чтобы сервер знал, можно ли его переписать
                "title_auto": bool(title_auto)}
    files: list[tuple[str, Path]] = [("artifact.json", artifact)]
    files += [(name, work / name) for name in SIDECARS if (work / name).is_file()]
    if (work / "slides.zip").is_file():
        files.append(("slides.zip", work / "slides.zip"))
    if slides_pdf:
        files.append(("slides.pdf", slides_pdf))
    return upload(work, site_url, cookies, manifest, files, video, wait=wait)


def cmd_run(args: argparse.Namespace) -> int:
    pipeline(Path(args.video),
             title=args.title, date=args.date, event=args.event,
             speakers=[s.strip() for s in (args.speakers or "").split(",") if s.strip()],
             tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()],
             summary=args.summary or "", slides=args.slides, site=args.site,
             with_stack=args.stack, with_screen=not args.no_screen, wait=not args.no_wait)
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Страница вместо командной строки: поднять локальный сервер и открыть браузер."""
    import ingest_ui
    return ingest_ui.serve(port=args.port, open_browser=not args.no_open)


def cmd_app(args: argparse.Namespace) -> int:
    """Окно приложения. Нет PyObjC (не мак, урезанный питон) — та же страница в браузере."""
    import ingest_app
    if not args.browser and ingest_app.available():
        return ingest_app.run(port=args.port)
    import ingest_ui
    say("окна нет (нужен PyObjC) — открываю страницу в браузере")
    return ingest_ui.serve(port=args.port, open_browser=True)


def cmd_status(args: argparse.Namespace) -> int:
    site, cookies = load_session(args.site)
    with client(site, cookies) as c:
        r = c.get(f"/api/ingest/{args.id}")
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    return 0 if r.status_code == 200 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="своя запись — на сайт: транскрибация у себя, сборка на сервере")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("login", help="войти на сайт и запомнить сессию")
    p.add_argument("--site", help="адрес сайта, https://…")
    p.add_argument("--login")
    p.set_defaults(fn=cmd_login)
    p = sub.add_parser("run", help="транскрибировать, снять экран, загрузить")
    p.add_argument("video")
    p.add_argument("--title", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD — дата выступления")
    p.add_argument("--event", help="рубрика — из списка сайта (ingest.py options)")
    p.add_argument("--speakers", help="докладчики через запятую, «Имя Фамилия»")
    p.add_argument("--tags", help="метки через запятую")
    p.add_argument("--summary", help="аннотация: о чём запись")
    p.add_argument("--slides", help="презентация PDF")
    p.add_argument("--site")
    p.add_argument("--no-screen", action="store_true", help="без экрана из видео (быстрее, но поиск не увидит слайды)")
    p.add_argument("--stack", action="store_true", help="поднять стек транскрибации перед работой и погасить после")
    p.add_argument("--no-wait", action="store_true", help="не ждать сборки на сервере")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("status", help="что с загрузкой на сервере")
    p.add_argument("id")
    p.add_argument("--site")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("app", help="окно приложения: перетащить видео и заполнить поля")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--browser", action="store_true", help="не окно, а страница в браузере")
    p.set_defaults(fn=cmd_app)
    p = sub.add_parser("ui", help="страница в браузере вместо командной строки")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--no-open", action="store_true", help="не открывать браузер самому")
    p.set_defaults(fn=cmd_ui)
    p = sub.add_parser("options", help="допустимые рубрики и потолки сайта")
    p.add_argument("--site")
    p.set_defaults(fn=cmd_options)
    args = ap.parse_args()
    try:
        return args.fn(args)
    except Step as error:
        say(f"⚠️ {error}")
        return 1
    except subprocess.CalledProcessError as error:
        say(f"⚠️ команда не прошла: {' '.join(map(str, error.cmd))[:200]}")
        return 1


def cmd_options(args: argparse.Namespace) -> int:
    site, cookies = load_session(args.site)
    with client(site, cookies) as c:
        r = c.get("/api/ingest/options")
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
