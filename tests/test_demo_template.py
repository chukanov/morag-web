"""Демо-корпус — то, что видит любой, кто повторяет платформу. Его `site.yml` правили руками
(цвета веток, пресеты вопросов), а генератор `tools/make_demo.py --force` переписал бы файл
своим шаблоном и молча откатил правки. Шаблон и файл обязаны совпадать."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_make_demo_template_matches_the_committed_site_yml():
    src = (REPO / "tools" / "make_demo.py").read_text(encoding="utf-8")
    i = src.index('SITE_YML = """\\\n') + len('SITE_YML = """\\\n')
    j = src.index('"""', i)
    assert src[i:j] == (REPO / "corpora" / "demo" / "site.yml").read_text(encoding="utf-8"), \
        "шаблон в make_demo.py разошёлся с corpora/demo/site.yml — правьте оба"
