"""Сборка сообщений для движка.

Здесь живёт «обогащение промпта контекстом чтения»: вопрос, заданный от реплики
в читалке, уходит в мораг вместе с записью, тайм-кодом и окном реплик вокруг.
Движку правок не требуется — он просто получает более полный вопрос.

Второй режим — вопрос ПРО ОДНУ ЗАПИСЬ целиком (кнопки «Краткое содержание», «Открытые вопросы»
и своё поле на странице записи). Канала «искать в подмножестве» между сайтом и движком нет: в
мораг уходит только `{model, stream, messages}`, — поэтому ограничение выражается ТОЛЬКО текстом
вопроса. Держится оно на инструментах агента: `get_doc(doc_id, query)` читает документ целиком,
`search(query, doc_ids=[…])` ищет внутри указанных. ⓘ Длинный `doc_id` движок принимает как есть
(`morag_pipeline._resolve_id_arg`: «не код по форме → длинный doc_id, прежнее поведение»), но
агенту велено звать `get_doc` только по УВИДЕННОМУ коду — поэтому в шаблоне идентификатор прямо
назван полученным от системы, а рядом стоит запасной ход через каталог.
"""

from __future__ import annotations

import logging

from ..content.records import RecordMeta
from ..content.transcript import Utterance, window

log = logging.getLogger("morag_web.prompt")

DEFAULT_TEMPLATE = (
    "Вопрос задан во время просмотра записи «{title}» на отметке {mmss}.\n"
    "Реплика, о которой спрашивают ({speaker}): «{quote}»\n\n"
    "Контекст вокруг:\n{context}\n\n"
    "Вопрос: {question}"
)

# Вопрос про запись целиком. Дефолт нейтральный: доменное (ветки, рубрики) живёт в site.yml.
DEFAULT_RECORD_TEMPLATE = (
    "Вопрос — про ОДНУ запись: «{title}» ({date}).\n"
    "{doc_ref}\n"
    "Ищи только в ней. Другие записи не подходят, даже если тема совпадает: спрашивают про эту. "
    "Ответ собирай из найденного в ней; не нашлось — так и скажи.\n\n"
    "Вопрос: {question}"
)
# ⚠️ Идентификатор — В КАВЫЧКАХ, и это не оформление: у записи он равен пути файла, а каталоги
# курсов называются с пробелом («Python 2023»); голый id автозагрузка движка обрывает на пробеле
# и молча грузит несуществующий документ (ловилось 15.09 на всех 72 записях курсов).
DEFAULT_DOC_REF = (
    "Идентификатор записи: \"{doc_id}\" — он получен от системы, а не выдуман; передавай его "
    "ДОСЛОВНО: get_doc(\"{doc_id}\", query) прочитает запись целиком, "
    "search(query, doc_ids=[\"{doc_id}\"]) сузит поиск до неё. "
    "Инструмент его не принял — найди запись в каталоге по заголовку и дате и работай по её коду."
)
DEFAULT_DOC_REF_UNKNOWN = (
    "Кода записи у тебя нет: найди её в каталоге по заголовку «{title}» и дате {date}, "
    "возьми оттуда её код и читай запись через get_doc(код, query)."
)


def safe_format(template: str, **fields) -> str:
    """Подстановка в шаблон ИЗ КОНФИГА: пусто, если шаблон кривой.

    ⚠️ Шаблон пишет владелец руками в `site.yml`, и одна лишняя `{` роняла бы `str.format`
    на КАЖДОМ вопросе — пятисоткой, без единой подсказки о причине. Ошибка шаблона не должна
    отнимать у человека возможность спросить: возвращаем пусто, вызывающий отдаёт голый вопрос,
    а причина уходит в лог.
    """
    try:
        return template.format(**fields).strip()
    except (KeyError, IndexError, ValueError) as error:
        log.warning("шаблон вопроса не собрался (%s): %s", type(error).__name__, error)
        return ""


def mmss(sec: float) -> str:
    total = int(sec)
    return f"{total // 60}:{total % 60:02d}"


def clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def trim_history(history: list[dict], *, turns: int, max_chars: int) -> list[dict]:
    """Оставляем хвост диалога: старое режем первым."""
    kept = [m for m in history if m.get("role") in {"user", "assistant"} and m.get("content")]
    kept = kept[-turns * 2 :]
    while kept and sum(len(m["content"]) for m in kept) > max_chars:
        kept.pop(0)
    return [{"role": m["role"], "content": m["content"]} for m in kept]


def build_reading_context(
    utterances: list[Utterance],
    sec: float,
    *,
    radius: int,
    max_chars: int,
) -> tuple[str, Utterance | None]:
    """Окно реплик вокруг секунды + сама цитируемая реплика."""
    around = window(utterances, sec, radius)
    if not around:
        return "", None

    center = min(around, key=lambda u: abs(u.sec - sec))
    lines = [f"[{mmss(u.sec)}] {u.speaker}: {u.text}" for u in around]

    # если не влезаем — режем с краёв, центральная реплика уходит последней
    while lines and sum(len(l) for l in lines) > max_chars and len(lines) > 1:
        head_dist = abs(around[0].sec - sec)
        tail_dist = abs(around[-1].sec - sec)
        if head_dist >= tail_dist:
            around, lines = around[1:], lines[1:]
        else:
            around, lines = around[:-1], lines[:-1]
    return "\n".join(lines), center


def compose_question(
    question: str,
    *,
    record: RecordMeta | None = None,
    utterances: list[Utterance] | None = None,
    sec: float = 0,
    settings: dict | None = None,
) -> str:
    """Голый вопрос — если контекста чтения нет; иначе обогащённый по шаблону из конфига."""
    if record is None or not utterances:
        return question

    settings = settings or {}
    template = settings.get("template") or DEFAULT_TEMPLATE
    radius = int(settings.get("window", 2))
    max_chars = int(settings.get("context_max_chars", 2500))

    context, center = build_reading_context(utterances, sec, radius=radius, max_chars=max_chars)
    if not context or center is None:
        return question

    return safe_format(
        template,
        title=record.title,
        # митап, к которому относится доклад: у записи он может быть не заполнен, и
        # шаблон обязан от этого не падать — потому и передаём пустую строку, а не None
        group=record.group,
        date=record.date,
        mmss=mmss(sec),
        speaker=center.speaker,
        quote=clip(center.text, 400),
        context=context,
        question=question,
    ) or question


def compose_about_record(
    question: str,
    *,
    record: RecordMeta,
    doc_id: str = "",
    settings: dict | None = None,
) -> str:
    """Вопрос про запись ЦЕЛИКОМ: заголовок, дата и идентификатор записи в базе движка.

    Реплики сюда не идут намеренно: спрашивают не про место, а про запись, и окно текста только
    уводило бы агента к одному фрагменту. Идентификатора нет (движкового конфига рядом нет) —
    ограничение держится словами, а запись ищется по заголовку и дате.
    """
    settings = settings or {}
    template = settings.get("template") or DEFAULT_RECORD_TEMPLATE
    if doc_id:
        ref = safe_format(settings.get("doc_ref") or DEFAULT_DOC_REF,
                          doc_id=doc_id, title=record.title, date=record.date)
    else:
        ref = safe_format(settings.get("doc_ref_unknown") or DEFAULT_DOC_REF_UNKNOWN,
                          title=record.title, date=record.date)
    return safe_format(
        template,
        title=record.title,
        group=record.group,
        date=record.date,
        section=record.section,
        speakers=", ".join(record.speakers),
        doc_id=doc_id,
        doc_ref=ref,
        question=question,
    ) or question


def build_messages(
    question: str,
    history: list[dict],
    *,
    turns: int,
    history_max_chars: int,
) -> list[dict]:
    return [
        *trim_history(history, turns=turns, max_chars=history_max_chars),
        {"role": "user", "content": question},
    ]
