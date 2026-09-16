"""Вход на сайт: форма + cookie, каталог и локальные пользователи, роли.

⚠️ Что здесь проверяется прежде всего — не «вход работает», а рубежи: без сессии данных нет
(API — 401 без челленджа, страницы — на форму), тексты отказов не выдают учёток, пустой
пароль не доходит до каталога, роли держат правку, а выключенная авторизация оставляет
поведение байт в байт прежним. Каталога в тестах нет и быть не может (он в контуре):
`app.auth.ldap.authenticate` подменяется, и подмена помнит, с чем её звали.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.auth import ldap as ldap_provider  # noqa: E402
from app.auth import local, roles, session, store  # noqa: E402
from app.auth.service import AuthService  # noqa: E402
from app.config import AuthCfg, LocalUserCfg, RolesCfg, LoginLimitCfg  # noqa: E402
from app.content.edits import Edits  # noqa: E402
from app.content.voices import Voices  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_GROUP = "CN=site-admins,OU=groups,DC=example,DC=org"
PHOTO = b"\xff\xd8\xff\xe0fake-jpeg"


def _cfg(**over) -> AuthCfg:
    base = dict(
        enabled=True,
        users=[
            LocalUserCfg(login="kuznetsova", name="Мария Кузнецова", password=local.hash_password("pw"), role="admin"),
            LocalUserCfg(login="kovalev", name="Пётр Ковалёв", password=local.hash_password("pw")),
        ],
        roles=RolesCfg(by_group={ADMIN_GROUP: "admin"}),
        login_limit=LoginLimitCfg(attempts=3, window_seconds=600, per_ip_attempts=30),
    )
    base.update(over)
    cfg = AuthCfg(**base)
    cfg.ldap.url = "ldaps://dc.example.org:636"
    cfg.ldap.base_dn = "dc=example,dc=org"
    cfg.ldap.bind_template = "{login}@example.org"
    return cfg


class FakeDirectory:
    """Подмена каталога: помнит вызовы; `fail` — чем ответить вместо профиля."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fail: Exception | None = None
        self.groups = [ADMIN_GROUP, "CN=everyone,OU=groups,DC=example,DC=org"]

    def __call__(self, cfg, login, password):
        self.calls.append((login, password))
        if self.fail:
            raise self.fail
        if login != "petrov" or password != "pw":
            raise local.BadCredentials()
        return {"login": login, "provider": "ldap", "name": "Никанор Петров", "given": "Никанор", "surname": "Петров",
                "mail": "petrov@example.org",
                "title": "инженер", "department": "платформа", "groups": list(self.groups),
                "dn": f"CN=Никанор Петров,OU=people,DC=example,DC=org", "photo": PHOTO,
                "raw": {"dn": "x", "sAMAccountName": [login]} if cfg.keep_raw_entry else None}


@pytest.fixture
def authed(tmp_path, monkeypatch):
    """Живое приложение с включённым входом: сервис и словари — во временном каталоге."""
    directory = FakeDirectory()
    monkeypatch.setattr(ldap_provider, "authenticate", directory)
    with TestClient(app) as c:
        cfg = _cfg()
        app.state.cfg.auth = cfg
        app.state.auth = AuthService(cfg, tmp_path / "auth")
        app.state.cfg.editing.enabled = True
        app.state.cfg.editing.local_only = True   # при включённом входе не читается — это проверяем
        family = tmp_path / "family"
        family.mkdir()
        app.state.voices = Voices(family)
        app.state.edits = Edits(family)
        try:
            yield c, directory, tmp_path
        finally:
            app.state.cfg.editing.enabled = False


def login(c, who="kuznetsova", pw="pw"):
    return c.post("/api/auth/login", json={"login": who, "password": pw})


# --- выключено = как раньше --------------------------------------------------

def test_без_секции_auth_всё_как_прежде():
    """⚠️ Умолчание в примере конфига — выключено: демо-корпус, тесты и будущий публичный
    `morag-web` живут без входа. Анонимный `me` — та же форма, что у вошедшего."""
    with TestClient(app) as c:
        assert app.state.auth.enabled is False
        assert c.get("/").status_code == 200
        me = c.get("/api/auth/me").json()
        assert me["login"] is None and me["can"] == {"edit": False, "voices": False, "claim": False}
        state = c.get("/api/auth/state").json()
        assert state["enabled"] is False and state["providers"] == []
        # «Дом» — пространство, не корень: на общем с чужим сайтом домене корень чужой.
        assert state["home"] == "/demo" and me["home"] == "/demo"
        assert c.post("/api/auth/login", json={"login": "a", "password": "b"}).status_code == 404
        slug = next(iter(app.state.corpora))
        assert c.get(f"/api/site?slug={slug}").json()["editing"] is False


# --- аноним --------------------------------------------------------------------

def test_аноним_на_странице_уходит_на_форму_с_возвратом(authed):
    c, _, _ = authed
    slug = next(iter(app.state.corpora))
    r = c.get(f"/{slug}/rec/x?y=1", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/signin?next=%2F{slug}%2Frec%2Fx%3Fy%3D1"
    assert r.headers["cache-control"] == "no-store"


def test_аноним_в_api_получает_401_без_челленджа(authed):
    """⚠️ `WWW-Authenticate` не отдаём НИКОГДА: прокси-помощники в браузерах коллег отвечают на
    него паролем от прокси и уводят вход в цикл (15.09)."""
    c, _, _ = authed
    r = c.get("/api/records")
    assert r.status_code == 401 and r.json()["detail"] == "нужен вход"
    assert "www-authenticate" not in {k.lower() for k in r.headers}
    assert c.post("/api/fixes", json={"was": "а", "now": "б"}).status_code == 401
    assert c.post("/api/voices/Speaker_1", json={"name": "x"}).status_code == 401


def test_форма_и_статика_открыты_а_мета_записи_анониму_не_отдаётся(authed):
    c, _, _ = authed
    for path in ("/signin", "/api/auth/state", "/js/main.js", "/css/app.css", "/favicon.svg", "/api/health"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code not in (301, 302, 401), path
    page = c.get("/signin").text
    assert "<title>" in page and "og:title" not in page


# --- вход ----------------------------------------------------------------------

def test_вход_ставит_cookie_и_даёт_личность(authed):
    c, _, tmp = authed
    r = login(c)
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["login"] == "kuznetsova" and me["role"] == "admin" and me["provider"] == "local"
    assert me["can"] == {"edit": True, "voices": True, "claim": True}
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie, "локально http — с Secure браузер cookie не примет"
    snapshot = json.loads((tmp / "auth" / "users" / "local" / "kuznetsova.json").read_text(encoding="utf-8"))
    assert snapshot["name"] == "Мария Кузнецова" and snapshot["logged_in_at"]
    assert c.get("/api/auth/me").json()["login"] == "kuznetsova"
    assert c.get("/api/records").status_code == 200


@pytest.mark.parametrize("who,pw", [("kuznetsova", "wrong"), ("nobody-here", "pw"), ("../x", "pw"), ("kuznetsova", "")])
def test_отказ_входа_одним_текстом(authed, who, pw):
    """Нет такого, неверный пароль, кривой логин, пустой пароль — снаружи одно и то же."""
    c, directory, _ = authed
    r = login(c, who, pw)
    assert r.status_code == 401 and r.json()["detail"] == "неверный логин или пароль"
    assert "set-cookie" not in r.headers


def test_пустой_пароль_не_доходит_до_каталога(authed):
    """⚠️ Simple bind с пустым паролем — анонимный bind, и каталог отвечает УСПЕХОМ."""
    c, directory, _ = authed
    assert login(c, "petrov", "").status_code == 401
    assert directory.calls == []


def test_логин_нормализуется(authed):
    """`CORP\\ivanov`, `ivanov@corp`, `Ivanov` — одна учётка."""
    c, directory, _ = authed
    for form in ("CORP\\Petrov", "petrov@corp.example", " Petrov "):
        assert login(c, form).status_code == 200, form
    assert {call[0] for call in directory.calls} == {"petrov"}


def test_secure_только_за_доверенным_прокси(authed):
    c, _, _ = authed
    app.state.cfg.server.trusted_proxy_hops = 0
    r = c.post("/api/auth/login", json={"login": "kovalev", "password": "pw"},
               headers={"x-forwarded-proto": "https"})
    assert "Secure" not in r.headers["set-cookie"], "заголовку без прокси верить нельзя"
    app.state.cfg.server.trusted_proxy_hops = 1
    r = c.post("/api/auth/login", json={"login": "kovalev", "password": "pw"},
               headers={"x-forwarded-proto": "https"})
    assert "Secure" in r.headers["set-cookie"]
    app.state.cfg.server.trusted_proxy_hops = 0
    app.state.auth.cfg.cookie_secure = True
    r = c.post("/api/auth/login", json={"login": "kovalev", "password": "pw"})
    assert "Secure" in r.headers["set-cookie"]


# --- каталог -------------------------------------------------------------------

def test_вход_через_каталог_пишет_снимок_и_фото(authed):
    c, directory, tmp = authed
    r = login(c, "petrov")
    assert r.status_code == 200 and directory.calls == [("petrov", "pw")]
    me = r.json()
    assert me["name"] == "Никанор Петров" and me["title"] == "инженер" and me["photo"] is True
    assert me["role"] == "admin", "группа из by_group даёт роль"
    users = tmp / "auth" / "users" / "ldap"
    snapshot = json.loads((users / "petrov.json").read_text(encoding="utf-8"))
    assert snapshot["groups"] == directory.groups and snapshot["department"] == "платформа"
    assert snapshot["given"] == "Никанор" and snapshot["surname"] == "Петров"
    assert me["speaker_name"] == "Никанор Петров"
    assert (users / "petrov.jpg").read_bytes() == PHOTO
    assert not (users / "petrov.raw.json").exists(), "сырая запись — только под флагом"
    photo = c.get("/api/auth/me/photo")
    assert photo.status_code == 200 and photo.content == PHOTO


def test_сырая_запись_только_под_флагом(authed):
    c, _, tmp = authed
    app.state.auth.cfg.ldap.keep_raw_entry = True
    assert login(c, "petrov").status_code == 200
    raw = json.loads((tmp / "auth" / "users" / "ldap" / "petrov.raw.json").read_text(encoding="utf-8"))
    assert raw["sAMAccountName"] == ["petrov"]


def test_каталог_недоступен_это_503_а_не_неверный_пароль(authed):
    c, directory, _ = authed
    directory.fail = ldap_provider.LdapUnavailable("нет связи")
    r = login(c, "petrov")
    assert r.status_code == 503 and "недоступен" in r.json()["detail"]
    directory.fail = local.BadCredentials()
    assert login(c, "petrov").json()["detail"] == "неверный логин или пароль"


def test_локальный_пользователь_к_каталогу_не_ходит(authed):
    c, directory, _ = authed
    assert login(c, "kovalev").status_code == 200
    assert directory.calls == []


def test_ldap3_не_установлен_это_недоступен_а_не_пятисотка(monkeypatch):
    cfg = _cfg().ldap
    with pytest.raises(local.BadCredentials):
        ldap_provider.authenticate(cfg, "x", "")   # пустой пароль отсекается до всего
    monkeypatch.setitem(sys.modules, "ldap3", None)  # `import ldap3` → ImportError
    with pytest.raises(ldap_provider.LdapUnavailable):
        ldap_provider.authenticate(cfg, "x", "pw")
    cfg.bind_template = cfg.bind_dn = ""
    with pytest.raises(ldap_provider.LdapUnavailable):
        ldap_provider.authenticate(cfg, "x", "pw")  # не настроен — тоже «недоступен», не 401


# --- сам провайдер: подменённый ldap3 ------------------------------------------------
#
# Настоящего каталога здесь нет и быть не может (он в контуре). Подменяем БИБЛИОТЕКУ, а не
# нашу функцию: так проверяется, что мы зовём её правильно — кем связываемся, что ищем, как
# читаем ответ и во что превращаем отказ. Это единственная проверка режима «своей учёткой»
# до живого пробника на сервере.

class FakeLdap3:
    """Модуль `ldap3` в миниатюре: Server/Connection/Tls и константы; ошибки — свои классы."""

    NONE, SUBTREE, ALL_ATTRIBUTES = "NO_INFO", "SUBTREE", "*"

    class Errors:
        class LDAPExceptionError(Exception): ...
        class LDAPOperationResult(LDAPExceptionError): ...
        class LDAPBindError(LDAPExceptionError): ...
        class LDAPSocketOpenError(LDAPExceptionError): ...

    def __init__(self):
        self.binds: list[tuple[str, str]] = []
        self.searches: list[tuple[str, str, list]] = []
        self.accept = {"petrov@example.org": "pw", "cn=svc,dc=example,dc=org": "svc-pw",
                       "CN=Никанор Петров,OU=people,DC=example,DC=org": "pw"}
        self.entry = {"type": "searchResEntry", "dn": "CN=Никанор Петров,OU=people,DC=example,DC=org",
                      "attributes": {"displayName": "Никанор Петров", "memberOf": [ADMIN_GROUP], "title": ["инженер"]},
                      "raw_attributes": {"thumbnailPhoto": [PHOTO], "displayName": [b"\xd0\x9d"]}}
        self.found = True
        self.search_fails = False
        self.down = False
        fake = self

        class Tls:
            def __init__(self, **kw): self.kw = kw

        class Server:
            def __init__(self, url, **kw): self.url, self.kw = url, kw

        class Connection:
            def __init__(self, server, user, password, **kw):
                # ⚠️ На Linux ldap3 пакует receive_timeout через struct.pack('LL') — float
                # роняет открытие сокета (ловилось на сервере). Заглушка требует того же.
                assert isinstance(kw["receive_timeout"], int) and isinstance(server.kw["connect_timeout"], int), kw
                fake.binds.append((user, password))
                if fake.down:
                    raise fake.Errors.LDAPSocketOpenError("connection refused")
                if fake.accept.get(user) != password:
                    raise fake.Errors.LDAPBindError("invalidCredentials data 52e")
                self.user, self.response = user, []

            def search(self, base, filt, scope, attributes, **kw):
                fake.searches.append((self.user, filt, list(attributes)))
                if fake.search_fails:
                    raise fake.Errors.LDAPOperationResult("insufficientAccessRights")
                self.response = [fake.entry] if fake.found else []
                return fake.found

            def unbind(self): ...

        self.Tls, self.Server, self.Connection = Tls, Server, Connection


@pytest.fixture
def fake_ldap3(monkeypatch):
    import types
    fake = FakeLdap3()
    core = types.ModuleType("ldap3.core"); core.exceptions = fake.Errors
    conv = types.ModuleType("ldap3.utils.conv")
    conv.escape_filter_chars = lambda text: text.replace("*", "\\2a").replace("(", "\\28").replace(")", "\\29")
    utils = types.ModuleType("ldap3.utils"); utils.conv = conv
    for name, mod in {"ldap3": fake, "ldap3.core": core, "ldap3.core.exceptions": fake.Errors,
                      "ldap3.utils": utils, "ldap3.utils.conv": conv}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return fake


def test_своей_учёткой_bind_по_шаблону_и_чтение_своей_записи(fake_ldap3):
    cfg = _cfg().ldap
    profile = ldap_provider.authenticate(cfg, "petrov", "pw")
    assert fake_ldap3.binds == [("petrov@example.org", "pw")], "одна связь — самого человека; сервисной нет"
    who, filt, attrs = fake_ldap3.searches[0]
    assert who == "petrov@example.org" and filt == "(sAMAccountName=petrov)"
    assert "memberOf" in attrs and "*" not in attrs
    assert profile["name"] == "Никанор Петров" and profile["groups"] == [ADMIN_GROUP]
    assert profile["photo"] == PHOTO and profile["raw"] is None and profile["title"] == "инженер"


def test_своей_учёткой_неверный_пароль_и_лежащий_каталог(fake_ldap3):
    cfg = _cfg().ldap
    with pytest.raises(local.BadCredentials):
        ldap_provider.authenticate(cfg, "petrov", "wrong")
    assert fake_ldap3.searches == [], "до чтения записи дело не дошло"
    fake_ldap3.down = True
    with pytest.raises(ldap_provider.LdapUnavailable):
        ldap_provider.authenticate(cfg, "petrov", "pw")


def test_своей_учёткой_вошёл_но_запись_не_прочитал(fake_ldap3):
    """Домен закрыл чтение или base_dn кривой: человек всё равно вошёл (пароль сверил
    контроллер), но без имени и групп — роль по умолчанию. В лог уходит крик, не молчание."""
    cfg = _cfg().ldap
    fake_ldap3.found = False
    profile = ldap_provider.authenticate(cfg, "petrov", "pw")
    assert profile["name"] == "petrov" and profile["groups"] == [] and profile["photo"] is None
    fake_ldap3.search_fails = True
    assert ldap_provider.authenticate(cfg, "petrov", "pw")["groups"] == []


def test_логин_в_фильтре_экранируется(fake_ldap3):
    """`safe_login` кривое не пропустит, но провайдер экранирует сам: он и из пробника зовётся."""
    cfg = _cfg().ldap
    fake_ldap3.accept["a*b@example.org"] = "pw"
    ldap_provider.authenticate(cfg, "a*b", "pw")
    assert fake_ldap3.searches[-1][1] == "(sAMAccountName=a\\2ab)"


def test_сервисной_учёткой_search_then_bind(fake_ldap3):
    cfg = _cfg().ldap
    cfg.bind_dn, cfg.bind_password, cfg.keep_raw_entry = "cn=svc,dc=example,dc=org", "svc-pw", True
    profile = ldap_provider.authenticate(cfg, "petrov", "pw")
    assert fake_ldap3.binds == [("cn=svc,dc=example,dc=org", "svc-pw"),
                                ("CN=Никанор Петров,OU=people,DC=example,DC=org", "pw")]
    assert fake_ldap3.searches[0][0] == "cn=svc,dc=example,dc=org" and fake_ldap3.searches[0][2] == ["*"]
    assert profile["raw"]["thumbnailPhoto"][0].startswith("base64:") and profile["raw"]["displayName"] == ["Н"]
    fake_ldap3.found = False
    with pytest.raises(local.BadCredentials):
        ldap_provider.authenticate(cfg, "nobody", "pw")   # сервисная не нашла — «неверный логин или пароль»
    cfg.bind_password = "bad"
    with pytest.raises(ldap_provider.LdapUnavailable):
        ldap_provider.authenticate(cfg, "petrov", "pw")   # сервисная не связалась — НАША ошибка, 503


# --- роли ----------------------------------------------------------------------

def test_роли_из_конфига_и_права_в_ручках(authed):
    c, _, _ = authed
    slug = next(iter(app.state.corpora))
    record = app.state.corpora[slug].index.all()[0].id

    login(c, "kovalev")                      # editor по умолчанию
    me = c.get("/api/auth/me").json()
    assert me["role"] == "editor" and me["can"] == {"edit": True, "voices": False, "claim": True}
    r = c.post("/api/voices/Speaker_1", json={"name": "Кто-то"})
    assert r.status_code == 403 and "роль admin" in r.json()["detail"]
    assert c.post("/api/fixes", json={"was": "а", "now": "б"}).status_code == 403
    r = c.post(f"/api/records/{record}/edits", json={"edits": []})
    assert r.status_code not in (401, 403), r.text
    assert c.get(f"/api/site?slug={slug}").json()["editing"] is True
    assert c.get("/api/voices").json()["editing"] is False

    login(c, "kuznetsova")                   # admin
    assert c.post("/api/voices/Speaker_1", json={"name": "Кто-то"}).status_code == 200
    assert c.get("/api/voices/Speaker_1").json()["editing"] is True


def test_viewer_не_правит_а_выключенная_правка_держит_и_админа(authed):
    c, _, _ = authed
    slug = next(iter(app.state.corpora))
    record = app.state.corpora[slug].index.all()[0].id
    app.state.auth.cfg.roles.default = "viewer"
    login(c, "kovalev")
    assert c.post(f"/api/records/{record}/edits", json={"edits": []}).status_code == 403
    login(c, "kuznetsova")
    app.state.cfg.editing.enabled = False
    r = c.post("/api/voices/Speaker_1", json={"name": "Кто-то"})
    assert r.status_code == 403 and "выключена" in r.json()["detail"]


def test_смена_конфига_ролей_действует_без_перелогина(authed):
    c, _, _ = authed
    login(c, "kovalev")
    assert c.get("/api/auth/me").json()["role"] == "editor"
    app.state.auth.cfg.roles.by_user["kovalev"] = "admin"
    assert c.get("/api/auth/me").json()["role"] == "admin"


def test_группа_совпадает_по_dn_или_cn_без_регистра():
    cfg = RolesCfg(by_group={"Site-Admins": "admin", "cn=readers,ou=g,dc=x": "viewer"}, default="editor")
    assert roles.role_for(cfg, login="a", groups=[ADMIN_GROUP]) == "admin"
    assert roles.role_for(cfg, login="a", groups=["CN=readers,OU=g,DC=x"]) == "viewer"
    assert roles.role_for(cfg, login="a", groups=["CN=readers,OU=g,DC=x", ADMIN_GROUP]) == "admin", "старшая из подошедших"
    assert roles.role_for(cfg, login="a", groups=[]) == "editor"
    assert roles.role_for(RolesCfg(default="boss"), login="a", groups=[]) == "viewer", "опечатка в умолчании — минимум"
    assert roles.role_for(RolesCfg(by_user={"A": "admin"}), login="a", groups=[]) == "admin"
    assert roles.role_for(RolesCfg(), login="a", groups=[], local_role="viewer") == "viewer"


# --- подпись правок и журнал ------------------------------------------------------

def test_подпись_правки_несёт_имя_и_логин(authed):
    c, _, tmp = authed
    login(c, "kuznetsova")
    assert c.post("/api/voices/Speaker_7", json={"name": "Некто"}).status_code == 200
    why = json.loads((tmp / "family" / "names.json").read_text(encoding="utf-8"))["_why"]["Speaker_7"]
    assert "kuznetsova" in why and "Мария Кузнецова" in why and "неизвестен" not in why
    assert c.post("/api/fixes", json={"was": "Grafanna", "now": "Grafana"}).status_code == 200
    rule = json.loads((tmp / "family" / "text_fixes.json").read_text(encoding="utf-8"))["global"][0]
    assert "kuznetsova" in rule["why"]


# --- кому сайт открыт: маски групп --------------------------------------------------

def test_маска_группы():
    """`*_site-users` ловит и hq2_, и hq5_ — по CN, без регистра; полный DN тоже годится."""
    g = ["CN=hq5_site-USERS,OU=Access,OU=Users group,DC=example,DC=org",
         "CN=Wi-Fi_Users,OU=x,DC=example,DC=org"]
    assert roles.member_of(g, ["*_site-users"])
    assert roles.member_of(g, ["cn=hq5_site-users,ou=access,ou=users group,dc=example,dc=org"])
    assert not roles.member_of(g, ["*_DP_DEV"])
    assert roles.member_of(g, []), "пустой список масок — правила нет"
    assert not roles.member_of([], ["*"]), "без групп в маску не попасть"


def test_доступ_только_по_группе_а_локальные_всегда(authed):
    """Пароль верный, а сайт закрыт: 403 с текстом из конфига, снимка и cookie нет, лимит попыток
    не трогается. Локальный пользователь проходит без групп — его для того и заводят."""
    c, directory, tmp = authed
    app.state.auth.cfg.access.groups = ["*_site-users"]
    app.state.auth.cfg.access.message = "Только для сотрудников отдела"
    r = login(c, "petrov")
    assert r.status_code == 403 and r.json()["detail"] == "Только для сотрудников отдела"
    assert "set-cookie" not in r.headers
    assert not (tmp / "auth" / "users" / "ldap" / "petrov.json").exists()
    for _ in range(3):
        login(c, "petrov")
    assert login(c, "petrov").status_code == 403, "отказ по доступу — не промах по паролю, 429 не будет"
    assert login(c, "kovalev").status_code == 200, "локальный — всегда"
    directory.groups.append("CN=hq2_site-users,OU=Access,OU=Users group,DC=example,DC=org")
    assert login(c, "petrov").status_code == 200


def test_ужесточили_правило_сессия_кончилась(authed):
    c, _, _ = authed
    login(c, "petrov")
    assert c.get("/api/auth/me").status_code == 200
    app.state.auth.cfg.access.groups = ["*_site-users"]
    assert c.get("/api/auth/me").status_code == 401, "снимок есть, но правило больше не пускает"
    app.state.auth.cfg.access.groups = []
    assert c.get("/api/auth/me").status_code == 200


# --- «это я»: безымянный голос — собой ---------------------------------------------

def test_это_я_имя_в_форме_корпуса():
    """`names.json` весь «Имя Фамилия», а AD отдаёт «Фамилия Имя Отчество»: из каталога — по
    givenName + sn, без них — перестановка трёх слов; два слова — как есть."""
    from app.auth.service import Identity
    mk = lambda **kw: Identity(login="x", provider="ldap", name=kw.pop("name", ""), role="editor", **kw)
    assert mk(name="Кузнецова Мария Львовна", given="Мария", surname="Кузнецова").speaker_name == "Мария Кузнецова"
    assert mk(name="Кузнецова Мария Львовна").speaker_name == "Мария Кузнецова"
    assert mk(name="Мария Кузнецова").speaker_name == "Мария Кузнецова"
    assert mk(name="Кузнецова").speaker_name == "Кузнецова"


def test_это_я_называет_безымянный_голос_вошедшим(authed):
    """Право у любого вошедшего; имя — из сессии, чужое в теле игнорируется; подпись — «это я»."""
    c, _, tmp = authed
    login(c, "kovalev")                      # editor: обычная правка голоса ему запрещена…
    assert c.post("/api/voices/Speaker_3", json={"name": "Кто-то"}).status_code == 403
    r = c.post("/api/voices/Speaker_3", json={"claim": True, "name": "Чужое Имя", "record": "зап-1"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Пётр Ковалёв"
    names = json.loads((tmp / "family" / "names.json").read_text(encoding="utf-8"))
    assert names["speakers"]["Speaker_3"] == "Пётр Ковалёв"
    assert "records" not in names or not names["records"], "претензия всегда на весь корпус"
    assert names["_why"]["Speaker_3"].startswith("Пётр Ковалёв — это я: kovalev")
    assert c.get("/api/auth/me").json()["speaker_name"] == "Пётр Ковалёв"


def test_это_я_не_перебивает_названный_голос(authed):
    c, _, _ = authed
    login(c, "kuznetsova")
    assert c.post("/api/voices/Speaker_4", json={"name": "Некто Другой"}).status_code == 200
    login(c, "kovalev")
    r = c.post("/api/voices/Speaker_4", json={"claim": True})
    assert r.status_code == 409 and "Некто Другой" in r.json()["detail"] and "администратор" in r.json()["detail"]
    # снятое имя снова открывает претензию
    login(c, "kuznetsova")
    assert c.post("/api/voices/Speaker_4", json={"name": ""}).status_code == 200
    login(c, "kovalev")
    assert c.post("/api/voices/Speaker_4", json={"claim": True}).status_code == 200


def test_это_я_без_входа_невозможно(tmp_path):
    """Без авторизации подставлять некого — 400, а не тихое имя «владелец»."""
    with TestClient(app) as c:
        family = tmp_path / "family"
        family.mkdir()
        app.state.voices = Voices(family)
        app.state.cfg.editing.enabled = True
        app.state.cfg.editing.local_only = False
        try:
            r = c.post("/api/voices/Speaker_3", json={"claim": True})
            assert r.status_code == 400 and "вошедшего" in r.json()["detail"]
        finally:
            app.state.cfg.editing.enabled = False
            app.state.cfg.editing.local_only = True


def test_без_входа_подпись_прежняя(tmp_path):
    """Байт в байт как до авторизации: «автор неизвестен»."""
    with TestClient(app) as c:
        family = tmp_path / "family"
        family.mkdir()
        app.state.voices = Voices(family)
        app.state.cfg.editing.enabled = True
        app.state.cfg.editing.local_only = False
        try:
            assert c.post("/api/voices/Speaker_7", json={"name": "Некто"}).status_code == 200
        finally:
            app.state.cfg.editing.enabled = False
            app.state.cfg.editing.local_only = True
        why = json.loads((family / "names.json").read_text(encoding="utf-8"))["_why"]["Speaker_7"]
        assert why.startswith("Некто — правка владельца, ") and "Автор неизвестен" in why


def test_журнал_знает_кто_спрашивал(authed):
    from test_ask_api import _FakeEngine
    c, _, tmp = authed
    slug = next(iter(app.state.corpora))
    real = app.state.engines[slug]
    app.state.engines[slug] = _FakeEngine()
    app.state.journal.path = tmp / "journal.jsonl"
    app.state.topic.enabled = False
    try:
        login(c, "kovalev")
        r = c.post("/api/ask", json={"question": "что там?", "corpus": slug, "session_id": "s1",
                                     "history": [], "want_topic": False})
        assert r.status_code == 200
    finally:
        app.state.engines[slug] = real
    line = json.loads((tmp / "journal.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["user"] == {"login": "kovalev", "name": "Пётр Ковалёв"}


# --- сессия --------------------------------------------------------------------

def test_подделка_срок_и_удалённый_снимок_это_401(authed):
    c, _, tmp = authed
    login(c, "kovalev")
    name = app.state.auth.cfg.cookie_name
    good = c.cookies[name]
    c.cookies.set(name, good[:-3] + "xyz")
    assert c.get("/api/auth/me").status_code == 401
    expired = session.sign(session.issue("kovalev", "local", -10), app.state.auth._secret)
    c.cookies.set(name, expired)
    assert c.get("/api/auth/me").status_code == 401
    c.cookies.set(name, good)
    assert c.get("/api/auth/me").status_code == 200
    (tmp / "auth" / "users" / "local" / "kovalev.json").unlink()
    assert c.get("/api/auth/me").status_code == 401, "снимок — это и есть сессия; удалил — разлогинил"


def test_скользящее_продление(authed):
    c, _, _ = authed
    login(c, "kovalev")
    name = app.state.auth.cfg.cookie_name
    assert "set-cookie" not in c.get("/api/auth/me").headers, "свежий токен не переиздаём"
    old = session.sign(session.issue("kovalev", "local", 14 * 86400, now=time.time() - 5 * 86400),
                       app.state.auth._secret)
    c.cookies.set(name, old)
    r = c.get("/api/auth/me")
    assert r.status_code == 200 and name in r.headers.get("set-cookie", "")


def test_выход(authed):
    c, _, _ = authed
    login(c, "kovalev")
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_лимит_попыток(authed):
    """Порог ниже блокировки в домене: сайт не должен быть местом, где коллега заблокировал
    себе почту, трижды промахнувшись по клавише."""
    c, directory, _ = authed
    for _ in range(3):
        assert login(c, "kovalev", "wrong").status_code == 401
    r = login(c, "kovalev", "pw")
    assert r.status_code == 429 and r.headers["retry-after"]
    r = c.post("/api/auth/login", json={"login": "kovalev", "password": "pw"},
               headers={"x-forwarded-for": "10.0.0.7"})
    assert r.status_code == 429, "лимит на ЛОГИН, не только на адрес"


# --- чистые модули ---------------------------------------------------------------

def test_scrypt_хэш():
    stored = local.hash_password("secret")
    assert stored.startswith("scrypt$") and local.verify_password("secret", stored)
    assert not local.verify_password("Secret", stored)
    assert not local.verify_password("secret", "plain") and not local.verify_password("", stored)
    with pytest.raises(ValueError):
        local.hash_password("")


def test_подпись_cookie():
    secret = b"k" * 32
    token = session.sign(session.issue("a", "local", 60, now=1000), secret)
    assert session.verify(token, secret, now=1030)["sub"] == "a"
    assert session.verify(token, secret, now=1061) is None
    assert session.verify(token, b"other" * 8, now=1030) is None
    assert session.verify(token + "x", secret, now=1030) is None
    assert session.verify("", secret) is None and session.verify("nodot", secret) is None


def test_безопасный_логин():
    assert store.safe_login("CORP\\Ivanov") == "ivanov"
    assert store.safe_login("ivanov@corp.example") == "ivanov"
    assert store.safe_login("  i.van-ov_2 ") == "i.van-ov_2"
    for bad in ("../x", "иванов", "", "a" * 70, ".start", "a b"):
        assert store.safe_login(bad) is None, bad


def test_схема_для_secure():
    assert session.is_https({"x-forwarded-proto": "https"}, "http", trusted_hops=1)
    assert not session.is_https({"x-forwarded-proto": "https"}, "http", trusted_hops=0)
    assert session.is_https({}, "https", trusted_hops=0)


def test_ключ_подписи_создаётся_один_раз(tmp_path):
    path = tmp_path / "auth" / "secret"
    first = session.load_or_create_secret(path)
    assert path.stat().st_mode & 0o777 == 0o600 and len(first) >= 32
    assert session.load_or_create_secret(path) == first


def test_signin_зарезервирован_роутером():
    from app.config import RESERVED_SLUGS
    assert "signin" in RESERVED_SLUGS
