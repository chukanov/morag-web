"""Склейка входа: кто в запросе, вход/выход, cookie, права, подпись правок и сам gate.

Один объект на `app.state.auth`; ручки и рубеж правки (`api/voices.py::_guard`) спрашивают
его, а не читают cookie сами. Выключен (`auth.enabled: false`) — `user_of()` всегда None,
`capabilities()` считает по одному `editing.enabled`, `why_line()` пуст: всё как без
авторизации, байт в байт.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from . import ldap, local, roles, session
from .local import BadCredentials
from .ldap import LdapUnavailable
from .store import ProfileStore, safe_login
from .throttle import LoginThrottle

log = logging.getLogger(__name__)

SIGNIN = "/signin"
# Что открыто без сессии. Форма входа и её ручки; статика фронта (доменного в ней нет — гейт
# `leak_check --web`); `/api/health` — liveness для доставки и мониторинга (одни счётчики);
# `/sw.js` — service worker: скрипт, пришедший редиректом, браузер отвергает.
PUBLIC_EXACT = frozenset({SIGNIN, "/api/auth/state", "/api/auth/login", "/api/health", "/favicon.svg", "/sw.js"})
PUBLIC_PREFIX = ("/js/", "/css/", "/assets/")
NO_STORE = {"Cache-Control": "no-store"}


class Throttled(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"подождите {retry_after} с")
        self.retry_after = retry_after


class Forbidden(Exception):
    """Пароль верный, но сайт этому человеку не открыт (`auth.access.groups`)."""


@dataclass(frozen=True)
class Identity:
    login: str
    provider: str
    name: str
    role: str
    title: str = ""
    department: str = ""
    mail: str = ""
    has_photo: bool = False
    given: str = ""
    surname: str = ""

    @property
    def speaker_name(self) -> str:
        """Имя для словаря голосов — в форме корпуса «Имя Фамилия» (`names.json` весь такой, и
        `same_person` считает фамилией ПОСЛЕДНЕЕ слово). Из каталога — `givenName` + `sn`;
        без них (старый снимок, локальный пользователь) — из трёх слов ФИО берём второе и
        первое, два слова оставляем как есть."""
        if self.given and self.surname:
            return f"{self.given} {self.surname}"
        words = self.name.split()
        if len(words) == 3:
            return f"{words[1]} {words[0]}"
        return self.name

    def public(self) -> dict:
        return {"login": self.login, "name": self.name, "provider": self.provider, "role": self.role,
                "title": self.title, "department": self.department, "photo": self.has_photo,
                "speaker_name": self.speaker_name}


class AuthService:
    def __init__(self, cfg, data_dir: Path):
        self.cfg = cfg
        self.enabled = bool(cfg.enabled)
        self.store = ProfileStore(data_dir)
        self.throttle = LoginThrottle(cfg.login_limit.attempts, cfg.login_limit.window_seconds,
                                      cfg.login_limit.per_ip_attempts)
        self.lifetime = cfg.session_days * 86400
        # Ключ — только когда авторизация включена: выключенной незачем заводить каталог.
        self._secret = (cfg.secret.encode("utf-8") if cfg.secret
                        else session.load_or_create_secret(Path(data_dir) / "secret") if self.enabled
                        else b"")

    # --- вход ---------------------------------------------------------------

    def providers(self) -> list[str]:
        out = []
        if self.cfg.users:
            out.append("local")
        if ldap.configured(self.cfg.ldap):
            out.append("ldap")
        return out

    def login(self, raw_login: str, password: str, ip: str | None) -> Identity:
        """Синхронный: каталог ходит по сети блокирующе, ручка зовёт через `asyncio.to_thread`,
        чтобы медленный контроллер не стопорил SSE-потоки других людей."""
        login = safe_login(raw_login)
        if not login or not password:
            # Считаем как промах по адресу (логина, может, и нет), к каталогу не ходим.
            self.throttle.failed(login, ip)
            raise BadCredentials()
        wait = self.throttle.retry_after(login, ip)
        if wait:
            raise Throttled(wait)
        user = local.find(self.cfg.users, login)
        try:
            if user is not None:
                profile = local.authenticate(user, password)
            elif ldap.configured(self.cfg.ldap):
                profile = ldap.authenticate(self.cfg.ldap, login, password)
            else:
                raise BadCredentials()
        except BadCredentials:
            self.throttle.failed(login, ip)
            raise
        self.throttle.cleared(login, ip)
        if not self.allowed(profile):
            # Не промах по паролю (лимит не трогаем) и не сессия: снимок не пишем, cookie не будет.
            log.info("вход отклонён: %s (%s) — не в группах доступа", login, profile.get("provider"))
            raise Forbidden(self.cfg.access.message)
        self.store.save(profile)
        identity = self.identity_of(self.store.load(profile["provider"], login) or profile)
        log.info("вход: %s (%s) с %s", login, identity.provider, ip)
        return identity

    def allowed(self, snapshot: dict) -> bool:
        """Открыт ли сайт этому человеку. Локальные — всегда: их и заводят для тех, кого нет в
        каталоге; доменные — поимённо (`auth.access.users`) или по маскам групп
        (`auth.access.groups`). Логин в снимке уже нормализован (`store.safe_login`), а в конфиге
        его могли написать как угодно — сводим той же функцией."""
        if snapshot.get("provider") == "local":
            return True
        login = str(snapshot.get("login") or "").lower()
        if login and login in {safe_login(u) or "" for u in self.cfg.access.users}:
            return True
        return roles.member_of(snapshot.get("groups") or [], self.cfg.access.groups)

    def identity_of(self, snapshot: dict) -> Identity:
        """Личность из снимка: роль считается ЗДЕСЬ, на каждый запрос, из конфига."""
        login = snapshot["login"]
        provider = snapshot.get("provider", "")
        user = local.find(self.cfg.users, login) if provider == "local" else None
        role = roles.role_for(self.cfg.roles, login=login, groups=snapshot.get("groups") or [],
                              local_role=(user.role if user is not None else ""))
        return Identity(login=login, provider=provider, name=snapshot.get("name") or login, role=role,
                        title=snapshot.get("title") or "", department=snapshot.get("department") or "",
                        mail=snapshot.get("mail") or "", has_photo=bool(snapshot.get("photo")),
                        given=snapshot.get("given") or "", surname=snapshot.get("surname") or "")

    # --- кто в запросе ----------------------------------------------------------

    def user_of(self, request: Request) -> Identity | None:
        """Личность по cookie или None. Результат кладётся в `request.state.user`, чтобы ручки
        не разбирали cookie по второму разу."""
        if not self.enabled:
            return None
        cached = getattr(request.state, "user", None)
        if cached is not None:
            return cached
        payload = session.verify(request.cookies.get(self.cfg.cookie_name), self._secret)
        if not payload:
            return None
        snapshot = self.store.load(payload["prv"], payload["sub"])
        if snapshot is None:
            return None  # снимок удалён = сессия отозвана
        if not self.allowed(snapshot):
            return None  # правило доступа ужесточили — чужая сессия кончается на первом запросе
        request.state.session = payload
        identity = self.identity_of(snapshot)
        request.state.user = identity
        return identity

    def capabilities(self, request: Request, editing_enabled: bool) -> dict[str, bool]:
        if not self.enabled:
            # Без авторизации права решает один флаг — как до неё.
            return {name: bool(editing_enabled) for name in roles.PERMISSIONS}
        user = self.user_of(request)
        return roles.capabilities(user.role if user else None, editing_enabled)

    def why_line(self, request: Request, subject: str = "") -> str:
        """Подпись правки в словаре. Пусто — когда автора нет (авторизация выключена), и тогда
        вызывающий пишет свой прежний текст «автор неизвестен» байт в байт."""
        user = self.user_of(request)
        if user is None:
            return ""
        head = f"{subject} — " if subject else ""
        return f"{head}правка: {user.name} ({user.login}), {date.today().isoformat()}, с сайта."

    def claim_line(self, request: Request, subject: str) -> str:
        """Подпись самоназвания «это я» — своя формулировка, чтобы админ видел их в словаре."""
        user = self.user_of(request)
        if user is None:
            return ""
        return f"{subject} — это я: {user.login}, {date.today().isoformat()}, с сайта."

    # --- cookie -------------------------------------------------------------------

    def secure_for(self, request: Request, trusted_hops: int) -> bool:
        if self.cfg.cookie_secure is not None:
            return bool(self.cfg.cookie_secure)
        return session.is_https(request.headers, request.url.scheme, trusted_hops)

    def issue_cookie(self, response: Response, identity: Identity, secure: bool) -> None:
        token = session.sign(session.issue(identity.login, identity.provider, self.lifetime), self._secret)
        response.set_cookie(self.cfg.cookie_name, token, max_age=self.lifetime, path="/",
                            httponly=True, samesite="lax", secure=secure)

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(self.cfg.cookie_name, path="/")

    def renew_if_due(self, request: Request, response: Response, trusted_hops: int) -> None:
        payload = getattr(request.state, "session", None)
        user = getattr(request.state, "user", None)
        if payload and user and session.should_renew(payload, self.lifetime):
            self.issue_cookie(response, user, self.secure_for(request, trusted_hops))


def is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX)


async def gate(request: Request, call_next):
    """Middleware: без сессии API отвечает 401 JSON, страница уводит на `/signin?next=…`.

    ⚠️ Отвечаем `return`, не `raise`: middleware стоит СНАРУЖИ обработчика исключений
    FastAPI, и HTTPException отсюда стал бы пятисоткой. ⚠️ `WWW-Authenticate` не ставим никогда:
    на него срабатывают прокси-помощники в браузерах коллег. Читается `request.app.state.auth`
    на каждый запрос, а не при сборке приложения: тесты подменяют сервис на живом приложении.
    """
    auth = getattr(request.app.state, "auth", None)
    if auth is None or not auth.enabled:
        request.state.user = None
        return await call_next(request)
    path = request.url.path
    user = auth.user_of(request)
    if user is None and not is_public(path):
        if path == "/api" or path.startswith("/api/") or request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "нужен вход"}, status_code=401, headers=NO_STORE)
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"{SIGNIN}?next={quote(target, safe='')}", status_code=302, headers=NO_STORE)
    request.state.user = user
    response = await call_next(request)
    if user is not None:
        auth.renew_if_due(request, response, request.app.state.cfg.server.trusted_proxy_hops)
    return response
