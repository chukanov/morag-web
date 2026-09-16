#!/usr/bin/env python3
"""Собрать `slides.md` — документ «что было на экране» рядом с `record.md`.

    python3 tools/make_slides_md.py <каталог записи>
    python3 tools/make_slides_md.py <каталог записи> --stdout     # показать, не писать

Формат — транскрипт: `[Экран] <!-- t:812.4 --> Слайд 12 «Архитектура»: …`. Чанкер морага в
режиме transcript такие строки уже понимает (`TranscriptChunker._parse_turns`), цитата получает
секунду и откроет читалку на слайде. Правок движка ноль, и — главное — переиндексации корпуса
ноль: новый файл индексируется как новый документ, `record.md` не трогается. Это способ
измерить, что экран даёт ретриву, ДО связки речь+экран в контексте чанка (та требует канала в
движке и полной переиндексации).

Что попадает: слайды с описанием (люди и нечитаемое — нет), выборка из живого экрана, и
разрешённые обращения докладчика («в момент слов „вот здесь“ на экране: …»). Повторный показ
слайда — своя строка с тем же текстом: это момент, а не документ.

⚠️ Экран демо — НЕ слайд. Слайд — авторский текст, его берём дословно (до `--max-text` знаков).
Окно программы, браузер, терминал — «что было на мониторе»: модель переписывает окно Jira
на 11 тысяч знаков вместе с чужими фамилиями и почтой (ловилось на «Офисной жизни»: профиль
пользователя с ФИО ушёл бы в индекс). Поэтому для `app`/`browser`/`terminal`/`other` по
умолчанию — заголовок, описание и короткий фрагмент (`--app-text short`, 300 знаков);
`full` — дословно, `none` — только заголовок и описание. Полный текст остаётся в сайдкаре.
Решение, что из экранов демо искать, — за владельцем.

Шапка копирует поля записи (`title`, `date`, `branch`, `category`, `topics`, …), чтобы каталог и
фильтры агента видели документ там же, где запись; `title` получает суффикс « · экран»,
`record` держит id записи-речи. ⓘ `speakers` не копируется: на экране никто не выступает, а
поле уезжает в payload каждого чанка и в каталог «кто выступал».

⚠️ Индексация — отдельное решение владельца: файл лежит в каталоге источника, и следующий
прогон `index` его подхватит.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADER_KEEP = ("date", "year", "event", "branch", "kind", "category", "topics", "media", "duration_sec", "post")
# Оформление слайда, которое 9B-модель описывает вопреки промпту («белый фон, зелёный узор,
# маркеры списка»): в индексе это шум. Режем по предложениям.
DECOR = re.compile(r"(фон\b|фоне\b|фоном\b|узор|маркер|абстракц|логотип|оформлен|шаблон|геометрич|"
                   r"декоратив|цветов(ая|ой|ые) (полос|панел|плашк)|градиент)", re.I)


def read_header(md: Path) -> dict:
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    head = text.split("\n---", 1)[0]
    out = {}
    for line in head.splitlines()[1:]:
        m = re.match(r"^([\w_]+):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def clean_visual(visual: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", visual.strip())
    keep = [p for p in parts if p and not DECOR.search(p)]
    return " ".join(keep).strip()


def one_paragraph(text: str) -> str:
    """Внутри реплики переводы строк допустимы (чанкер режет по ПУСТОЙ строке), пустые — нет."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


AUTHORED = ("slide", "code", "diagram")


def cut(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0].rstrip() + " …"


def slide_line(e: dict, kind_word: str = "Слайд", app_text: str = "short", max_text: int = 2500) -> str | None:
    d = e.get("desc")
    if not d or d["kind"] == "people" or d.get("unreadable"):
        return None
    title = d.get("title", "").strip()
    text = one_paragraph(d.get("text", ""))
    if d["kind"] in AUTHORED:
        text = cut(text, max_text)
    else:
        text = cut(text, {"none": 0, "short": 300, "full": max_text}[app_text])
    visual = clean_visual(d.get("visual", ""))
    if not (title or text or visual):
        return None
    what = {"slide": "Слайд", "code": "Код на экране", "app": "Окно программы", "terminal": "Терминал",
            "browser": "Браузер", "diagram": "Схема", "other": "Экран"}.get(d["kind"], "Экран")
    if kind_word == "Слайд" and "slide" in e:
        head = f"{what} {e['slide']}" if d["kind"] == "slide" else what
    else:
        head = what
    if title:
        head += f" «{title}»"
    body = head + ":"
    if text and text.replace("\n", " ").strip() != title:
        body += "\n" + text
    if visual:
        body += f"\nИзображено: {visual}"
    return body


def screen_lines(record: Path, app_text: str = "short", max_text: int = 2500) -> tuple[dict | None, list[tuple[float, str]]]:
    """(сайдкар шкалы, [(секунда, текст строки экрана)]) — слайды, выборка, разрешённые обращения."""
    slides_path = record / "record.slides.json"
    if not slides_path.is_file():
        return None, []
    sl = json.loads(slides_path.read_text(encoding="utf-8"))
    refs_path = record / "record.refs.json"
    refs = json.loads(refs_path.read_text(encoding="utf-8"))["refs"] if refs_path.is_file() else []
    lines: list[tuple[float, str]] = []
    for e in sl.get("slides", []):
        body = slide_line(e, app_text=app_text, max_text=max_text)
        if body:
            lines.append((e["t0"], body))
    for s in sl.get("samples", []):
        body = slide_line(s, kind_word="Экран", app_text=app_text, max_text=max_text)
        if body:
            lines.append((s["t"], body))
    for r in refs:
        res = (r.get("resolved") or "").strip()
        if not res or re.match(r"^\W*неясно", res, re.I):
            continue
        lines.append((r["t"], f"В момент слов «{r['quote']}» на экране: {res}"))
    lines.sort(key=lambda x: x[0])
    return sl, lines


def build(record: Path, app_text: str = "short", max_text: int = 2500) -> str | None:
    sl, lines = screen_lines(record, app_text, max_text)
    if not sl or not lines:
        return None
    hdr = read_header(record / "record.md") if (record / "record.md").is_file() else {}
    title = hdr.get("title", "").strip('"')
    out = ["---", f'title: "{title} · экран"' if title else 'title: "Экран"']
    for k in HEADER_KEEP:
        if k in hdr:
            out.append(f"{k}: {hdr[k]}")
    out.append(f'record: "{record.name}"')
    out.append('source: "video"')
    out.append(f"screen_slides: {sl['summary'].get('slides', 0)}")
    out.append("---")
    out.append("")
    for t, body in lines:
        out.append(f"[Экран] <!-- t:{t:.1f} --> {body}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


TURN_RE = re.compile(r"^\[[^\]]+\]\s*<!--\s*t:([\d.]+)\s*-->", re.S)


def inline(record: Path, app_text: str = "short", max_text: int = 2500) -> str | None:
    """Копия `record.md`, где строки экрана вставлены в момент смены слайда — МЕЖДУ репликами, а
    длинная реплика РЕЖЕТСЯ по слову на этом времени (времена слов — `record.words.json`), и хвост
    получает своё мереное время. Так экран попадает в тот же чанк, что и речь этого момента:
    чанкер склеивает подряд идущие реплики. Это имитация «поля экрана у чанка» без правки
    движка — сравнить два индекса одних записей, с экраном и без, ДО того как заводить поле.
    ⚠️ В корпус сайта такой файл не кладут: строки `[Экран]` стали бы репликами читалки."""
    md = record / "record.md"
    if not md.is_file():
        return None
    _, lines = screen_lines(record, app_text, max_text)
    if not lines:
        return None
    text = md.read_text(encoding="utf-8")
    head, _, body = text.partition("\n---\n")
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    words_path = record / "record.words.json"
    turns = json.loads(words_path.read_text(encoding="utf-8"))["turns"] if words_path.is_file() else []
    out: list[str] = []
    k = 0

    def screen(t: float, body_line: str) -> str:
        return f"[Экран] <!-- t:{t:.1f} --> {body_line}"

    for i, p in enumerate(paras):
        m = TURN_RE.match(p)
        if not m:
            out.append(p)
            continue
        t0 = float(m.group(1))
        while k < len(lines) and lines[k][0] < t0:
            out.append(screen(*lines[k]))
            k += 1
        label = p[: m.end()]
        speaker = label[: label.index("]") + 1]
        tokens = p[m.end():].split()
        words = turns[i]["words"] if i < len(turns) and abs(float(turns[i].get("start") or -1) - t0) < 0.1 else None
        # Слово реплики ↔ токен абзаца: токены из одних знаков препинания («!» в начале)
        # пропускаем — на живой записи именно из-за них не сходились 3 реплики из 98.
        tok_idx = [n for n, tok in enumerate(tokens) if re.search(r"\w", tok)]
        wrd = [w for w in (words or []) if re.search(r"\w", str(w[0]))]  # «—» тоже бывает «словом»
        if not wrd or len(wrd) != len(tok_idx):
            out.append(p)
            continue
        cur_t, start = t0, 0  # start — индекс в wrd/tok_idx
        end_t = float(turns[i].get("end") or wrd[-1][2])
        while k < len(lines) and lines[k][0] < end_t:
            t_line, body_line = lines[k]
            j = next((n for n in range(start, len(wrd)) if float(wrd[n][1]) >= t_line), None)
            if j is None or j <= start:
                # смена в самом конце реплики или до её первого слова — экран после/перед целиком
                break
            out.append(f"{speaker} <!-- t:{cur_t:.1f} --> " + " ".join(tokens[tok_idx[start]:tok_idx[j]]))
            out.append(screen(t_line, body_line))
            cur_t, start = float(wrd[j][1]), j
            k += 1
        out.append(f"{speaker} <!-- t:{cur_t:.1f} --> " + " ".join(tokens[tok_idx[start]:]))
    for t, body_line in lines[k:]:
        out.append(screen(t, body_line))
    return head + "\n---\n\n" + "\n\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", type=Path)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--app-text", choices=["none", "short", "full"], default="short",
                    help="сколько текста окон программ класть в документ (по умолчанию short — 300 знаков)")
    ap.add_argument("--max-text", type=int, default=2500, help="потолок текста слайда, знаков")
    ap.add_argument("--inline", type=Path, metavar="OUT.md",
                    help="вместо slides.md — копия record.md со строками экрана между репликами (эксперимент)")
    a = ap.parse_args()
    if a.inline:
        text = inline(a.record, app_text=a.app_text, max_text=a.max_text)
        if text is None:
            sys.exit("нечего вставлять: нет record.md или описанных кадров")
        a.inline.parent.mkdir(parents=True, exist_ok=True)
        a.inline.write_text(text, encoding="utf-8")
        print(f"→ {a.inline}: {text.count(chr(10) + '[Экран]')} строк экрана вставлено")
        return 0
    text = build(a.record, app_text=a.app_text, max_text=a.max_text)
    if text is None:
        sys.exit("нечего собирать: нет record.slides.json или ни одного описанного кадра")
    if a.stdout:
        print(text)
        return 0
    out = a.record / "slides.md"
    # пишем только при изменении: индексатор решает «изменился ли документ» по mtime
    if out.is_file() and out.read_text(encoding="utf-8") == text:
        print(f"без изменений: {out}")
        return 0
    out.write_text(text, encoding="utf-8")
    n = text.count("\n[Экран]")
    print(f"→ {out}: {n} строк экрана, {len(text)} знаков")
    return 0


if __name__ == "__main__":
    sys.exit(main())
