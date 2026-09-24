#!/usr/bin/env python3
"""Запись доразмечает себя: название, категория, темы, аннотация — одним шагом после сборки.

    python3 tools/auto_meta.py <каталог записи>            # всё, что умеет
    python3 tools/auto_meta.py <каталог записи> --dry      # показать, ничего не писать

Кому это нужно. Запись, загруженную через сайт (`app/api/upload.py`), человек описывает двумя
полями — файл и, если захочет, название. Остальное знает корпус: таксономия категорий и тем
лежит рядом со словарями, а сводка записи (`x_enriched.doc_summary`) — в сыром сайдкаре. Поэтому
разметку делает сервер, шагом `upload.after`, СРАЗУ после сборки и ДО индексации: поля шапки
уезжают в payload каждого чанка, и переиндексация не нужна.

Порядок шагов обязателен и вот почему:
  1. `classify` — пишет `classification` (и `title`, если у записи заголовка нет) в мету;
  2. пересборка `make_record` — `labels_of` читает `classification` и кладёт `category`/`topics`/
     `kind` в шапку; заголовок подставляется флагом `--title`, потому что из меты он не берётся;
  3. `make_blurb` — промпт читает УЖЕ ГОТОВУЮ шапку (заголовок, ветку, категорию), поэтому он
     последний; и только если автор не написал аннотацию сам — его текст важнее сочинённого.

Каждый шаг необязателен (ключи `--no-*`), и ни один не роняет остальные: не разметилось — запись
просто остаётся без категории, это видно в фильтрах и чинится плановым `classify.py --all`.

Адрес, модель и ключ шлюза — как у всех инструментов: `ASR_LLM_BASE_URL`, `ASR_LLM_MODEL`,
`OR_KEY` из окружения, иначе из файла стека (`$ASR_STACK_ENV`). Сайт передаёт их шагу сам, взяв
из своего конфига (`topic.base_url/model` и ключ движка) — отдельного файла на сервере не нужно.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PLACEHOLDER = {"видео", "video", "запись", "record", "без названия", "untitled"}


def head_of(record: Path) -> dict:
    """Шапка записи — тем же разбором, что у сборщика (пустая, если записи ещё нет)."""
    from make_record import parse_head, split_head  # noqa: PLC0415

    md = record / "record.md"
    if not md.is_file():
        return {}
    head, _ = split_head(md.read_text(encoding="utf-8"))
    return parse_head(head) or {}


def meta_of(record: Path) -> dict:
    path = record / "record.meta.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return {}


def needs_title(head: dict, record_id: str, meta: dict | None = None) -> bool:
    """Нужно ли придумывать заголовок.

    ⚠️ Главный признак — не догадка, а слово загрузившего: приложение говорит, подставило ли оно
    название из имени файла (`sources.upload.title_from == "file"`). Само по себе имя файла бывает
    осмысленным («Kafka без боли.mp4»), и угадать по тексту нельзя. Догадки остаются запасным
    ходом для тех, кто грузит из командной строки: пусто, «видео», сам идентификатор записи.
    """
    if ((meta or {}).get("sources") or {}).get("upload", {}).get("title_from") == "file":
        return True
    title = str(head.get("title") or "").strip()
    if not title:
        return True
    low = title.lower()
    return low in PLACEHOLDER or low == record_id.lower() or low == Path(record_id).stem.lower()


def run(argv: list[str], dry: bool) -> tuple[bool, str]:
    """Шаг конвейера. Возвращает (получилось, хвост вывода) — упавший шаг не останавливает остальные."""
    print(f"  → {' '.join(Path(a).name if a.startswith('/') else a for a in argv[1:4])}…", flush=True)
    if dry:
        return True, "(--dry)"
    done = subprocess.run(argv, cwd=str(HERE.parent), capture_output=True, text=True)
    tail = (done.stdout + done.stderr).strip()[-500:]
    if done.returncode:
        print(f"  ⚠️ не получилось (код {done.returncode}): {tail[-300:]}", flush=True)
        return False, tail
    return True, tail


def enrich(record: Path, *, classify: bool = True, title: bool = True, blurb: bool = True,
           dry: bool = False) -> dict:
    """Разметить одну запись. Возвращает, что удалось сделать."""
    record = record.resolve()
    if not (record / "record.md").is_file():
        raise SystemExit(f"нет {record}/record.md — размечать нечего")
    rid = record.name
    out: dict = {"id": rid, "classified": False, "titled": "", "blurb": False}
    py = sys.executable
    head = head_of(record)

    if classify:
        ok, _ = run([py, str(HERE / "classify.py"), "--all", rid] + (["--dry"] if dry else []), dry)
        out["classified"] = ok

    # Заголовок: берём тот, что предложил классификатор, и отдаём сборщику флагом — из меты
    # `make_record` его не читает (там только category/topics/kind).
    want_title = title and needs_title(head, rid, meta_of(record))
    new_title = ""
    if want_title:
        new_title = str((meta_of(record).get("classification") or {}).get("title_auto") or "").strip()
        if new_title:
            print(f"  название: «{new_title}»", flush=True)
            out["titled"] = new_title
        else:
            print("  название не предложено — оставляю как есть", flush=True)

    rebuild = [py, str(HERE / "make_record.py"), str(record)]
    if new_title:
        rebuild += ["--title", new_title]
    if not dry:
        run(rebuild, dry)

    if blurb:
        # Авторская аннотация важнее сочинённой, а одиночный режим `make_blurb` свою проверку
        # пропускает — поэтому решаем здесь, по свежей шапке.
        if str(head_of(record).get("summary") or "").strip():
            print("  аннотация автора на месте — свою не сочиняю", flush=True)
        else:
            ok, _ = run([py, str(HERE / "make_blurb.py"), str(record)] + (["--dry"] if dry else []), dry)
            out["blurb"] = ok
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="доразметить запись: название, категория, темы, аннотация")
    ap.add_argument("record", type=Path, help="каталог записи")
    ap.add_argument("--no-classify", action="store_true")
    ap.add_argument("--no-title", action="store_true")
    ap.add_argument("--no-blurb", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    out = enrich(args.record, classify=not args.no_classify, title=not args.no_title,
                 blurb=not args.no_blurb, dry=args.dry)
    head = head_of(Path(args.record))
    print(f"готово: {out['id']} — категория «{head.get('category') or '—'}», "
          f"тем {len(head.get('topics') or [])}, аннотация {'есть' if out['blurb'] else 'нет'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
