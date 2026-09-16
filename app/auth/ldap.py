"""Каталог AD/LDAP: вход своей учёткой, без сервисной (или с ней — запасной режим).

**Без сервисной (`bind_dn` пуст):** имя для bind'а строится из логина по `bind_template`
(`{login}@corp.example` — AD принимает UPN, DN искать не нужно), контроллер САМ сверяет пароль —
`bind` и есть операция «проверь, что я такой-то», — а той же связью, уже как этот человек,
читается его собственная запись: имя, должность, отдел, группы, фото. На сервере не лежит
ничего, чем можно войти. Так входит в домен и обычный Windows-ноутбук.

**С сервисной:** search-then-bind — сервисная связь находит DN по `login_attribute`, пароль
человека проверяется его bind'ом по найденному DN. Нужно только там, где сотруднику запрещено
читать собственные группы. ⚠️ Учётка AD, даже бесправная, читает весь каталог.

Единственный модуль с `ldap3`, и импорт — внутри функции: приложение и тесты живут без
библиотеки (каталог доступен только с сервера; в тестах эту функцию подменяют или подменяют
сам `ldap3`), а `tools/ldap_probe.py` зовёт её же из консоли.

⚠️ Пустой пароль отсекается ДО сети: simple bind с пустым паролем — это анонимный bind, и
каталог отвечает на него УСПЕХОМ. Классическая дыра, ловится не тестом на каталоге, а знанием.
⚠️ Логин экранируется в фильтре поиска (`escape_filter_chars`), иначе `*` или `)(` в поле
логина превращаются в свой запрос к каталогу. В шаблон bind'а логин подставляется уже
прошедшим белый список `[a-z0-9._-]` (`store.safe_login`) — там экранировать нечего.
"""

from __future__ import annotations

import base64
import logging

from .local import BadCredentials

log = logging.getLogger(__name__)


class LdapUnavailable(Exception):
    """Каталог не отвечает или не настроен: снаружи это 503 «попробуйте позже», а не 401 —
    человек не должен думать, что ошибся паролем."""


def _values(value) -> list[str]:
    """Значение атрибута как список строк: без схемы ldap3 отдаёт то str, то list, то bytes."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for item in items:
        if isinstance(item, bytes):
            try:
                out.append(item.decode("utf-8"))
            except UnicodeDecodeError:
                continue
        elif item is not None:
            out.append(str(item))
    return out


def _scalar(attrs: dict, name: str) -> str:
    values = _values(attrs.get(name))
    return values[0].strip() if values else ""


def _raw_entry(dn: str, raw_attrs: dict) -> dict:
    """Вся запись каталога для разового просмотра: текст — как есть, бинарное — base64."""
    out: dict = {"dn": dn}
    for name, values in (raw_attrs or {}).items():
        shown = []
        for v in values if isinstance(values, (list, tuple)) else [values]:
            if isinstance(v, bytes):
                try:
                    shown.append(v.decode("utf-8"))
                except UnicodeDecodeError:
                    shown.append("base64:" + base64.b64encode(v).decode("ascii"))
            else:
                shown.append(str(v))
        out[name] = shown
    return out


def _profile(login: str, entry: dict | None, keep_raw: bool) -> dict:
    """Профиль той же формы, что у локального пользователя. Записи нет (каталог не отдал
    свою же запись человеку) — имя = логин, групп нет: человек вошёл, роль будет по умолчанию."""
    if entry is None:
        return {"login": login, "provider": "ldap", "name": login, "given": "", "surname": "",
                "mail": "", "title": "", "department": "", "groups": [], "dn": "", "photo": None, "raw": None}
    attrs = entry.get("attributes") or {}
    raw = entry.get("raw_attributes") or {}
    photos = raw.get("thumbnailPhoto") or raw.get("jpegPhoto") or []
    photo = photos[0] if photos and isinstance(photos[0], bytes) else None
    dn = entry.get("dn") or ""
    return {
        "login": login,
        "provider": "ldap",
        "name": _scalar(attrs, "displayName") or _scalar(attrs, "cn") or login,
        "given": _scalar(attrs, "givenName"),
        "surname": _scalar(attrs, "sn"),
        "mail": _scalar(attrs, "mail"),
        "title": _scalar(attrs, "title"),
        "department": _scalar(attrs, "department"),
        "groups": _values(attrs.get("memberOf")),
        "dn": dn,
        "photo": photo,
        "raw": _raw_entry(dn, raw) if keep_raw else None,
    }


def configured(cfg) -> bool:
    """Хватает ли конфига для входа: адрес, base DN и либо шаблон bind'а, либо сервисная."""
    return bool(cfg.url and cfg.base_dn and (cfg.bind_template or cfg.bind_dn))


def authenticate(cfg, login: str, password: str) -> dict:
    """Профиль вошедшего или исключение: `BadCredentials` — нет такого / неверный пароль;
    `LdapUnavailable` — каталог недоступен или не настроен."""
    if not password:
        raise BadCredentials()
    if not configured(cfg):
        raise LdapUnavailable("каталог не настроен: auth.ldap.url / base_dn / bind_template")

    import ssl

    try:
        import ldap3
        from ldap3.core import exceptions as errors
        from ldap3.utils.conv import escape_filter_chars
    except ImportError:
        # Библиотека ставится `push`'ем на сервере; без неё вход через каталог — «недоступен»,
        # а не пятисотка.
        log.error("каталог: библиотека ldap3 не установлена (app/requirements.txt)")
        raise LdapUnavailable("ldap3 не установлен") from None

    tls = None
    if cfg.url.lower().startswith("ldaps://") or cfg.ca_file or not cfg.verify_tls:
        tls = ldap3.Tls(
            validate=ssl.CERT_REQUIRED if cfg.verify_tls else ssl.CERT_NONE,
            ca_certs_file=cfg.ca_file or None,
        )
    # ⚠️ Таймауты — ЦЕЛЫЕ. `receive_timeout` ldap3 на Linux пакует `struct.pack('LL', …)`, и
    # float из конфига (`5.0`) роняет открытие сокета на КАЖДОМ контроллере: «unable to open
    # socket … required argument is not an integer». Ловилось на сервере первым же пробником;
    # на ноутбуке — нет, там пробовали с целым.
    timeout = max(1, int(round(cfg.timeout)))
    server = ldap3.Server(cfg.url, get_info=ldap3.NONE, connect_timeout=timeout, tls=tls)
    common = dict(auto_bind=True, raise_exceptions=True, read_only=True,
                  receive_timeout=timeout, auto_referrals=False)
    attributes = [ldap3.ALL_ATTRIBUTES] if cfg.keep_raw_entry else list(cfg.attributes)
    filt = f"({cfg.login_attribute}={escape_filter_chars(login)})"

    def search(conn, who: str) -> list[dict]:
        conn.search(cfg.base_dn, filt, ldap3.SUBTREE, attributes=attributes,
                    size_limit=2, time_limit=timeout)
        return [e for e in conn.response or [] if e.get("type") == "searchResEntry"]

    def bind(who: str, secret: str):
        """Связь от чьего-то имени. Код 49 с любым data (неверный, заблокирован, просрочен)
        снаружи один: «неверный логин или пароль»; подробность — в лог для владельца."""
        try:
            return ldap3.Connection(server, user=who, password=secret, **common)
        except (errors.LDAPBindError, errors.LDAPOperationResult) as error:
            log.info("каталог: отказ входа %s: %s", who, _brief(error))
            raise BadCredentials() from None
        except errors.LDAPExceptionError as error:
            log.warning("каталог: связь для %s не удалась: %s", who, _brief(error))
            raise LdapUnavailable(_brief(error)) from None

    def close(conn) -> None:
        try:
            conn.unbind()
        except Exception:  # noqa: BLE001 — на выходе нам всё равно, что там с сокетом
            pass

    if not cfg.bind_dn:
        # --- своей учёткой: bind по шаблону, потом чтение своей записи ---------------------
        who = cfg.bind_template.format(login=login)
        person = bind(who, password)
        try:
            try:
                entries = search(person, who)
            except errors.LDAPExceptionError as error:
                # Вошёл, но записи не прочитать (закрыли чтение? кривой base_dn?) — пускаем с
                # ролью по умолчанию и кричим в лог: молча это выглядело бы как «группы не работают».
                log.warning("каталог: %s вошёл, но свою запись не прочитал: %s", login, _brief(error))
                entries = []
        finally:
            close(person)
        if len(entries) != 1:
            log.warning("каталог: %s вошёл, записей по фильтру %s — %d; групп и имени не будет",
                        login, filt, len(entries))
        return _profile(login, entries[0] if len(entries) == 1 else None, cfg.keep_raw_entry)

    # --- с сервисной: search-then-bind -----------------------------------------------------
    # Отказ сервисной — НАША ошибка настройки, а не человека: 503, не 401.
    try:
        service = ldap3.Connection(server, user=cfg.bind_dn, password=cfg.bind_password, **common)
    except errors.LDAPExceptionError as error:
        log.warning("каталог: сервисная связь не удалась: %s", _brief(error))
        raise LdapUnavailable(_brief(error)) from None
    try:
        try:
            entries = search(service, cfg.bind_dn)
        except errors.LDAPExceptionError as error:
            log.warning("каталог: поиск %s не удался: %s", login, _brief(error))
            raise LdapUnavailable(_brief(error)) from None
    finally:
        close(service)
    if len(entries) != 1:
        log.info("каталог: логин %s — записей %d", login, len(entries))
        raise BadCredentials()
    entry = entries[0]
    close(bind(entry["dn"], password))
    return _profile(login, entry, cfg.keep_raw_entry)


def _brief(error: Exception) -> str:
    """Текст ошибки без пароля: ldap3 паролей в сообщения не кладёт, но длинные дампы режем."""
    text = str(error) or error.__class__.__name__
    return text[:300]
