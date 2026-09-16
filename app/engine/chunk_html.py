"""HTML чанка → текст и ключевые слова.

Движок кладёт в `document[i]` самодостаточный HTML со встроенным `<style>`.
Регуляркой теги не срезаем: CSS утечёт в карточку.

Внутри этого HTML движок уже отметил `<mark>` каждое слово, чей корень встречался
в поисковых запросах агента, — то есть сам сказал, ЧЕМ этот фрагмент оказался
релевантен. Раньше мы это стирали вместе с тегами (замер на фикстуре: 54 метки на
14 цитат). Теперь словоформы забираем: по ним фронт находит смысловое ядро цитаты
в чанке на полторы минуты.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

# только парные теги: void-элементы (meta, link) закрытия не имеют и навсегда
# завесили бы счётчик пропуска — их и так покрывает <head>
_SKIP = {"style", "script", "head", "title"}
_BREAK = {"p", "br", "li", "tr", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
_BLANKS = re.compile(r"\n{3,}")
_WORD = re.compile(r"[^\W\d_][\w-]*|\d[\w-]*", re.UNICODE)
_MIN_KEYWORD = 3  # «на», «и» в подсветку попадать не должны — они есть везде


@dataclass(frozen=True)
class Chunk:
    text: str
    keywords: tuple[str, ...]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.marked: list[str] = []
        self._skip_depth = 0
        self._mark_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "body":
            self._skip_depth = 0  # страховка от незакрытого <head> в кривом HTML
        elif tag in _SKIP:
            self._skip_depth += 1
        elif tag == "mark":
            self._mark_depth += 1
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "mark":
            self._mark_depth = max(0, self._mark_depth - 1)
        elif tag in _BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._mark_depth:
            self.marked.append(data)


def extract(html: str) -> Chunk:
    """Текст чанка и словоформы, которые движок счёл ключевыми."""
    if not html:
        return Chunk("", ())
    if "<" not in html:
        return Chunk(html.strip(), ())
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # битый HTML не должен стоить нам цитаты
        return Chunk(re.sub(r"<[^>]+>", " ", html).strip(), ())
    text = "".join(parser.parts)
    text = "\n".join(line.strip() for line in text.splitlines())
    return Chunk(_BLANKS.sub("\n\n", text).strip(), _keywords(parser.marked))


def _keywords(marked: list[str]) -> tuple[str, ...]:
    """Словоформы из `<mark>`, без повторов и в порядке появления.

    Формы не склеиваем («видеокарт» и «видеокарта» приходят обе): стеммер живёт в
    движке, тащить его в BFF ради этого не стоит — сопоставляет их фронт по общему
    началу, там же, где подсвечивает.
    """
    out: dict[str, None] = {}
    for piece in marked:
        for word in _WORD.findall(piece.lower()):
            if len(word) >= _MIN_KEYWORD:
                out.setdefault(word, None)
    return tuple(out)


def html_to_text(html: str) -> str:
    return extract(html).text
