#!/usr/bin/env python3
"""Пробник каталога: тот же вход, что у сайта, но из консоли и с подробным выводом.

    venv/bin/python tools/ldap_probe.py <логин> [--raw]

Читает `auth.ldap` из app/config.yml (`MORAG_WEB_CONFIG` — другой файл), пароль спрашивает
через getpass, зовёт `app.auth.ldap.authenticate` — ровно тот код, что работает на сайте, —
и печатает DN, имя, почту, должность, отдел, группы, размер фото; `--raw` — все атрибуты
записи (бинарные — как base64). Пароль не печатается и не логируется. Это и есть первая
проверка «домен принимает bind по шаблону и отдаёт человеку его запись»: если групп в выводе
нет, а в логе выше «свою запись не прочитал» — домен закрыл чтение, нужен запасной режим.

Запускать там, откуда виден каталог (на сервере — из корня чекаута, через venv сайта). Прокси
в окружении быть не должно: контроллер домена в контуре.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import ldap  # noqa: E402
from app.auth.local import BadCredentials  # noqa: E402
from app.auth.store import safe_login  # noqa: E402
from app.config import load_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="пробник входа через каталог")
    ap.add_argument("login")
    ap.add_argument("--raw", action="store_true", help="напечатать все атрибуты записи")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config().auth.ldap
    if not ldap.configured(cfg):
        print("в app/config.yml не заполнены auth.ldap.url / base_dn / bind_template", file=sys.stderr)
        return 2
    print("режим:       " + ("search-then-bind сервисной учёткой" if cfg.bind_dn
                            else f"своей учёткой, bind как {cfg.bind_template.format(login='<логин>')}"))
    login = safe_login(args.login)
    if not login:
        print(f"логин «{args.login}» не проходит белый список [a-z0-9._-]", file=sys.stderr)
        return 2
    password = getpass.getpass(f"пароль {login}: ")
    if args.raw:
        cfg = cfg.model_copy(update={"keep_raw_entry": True})
    try:
        profile = ldap.authenticate(cfg, login, password)
    except BadCredentials:
        print("отказ: неверный логин или пароль (подробность кода 49 — в логе выше)")
        return 1
    except ldap.LdapUnavailable as error:
        print(f"каталог недоступен: {error}")
        return 3

    print(f"DN:          {profile['dn']}")
    print(f"имя:         {profile['name']}")
    print(f"почта:       {profile['mail']}")
    print(f"должность:   {profile['title']}")
    print(f"отдел:       {profile['department']}")
    print(f"фото:        {len(profile['photo'])} байт" if profile["photo"] else "фото:        нет")
    print(f"группы ({len(profile['groups'])}):")
    for group in profile["groups"]:
        print(f"  {group}")
    if args.raw and profile.get("raw"):
        print("--- вся запись ---")
        for name, values in profile["raw"].items():
            for value in (values if isinstance(values, list) else [values]):
                shown = value if len(str(value)) < 200 else f"{str(value)[:80]}… ({len(str(value))} симв.)"
                print(f"  {name}: {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
