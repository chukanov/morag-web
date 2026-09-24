// Мелкие помощники — КОПИЯ нужной части `web/js/ui/dom.js`. Окно ставится коллеге с зеркала и
// работает офлайн, ссылаться на файлы сайта ему нечем; копия закреплена тестом на совпадение
// поведения, а не байтов (здесь взято только то, что окну нужно).
//
// Всё, что пришло с сервера, вставляем ТЕКСТОМ (`text`), а `html` — только для своих литералов.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);

/** m:ss — как в читалке: тайм-код у исправления это место в записи, а не абстрактное число. */
export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Уважать «меньше движения» обязаны и сцены на canvas: медиа-запрос их не гасит. */
export const reduced = () =>
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
