"""Копии сайта в `tools/ui/`: токены, шрифты, помощники DOM.

Окно загрузки ставится коллеге с ЗЕРКАЛА и работает офлайн — ссылаться на файлы сайта ему нечем,
поэтому токены и шрифты лежат копией рядом. Копия без проверки разъезжается молча: сайт
перекрасят, окно останется прежним, и через месяц никто не вспомнит почему.

⚠️ Проверяем побайтово. «Похоже» здесь не годится: смысл копии в том, что окно выглядит ровно
как сайт, и любая разница — это уже разъезд.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "tools" / "ui"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_tokens_are_a_byte_for_byte_copy_of_the_site():
    assert sha(UI / "tokens.css") == sha(REPO / "web" / "css" / "tokens.css")


def test_all_six_fonts_are_byte_for_byte_copies():
    theirs = sorted((REPO / "web" / "assets" / "fonts").glob("*.woff2"))
    ours = sorted((UI / "fonts").glob("*.woff2"))
    assert [p.name for p in ours] == [p.name for p in theirs], "состав шрифтов совпадает"
    for a, b in zip(ours, theirs):
        assert sha(a) == sha(b), f"{a.name} разъехался с сайтом"


def test_the_font_css_is_the_site_one_with_local_paths():
    """Отличаться ей позволено РОВНО одним: путями до своей папки."""
    mine = (UI / "fonts.css").read_text(encoding="utf-8")
    theirs = (REPO / "web" / "css" / "fonts.css").read_text(encoding="utf-8")
    assert mine.endswith(theirs.replace('url("../assets/fonts/', 'url("./fonts/'))
    assert "../assets" not in mine, "путь сайта в копии не работает"


def test_the_page_carries_no_palette_of_its_own():
    """⚠️ Своя палитра — то, из-за чего окно выглядело чужой утилитой. Цвета берутся токенами."""
    page = (REPO / "tools" / "upload-ui.html").read_text(encoding="utf-8")
    css = (UI / "upload.css").read_text(encoding="utf-8")
    for line in css.splitlines():
        if line.strip().startswith("/*") or "*/" in line:
            continue
        assert "#" not in line or "var(" in line, f"жёсткий цвет в стилях окна: {line.strip()}"
    assert "prefers-color-scheme" not in page + css, \
        "тема задаётся data-theme, как на сайте, а не системой"


def test_reduced_motion_is_honoured_in_css_and_in_js():
    """⚠️ Canvas под медиа-запрос НЕ попадает — сцену волны обязан гасить сам скрипт."""
    css = (UI / "upload.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css
    assert "prefers-reduced-motion" in (UI / "dom.js").read_text(encoding="utf-8")
    assert "reduced()" in (UI / "wave.js").read_text(encoding="utf-8")
