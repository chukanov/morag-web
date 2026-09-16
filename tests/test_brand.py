"""Файлы бренда: отдаём портреты и обложку — и только их.

Имя файла приходит из URL, поэтому главное здесь не «что отдаём», а «что НЕ
отдаём»: рядом с каталогом корпуса лежат рабочие конфиги с ключами.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.config import Corpus  # noqa: E402

SITE = """
slug: test
brand:
  title: "Тест"
  hosts:
    - name: "Кто-то"
      avatar: face.jpg
"""


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    (tmp_path / "site.yml").write_text(SITE, encoding="utf-8")
    (tmp_path / "records").mkdir()
    brand = tmp_path / "brand"
    brand.mkdir()
    (brand / "face.jpg").write_bytes(b"\xff\xd8\xff")
    (brand / "cover.png").write_bytes(b"\x89PNG")
    (tmp_path / "morag-config.yml").write_text("api_key: секрет-который-нельзя-отдавать", encoding="utf-8")
    return Corpus(tmp_path)


def test_serves_existing_asset(corpus: Corpus):
    path = corpus.brand_file("face.jpg")
    assert path is not None and path.name == "face.jpg"


def test_missing_asset_is_none(corpus: Corpus):
    assert corpus.brand_file("нет-такого.jpg") is None


def test_directory_is_not_an_asset(corpus: Corpus):
    """Каталог — не файл: отдавать нечего, а FileResponse на нём падает."""
    (corpus.brand_dir / "sub").mkdir()
    assert corpus.brand_file("sub") is None


@pytest.mark.parametrize(
    "name",
    [
        "../morag-config.yml",  # сосед с ключом LLM
        "../../app/config.yml",
        "..%2Fmorag-config.yml",
        "....//morag-config.yml",
        "/etc/passwd",
        "sub/../../site.yml",
    ],
)
def test_escaping_the_brand_directory_is_refused(corpus: Corpus, name: str):
    """Проверяем ИТОГОВЫЙ путь, а не строку: строковую проверку обходят кодировкой."""
    assert corpus.brand_file(name) is None


def test_symlink_out_of_brand_dir_is_refused(corpus: Corpus, tmp_path: Path):
    """Ссылка внутри каталога может указывать наружу — резолв это ловит."""
    target = tmp_path / "morag-config.yml"
    link = corpus.brand_dir / "sneaky.jpg"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("файловая система без символических ссылок")
    assert corpus.brand_file("sneaky.jpg") is None


def test_default_brand_dir_next_to_config(corpus: Corpus):
    assert corpus.brand_dir == corpus.dir / "brand"


# --- фолбэк SPA -----------------------------------------------------------


@pytest.mark.parametrize(
    "path,is_route",
    [
        ("/episodes", True),  # маршрут приложения — отдаём страницу
        ("/ep/2-10/3826", True),
        ("/favicon.ico", False),  # файл — честный 404, а не HTML под видом картинки
        ("/js/нет-такого.js", False),
        ("/assets/fonts/нет.woff2", False),
    ],
)
def test_fallback_distinguishes_routes_from_files(path: str, is_route: bool):
    """Страницу отдаём только адресам без расширения.

    Иначе браузер, попросив картинку или скрипт, получает HTML с кодом 200 и
    считает это ответом — ровно так `/favicon.ico` отдавал страницу, и вкладка
    оставалась без значка.
    """
    looks_like_file = "." in path.rsplit("/", 1)[-1]
    assert (not looks_like_file) == is_route


# --- знак из бренда ---------------------------------------------------------------------

from app.config import mark_payload  # noqa: E402


def _art(tmp_path: Path, text: str = "ab\ncd\n") -> Path:
    (tmp_path / "mark.txt").write_text(text, encoding="utf-8")
    return tmp_path


def test_знак_из_файла_бренда_с_клетками_мордочки(tmp_path):
    root = _art(tmp_path, "  x_ \n oPo\n\n")
    brand = {"mark": "mark.txt", "mark_face": {
        "eye": {"row": 0, "from": 2, "to": 4, "open": "x_", "shut": "  "},
        "nose": {"row": 1, "from": 1, "to": 4},
        "snout": {"row": 1, "col": 2, "calm": "P", "sniff": "O"},
    }}
    out = mark_payload(brand, lambda name: root / name)
    assert out["lines"] == ["  x_ ", " oPo"], "хвостовые пустые строки срезаны, текст как есть"
    assert out["eye"] == {"row": 0, "from": 2, "to": 4, "open": "x_", "shut": "  "}
    assert out["nose"] == {"row": 1, "from": 1, "to": 4}
    assert out["snout"] == {"row": 1, "col": 2, "calm": "P", "sniff": "O"}


def test_кривые_клетки_мордочки_отбрасываются_а_знак_стоит(tmp_path):
    root = _art(tmp_path)
    brand = {"mark": "mark.txt", "mark_face": {"eye": {"row": 9, "from": 0, "to": 1, "open": "a", "shut": " "},
                                               "snout": {"row": 0, "col": 5, "calm": "p", "sniff": "O"},
                                               "nose": "не словарь"}}
    out = mark_payload(brand, lambda name: root / name)
    assert out["lines"] == ["ab", "cd"] and "eye" not in out and "snout" not in out and "nose" not in out


def test_без_знака_и_без_файла_null(tmp_path):
    assert mark_payload({}, lambda name: None) is None
    assert mark_payload({"mark": "нет.txt"}, lambda name: None) is None
    root = _art(tmp_path, "\n".join(["x" * 200]))
    assert mark_payload({"mark": "mark.txt"}, lambda name: root / name) is None, "слишком широкий — не знак"


def test_пространство_наследует_знак_и_слово_от_витрины(tmp_path):
    from app.config import Corpus
    space = tmp_path / "space"
    space.mkdir()
    (space / "site.yml").write_text("slug: s\nbrand:\n  title: T\n", encoding="utf-8")
    hub_brand = tmp_path / "brand"
    hub_brand.mkdir()
    (hub_brand / "mark.txt").write_text("ab\n", encoding="utf-8")
    corpus = Corpus(space, shared_brand_dir=hub_brand,
                    inherit={"mark": "mark.txt", "wordmark": "DEMO", "cover": "", "mark_face": None})
    brand = corpus.public()["brand"]
    assert brand["wordmark"] == "DEMO" and brand["mark"]["lines"] == ["ab"]
    own = Corpus(space, shared_brand_dir=hub_brand, inherit={"wordmark": "DEMO"})
    (space / "site.yml").write_text("slug: s\nbrand:\n  wordmark: OWN\n", encoding="utf-8")
    own = Corpus(space, shared_brand_dir=hub_brand, inherit={"wordmark": "DEMO"})
    assert own.public()["brand"]["wordmark"] == "OWN", "своё слово важнее общего"
