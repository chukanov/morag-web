"""Реестр голосов: кто говорит — решает СЕРВЕР по вектору, а не машина, где шла расшифровка.

Зачем это здесь. Голос узнаётся по отпечатку (CAM++, 192 числа): у каждого голоса корпуса в
реестре лежат один-восемь центроидов, и новая запись сравнивается с ними косинусом. Раньше это
делала каждая машина сама — со своим файлом реестра, — и записи с чужого ноутбука приезжали с
номерами, которые ничего не значат: их приходилось разводить в отдельный диапазон, чтобы они не
подписались чужими именами. Решение владельца 24.09: **номера присваивает только сервер**, и
тогда «незнакомый Speaker_100001» перестаёт существовать — знакомый голос узнаётся, незнакомый
заводится один раз на весь корпус.

Арифметика перенесена из движка (`stages/registry.py::assign`) один в один, чтобы решения не
разошлись с теми, что уже приняты по корпусу: косинус как скалярное произведение
L2-нормированных векторов, максимум по ВСЕМ центроидам ВСЕХ голосов, порядок обработки — по
эфиру убыванием (детерминизм), три исхода:

  * **совпал уверенно** (`cos ≥ threshold`) — голос получает свой номер, а реестр обогащается
    ещё одним центроидом (до `max_centroids`: один и тот же человек звучит по-разному с разных
    микрофонов);
  * **не совпал** — новый номер из `next_id`. ⚠️ ВСЕГДА новый, даже если человек сказал две
    фразы: приклеивать короткий незнакомый голос к ближайшему (так делает движок) значит
    приписывать чужие слова конкретному человеку — молча и неисправимо. Решение владельца 24.09.
  * **похож, но не уверенно** (`suspect ≤ cos < threshold`) — тоже НОВЫЙ номер, но с подсказкой
    «похож на Speaker_N (0.69)»: и в отчёте, и в реестре. Так дешёвая ошибка (лишний номер
    правится строкой в словаре имён) отделена от дорогой (склейка двух людей необратима), а
    человеку есть что подтвердить.

Короткий голос (`air < short_air_sec`) помечается в реестре `short: true` — он остаётся честным
участником записи, но верстак может не показывать такие в общем списке.

⚠️ **Битый файл реестра — отказ вслух.** В движке на этом месте `except → пустой реестр`, и файл
перезаписывается: для машинного состояния это терпимо, для корпуса — катастрофа, потому что
номера начнутся с нуля и подпишут не тех (о чём предупреждает `_registry_warning` в словаре
имён). Здесь мы отказываемся работать и говорим, что чинить.

⚠️ Реестр — БИОМЕТРИЯ. Файл живёт вне репозитория и вне доставки, путь задаётся конфигом
(`voices.registry`); пусто — узнавание выключено, и приём записи работает как раньше.
"""

from __future__ import annotations

import fcntl
import json
import math
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

DIM = 192                  # размерность вектора CAM++; чужая длина — это не наш отпечаток
VERSION = 1


class RegistryError(Exception):
    """Реестром пользоваться нельзя: битый файл, чужой формат, не тот вектор."""


def _empty() -> dict:
    return {"version": VERSION, "next_id": 0, "speakers": {}}


def load(path: Path) -> dict:
    """Реестр с диска. Нет файла — пустой; есть, но нечитаемый — отказ, а не «начнём заново»."""
    path = Path(path)
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RegistryError(f"реестр голосов не читается ({path}): {error}. "
                            f"Рядом должна лежать копия {path.name}.bak — восстановите её; "
                            "пустой реестр начал бы нумерацию заново и подписал бы не тех") from None
    if not isinstance(data, dict) or not isinstance(data.get("speakers"), dict):
        raise RegistryError(f"это не реестр голосов: {path}")
    data.setdefault("next_id", (max((int(k) for k in data["speakers"] if k.isdigit()), default=-1) + 1))
    return data


def save(path: Path, reg: dict) -> None:
    """Записать реестр: копия рядом, потом атомарная подмена. Вызывать только под `locked`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass   # копия — удобство, а не условие: без неё запись всё равно атомарна
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def locked(path: Path):
    """Эксклюзивная блокировка на всё чтение-правку-запись.

    Замок — ОТДЕЛЬНЫЙ файл рядом: блокировать сам реестр нельзя, его подменяют переименованием
    (`tmp.replace`), и блокировка уехала бы вместе со старым inode. Тот же файл и тот же приём,
    что у конвейера транскрибации, — значит две стороны не наступят друг другу на руки.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _unit(vector) -> list[float]:
    """Вектор единичной длины. CAM++ нормирует сам, но принимать на веру чужой вход не будем."""
    try:
        values = [float(x) for x in vector]
    except (TypeError, ValueError):
        raise RegistryError("отпечаток голоса — список чисел") from None
    if len(values) != DIM:
        raise RegistryError(f"отпечаток голоса — {DIM} чисел, пришло {len(values)}")
    norm = math.sqrt(sum(x * x for x in values)) or 1e-9
    return [x / norm for x in values]


def cosine(a: list[float], b: list[float]) -> float:
    """Косинус двух нормированных векторов — их скалярное произведение."""
    return sum(x * y for x, y in zip(a, b))


def best_match(centroid: list[float], speakers: dict) -> tuple[str | None, float]:
    """Ближайший голос реестра и его косинус: максимум по всем центроидам всех голосов."""
    best_id, best = None, -1.0
    for sid, rec in speakers.items():
        for known in rec.get("centroids") or []:
            cos = cosine(centroid, known)
            if cos > best:
                best_id, best = sid, cos
    return best_id, best


def identify(path: Path, voices: dict, *, episode: str = "", threshold: float = 0.75,
             suspect: float = 0.65, short_air_sec: float = 15.0,
             max_centroids: int = 8, dry: bool = False) -> tuple[dict, list[dict]]:
    """Узнать голоса записи и завести незнакомые. Возвращает карту меток и отчёт по каждому.

    `voices` — `{метка: {"centroid": [...], "air_sec": 701.3, "cluster": "SPEAKER_00"}}`; метки
    здесь ЧУЖИЕ (нумерация той машины, где шла расшифровка) и нужны только чтобы вернуть карту.
    Отчёт (`matched` / `new` / `folded`, косинус, имя) идёт в лог приёма и в ответ ручки: без
    него узнавание — чёрный ящик, а решать «тот ли это человек» иногда приходится человеку.

    ⚠️ `dry` — посмотреть и не тронуть. Узнавание ПИШЕТ (новый голос занимает номер, узнанный
    получает центроид), поэтому предпросмотр без этого флага оставлял в реестре голоса записи,
    которую так и не приняли: ловилось на первой же перенумерации — «показать карту» забрало
    четыре номера. Считаем на копии из файла и просто не сохраняем.
    """
    order = sorted(voices, key=lambda label: -float(voices[label].get("air_sec") or 0))
    mapping: dict[str, str] = {}
    report: list[dict] = []
    stamp = time.strftime("%Y-%m-%d")
    with locked(path):
        reg = load(path)
        speakers = reg["speakers"]
        for label in order:
            item = voices[label] or {}
            centroid = _unit(item.get("centroid"))
            air = float(item.get("air_sec") or 0)
            near, cos = best_match(centroid, speakers)
            prov = {"episode": episode or "upload", "cluster": item.get("cluster") or label,
                    "air_sec": round(air, 1), "added": stamp, "identified": True}
            similar = ""
            if near is not None and cos >= threshold:
                sid = near
                rec = speakers[sid]
                if len(rec.setdefault("centroids", [])) < max_centroids:
                    rec["centroids"].append(centroid)
                rec.setdefault("provenance", []).append(prov)
                how = "matched"
            else:
                sid = str(reg["next_id"])
                reg["next_id"] += 1
                fresh = {"centroids": [centroid], "provenance": [prov]}
                if air < short_air_sec:
                    # Короткая реплика — честный участник записи, но не тот, кого ищут в
                    # верстаке: помечаем, чтобы список голосов не зарастал десятисекундными.
                    fresh["short"] = True
                if near is not None and cos >= suspect:
                    similar = f"Speaker_{near}"
                    fresh["similar_to"] = {"voice": similar, "cos": round(cos, 3), "at": stamp}
                speakers[sid] = fresh
                how = "new"
            mapping[label] = f"Speaker_{sid}"
            row = {"from": label, "to": mapping[label], "how": how, "cos": round(cos, 3),
                   "air_sec": round(air, 1), "name": (speakers.get(sid) or {}).get("name", "")}
            if similar:
                row["similar_to"] = similar
                row["similar_name"] = (speakers.get(near) or {}).get("name", "")
            report.append(row)
        if not dry:
            save(path, reg)
    return mapping, report


def stats(path: Path) -> dict:
    """Что в реестре — для проверки доставки и страницы голосов. Секретов не отдаёт."""
    path = Path(path)
    if not path.is_file():
        return {"exists": False, "voices": 0, "next_id": 0, "named": 0, "at": ""}
    reg = load(path)
    speakers = reg.get("speakers") or {}
    return {"exists": True, "voices": len(speakers), "next_id": reg.get("next_id", 0),
            "named": sum(1 for v in speakers.values() if v.get("name")),
            "at": time.strftime("%Y-%m-%dT%H:%M", time.localtime(path.stat().st_mtime))}
