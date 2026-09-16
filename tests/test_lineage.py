"""Каркас приехал копией из `morag-audio-web` — манифест не даёт копии разойтись молча.

Форка нет и общего пакета нет (почему — `docs/lineage.json`, ключ `_why`), поэтому единственная
страховка от расхождения — знать про КАЖДЫЙ файл каркаса, правили мы его или нет. Пути в обоих
репозиториях одинаковые, значит сверка с источником — это `diff` по списку `verbatim`.

Тест ловит три ошибки, каждая из которых иначе всплыла бы месяцы спустя:
  * правку в verbatim-файле без отметки в манифесте — «почему у нас не как у них?»;
  * новый файл каркаса, забытый в манифесте, — сверка его тихо не заметит;
  * запись о файле, которого больше нет.
"""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "docs" / "lineage.json").read_text(encoding="utf-8"))
FILES = MANIFEST["files"]

# Каркас — это код приложения, фронт и тесты. Всё остальное (корпус, tools/, docs/) наше и
# происхождения не имеет.
WATCHED = ("app", "web", "tests")
SKIP = {"__pycache__", "node_modules", ".pytest_cache", "data"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ignored() -> set[str]:
    """Файлы, которых git не видит: рабочий `app/config.yml`, локальный мусор.

    ⚠️ Спрашиваем git, а не список расширений. Каркас — это то, что ПОЕДЕТ в чужую репу,
    а не то, что лежит на этой машине; рабочий конфиг с адресами и ключами не поедет
    никогда, и требовать для него запись о происхождении бессмысленно.
    """
    out: set[str] = set()
    for top in WATCHED:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--others", "--ignored",
             "--exclude-standard", "--directory", top],
            capture_output=True, text=True,
        )
        out |= {line.strip().rstrip("/") for line in done.stdout.splitlines() if line.strip()}
    return out


def skeleton_files() -> list[str]:
    skip_paths = ignored()
    out = []
    for top in WATCHED:
        for path in sorted((ROOT / top).rglob("*")):
            rel = path.relative_to(ROOT).as_posix()
            if not path.is_file() or SKIP & set(path.parts) or path.suffix == ".pyc":
                continue
            if rel in skip_paths or any(rel.startswith(f"{d}/") for d in skip_paths):
                continue
            out.append(rel)
    return out


def test_every_skeleton_file_is_recorded():
    forgotten = [p for p in skeleton_files() if p not in FILES]
    assert not forgotten, (
        "нет записи в docs/lineage.json: " + ", ".join(forgotten) +
        ". Наш собственный файл — status 'ours', скопированный — 'verbatim' с sha256.")


def test_verbatim_files_are_byte_identical_to_the_source():
    changed = [p for p, meta in FILES.items()
               if meta.get("status") == "verbatim" and (ROOT / p).is_file()
               and sha256(ROOT / p) != meta["sha256"]]
    assert not changed, (
        "verbatim-файл правили, а в манифесте он всё ещё verbatim: " + ", ".join(changed) +
        ". Переведите его в 'edited' (sha256 при этом — хеш ИСХОДНОГО файла) или откатите правку.")


def test_manifest_has_no_records_of_deleted_files():
    gone = [p for p in FILES if not (ROOT / p).is_file()]
    assert not gone, "в манифесте есть, на диске нет: " + ", ".join(gone)
