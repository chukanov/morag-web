#!/usr/bin/env python3
"""Своя запись — на сайт: транскрибация и экран у себя на Mac, сборка и индексация на сервере.

    python3 tools/upload.py login --site https://site.example.org
    python3 tools/upload.py run talk.mp4 --title "Kafka без боли" --date 2026-03-12 \\
        [--event "Доклады"] [--speakers "Мария Кузнецова"] [--tags kafka,streams] \\
        [--summary "О чём доклад"] [--slides deck.pdf] [--no-screen] [--stack] [--no-wait]
    python3 tools/upload.py status 2026-03-12-kafka-bez-boli

Что происходит (`run`), по шагам, каждый — с возобновлением: упало на третьем — второй раз
начнётся с третьего (состояние — `~/morag-upload/<id>/state.json`):

  1. стек транскрибации у вас на машине (`morag/services/asr-adaptor/deploy/mac/stack.sh`,
     ставится `tools/upload-install.sh`): звук вынимает ffmpeg, адаптер гонит диаризацию,
     whisper и LLM-стадии через ВАШ ключ (`~/.asr-stack.env`) → `artifact.json`;
  2. экран из видео (`--no-screen` пропускает): шкала слайдов, заставка, описания кадров
     Vision-моделью, обращения «вот здесь», аннотации — инструменты `tools/*.py` под
     `~/asr-stack/video-venv`; для них запись собирается ВО ВРЕМЕННОЙ семье с пустыми словарями
     (`~/morag-upload/family`) — на сайт эта сборка не едет, едет сырой артефакт;
  3. пакет уезжает на сайт под вашей учёткой (`/api/upload`): манифест, артефакт, сайдкары
     экрана, кадры, слайды, видео (последним, с прогрессом); сервер собирает запись с
     настоящими словарями и раскладкой и отвечает адресом. Индексация — отдельно и планово:
     ждать её человеку незачем, запись читается на сайте сразу.

Голоса в записи приедут безымянными (`Speaker_N`): у вашей машины свой реестр голосов, и на
сайте их называют потом — «Это я» у своего голоса, карточка голоса у остальных. Категория и
темы — позже, обычной разметкой корпуса.

Зависимости: python3.10+, ffmpeg, httpx (есть в `video-venv`). Стек — `upload-install.sh`.
"""

from __future__ import annotations

import argparse
import base64
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
    sys.exit("нужен httpx: ~/asr-stack/video-venv/bin/python tools/upload.py … (или pip install httpx)")

# ⚠️ Сертификат корпоративного сайта подписан ВНУТРЕННИМ центром сертификации, а httpx носит с
# собой только публичные корни: без этого вход отвечает `CERTIFICATE_VERIFY_FAILED`, и выглядит
# это как «приложение не видит сайт» (ловилось на живой установке 23.09). `truststore` отдаёт
# проверку тому же хранилищу, которым пользуются Safari и системный curl. Нет пакета — работаем
# как раньше: в установке с зеркала то же самое делает `SSL_CERT_FILE` в окружении.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 — не мак, старый питон, пакета нет: не повод падать
    pass

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from make_record import slugify  # noqa: E402

HOME = Path(os.environ.get("MORAG_UPLOAD_HOME") or (Path.home() / "morag-upload"))
SESSION = Path.home() / ".morag-upload" / "session.json"
ASR_BASE = os.environ.get("ASR_BASE", "http://127.0.0.1:8082")
STACK_HOME = Path(os.environ.get("ASR_STACK_HOME") or (Path.home() / "asr-stack"))
STACK_ENV = Path(os.environ.get("ASR_STACK_ENV") or (Path.home() / ".asr-stack.env"))
VIDEO_PY = STACK_HOME / "video-venv" / "bin" / "python"
MORAG_REPO = Path(os.environ.get("MORAG_REPO") or (REPO.parent / "morag"))
VIDEO_EXT = ("mp4", "webm", "mov", "mkv")
SIDECARS = ("record.slides.json", "record.refs.json", "record.annotations.json")
POLL_SEC = 15
# ⚠️ С лентой событий опрашиваем адаптер РАЗ В СЕКУНДУ, а не раз в пятнадцать: он локальный,
# пустой ответ ~120 байт, а раз в пятнадцать секунд картинка дёргалась бы рывками по четверти
# минуты. В терминальный лог при этом по-прежнему пишем раз в минуту — там частить незачем.
EVENT_POLL_SEC = 1.0
EVENTS_MAX = 20000


class Step(Exception):
    """Шаг не прошёл: причина для человека, без трейсбека."""


# Хвост сообщений — для страницы (`upload_ui.py`): те же строки, что в терминале. Кольцо, а не
# файл: страница показывает ход работы, а разбор потом — в терминале.
LOG: list[str] = []

# Лента событий стадий — то, из чего страница рисует работу. События приходят из адаптера
# (диаризация, куски, замены, голоса) и добавляются здесь (шаги клиента, пики волны).
# ⚠️ Нумерация СВОЯ и сквозная: у страницы должен быть ОДИН монотонный курсор, иначе она не
# отличит «событие адаптера #5» от «своего #5» и покажет кашу.
EVENTS: list[dict] = []
_SEQ = [0]
_T0 = [0.0]                     # начало прогона: время событий считается от него, а не от эпохи
TRACE: list[Path] = []          # куда писать трассу прогона (стенд); пусто — не пишем


def emit(kind: str, **fields) -> None:
    """Событие в ленту. ⚠️ Имя `event` занято: так зовётся рубрика записи в конвейере — совпадение
    имён давало «NoneType is not callable» в середине прогона.

    Событие в ленту. Показ работы — украшение: оно не имеет права уронить загрузку."""
    try:
        if not _T0[0]:
            _T0[0] = time.monotonic()
        _SEQ[0] += 1
        # ⚠️ Номер конверта — `seq`, а не `i`: `i` у события занято смыслом (номер куска).
        evt = {"t": kind, "seq": _SEQ[0], "at": round(time.monotonic() - _T0[0], 2), **fields}
        EVENTS.append(evt)
        del EVENTS[:-EVENTS_MAX]
        for path in TRACE:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(evt, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def events_since(cursor: int) -> tuple[list[dict], int]:
    items = [e for e in EVENTS if e["seq"] > cursor]
    return items, (items[-1]["seq"] if items else cursor)


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
# сервер называет его сам в `/api/upload/options`, здесь — запасное значение.
SITE_LLM_PATH = "/api/upload/llm"

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
        raise Step("не знаю адрес сайта: сначала `upload.py login --site …`")
    return site.rstrip("/"), {}


def save_session(site: str, cookies: dict[str, str]) -> None:
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps({"site": site, "cookies": cookies}), encoding="utf-8")
    SESSION.chmod(0o600)


TRANSPORT: httpx.BaseTransport | None = None   # тесты подменяют сайт и адаптер
# Когда принятая запись попадёт в поиск — «now» или «later» (плановая индексация). Сервер
# говорит это в статусе; окно показывает в карточке «готово», чтобы не обещать лишнего.
LAST_SEARCH = ""


def client(site: str, cookies: dict[str, str], timeout=60.0) -> httpx.Client:
    """⚠️ `trust_env=False`: прокси из окружения нам только мешает. Сайт и стек — внутри
    периметра, а корпоративный прокси отвечает на них 503/407; ловилось в терминале, где
    переменные прокси заданы профилем оболочки (у приложения из Finder их нет вовсе)."""
    return httpx.Client(base_url=site, cookies=cookies, timeout=timeout, follow_redirects=False,
                        trust_env=False, transport=TRANSPORT)


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
        r = c.get("/api/upload/options")
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


WAVE_BARS = 1200      # столбиков на всю запись: шире окна, мельче — глазу не нужно
WAVE_RATE = 4000      # частота для огибающей; больше незачем, а меньше теряет короткие реплики


def wave_peaks(audio: Path) -> None:
    """Огибающая звука для шкалы в окне: одно событие на всю запись, ~1.6 КБ.

    ⚠️ Нормируем по 99-му ПЕРЦЕНТИЛЮ, а не по максимуму: один хлопок дверью или щелчок микрофона
    иначе придавливает всю запись в ровную ниточку — ради одного столбика теряется вся картинка.

    Украшение не имеет права ронять загрузку: не нашёлся ffmpeg, не встала numpy, битый звук —
    молча уходим, окно нарисует ровную линию.
    """
    try:
        import numpy as np  # noqa: PLC0415 — нужен только здесь

        raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio),
                              "-ac", "1", "-ar", str(WAVE_RATE), "-f", "s16le", "-"],
                             check=True, capture_output=True).stdout
        x = np.frombuffer(raw, dtype="<i2")
        if x.size < WAVE_BARS:
            return
        x = np.abs(x[:x.size - x.size % WAVE_BARS].reshape(WAVE_BARS, -1)).max(axis=1)
        top = float(np.percentile(x, 99)) or 1.0
        bars = np.clip(x / top * 255.0, 0, 255).astype("uint8")
        emit("wave.peaks", n=WAVE_BARS, b64=base64.b64encode(bars.tobytes()).decode())
    except Exception as error:  # noqa: BLE001
        say(f"  шкала звука не построилась ({type(error).__name__}) — окно покажет работу без неё")


def stack_health() -> dict:
    try:
        with client(ASR_BASE, {}, timeout=20) as c:
            r = c.get("/health")
        return r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {}


def warm() -> dict:
    """Прогреть бэкенды: модели грузятся по первому запросу, а не при старте.

    ⚠️ Без этого первая стадия МОЛЧИТ минутами — человек видит «идёт работа» и ничего
    больше (живьём: три минуты тишины на первой записи). Греем пока он заполняет поля.
    """
    try:
        with client(ASR_BASE, {}, timeout=900) as c:
            r = c.post("/warmup")
        return r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {}


def stack(command: str, *, check: bool = True) -> None:
    """Поднять или погасить стек транскрибации.

    ⚠️ `check=False` для гашения — и это не мелочь. `stack.sh down` возвращает 1, если последний
    порт уже свободен (последняя строка функции — ложная проверка `[[ -n … ]] &&`), а гасим мы в
    `finally`: исключение из уборки ЗАМЕНЯЕТ настоящую ошибку. Ловилось 24.09 живьём — человек
    увидел «CalledProcessError: stack.sh down», а на деле не стартовал адаптер.
    """
    script = MORAG_REPO / "services" / "asr-adaptor" / "deploy" / "mac" / "stack.sh"
    if not script.is_file():
        raise Step(f"нет {script}: чекаут morag ожидается рядом (MORAG_REPO) — см. upload-install.sh")
    env = {**os.environ, "ASR_STACK_ENV": str(STACK_ENV)}
    say(f"стек: {command}")
    done = subprocess.run([str(script), command], check=check, env=env)
    if done.returncode and not check:
        say(f"  (гашение вернуло {done.returncode} — не страшно, порты свободны)")


def stack_trouble() -> str:
    """Почему стек не поднялся — последняя внятная строка логов бэкендов.

    Без неё человек видит «стек не отвечает» и идёт спрашивать; с ней он видит «Missing
    credentials» и понимает, что не настроен ключ. Логи пишет сам `stack.sh` (`$STACK/logs`).
    """
    logs = Path(os.environ.get("ASR_STACK_HOME") or STACK_HOME) / "logs"
    out = []
    for name in ("adaptor", "diarizer", "whisper", "campp"):
        path = logs / f"{name}.log"
        if not path.is_file():
            continue
        tail = [x.strip() for x in path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]]
        bad = [x for x in tail if ("Error" in x or "error:" in x.lower()) and "INFO" not in x]
        if bad:
            out.append(f"{name}: {bad[-1][:200]}")
    return "; ".join(out)


def ensure_gateway() -> None:
    """Перед подъёмом стека убедиться, что стадиям с ИИ есть куда ходить.

    ⚠️ Без ключа адаптер не просто теряет LLM-стадии — он НЕ СТАРТУЕТ вовсе (строит клиента на
    импорте модуля), и снаружи это выглядит как «стек не отвечает» через две минуты ожидания.
    Настройка через сайт делается при входе, но вход мог случиться раньше обновления — поэтому
    проверяем здесь, у самой работы, и чиним молча.
    """
    if stack_env_value("OR_KEY"):
        return
    say("шлюз ещё не настроен — беру доступ у сайта…")
    out = use_site_llm()
    if not out.get("via_site") or not stack_env_value("OR_KEY"):
        raise Step("нечем ходить в LLM-шлюз: войдите на сайт в настройках приложения "
                   "(или впишите свой ключ — «У меня свой ключ»)")


def voiceprint(work: Path, artifact: Path) -> Path | None:
    """Отпечатки голосов записи — чтобы сервер узнал, КТО говорит, а не выдавал незнакомцев.

    Считает CAM++ из того же стека (он ещё поднят после расшифровки); 192 числа на голос.
    ⚠️ Шаг необязательный и загрузку не роняет: не ответил CAM++ — пакет уедет без отпечатков, и
    сервер разведёт номера по отдельному диапазону, как делал раньше. Потерять запись из-за
    неузнанных голосов было бы куда хуже.
    """
    out = work / "voices.json"
    if out.is_file() and out.stat().st_size:
        say("отпечатки голосов уже есть — пропускаю")
        return out
    audio = work / "audio.mp3"
    if not audio.is_file():
        # ⚠️ Молчать тут нельзя: ровно эта тишина и прятала дефект — звук удалялся шагом раньше,
        # отпечатки не считались никогда, и снаружи всё выглядело исправным.
        say("  звука нет — отпечатки голосов пропускаю, голоса приедут безымянными")
        return None
    try:
        import voiceprints   # noqa: PLC0415 — нужен только здесь

        url = stack_env_value("ASR_CAMPP_URL") or voiceprints.DEFAULT_URL
        prints = voiceprints.fingerprints(artifact, audio, url=url,
                                          key=stack_env_value("ASR_CAMPP_KEY"), work=work)
    except Exception as error:  # noqa: BLE001 — любая осечка здесь не повод терять запись
        say(f"  отпечатки голосов не посчитались ({type(error).__name__}: {str(error)[:120]}) — "
            "голоса приедут безымянными")
        return None
    if not prints:
        return None
    out.write_text(json.dumps(prints, ensure_ascii=False), encoding="utf-8")
    say(f"отпечатки голосов: {len(prints)} — сервер узнает знакомых")
    return out


def transcribe(work: Path, video: Path, rid: str, title: str, speakers: list[str]) -> Path:
    artifact = work / "artifact.json"
    if artifact.is_file():
        say("транскрибация уже есть — пропускаю")
        return artifact
    if not stack_health():
        why = stack_trouble()
        raise Step(f"стек транскрибации не отвечает на {ASR_BASE}/health"
                   + (f" — {why}" if why else " — поднимите `stack.sh up` или запустите с --stack"))
    audio = work / "audio.mp3"
    ffmpeg_audio(video, audio)
    wave_peaks(audio)      # шкала нужна с первой секунды показа, а не после расшифровки
    hints = json.dumps({"about": title, "names": speakers, "terms": []}, ensure_ascii=False)
    say(f"отправляю звук в адаптер ({size_of(audio.stat().st_size)})…")
    with client(ASR_BASE, {}, timeout=900) as c:
        with audio.open("rb") as fh:
            r = c.post("/v1/audio/transcriptions",
                       files={"file": (audio.name, fh, "audio/mpeg")},
                       data={"mode": "async", "episode": rid, "title": title, "url": str(video),
                         "hints": hints, "events": "1"})
        r.raise_for_status()
        job = r.json()["job_id"]
        say(f"задача {job}; жду (диаризация + whisper + LLM-стадии — на час записи ~10–15 минут)")
        polls = 0
        cursor = 0
        spent = 0.0
        while True:
            time.sleep(EVENT_POLL_SEC)
            spent += EVENT_POLL_SEC
            try:
                s = c.get(f"/v1/jobs/{job}", params={"since": cursor}, timeout=60).json()
            except (httpx.HTTPError, ValueError):
                continue
            # ⚠️ Старый адаптер про ленту не знает и просто не вернёт этих ключей — тогда работаем
            # как раньше, по строке прогресса. Разъезд версий не должен ломать загрузку.
            for evt in s.get("events") or ():
                # ⚠️ Нумерацию и время ставим СВОИ: у адаптера они относительны его задачи, а
                # окну нужна одна шкала на весь прогон — вместе с шагами клиента.
                emit(str(evt.pop("t", "?")),
                     **{k: v for k, v in evt.items() if k not in ("seq", "at")})
            if s.get("dropped"):
                # Дыру показываем, а не прячем: иначе картинка будет плавной, но с провалом.
                emit("gap", n=int(s["dropped"]))
            cursor = int(s.get("cursor") or cursor)
            status = s.get("status")
            if status == "done":
                break
            if status == "error":
                raise Step(f"адаптер вернул ошибку: {json.dumps(s, ensure_ascii=False)[:600]}")
            polls += 1
            if spent >= 60 and polls % int(60 / EVENT_POLL_SEC) == 0:
                say(f"  …{s.get('progress', status)} (~{int(spent)} с)")
    result = s["result"]
    x = result.get("x_enriched") or {}
    if not x.get("markdown") or not (x.get("words") or {}).get("turns"):
        raise Step("в ответе адаптера нет x_enriched.markdown/words — это не тот адаптер или прогон без выравнивания")
    artifact.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    (work / "transcript.md").write_text(x["markdown"], encoding="utf-8")
    # ⚠️⚠️ Звук здесь НЕ удаляем. Удалял — и следующий шаг, отпечатки голосов, молча не работал
    # НИ РАЗУ: он читает тот же `audio.mp3` и без него просто возвращает `None`. Пакет уезжал без
    # `voices.json`, сервер честно откатывался на «незнакомцы под номерами», и узнавание голосов,
    # ради которого всё это заведено, не срабатывало вообще. Тесты не ловили: они создают
    # `audio.mp3` руками, а порядок шагов конвейера не проверял никто.
    # Теперь звук живёт до конца работы: его же читает шкала волны в окне, и на возобновлённом
    # прогоне не приходится снова гонять ffmpeg по гигабайтному видео.
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
    say("черновик записи — по нему обращения «вот здесь» привязываются к экрану "
        "(видео и настоящая запись собираются на сервере)")
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
            r = c.post("/api/upload", json=manifest)
            if r.status_code == 401:
                raise Step("сессия сайта протухла: `upload.py login`")
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
            r = c.put(f"/api/upload/{rid}/files/{name}", content=body,
                      headers={"Content-Length": str(body.total), "Content-Type": "application/octet-stream"})
            if r.status_code != 200:
                raise Step(f"{name}: сайт ответил {r.status_code}: {r.text[:300]}")
            sent.add(name)
            state["sent"] = sorted(sent)
            state_path.write_text(json.dumps(state), encoding="utf-8")
        r = c.post(f"/api/upload/{rid}/finish")
        if r.status_code != 200:
            raise Step(f"приём не запустился ({r.status_code}): {r.json().get('detail', r.text)}")
        say(f"пакет принят, сервер собирает запись {rid}")
        if not wait:
            return rid
        seen = ""
        while True:
            time.sleep(5)
            s = c.get(f"/api/upload/{rid}").json()
            if s.get("state") != seen:
                seen = s.get("state")
                say(f"  сервер: {seen}")
            if seen == "done":
                say(f"готово: {site}{s.get('url') or ''}")
                globals()["LAST_SEARCH"] = s.get("search") or ""
                if s.get("search") == "later":
                    say("  (в поиске запись появится после ближайшей плановой индексации — обычно ночью)")
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
    страницы (`upload_ui.py`) — шаги, возобновление и сообщения обязаны быть одни и те же."""
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
            ensure_gateway()
            stack("up")
            stack_started = True
        TRACE[:] = [work / "events.jsonl"]   # трасса прогона: по ней настраивается окно (стенд)
        emit("client.step", step="audio", say="звук из видео")
        artifact = transcribe(work, video, rid, title, speakers)
        emit("client.step", step="voices", say="отпечатки голосов")
        voiceprint(work, artifact)
        if with_screen:
            emit("client.step", step="record", say="черновик записи")
            record = local_record(work, artifact, rid, title, date)
            emit("client.step", step="screen", say="экран из видео")
            screen(work, video, record)
    finally:
        if stack_started:
            stack("down", check=False)

    manifest = {"title": title, "date": date, "event": event or "", "tags": tags,
                "summary": summary or "", "speakers": speakers, "video": f"video.{ext}",
                # «название подставилось само» — чтобы сервер знал, можно ли его переписать
                "title_auto": bool(title_auto)}
    files: list[tuple[str, Path]] = [("artifact.json", artifact)]
    if (work / "voices.json").is_file():
        files.append(("voices.json", work / "voices.json"))
    files += [(name, work / name) for name in SIDECARS if (work / name).is_file()]
    if (work / "slides.zip").is_file():
        files.append(("slides.zip", work / "slides.zip"))
    if slides_pdf:
        files.append(("slides.pdf", slides_pdf))
    rid = upload(work, site_url, cookies, manifest, files, video, wait=wait)
    # Звук держим до этого места: до принятия пакета он может понадобиться — отпечаткам голосов,
    # шкале волны в окне и возобновлённому прогону (иначе ffmpeg снова полезет в гигабайтное
    # видео). Пакет принят — больше не нужен.
    (work / "audio.mp3").unlink(missing_ok=True)
    return rid


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
    import upload_ui
    return upload_ui.serve(port=args.port, open_browser=not args.no_open)


def cmd_app(args: argparse.Namespace) -> int:
    """Окно приложения. Нет PyObjC (не мак, урезанный питон) — та же страница в браузере."""
    import upload_app
    if not args.browser and upload_app.available():
        return upload_app.run(port=args.port)
    import upload_ui
    say("окна нет (нужен PyObjC) — открываю страницу в браузере")
    return upload_ui.serve(port=args.port, open_browser=True)


def cmd_status(args: argparse.Namespace) -> int:
    site, cookies = load_session(args.site)
    with client(site, cookies) as c:
        r = c.get(f"/api/upload/{args.id}")
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
    p.add_argument("--event", help="рубрика — из списка сайта (upload.py options)")
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
        r = c.get("/api/upload/options")
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
