"""Пути в конфиге — от файла конфига, не от `app/` (шаг к разделению на публичный morag-web и
приватный корпус). Иначе конфиг корпуса из соседнего репозитория не нашёл бы корпус, а
`./data/auth` (ключ cookie, снимки учёток) молча уехал бы в каталог кода."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.config import APP_DIR, CORPUS_ENV, family_dir, load_config  # noqa: E402


def test_относительные_пути_от_каталога_конфига(tmp_path):
    cfg_dir = tmp_path / "corp" / "app"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yml").write_text(
        "corpora:\n  - dir: ../corpora/x\njournal:\n  path: ./data/j.jsonl\n"
        "auth:\n  data_dir: ./data/auth\nlimits:\n  rate_limit:\n    state_path: ./data/rl.json\n",
        encoding="utf-8")
    cfg = load_config(cfg_dir / "config.yml")
    assert Path(cfg.corpora[0].dir) == (tmp_path / "corp" / "corpora" / "x").resolve()
    assert Path(cfg.journal.path) == (cfg_dir / "data" / "j.jsonl").resolve()
    assert Path(cfg.auth.data_dir) == (cfg_dir / "data" / "auth").resolve()
    assert Path(cfg.limits.rate_limit.state_path) == (cfg_dir / "data" / "rl.json").resolve()
    assert family_dir(cfg) == (tmp_path / "corp" / "corpora" / "x").resolve()
    # Статика — самого приложения: как была, от app/.
    assert cfg.server.web_root == "../web"


def test_пример_конфига_ведёт_на_демо_от_app():
    """Слой примера лежит в `app/` — его пути считаются от `app/`, как и раньше."""
    cfg = load_config(APP_DIR / "config.example.yml")
    assert Path(cfg.corpora[0].dir) == (APP_DIR / ".." / "corpora" / "demo").resolve()
    assert Path(cfg.auth.data_dir) == (APP_DIR / "data" / "auth").resolve()


def test_унаследованный_путь_остаётся_у_слоя_где_записан(tmp_path):
    """Конфиг корпуса не назвал `auth.data_dir` — берётся из примера и считается от `app/`.
    Хочешь данные рядом с корпусом — назови путь у себя (три строки `./data/…`)."""
    (tmp_path / "config.yml").write_text("corpora:\n  - dir: ./c\n", encoding="utf-8")
    cfg = load_config(tmp_path / "config.yml")
    assert Path(cfg.corpora[0].dir) == (tmp_path / "c").resolve()
    assert Path(cfg.auth.data_dir) == (APP_DIR / "data" / "auth").resolve()


def test_корпус_из_env_перебивает_список(tmp_path, monkeypatch):
    (tmp_path / "config.yml").write_text("corpora:\n  - dir: ./c\n  - dir: ./d\n", encoding="utf-8")
    monkeypatch.setenv(CORPUS_ENV, str(tmp_path / "only"))
    cfg = load_config(tmp_path / "config.yml")
    assert [Path(c.dir) for c in cfg.corpora] == [(tmp_path / "only").resolve()]
    assert family_dir(cfg) == (tmp_path / "only").resolve()
    # Служебные переменные не становятся полями конфига.
    assert not hasattr(cfg, "corpus")
