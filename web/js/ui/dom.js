// Мелкие помощники. Всё, что пришло с сервера или из конфига, вставляем
// текстом (textContent) — innerHTML только для статических литералов.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;   // только для своих SVG-литералов
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
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function fmt(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtDuration(seconds) {
  const m = Math.round((seconds || 0) / 60);
  return `${m} мин`;
}

/**
 * Русское склонение при числе: `plural(12, "запись", "записи", "записей")`.
 * Отдельная функция, потому что правило неочевидно ровно в одном месте:
 * 11-14 идут с формой множества («11 записей»), хотя кончаются на 1-4.
 */
export function plural(n, one, few, many) {
  const rest = Math.abs(n) % 100;
  if (rest > 10 && rest < 20) return many;
  const last = rest % 10;
  if (last === 1) return one;
  if (last > 1 && last < 5) return few;
  return many;
}

/** Число со склонённым словом: «12 записей». */
export const countOf = (n, one, few, many) => `${n} ${plural(n, one, few, many)}`;

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

let toastTimer = null;
export function toast(message) {
  const node = $("#toast");
  if (!node) return;
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 1800);
}

export const reducedMotion = () => matchMedia("(prefers-reduced-motion:reduce)").matches;

// Статусы движка приходят с ведущим значком: «🔍 …», «📄 …», «→ 5 документов».
// Отделяем его, чтобы показать иконкой, а не частью текста.
const LEADING_ICON =
  /^((?:\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic})*)|[→←↳⇒])\s*(.*)$/u;

export function splitIcon(text = "") {
  const match = LEADING_ICON.exec(String(text).trim());
  return match ? [match[1], match[2]] : ["", String(text).trim()];
}

/** Положить ссылку в буфер и сказать об этом человеку.
 *
 * `navigator.clipboard` требует защищённого контекста: по http (или по голому IP
 * при отладке) его просто нет, поэтому оставлен старый способ через скрытое поле —
 * иначе кнопка «поделиться» молча не работала бы ровно там, где её проверяют.
 */
export async function copyLink(url, message = "Ссылка скопирована") {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
    } else {
      const field = el("textarea", { style: "position:fixed;opacity:0", text: url });
      document.body.append(field);
      field.select();
      document.execCommand("copy");
      field.remove();
    }
    toast(message);
    return true;
  } catch {
    // Отказали в буфере — показываем адрес, чтобы человек скопировал руками.
    toast(url);
    return false;
  }
}
