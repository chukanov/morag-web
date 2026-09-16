#!/usr/bin/env python3
"""Отрезать у записи ТИШИНУ В ХВОСТЕ — ту, что идёт до самого конца файла.

    python3 tools/trim_tail.py corpora/<семья>/inbox     # подрезать всё, что нашлось
    python3 tools/trim_tail.py corpora/<семья>/inbox --dry  # только показать

Зачем. Встречу заканчивают, а запись забывают выключить — и в файле остаются десятки минут
цифровой тишины (замерено на сервере: −91.0 дБ против −27.9 в рабочей части). Беда не в объёме:
**whisper на тишине не молчит, а сочиняет**. В корпусе от этого лежало 989 слов вида «Извание
Извание Извание», «не знаю» 70 раз подряд, «я не могу сказать» 16 раз — с тайм-кодами, то есть
готовые к попаданию в индекс и в читалку. Плюс конвейер честно тратит на эту тишину время.

⚠️ **Режем только тишину, упирающуюся в КОНЕЦ файла.** Реплику, сказанную после долгой паузы,
отрезать нечем по построению: если после тишины есть звук, эта тишина не последняя и не трогается.
Сверх того от границы отступаем `MARGIN` секунд назад.

⚠️ Хвост, а не начало: тайм-коды слов отсчитываются от нуля и должны сойтись с видео на сайте.
Подрезка начала сдвинула бы всю запись относительно картинки.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

NOISE_DB = -50.0    # ниже этого считаем тишиной; у наших хвостов −91, у речи −28
MIN_SILENCE = 20.0  # короче — обычная пауза в разговоре, не забытая запись
MARGIN = 5.0        # отступ от границы тишины назад: детектор не идеален
MIN_SAVE = 60.0     # меньше минуты резать не стоит — риск не окупается
PROBE_TAIL = 60.0   # дешёвая проверка: тих ли последний кусок вообще
EOF_EPS = 1.0       # ближе этого к концу файла — считаем, что тишина упёрлась в EOF


def run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True).stderr


def duration(path: Path) -> float:
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'csv=p=0', str(path)], capture_output=True, text=True).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def tail_is_quiet(path: Path, dur: float) -> bool:
    """Дешёвый отсев: если последняя минута звучит, полный разбор файла не нужен."""
    err = run(['ffmpeg', '-hide_banner', '-nostats', '-ss', str(max(0.0, dur - PROBE_TAIL)),
               '-i', str(path), '-af', 'volumedetect', '-f', 'null', '-'])
    m = re.search(r'max_volume:\s*(-?[\d.]+) dB', err)
    return bool(m) and float(m.group(1)) < NOISE_DB


def final_silence_start(path: Path, dur: float) -> float | None:
    """Начало тишины, которая тянется до конца файла. None — такой нет."""
    err = run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path),
               '-af', f'silencedetect=noise={NOISE_DB}dB:d={MIN_SILENCE}', '-f', 'null', '-'])
    starts = [float(x) for x in re.findall(r'silence_start:\s*(-?[\d.]+)', err)]
    ends = [float(x) for x in re.findall(r'silence_end:\s*([\d.]+)', err)]
    if not starts:
        return None
    last = starts[-1]
    # ⚠️ ffmpeg закрывает последнюю тишину `silence_end` и на КОНЦЕ ФАЙЛА — по нему одному
    # «речь возобновилась» от «файл кончился» не отличить. Ловился на этом: проверка отвергала
    # ровно те хвосты, ради которых написана. Отличаем по времени: конец в пределах EOF_EPS от
    # длины файла — это EOF, раньше — действительно снова зазвучала речь, и тогда не трогаем.
    if ends and ends[-1] > last and ends[-1] < dur - EOF_EPS:
        return None
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description='подрезать тишину в хвосте записей')
    ap.add_argument('target', help='файл или каталог с аудио')
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()

    target = Path(args.target)
    files = sorted(target.glob('*.flac')) if target.is_dir() else [target]
    if not files:
        print(f'в {target} нет .flac')
        return 0

    saved = 0.0
    touched = 0
    for path in files:
        dur = duration(path)
        if dur <= 0 or not tail_is_quiet(path, dur):
            continue
        start = final_silence_start(path, dur)
        if start is None:
            continue
        keep = max(0.0, start + MARGIN)
        cut = dur - keep
        if cut < MIN_SAVE:
            continue
        touched += 1
        saved += cut
        print(f'  {path.stem[:52]:<54} {dur/60:5.1f} → {keep/60:5.1f} мин  (тишина {cut/60:.1f} мин)')
        if args.dry:
            continue
        tmp = path.with_suffix('.trim.flac')
        # ⚠️ ПЕРЕКОДИРУЕМ, а не `-c copy`. При копировании потока flac переносит из исходника
        # заголовок STREAMINFO с прежним числом сэмплов: данные обрезаны, а `ffprobe` показывает
        # старую длину. Конвейер берёт длину звука из метаданных — и насчитал бы «потеряно 44%
        # речи» и чанки за концом файла (ту самую беду, что чинилась клампом в pipeline.py).
        # Ловился на этом здесь же. Перекодирование часового flac стоит секунды.
        res = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                              '-i', str(path), '-t', f'{keep:.3f}', '-c:a', 'flac', str(tmp)])
        if res.returncode != 0 or not tmp.exists():
            print(f'    ⚠️ не подрезалось, оставляю как есть')
            tmp.unlink(missing_ok=True)
            continue
        tmp.replace(path)

    if not touched:
        print('тишины в хвостах не нашлось')
    else:
        print(f'\n{"нашлось" if args.dry else "подрезано"}: {touched} записей, {saved/60:.0f} минут тишины')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
