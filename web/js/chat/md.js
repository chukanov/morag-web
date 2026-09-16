// Минимальный Markdown для ответа агента.
//
// Строим DOM-узлы, а не строку HTML: текст ответа сочиняет модель, и innerHTML
// на нём — это дыра для инъекции. Поддерживаем ровно то, что модель реально
// использует: абзацы, заголовки, списки, **жирный**, *курсив*, `код`, ссылки
// и наши маркеры цитат [N].
import { el } from "../ui/dom.js";

const INLINE =
  /\*\*([^*]+)\*\*|__([^_]+)__|`([^`]+)`|\[(\d+)\]|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*([^*\n]+)\*/g;

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;

/**
 * @param {string} text  сырой текст ответа
 * @param {(n:number)=>Node|null} makeRef  ссылка на карточку-момент (null — оставить текстом)
 */
// Замерено на живых ответах: модель ставит подзаголовок ОТДЕЛЬНОЙ строкой,
// целиком жирной, а тело — следующими строками через одинарный перенос.
// Поэтому разбираем построчно, а не только по пустым строкам.
const BOLD_LINE = /^\*\*([^*]+?)\*\*[:：]?$/;

export function renderMarkdown(text, { makeRef, onClaim } = {}) {
  const frag = document.createDocumentFragment();
  const lines = String(text || "")
    .replace(/\r\n/g, "\n")
    .split("\n");

  let paragraph = [];  // копим строки обычного текста
  let list = null;     // текущий список

  const flushParagraph = () => {
    if (!paragraph.length) return;
    frag.append(withClaims(el("p"), paragraph.join(" "), makeRef, onClaim));
    paragraph = [];
  };
  const flushList = () => {
    if (list) frag.append(list);
    list = null;
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushAll();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushAll();
      const level = Math.min(4, Math.max(3, heading[1].length)); // h1/h2 в ответе не нужны
      frag.append(inlineInto(el(`h${level}`), heading[2], makeRef));
      continue;
    }

    const boldHeading = BOLD_LINE.exec(line);
    if (boldHeading) {
      flushAll();
      frag.append(inlineInto(el("h3"), boldHeading[1], makeRef));
      continue;
    }

    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    if (bullet || ordered) {
      flushParagraph();
      const wanted = bullet ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== wanted) {
        flushList();
        list = el(wanted);
      }
      list.append(withClaims(el("li"), (bullet || ordered)[1], makeRef, onClaim));
      continue;
    }

    flushList();
    paragraph.push(line);
  }
  flushAll();
  return frag;
}

// Конец предложения в живом ответе модели: точка/восклицание/вопрос, а ещё
// перенос строки и начало пункта списка — маркер часто стоит в самом конце
// пункта, и без этого «предложение» уползало в соседний.
const CLAIM_EDGE = /[.!?…]\s|\n/g;
const CLAIM_MAX = 260; // длиннее — уже не «утверждение», а пересказ ответа

/**
 * Утверждения ответа по номерам цитат: `[N]` → предложения, где он стоит.
 *
 * Это самый честный ответ на «зачем эта цитата»: не ранжирование и не совпадение
 * слов, а то место, ради подкрепления которого агент её и привёл. Считается
 * даром из уже полученного текста — движок для этого не нужен.
 *
 * @returns {Map<number, string[]>}
 */
export function claimsByRef(text) {
  const src = String(text || "").replace(/\r\n/g, "\n");
  const out = new Map();
  for (const m of src.matchAll(/\[(\d+)\]/g)) {
    const n = Number(m.group?.[1] ?? m[1]);
    if (!Number.isFinite(n)) continue;

    // Границы предложения вокруг маркера: назад — до конца предыдущего,
    // вперёд — до конца текущего.
    let from = 0;
    CLAIM_EDGE.lastIndex = 0;
    for (const edge of src.slice(0, m.index).matchAll(CLAIM_EDGE)) {
      from = edge.index + edge[0].length;
    }
    const rest = src.slice(m.index);
    const tail = CLAIM_EDGE.exec(rest);
    CLAIM_EDGE.lastIndex = 0;
    const to = m.index + (tail ? tail.index + tail[0].length : rest.length);

    let claim = src
      .slice(from, to)
      .replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "") // маркер списка в утверждение не входит
      .replace(/\s+/g, " ")
      .trim();
    if (claim.length > CLAIM_MAX) claim = `${claim.slice(0, CLAIM_MAX - 1).trimEnd()}…`;
    if (!claim || claim === m[0]) continue;

    const list = out.get(n) || [];
    if (!list.includes(claim)) list.push(claim);
    out.set(n, list);
  }
  return out;
}

/**
 * Разложить текст в узел, обернув предложения со сносками в отдельные span'ы.
 *
 * Обёртка НИЧЕГО не меняет в виде текста — она нужна, чтобы потом подсветить
 * ровно то утверждение, ради которого открыли цитату. Границы предложения те
 * же, что у `claimsByRef`: точка/восклицание/вопрос либо конец строки.
 */
function withClaims(node, text, makeRef, onClaim) {
  const src = String(text);
  const marks = [...src.matchAll(/\[(\d+)\]/g)];
  if (!marks.length) return inlineInto(node, src, makeRef);

  let cursor = 0;
  for (const mark of marks) {
    if (mark.index < cursor) continue; // сноска внутри уже обёрнутого предложения
    let from = cursor;
    for (const edge of src.slice(cursor, mark.index).matchAll(CLAIM_EDGE)) {
      from = cursor + edge.index + edge[0].length;
    }
    const rest = src.slice(mark.index);
    const tail = CLAIM_EDGE.exec(rest);
    CLAIM_EDGE.lastIndex = 0;
    const to = mark.index + (tail ? tail.index + tail[0].length : rest.length);

    if (from > cursor) inlineInto(node, src.slice(cursor, from), makeRef);
    const claim = el("span", { class: "claim", "data-n": mark[1] });
    inlineInto(claim, src.slice(from, to), makeRef);
    node.append(claim);
    // Узел отдаём наружу, а не ищем потом селектором: подсветка не должна
    // зависеть от того, как разметка устроена внутри.
    onClaim?.(Number(mark[1]), claim);
    cursor = to;
  }
  if (cursor < src.length) inlineInto(node, src.slice(cursor), makeRef);
  return node;
}

// Голый адрес в тексте тоже делаем ссылкой. Замерено на живом ответе: модель
// пишет URL как придётся — просили markdown-ссылку, получили `**адрес**`
// жирным. Полагаться на её разметку нельзя, поэтому ловим сам адрес.
// Схема обязательна: без неё под «адрес» подошли бы `config.yml/x` и `и т.д./`.
const BARE_URL = /https?:\/\/[^\s<>()[\]«»"']+/g;
const URL_TAIL = /[.,;:!?»)]+$/; // точка в конце предложения — не часть адреса

function withLinks(node, text) {
  let last = 0;
  for (const m of String(text).matchAll(BARE_URL)) {
    const url = m[0].replace(URL_TAIL, "");
    if (!url) continue;
    if (m.index > last) node.append(text.slice(last, m.index));
    node.append(el("a", { href: url, target: "_blank", rel: "noopener", text: url }));
    last = m.index + url.length;
  }
  if (last < text.length) node.append(text.slice(last));
  return node;
}

function inlineInto(node, text, makeRef) {
  let last = 0;
  for (const m of String(text).matchAll(INLINE)) {
    if (m.index > last) withLinks(node, text.slice(last, m.index));
    const [, bold, boldAlt, code, refNum, linkText, linkUrl, italic] = m;

    // Внутри жирного и курсива адрес тоже бывает — там его и ловим.
    if (bold || boldAlt) node.append(withLinks(el("strong"), bold || boldAlt));
    else if (code) node.append(el("code", { text: code })); // в коде адрес — это текст
    else if (refNum) {
      const ref = makeRef?.(Number(refNum));
      node.append(ref || m[0]); // цитаты нет — оставляем как было, не врём ссылкой
    } else if (linkText) {
      node.append(el("a", { href: linkUrl, target: "_blank", rel: "noopener", text: linkText }));
    } else if (italic) node.append(withLinks(el("em"), italic));

    last = m.index + m[0].length;
  }
  if (last < text.length) withLinks(node, text.slice(last));
  return node;
}
