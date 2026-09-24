"""Роли и права: `viewer` < `editor` < `admin`.

Таблица прав — ЗДЕСЬ и только здесь. Ручка спрашивает `allows(role, PERMISSIONS["voices"])`,
а не сравнивает строки сама: одно место, где сказано «имена голосов — админам», и ноль мест,
где это повторено. Решение владельца 16.09: реплики правят все вошедшие (правка меняет одну
запись), имена голосов и правила словаря — только админы (действуют на весь корпус).
"""

from __future__ import annotations

from fnmatch import fnmatchcase

ROLES = ("viewer", "editor", "admin")

# Право → минимальная роль. `edit` — правка реплик и их снятие; `voices` — имя голоса и
# «починить везде» (правило словаря на весь корпус); `claim` — «это я»: назвать БЕЗЫМЯННЫЙ
# голос собой (имя берётся из сессии, сервер отказывает, если голос уже назван) — смысл кнопки в
# том, что сотрудник сам находит себя в записи, поэтому право у любого вошедшего.
# `upload` — загрузить свою запись (транскрибированную у себя): любой вошедший, как правка реплик.
PERMISSIONS = {"edit": "editor", "voices": "admin", "claim": "editor", "upload": "editor"}


def rank(role: str | None) -> int:
    return ROLES.index(role) if role in ROLES else -1


def normalize(role: str | None) -> str:
    """Роль из конфига: регистр и пробелы не важны; незнакомая — пусто (а не тихий viewer:
    решает вызывающий, чем заменить)."""
    text = (role or "").strip().lower()
    return text if text in ROLES else ""


def allows(role: str | None, need: str) -> bool:
    return 0 <= rank(need) <= rank(role)


def _cn(dn: str) -> str:
    """`CN=site-admins,OU=groups,DC=example,DC=org` → `site-admins`. Первый RDN, без учёта
    регистра ключа. Экранированные запятые в именах групп встречаются редко и здесь не
    разбираются — такую группу задают полным DN."""
    head = dn.split(",", 1)[0]
    _, _, value = head.partition("=")
    return value.strip() if value else head.strip()


def role_for(cfg, *, login: str, groups: list[str], local_role: str = "") -> str:
    """Роль вошедшего. Порядок: `by_user` > `by_group` > роль локального пользователя > `default`.

    Считается на каждый запрос, а не при входе: сменил конфиг — роль поменялась без
    перелогина. Из нескольких подошедших групп берётся СТАРШАЯ роль. Ключ `by_group` — полный
    DN или один CN, регистр не важен.
    """
    login = (login or "").lower()
    for user, role in (cfg.by_user or {}).items():
        if str(user).lower() == login and normalize(role):
            return normalize(role)

    best = ""
    if cfg.by_group and groups:
        wanted = {str(k).lower(): normalize(v) for k, v in cfg.by_group.items() if normalize(v)}
        for group in groups:
            g = str(group)
            for key in (g.lower(), _cn(g).lower()):
                role = wanted.get(key, "")
                if rank(role) > rank(best):
                    best = role
    if best:
        return best
    if normalize(local_role):
        return normalize(local_role)
    # Незнакомое умолчание в конфиге — это опечатка, а не «пусть будет admin»: даём минимум.
    return normalize(cfg.default) or "viewer"


def member_of(groups: list[str], patterns: list[str]) -> bool:
    """Состоит ли человек хотя бы в одной группе по маскам (`*_site-users`). Маска сверяется и
    с CN, и с полным DN, без учёта регистра. Пустой список масок — правила нет, проходят все."""
    masks = [str(m).strip().lower() for m in patterns or [] if str(m).strip()]
    if not masks:
        return True
    for group in groups or []:
        g = str(group)
        for candidate in (g.lower(), _cn(g).lower()):
            if any(fnmatchcase(candidate, mask) for mask in masks):
                return True
    return False


def capabilities(role: str | None, editing_enabled: bool, upload_enabled: bool = False) -> dict[str, bool]:
    """Что может человек с этой ролью. Флаг включения — первый рубеж, он старше любой роли:
    выключенная правка выключена и для админа. У загрузки записей флаг свой (`upload.enabled`)."""
    flags = {name: (upload_enabled if name == "upload" else editing_enabled) for name in PERMISSIONS}
    return {name: bool(flags[name] and allows(role, need)) for name, need in PERMISSIONS.items()}
