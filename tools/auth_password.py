#!/usr/bin/env python3
"""Хэш пароля для локального пользователя сайта (`auth.users` в app/config.yml).

    python3 tools/auth_password.py <логин>

Пароль спрашивается дважды через getpass — не аргументом и не через stdin-пайп: аргумент
остаётся в истории оболочки и в `ps`. Печатает готовую строку для `auth.users`.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.local import hash_password  # noqa: E402


def main() -> int:
    login = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""
    if not login:
        print("нужен логин: python3 tools/auth_password.py <логин>", file=sys.stderr)
        return 2
    first = getpass.getpass(f"пароль для {login}: ")
    second = getpass.getpass("ещё раз: ")
    if not first or first != second:
        print("пароли не совпали или пусты", file=sys.stderr)
        return 1
    print("  users:")
    print(f"    - login: {login}")
    print(f"      name: \"\"                # как показывать в шапке и в подписи правок")
    print(f"      password: \"{hash_password(first)}\"")
    print(f"      role: \"\"                # пусто — по roles.default; admin — имена голосов и словарь")
    return 0


if __name__ == "__main__":
    sys.exit(main())
