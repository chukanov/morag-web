"""Конфиг BFF и корпусов.

Слои: `config.example.yml` (дефолты) ← `config.yml` (рабочий, с ключами) ← env.
Секреты в git не кладём: рабочий файл в .gitignore, а в проде их удобнее
держать в env деплой-машины (`MORAG_WEB_ENGINE__API_KEY`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .content.records import RecordIndex

# Имя продукта — ОДНО место: заголовок приложения, префикс переменных окружения, имя логгера.
# Веб-морда generic и живёт отдельно от корпуса (публичный `morag-web`), имя сайта — из бренда
# корпуса (`site.yml`/`hub.yml`), а не отсюда.
PRODUCT = "morag-web"
ENV_PREFIX = PRODUCT.upper().replace("-", "_") + "_"   # MORAG_WEB_
APP_DIR = Path(__file__).resolve().parent


class TimeoutCfg(BaseModel):
    connect: float = 10
    read: float = 180
    write: float = 10
    pool: float = 10


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    web_root: str | None = "../web"
    trusted_proxy_hops: int = 0


class EngineCfg(BaseModel):
    base_url: str = "http://127.0.0.1:9099"
    model: str = "morag"
    api_key: str = "0p3n-w3bu!"
    timeout: TimeoutCfg = Field(default_factory=TimeoutCfg)
    # Вторая дверь ТОГО ЖЕ корпуса — процесс морага для вопроса про ОДНУ запись (15.09): тот же
    # индекс, свой промпт и набор инструментов (`deploy/docker-compose.yml`,
    # `pipelines-<слаг>-record`). Не задана — вопрос к записи идёт в общий движок, а ограничение
    # держится только текстом вопроса, как раньше. Вложенность, а не соседний ключ в `engines`:
    # это не другой корпус, а другой режим того же.
    record: "EngineCfg | None" = None


EngineCfg.model_rebuild()


class TopicCfg(BaseModel):
    """Авто-тема сессии: один дешёвый вызов LLM после первого ответа.

    Адрес по умолчанию НЕ ставим: у корпоративного эндпоинта он свой и живёт в config.yml.
    """

    enabled: bool = True
    base_url: str = ""
    model: str = ""
    # Свой ключ можно не заводить: возьмём из конфига корпуса (он и так с ключом).
    api_key: str = ""
    borrow_key_from_corpus: bool = True
    # Контур корпоративный, наружу не ходим — прокси не нужен. Поле оставлено, потому что
    # это настройка сети, а не домена: в другом контуре пригодится.
    proxy: str | None = None
    timeout: float = 12


class RateLimitCfg(BaseModel):
    """Rate-limit по адресу. Снимается целиком (`enabled: false`) или по одному
    рубежу — нулём в соответствующем поле."""

    enabled: bool = True
    # Ведро: сколько вопросов доступно сразу и как быстро копятся новые.
    burst: int = 5
    refill_seconds: float = 120
    # Сколько вопросов с одного адреса могут идти одновременно (0 — не ограничивать).
    per_ip_concurrent: int = 1
    # Потолок на весь сайт в сутки — последний рубеж против распределённого долбёжа.
    daily_total: int = 500
    # Дневной счётчик переживает перезапуск, вёдра — нет (им незачем).
    state_path: str = "./data/ratelimit.json"
    # Ключ для IPv6 — сеть, а не адрес: клиенту выдают целую подсеть.
    ipv6_prefix: int = 64


class LimitsCfg(BaseModel):
    max_concurrent_streams: int = 2
    history_turns: int = 6
    question_max_chars: int = 1000
    history_max_chars: int = 12000
    rate_limit: RateLimitCfg = Field(default_factory=RateLimitCfg)


class JournalCfg(BaseModel):
    path: str = "./data/journal.jsonl"
    enabled: bool = True


class EditingCfg(BaseModel):
    """Правка корпуса со стороны сайта.

    Это запись в корпус из браузера, и рубежей у неё три, а не один — каждый ловит свой промах:

      1. `enabled` по умолчанию FALSE — в примере конфига (он в git) правка выключена;
      2. сервер отвечает 403, а не прячет кнопку: гарантия на фронте не гарантия;
      3. КТО правит. При `auth.enabled` (см. `AuthCfg`) третий рубеж — роль вошедшего:
         реплики — `editor`, имена голосов и правила словаря — `admin` (`app/auth/roles.py`),
         а `_why` в словаре несёт имя правившего. Без авторизации третий рубеж — `local_only`:
         правку принимаем только с петлевого адреса, поэтому флаг, случайно уехавший на сервер,
         сам по себе дыры не даёт, а автор правки записывается как неизвестный.

    ⓘ Четвёртый рубеж достался даром: пересборка требует сырых сайдкаров, которых нет в git и
    нет нигде, кроме машины транскрибации. На сервере правка физически не доедет до файлов.

    ⓘ На сервере компании конфиг собирает `tools/deploy_server.py`: `enabled: true`,
    `local_only: true` — при включённой авторизации петля не проверяется, а если авторизацию
    там когда-нибудь выключат, правка схлопнется до петли, а не откроется всем.
    """

    enabled: bool = False
    local_only: bool = True
    # Чем пересобирать запись после правки имени. Команда, а не импорт: `app/` переезжает в
    # отдельный репозиторий веб-морды, а инструменты корпуса остаются здесь — прямая зависимость
    # связала бы их навсегда. `{record_dir}` подставляется.
    rebuild: list[str] = Field(default_factory=lambda: [
        sys.executable, "tools/make_record.py", "{record_dir}"])


class LdapCfg(BaseModel):
    """Каталог AD/LDAP. Два режима, выбирается по `bind_dn`.

    **Без сервисной учётки (`bind_dn` пуст, решение владельца 16.09):** человек связывается
    с каталогом САМ — `bind_template` превращает логин в имя для bind'а (`{login}@corp.example`
    или `CORP\\{login}`: AD принимает обе формы, DN искать не нужно), контроллер сам сверяет
    пароль, и той же связью читается его собственная запись (имя, группы, должность, фото).
    На сервере при этом не хранится НИЧЕГО, чем можно войти: адрес, base DN и шаблон — не
    секреты (контроллеры домена видны по DNS SRV любому сотруднику).

    **С сервисной учёткой (`bind_dn`/`bind_password`):** классический search-then-bind —
    сервисная находит DN по `login_attribute`, пароль человека проверяется его bind'ом по
    найденному DN. Запасной ход на случай, если домен запрещает сотруднику читать собственные
    группы. ⚠️ Учётка AD, даже бесправная, читает почти весь каталог (все сотрудники, группы,
    оргструктура) и входит в любую систему, доверяющую AD; если заводить — просить минимальную:
    только чтение, без интерактивного входа, вход только с этого хоста.

    Адрес и прочее — данные инфраструктуры: рабочий `app/config.yml` (вне git) или env
    `MORAG_WEB_AUTH__LDAP__…`.
    """

    # `ldaps://host:636` — TLS с проверкой сертификата (корпоративный корень — `ca_file`);
    # `ldap://host:389` — открытый канал, годится только если домен не требует подписи.
    url: str = ""
    base_dn: str = ""
    # Как представить человека контроллеру: `{login}@corp.example` (UPN) или `CORP\\{login}`.
    bind_template: str = ""
    # Сервисная учётка — только для search-then-bind; пусто = вход без неё.
    bind_dn: str = ""
    bind_password: str = ""
    login_attribute: str = "sAMAccountName"
    # Что читаем о человеке после входа. Снимок хранится в `data/auth/users/…` — минимум,
    # который нужен сайту: имя в шапке и подписи правок, должность и отдел в меню, группы для
    # ролей, фото для кружка в шапке (если каталог его отдаёт), имя и фамилия отдельно — для
    # подписи голоса «это я» в форме корпуса «Имя Фамилия» (`displayName` в AD — «Фамилия Имя
    # Отчество», как есть в словарь не годится).
    attributes: list[str] = Field(default_factory=lambda: [
        "displayName", "givenName", "sn", "mail", "title", "department", "memberOf", "thumbnailPhoto"])
    # ⚠️ Разовая отладка: писать в снимок ВСЮ запись каталога (все атрибуты, байты — base64),
    # чтобы посмотреть, что вообще есть по учётке. Персональные данные — включать на один
    # вход и снимать, сырец удалять руками.
    keep_raw_entry: bool = False
    ca_file: str = ""
    verify_tls: bool = True
    timeout: float = 5


class LocalUserCfg(BaseModel):
    """Пользователь вне каталога: гость, демо, локальная разработка (LDAP с ноутбука
    недоступен), вход при лежащем каталоге. Пароль — только хэшем: `tools/auth_password.py`."""

    login: str
    name: str = ""
    # `scrypt$n$r$p$соль$хэш` (`app/auth/local.py`). Пусто — вход невозможен.
    password: str = ""
    # Пусто — роль по `roles`, как у всех.
    role: str = ""


class RolesCfg(BaseModel):
    """Кто что может: `viewer` < `editor` < `admin` (таблица прав — `app/auth/roles.py`).

    Роль считается на КАЖДЫЙ запрос из снимка учётки и этого конфига, а не пишется в cookie:
    сменил конфиг — роль поменялась без перелогина. Порядок: `by_user` > `by_group` >
    роль локального пользователя > `default`.
    """

    # Любой вошедший — редактор реплик (решение владельца 16.09).
    default: str = "editor"
    # Группа каталога → роль. Ключ — полный DN группы или её CN, регистр не важен.
    # ⚠️ `memberOf` даёт только ПРЯМОЕ членство: вложенная группа сюда не попадёт.
    by_group: dict[str, str] = Field(default_factory=dict)
    # Логин → роль. Побеждает группы.
    by_user: dict[str, str] = Field(default_factory=dict)


class AccessCfg(BaseModel):
    """Кому сайт открыт вообще (владелец, 16.09: только сотрудникам своего подразделения).

    `groups` — маски по имени группы каталога (CN или полный DN, без учёта регистра, `*` и `?`
    как в shell): доменный пользователь входит, только если состоит хотя бы в одной. Пусто —
    любой вошедший. Локальные пользователи (`auth.users`) проходят всегда: они и заводятся для
    того, чтобы пустить того, кого нет в каталоге. Проверяется и при входе (403 с `message`), и
    на каждом запросе по снимку учётки — ужесточили правило, чужие сессии кончились сразу.
    """

    groups: list[str] = Field(default_factory=list)
    message: str = "нет доступа к сайту"


class LoginLimitCfg(BaseModel):
    """Лимит попыток входа. Не столько от перебора, сколько от БЛОКИРОВКИ УЧЁТКИ: повторные
    неверные bind'ы блокируют доменную учётку целиком (почта, всё), а порог в политике домена
    обычно 5-10. Поэтому на логин — ниже этого порога, на адрес — отдельно."""

    attempts: int = 4
    window_seconds: int = 900
    per_ip_attempts: int = 30


class AuthCfg(BaseModel):
    """Вход на сайт: форма + cookie, провайдеры — каталог (`ldap`) и локальные (`users`).

    Почему своя форма, а не Basic Auth на прокси: у коллег с корпоративным прокси стоят
    прокси-помощники, которые отвечают на ЛЮБОЙ 401-челлендж паролем от прокси и уводят вход в
    цикл (Proxy Helper, 15.09). Форма с cookie челленджа не шлёт — перехватывать нечего. И
    только так сайт знает, КТО правит: роли и подпись правок живут внутри BFF.

    Выключено (умолчание в примере конфига): всё работает как без авторизации — анонимно,
    рубежи правки по `EditingCfg`. Включено: всё, кроме формы входа и статики, требует сессии;
    API отвечает 401 JSON (без `WWW-Authenticate` — см. выше), страницы уводят на `/signin`.

    Cookie подписана HMAC (ключ — `secret`, а пустой генерируется в `data_dir/secret` при первом
    старте: `app/data` вне git и вне доставки, у каждой машины свой). Внутри cookie только логин,
    провайдер и сроки; всё остальное — в снимке учётки на диске, и снимок это и есть «сессия
    существует»: удалил файл — человек разлогинен везде.
    """

    enabled: bool = False
    secret: str = ""
    session_days: int = 14
    cookie_name: str = "morag_session"
    # `Secure` у cookie: null — по факту (https сам или `X-Forwarded-Proto: https` от
    # ДОВЕРЕННОГО прокси, `server.trusted_proxy_hops > 0`); true/false — принудительно.
    cookie_secure: bool | None = None
    data_dir: str = "./data/auth"
    ldap: LdapCfg = Field(default_factory=LdapCfg)
    users: list[LocalUserCfg] = Field(default_factory=list)
    roles: RolesCfg = Field(default_factory=RolesCfg)
    access: AccessCfg = Field(default_factory=AccessCfg)
    login_limit: LoginLimitCfg = Field(default_factory=LoginLimitCfg)


class CorpusRef(BaseModel):
    dir: str


class AppConfig(BaseModel):
    server: ServerCfg = Field(default_factory=ServerCfg)
    corpora: list[CorpusRef] = Field(default_factory=list)
    default_corpus: str | None = None
    engine: EngineCfg = Field(default_factory=EngineCfg)
    # Свой движок у пространства: отдельный процесс морага со своей парой коллекций. Один
    # процесс обслуживает ровно один конфиг — маршрутизации по запросу в движке нет, поэтому
    # раздельный поиск это и есть «несколько адресов». Мержится ПОВЕРХ `engine`.
    # ⚠️ Карта, а не поле у элемента `corpora`: из env список не переопределить (индекс стал бы
    # ключом "0"), а `MORAG_WEB_ENGINES__TALKS__API_KEY` работает.
    engines: dict[str, EngineCfg] = Field(default_factory=dict)
    # Откуда фронт берёт видео. Это адрес инфраструктуры, поэтому он ЗДЕСЬ, а не в `site.yml`:
    # тот лежит в git. Пусто — раздаём из локального каталога корпуса, как раньше.
    media_base: str = ""
    editing: EditingCfg = Field(default_factory=EditingCfg)
    auth: AuthCfg = Field(default_factory=AuthCfg)
    topic: TopicCfg = Field(default_factory=TopicCfg)
    limits: LimitsCfg = Field(default_factory=LimitsCfg)
    journal: JournalCfg = Field(default_factory=JournalCfg)


# --- загрузка --------------------------------------------------------------


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce(text: str) -> Any:
    low = text.strip().lower()
    if low in {"true", "false"}:
        return low == "true"
    if low in {"null", "none", ""}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _env_overlay() -> dict:
    """`MORAG_WEB_ENGINE__API_KEY=x` → {"engine": {"api_key": "x"}}."""
    overlay: dict = {}
    for name, value in os.environ.items():
        if not name.startswith(ENV_PREFIX) or name in (CONFIG_ENV, CORPUS_ENV):
            continue
        path = name[len(ENV_PREFIX) :].lower().split("__")
        node = overlay
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return overlay


# Путь к рабочему конфигу переопределяется переменной. Нужно тестам: они обязаны идти по
# ДЕМО-корпусу, а не по тому, что случайно лежит рядом на машине разработчика. Проверка,
# которая молча берёт рабочий корпус, зелена и там, где приложение без него не поднимется.
CONFIG_ENV = ENV_PREFIX + "CONFIG"
# Короткий ключ «корпус вот здесь»: один каталог вместо списка `corpora` (список из env не
# задать — индекс стал бы ключом "0"). Его же читают инструменты (`tools/spaces.py`), поэтому
# сайт и инструменты смотрят в один корпус по построению.
CORPUS_ENV = ENV_PREFIX + "CORPUS"


def _anchor(data: dict, base: Path) -> dict:
    """Относительные пути в конфиге — ОТ КАТАЛОГА ФАЙЛА, где они записаны, а не от `app/`.

    Код сайта и конфиг корпуса живут в разных репозиториях (сайт — публичный `morag-web`,
    конфиг — у корпуса), и `../corpora/<семья>` из чужого каталога не найдётся, а `./data/auth`
    (ключ cookie, снимки учёток) молча уехал бы в каталог кода — на сервере это цель
    `rsync --delete`. Поэтому каждый слой абсолютизирует свои пути сам. `server.web_root`
    нарочно не здесь: это статика самого приложения, она от `app/`.
    """
    def fix(node: dict, key: str) -> None:
        value = node.get(key)
        if isinstance(value, str) and value and not Path(value).is_absolute():
            node[key] = str((base / value).resolve())

    for ref in data.get("corpora") or []:
        if isinstance(ref, dict):
            fix(ref, "dir")
    if isinstance(data.get("journal"), dict):
        fix(data["journal"], "path")
    if isinstance(data.get("auth"), dict):
        fix(data["auth"], "data_dir")
    limits = data.get("limits")
    if isinstance(limits, dict) and isinstance(limits.get("rate_limit"), dict):
        fix(limits["rate_limit"], "state_path")
    return data


def load_config(path: Path | None = None) -> AppConfig:
    example = APP_DIR / "config.example.yml"
    override = os.environ.get(CONFIG_ENV)
    working = Path(path) if path else Path(override) if override else APP_DIR / "config.yml"

    data: dict = {}
    if example.exists():
        data = _anchor(yaml.safe_load(example.read_text(encoding="utf-8")) or {}, example.parent)
    if working.exists():
        layer = _anchor(yaml.safe_load(working.read_text(encoding="utf-8")) or {}, working.resolve().parent)
        data = _deep_merge(data, layer)
    data = _deep_merge(data, _env_overlay())
    corpus = os.environ.get(CORPUS_ENV)
    if corpus:
        data["corpora"] = [{"dir": str(Path(corpus).resolve())}]
    return AppConfig.model_validate(data)


def family_dir(cfg: AppConfig) -> Path:
    """Семья корпуса — каталог, на который указывает конфиг: витрина (`hub.yml`) или
    единственное пространство. Там живут общие словари (`names.json`, `text_fixes.json`,
    `turn_fixes.json`) и снимок голосов. Одно понятие на сайт и инструменты."""
    if cfg.corpora:
        d = Path(cfg.corpora[0].dir)
        return d if d.is_absolute() else (APP_DIR / d).resolve()
    return (APP_DIR / ".." / "corpora" / "demo").resolve()


def engine_for(cfg: AppConfig, slug: str) -> EngineCfg:
    """Движок пространства: своя запись в `engines`, иначе общий `engine`.

    ⓘ Пробуем и `slug`, и `slug` с подчёркиваниями: имена переменных окружения дефис не берут,
    поэтому `MORAG_WEB_ENGINES__SHIFT_QA_2024__MODEL` приезжает ключом `shift_qa_2024`.
    """
    return cfg.engines.get(slug) or cfg.engines.get(slug.replace("-", "_")) or cfg.engine


# --- корпус ----------------------------------------------------------------


def _presets_public(raw: Any) -> dict[str, list[dict]]:
    """Кнопки-пресеты из конфига — в чистом виде для браузера.

    Форма: `{ветка: [{label, question}]}`; ключ `*` — набор для веток без своей строки. Мусор
    (не-словари, пустые подписи) выбрасывается здесь, как у цветов веток: конфиг пишет человек
    руками, а фронту нужен вход, на котором нечему разваливаться.
    """
    out: dict[str, list[dict]] = {}
    for branch, items in (raw or {}).items() if isinstance(raw, dict) else ():
        clean = []
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            question = str(item.get("question") or "").strip()
            if label and question:
                clean.append({"label": label, "question": question})
        if clean:
            out[str(branch)] = clean
    return out



def halo_payload(theme: dict) -> dict | None:
    """`theme.halo` из site.yml — перелив знака и заставки темы (узор, палитра, цель, период).
    ⚠️ Тема отдаётся фронту ПО КЛЮЧАМ, и этот ключ до 15.09 не пробрасывался вовсе: стенд
    `/lab/halo.html` и конфиг были готовы, а выбор владельца до сайта не доезжал — «кажется,
    это пропустилось». Только известные поля и только чистые типы: файл пишут руками."""
    raw = theme.get("halo")
    if not isinstance(raw, dict):
        return None
    out: dict = {k: str(raw[k]) for k in ("pattern", "palette", "target") if isinstance(raw.get(k), str)}
    # Пул целей для `target: random`; повтор — вес (владелец, 15.09: «символы» чаще остальных).
    if isinstance(raw.get("targets"), list):
        pool = [str(t) for t in raw["targets"] if isinstance(t, str)]
        if pool:
            out["targets"] = pool
    period = raw.get("period")
    if isinstance(period, (int, float)) and not isinstance(period, bool) and period > 0:
        out["period"] = float(period)
    return out or None

# Что пространство наследует от витрины, если не задало своё: картинка обложки, знак (рисунок
# и клетки мордочки) и слово в шапке. Один рисунок на весь сайт, копия у каждого пространства —
# лишнее место, где он разойдётся.
INHERITED_BRAND = ("cover", "mark", "mark_face", "wordmark")
MARK_MAX_ROWS, MARK_MAX_COLS = 40, 160


def mark_payload(brand: dict, find_file) -> dict | None:
    """Знак из бренда — рисунок ASCII файлом (`brand.mark`) и клетки мордочки (`brand.mark_face`)
    для моргания и «нюха». Рисунок — доменный (у нас это чужой зверь без лицензии), поэтому он
    живёт у корпуса, а не в коде фронта: платформа без знака показывает одно слово.

    `find_file` — как искать файл (`Corpus.brand_file`: свой каталог, потом витрины). Клетки
    мордочки проверяются на попадание в сетку; кривые — молча отбрасываются (моргать нечем, но
    знак стоит). Только чистые типы: файл пишут руками.
    """
    name = brand.get("mark")
    if not isinstance(name, str) or not name:
        return None
    path = find_file(name)
    if path is None:
        return None
    lines = [ln.rstrip("\n").expandtabs(4) for ln in path.read_text(encoding="utf-8").splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or len(lines) > MARK_MAX_ROWS or max(len(ln) for ln in lines) > MARK_MAX_COLS:
        return None
    rows, cols = len(lines), max(len(ln) for ln in lines)
    out: dict = {"lines": lines}
    face = brand.get("mark_face") if isinstance(brand.get("mark_face"), dict) else {}

    def cell(v, lo, hi) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v < hi else None

    eye = face.get("eye") if isinstance(face.get("eye"), dict) else None
    if eye and cell(eye.get("row"), 0, rows) is not None:
        fr, to = cell(eye.get("from"), 0, cols), cell(eye.get("to"), 1, cols + 1)
        if fr is not None and to is not None and fr < to and isinstance(eye.get("open"), str) and isinstance(eye.get("shut"), str):
            out["eye"] = {"row": eye["row"], "from": fr, "to": to, "open": eye["open"], "shut": eye["shut"]}
    nose = face.get("nose") if isinstance(face.get("nose"), dict) else None
    if nose and cell(nose.get("row"), 0, rows) is not None:
        fr, to = cell(nose.get("from"), 0, cols), cell(nose.get("to"), 1, cols + 1)
        if fr is not None and to is not None and fr < to:
            out["nose"] = {"row": nose["row"], "from": fr, "to": to}
    snout = face.get("snout") if isinstance(face.get("snout"), dict) else None
    if snout and cell(snout.get("row"), 0, rows) is not None and cell(snout.get("col"), 0, cols) is not None:
        if isinstance(snout.get("calm"), str) and isinstance(snout.get("sniff"), str):
            out["snout"] = {"row": snout["row"], "col": snout["col"], "calm": snout["calm"], "sniff": snout["sniff"]}
    return out


class Corpus:
    """Одно пространство: бренд, тема и его записи.

    Пространство самодостаточно: свой каталог записей (он же `sources[].path` индексатора),
    свой бренд, своя тема, свой префикс в адресе. Корпус из одного пространства — это обычный
    сайт, и именно так устроен будет любой заказ с единственным корпусом.
    """

    def __init__(self, directory: Path, *, media_base: str = "",
                 shared_brand_dir: Path | None = None, inherit: dict | None = None) -> None:
        self.dir = Path(directory).resolve()
        raw = yaml.safe_load((self.dir / "site.yml").read_text(encoding="utf-8")) or {}
        self.raw = raw
        self.slug: str = str(raw.get("slug") or self.dir.name)
        self.brand: dict = raw.get("brand") or {}
        self.theme: dict = raw.get("theme") or {}
        self.chat: dict = raw.get("chat") or {}
        self._doc_prefix: str | None = None  # см. `doc_prefix()`: читается один раз

        content = raw.get("content") or {}
        # ⚠️ Каталогов записей может быть НЕСКОЛЬКО, и это не удобство, а рубеж: второй корень
        # лежит вне `sources[].path` индексатора, поэтому его записи показываются на сайте, но в
        # поиск не попадают никак. Физическое разделение, а не шаблон-исключение: промах в
        # шаблоне дал бы тихую утечку записей в базу, а каталог, о котором индексатор не знает,
        # туда не попадёт вовсе. Первый корень — основной, он же индексируемый.
        raw_records = content.get("records") or "records"
        names = [raw_records] if isinstance(raw_records, str) else list(raw_records)
        roots = []
        for name in names:
            root = self.dir / str(name)
            if root not in roots:
                roots.append(root)
        # Ось группировки — обычное строковое поле шапки. Имя поля в конфиге, потому что
        # доменное: у нас это митап (`event`), у другого корпуса — курс, отдел, релиз. Она
        # работает там, где записи лежат ПЛОСКО; где есть каталоги, разделы берутся с диска.
        self.index = RecordIndex(roots, group_by=str(content.get("group_by") or "event"),
                                 order=str(content.get("order") or "desc"),
                                 section_order=content.get("section_order") or {})
        # ⚠️ Пространство, которое НЕ идёт в поиск, говорит об этом вслух. Умолчание «нет
        # мораг-конфига — значит не индексируем» читается как забывчивость, а цена ошибки здесь
        # — голоса коллег в базе. Веха поиска обязана это поле читать.
        self.indexable: bool = bool(raw.get("indexable", True))
        # Адрес медиа: сначала своё (у пространства бывает свой архив), потом общий из
        # app/config.yml. В `site.yml` его обычно нет — это инфраструктура, а файл в git.
        self.media_base: str = str(content.get("media_base") or media_base or "")
        # Видео — вне каталога записи: гигабайты в git не кладём, и доставляются они отдельно.
        self.media_dir = self.dir / str(content.get("media") or "media")
        # Картинки бренда лежат рядом с конфигом корпуса, а не в общей статике:
        # у второго корпуса будет свой набор, и мешать их в одну кучу нельзя.
        self.brand_dir = self.dir / str(content.get("brand_dir") or "brand")
        # Своя картинка важнее общей, но если её нет — берём сайтовую. Так пространство МОЖЕТ
        # завести своё оформление, но не ОБЯЗАНО, и заводить шесть копий одного файла не нужно.
        self.shared_brand_dir = Path(shared_brand_dir) if shared_brand_dir else None
        for key, value in (inherit or {}).items():
            if key in INHERITED_BRAND and value and not self.brand.get(key):
                self.brand = {**self.brand, key: value}

    def brand_file(self, name: str) -> Path | None:
        """Файл бренда по имени из конфига. Наружу — только то, что внутри каталога.

        Имя приходит из URL, поэтому проверяем не строку («нет ли ..»), а итоговый
        путь: символическая ссылка или хитрая кодировка обманули бы проверку строки,
        а разрешённый путь — нет.
        """
        found = _inside(self.brand_dir, name)
        if found is None and self.shared_brand_dir is not None:
            found = _inside(self.shared_brand_dir, name)
        return found

    def media_file(self, name: str) -> Path | None:
        """Видео записи по имени файла из шапки. Та же проверка пути, что у бренда.

        В проде медиа раздаёт Caddy напрямую — это для разработки на ноутбуке, где
        никакого Caddy нет, а караоке проверять надо.
        """
        return _inside(self.media_dir, name)

    def catalog_hint(self, limit: int = 80) -> str:
        """Короткая выжимка «о чём корпус» — контекст для авто-темы.

        У подкаста здесь были темы из заголовка выпуска; у нас заголовок не разбирается, и
        честная выжимка — сами заголовки докладов. Считается один раз: список меняется
        только при добавлении записи.
        """
        cached = getattr(self, "_catalog_hint", None)
        if cached is not None:
            return cached
        seen: list[str] = []
        for record in self.index.all():
            for value in [*record.tags, record.title]:
                if value and value not in seen:
                    seen.append(value)
            if len(seen) >= limit:
                break
        hint = " · ".join(seen[:limit])
        self._catalog_hint = hint
        return hint

    def engine_llm_key(self) -> str:
        """Ключ LLM из движкового конфига корпуса — чтобы не плодить копию секрета.

        Файл `morag-config.yml` gitignored и уже содержит рабочий ключ; читаем его
        только ради авто-темы. Нет файла или ключа — вернём пусто, тема отключится.
        """
        path = self.dir / "morag-config.yml"
        if not path.is_file():
            return ""
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return ""
        for llm in data.get("llms") or []:
            key = str((llm or {}).get("api_key") or "")
            if key and not key.startswith("YOUR_"):
                return key
        return ""

    def doc_prefix(self) -> str:
        """`<kind>:<name>:` — начало идентификатора документа в базе движка (`sources[0]`).

        Нужно для вопроса, ограниченного одной записью: сайт называет агенту её `doc_id`, а тот
        собирается как «kind:name:путь относительно `sources[].path`». Путь у нас уже есть —
        `RecordMeta.path` относителен тому же каталогу записей, который движок монтирует
        источником (обратную сторону разбирает `content/resolve.py::parse_doc_id`).

        ⚠️ Читаем движковый конфиг, а не храним копию: разъехавшись, копия молча уводила бы
        агента в несуществующий документ. Рабочий файл gitignored, поэтому запасной — пример
        рядом: `kind` и `name` в нём настоящие, секретов там нет. Нет и его — вернём пусто, и
        вопрос уйдёт без идентификатора (ограничение удержится словами).
        """
        if self._doc_prefix is not None:
            return self._doc_prefix
        self._doc_prefix = ""
        for name in ("morag-config.yml", "morag-config.example.yml"):
            path = self.dir / name
            if not path.is_file():
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            source = (data.get("sources") or [{}])[0] or {}
            kind, source_name = str(source.get("kind") or ""), str(source.get("name") or "")
            if kind and source_name:
                self._doc_prefix = f"{kind}:{source_name}:"
                break
        return self._doc_prefix

    def doc_id_of(self, record) -> str:
        """Идентификатор записи в базе движка — или пусто, если источник неизвестен."""
        prefix = self.doc_prefix()
        return f"{prefix}{record.path}" if prefix and record.path else ""

    def public(self) -> dict:
        """То, что уезжает в браузер. Явный список полей, а не «конфиг минус секреты»."""
        analytics = self.raw.get("analytics") or {}
        return {
            "slug": self.slug,
            # только два пути и ничего больше: идентификаторов и ключей у счётчика нет
            "analytics": {"script": analytics.get("script"), "endpoint": analytics.get("endpoint")},
            "brand": {
                "title": self.brand.get("title", ""),
                "tagline": self.brand.get("tagline", ""),
                "about": self.brand.get("about", ""),
                "cover": self.brand.get("cover"),
                "mark": mark_payload(self.brand, self.brand_file),
                "wordmark": str(self.brand.get("wordmark") or ""),
                "links": [
                    {"kind": l.get("kind"), "label": l.get("label"), "url": l.get("url")}
                    for l in (self.brand.get("links") or [])
                    if l.get("url")
                ],
                "examples": self.brand.get("examples", []),
            },
            "theme": {
                "mood": self.theme.get("mood"),
                "tokens": self.theme.get("tokens", {}),
                "fonts": self.theme.get("fonts", {}),
                # Цвет ветки (раздела): карта «имя каталога → hex», ею фронт маркирует записи
                # везде. Только строки: конфиг — файл владельца, но фронту нужен чистый вход.
                "sections": {str(k): v for k, v in (self.theme.get("sections") or {}).items()
                             if isinstance(v, str)},
                "halo": halo_payload(self.theme),
            },
            "records_count": len(self.index),
            "hours": round(self.index.total_sec / 3600, 1),
            # Не секрет: этот адрес и так уедет в каждый <video src>.
            "media_base": self.media_base,
            "chat_enabled": bool((self.chat or {}).get("enabled", True)),
            # Кнопки-пресеты вопросов к записи: {ветка: [{label, question}]}, ключ «*» — общие.
            # Тексты доменные, поэтому живут в конфиге корпуса, а не в коде фронта.
            "ask_presets": _presets_public((self.chat or {}).get("presets")),
        }


def _inside(base: Path, name: str) -> Path | None:
    """Файл `name` внутри `base` — или None. Проверяем РАЗРЕШЁННЫЙ путь, а не строку имени."""
    try:
        path = (base / name).resolve()
        path.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


# Слаги, которые нельзя отдать пространству: их занимает сам роутер. Пространство с таким
# именем перехватило бы свою же страницу, и выглядело бы это как «раздел иногда не открывается».
# ⚠️ Тот же список продублирован в web/js/router.js (RESERVED) — менять оба разом.
RESERVED_SLUGS = {"chat", "records", "rec", "api", "css", "js", "assets", "voices", "calendar", "signin"}
HUB_FILE = "hub.yml"


class Hub:
    """Витрина пространств: бренд сайта целиком и порядок карточек на главной.

    Хаб — НЕ корпус: записей у него нет, читать ему нечего. Он существует потому, что у сайта
    из нескольких пространств есть общее имя и общий вход, а у пространства — свой поиск и своя
    база. Корпус из одного пространства хаба не имеет вовсе и работает как обычный сайт.
    """

    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory).resolve()
        raw = yaml.safe_load((self.dir / HUB_FILE).read_text(encoding="utf-8")) or {}
        self.raw = raw
        self.brand: dict = raw.get("brand") or {}
        self.theme: dict = raw.get("theme") or {}
        # Картинки сайта целиком (обложка). Лежат у витрины, а не у пространства: рисунок один
        # на весь сайт, и шесть копий одного файла — это шесть мест, где он разойдётся.
        self.brand_dir = self.dir / "brand"
        # Порядок карточек. Каталог пространства — относительно каталога хаба.
        self.spaces: list[dict] = [dict(x) for x in (raw.get("spaces") or [])]
        # ⚠️ Куда класть НОВУЮ запись. Здесь единственная копия этого знания: BFF правил не
        # читает вовсе (каждое пространство просто читает свой каталог), их читают только
        # инструменты авторинга через tools/spaces.py. Первое совпавшее правило выигрывает.
        self.routing: list[dict] = [dict(x) for x in (raw.get("routing") or [])]

    def dirs(self) -> list[tuple[str, Path]]:
        return [(str(x.get("slug") or ""), self.dir / str(x.get("dir") or "")) for x in self.spaces]

    def public(self) -> dict:
        """То, что уезжает в браузер. Явный список полей, а не «конфиг минус секреты»."""
        return {
            "title": self.brand.get("title", ""),
            "tagline": self.brand.get("tagline", ""),
            "about": self.brand.get("about", ""),
            "mark": mark_payload(self.brand, lambda name: _inside(self.brand_dir, name)),
            "wordmark": str(self.brand.get("wordmark") or ""),
            "theme": {
                "mood": self.theme.get("mood"),
                "tokens": self.theme.get("tokens", {}),
                "fonts": self.theme.get("fonts", {}),
                "halo": halo_payload(self.theme),
            },
        }


def load_corpora(cfg: AppConfig) -> tuple[dict[str, Corpus], Hub | None]:
    """Пространства и (если есть) их витрина.

    Каталог, на который указывает конфиг, — либо ОДНО пространство (есть `site.yml`), либо
    ХАБ пространств (есть `hub.yml`). Одно правило покрывает оба будущих случая, и вырожденный
    случай — обычный сайт — не требует ни строчки особого кода.
    """
    corpora: dict[str, Corpus] = {}
    hub: Hub | None = None
    for ref in cfg.corpora:
        directory = (APP_DIR / ref.dir).resolve() if not Path(ref.dir).is_absolute() else Path(ref.dir)
        if (directory / HUB_FILE).is_file():
            hub = Hub(directory)
            spaces = [d for _, d in hub.dirs()]
        else:
            spaces = [directory]
        for space in spaces:
            corpus = Corpus(
                space,
                media_base=cfg.media_base,
                shared_brand_dir=hub.brand_dir if hub else None,
                inherit={k: hub.brand.get(k) for k in INHERITED_BRAND} if hub else None,
            )
            if corpus.slug in RESERVED_SLUGS:
                raise ValueError(
                    f"слаг «{corpus.slug}» занят роутером ({space}): выберите другой")
            if corpus.slug in corpora:
                raise ValueError(f"слаг «{corpus.slug}» уже занят: {space}")
            corpora[corpus.slug] = corpus
    return corpora, hub
