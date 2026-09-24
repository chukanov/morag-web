#!/usr/bin/env python3
"""Кто говорит в записи — спросить у сервера и переписать номера голосов.

    python3 tools/voices_assign.py <каталог записи> [--audio звук] [--dry]

Зачем. Реестр голосов живёт на сервере и только там (решение владельца 24.09): номера присваивает
он, по отпечаткам. Этот инструмент — та же дорога для записей, которые собраны мимо приёма: свежий
прогон на своей машине или запись, принятая ДО того, как узнавание появилось (её голоса тогда
развели в отдельный диапазон, и знакомые люди выглядят незнакомцами).

Что делает: считает отпечатки (CAM++ из поднятого стека), просит у сайта карту
(`POST /api/voices/identify`), переписывает метки в сыром сайдкаре и в ссылках экрана.

⚠️ **Имена переезжают вместе с номерами.** Если голос уже назван в словаре под старым номером, а
запись пересобрать с новым — `make_record` НИЧЕГО не скажет: он ищет старую метку в тексте, не
находит и молча идёт дальше. Имя осиротеет, а человек увидит безымянный голос там, где вчера было
имя. Поэтому словарь правится здесь же, одной транзакцией с сайдкаром.

⚠️ Пересборку и индексацию запускает человек: инструмент меняет данные записи, и решать, когда
корпус их подхватит, должен тот, кто видит остальное хозяйство.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ingest          # noqa: E402 — сессия сайта и его клиент
import voiceprints     # noqa: E402

SIDECAR = "record.json"
REFS = "record.refs.json"


def remap(text: str, mapping: dict[str, str]) -> str:
    """Одним проходом со словарём: узнавание возвращает перестановки, а цепочка замен схлопнула
    бы два голоса в один (тот же приём, что у приёма записи — `app/content/ingest.py`)."""
    import re

    return re.sub(r"\bSpeaker_\d+\b", lambda m: mapping.get(m.group(0), m.group(0)), text)


def corpus_names(record: Path) -> Path | None:
    """Словарь имён корпуса: вверх по дереву до каталога, где он лежит."""
    for parent in [record, *record.parents]:
        candidate = parent / "names.json"
        if candidate.is_file():
            return candidate
    return None


def elsewhere(record: Path, label: str) -> list[str]:
    """В каких ЕЩЁ записях корпуса звучит этот номер (по сырым сайдкарам).

    ⚠️⚠️ Без этой проверки перенос имени — способ испортить весь корпус. Метка приехавшей
    записи может СОВПАСТЬ с номером корпуса случайно (у чужой машины свой счёт голосов, её
    `Speaker_3` не наш), а у нашего `Speaker_3` уже стоит имя. Слепой перенос увёл бы имя
    живого человека с его сорока записей на одного незнакомца — и молча.
    """
    import re

    needle = re.compile(rf"\b{re.escape(label)}\b")
    found = []
    for path in sorted(record.parent.parent.parent.rglob(f"*/{SIDECAR}")):
        if path.parent == record:
            continue
        if needle.search(path.read_text(encoding="utf-8", errors="ignore")):
            found.append(path.parent.name)
    return found


def drop_conflicts(record: Path, names: Path | None, mapping: dict[str, str]) -> dict[str, str]:
    """Убрать из карты то, что переносить НЕЛЬЗЯ. Возвращает безопасную карту.

    ⚠️⚠️ Правило одно: **номер, который звучит и в других записях корпуса, не переименовывается
    этой записью**. Иначе имя живого человека уехало бы с его сорока записей на одного
    незнакомца — метка приехавшей записи может совпасть с корпусным номером случайно (у чужой
    машины свой счёт голосов, её `Speaker_3` не наш), а у нашего `Speaker_3` уже стоит имя.
    Такое совпадение — не перенумерация, а столкновение, и решает его человек.

    ⚠️ Проверка стоит ДО ЛЮБОЙ записи на диск: сайдкар, ссылки и словарь обязаны меняться одной
    и той же картой, иначе текст уедет, а имя останется (или наоборот).
    """
    if not names or not names.is_file():
        return mapping
    named = json.loads(names.read_text(encoding="utf-8")).get("speakers") or {}
    safe = dict(mapping)
    for old, new in mapping.items():
        if old == new or old not in named:
            continue
        others = elsewhere(record, old)
        if others:
            print(f"  ⚠️ {old} («{named[old]}») звучит ещё в {len(others)} записях "
                  f"({', '.join(others[:3])}{'…' if len(others) > 3 else ''}) — НЕ трогаю: "
                  f"сервер зовёт его {new}, но переименовать значило бы увести имя из тех записей.")
            safe.pop(old)
    return safe


def move_names(path: Path, mapping: dict[str, str], dry: bool) -> list[str]:
    """Перенести имена и подписи на новые номера. Возвращает, что перенесли."""
    data = json.loads(path.read_text(encoding="utf-8"))
    moved = []
    for section in ("speakers", "_why"):
        block = data.get(section) or {}
        for old, new in mapping.items():
            if old in block and old != new:
                block[new] = block.pop(old)
                moved.append(f"{section}: {old} → {new}")
        if block:
            data[section] = block
    for record_id, block in (data.get("records") or {}).items():
        for old, new in mapping.items():
            if old in block and old != new:
                block[new] = block.pop(old)
                moved.append(f"records[{record_id}]: {old} → {new}")
    if moved and not dry:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    return moved


def identify(site: str, cookies: dict, episode: str, prints: dict,
             dry: bool = False) -> tuple[dict, list[dict]]:
    """⚠️ `dry` уезжает НА СЕРВЕР, а не остаётся здесь: узнавание пишет в реестр (новый голос
    занимает номер), и «просто посмотреть» без этого флага забирает номера под запись, которую
    ещё не решили перенумеровывать."""
    with ingest.client(site, cookies, timeout=120) as c:
        answer = c.post("/api/voices/identify",
                        json={"episode": episode, "voices": prints, "dry": dry})
    if answer.status_code != 200:
        raise SystemExit(f"сайт не узнал голоса ({answer.status_code}): "
                         f"{answer.json().get('detail', answer.text)[:300]}")
    body = answer.json()
    return body["map"], body.get("report") or []


def main() -> int:
    ap = argparse.ArgumentParser(description="узнать голоса записи у сервера и переписать номера")
    ap.add_argument("record", type=Path, help="каталог записи (где record.json)")
    ap.add_argument("--audio", type=Path, help="звук записи; умолчание — ~/asr-stack/audio/<id>.flac")
    ap.add_argument("--site", default="", help="адрес сайта; умолчание — сохранённая сессия")
    ap.add_argument("--dry", action="store_true", help="показать карту и ничего не менять")
    args = ap.parse_args()

    record = args.record.resolve()
    sidecar = record / SIDECAR
    if not sidecar.is_file():
        raise SystemExit(f"нет {sidecar}: без сырого сайдкара номера переписывать не в чем")
    audio = args.audio or (Path.home() / "asr-stack" / "audio" / f"{record.name}.flac")
    if not Path(audio).is_file():
        raise SystemExit(f"нет звука {audio} — заберите его: python3 tools/fetch_audio.py --id {record.name}")

    site, cookies = ingest.load_session(args.site or None)
    # ⚠️ Черновики — во ВРЕМЕННЫЙ каталог, не в каталог записи. Отпечатки режут из звука wav на
    # весь эфир: на 50-минутной записи это 95 МБ, и однажды оставленные в записи (ранний выход
    # «менять нечего») они уехали бы на сервер ближайшим rsync — звука в корпусе быть не должно,
    # а `.gitignore` от rsync не спасает. Пересчёт стоит пару секунд, кэшировать нечего.
    with tempfile.TemporaryDirectory(prefix="voices-") as work:
        prints = voiceprints.fingerprints(sidecar, Path(audio), work=Path(work),
                                          url=ingest.stack_env_value("ASR_CAMPP_URL") or voiceprints.DEFAULT_URL,
                                          key=ingest.stack_env_value("ASR_CAMPP_KEY"))
    if not prints:
        raise SystemExit("в записи нет безымянных голосов с речью — узнавать нечего")
    print(f"отпечатков: {len(prints)} ({', '.join(prints)})")

    mapping, report = identify(site, cookies, record.name, prints, dry=args.dry)
    for row in report:
        mark = {"matched": "узнали", "new": "новый голос", "folded": "приклеили к ближайшему"}.get(row["how"], row["how"])
        print(f"  {row['from']} → {row['to']}  {mark}, cos {row['cos']}, эфир {row['air_sec']}с"
              + (f", имя: {row['name']}" if row.get("name") else ""))
    names = corpus_names(record)
    mapping = drop_conflicts(record, names, mapping)
    if not any(old != new for old, new in mapping.items()):
        print("менять нечего: сервер зовёт эти голоса теми же номерами")
        return 0
    if args.dry:
        print("(--dry: ни запись, ни реестр не тронуты; номера новых голосов при настоящем "
              "прогоне могут быть другими — их занимают по порядку)")
        return 0

    for name in (SIDECAR, REFS):
        path = record / name
        if path.is_file():
            path.write_text(remap(path.read_text(encoding="utf-8"), mapping), encoding="utf-8")
            print(f"  переписан {name}")
    if names:
        for line in move_names(names, mapping, args.dry):
            print(f"  словарь — {line}")
    print("дальше: пересобрать запись (make_record) и переиндексировать корпус")
    return 0


if __name__ == "__main__":
    sys.exit(main())
