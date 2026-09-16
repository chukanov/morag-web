// Тема: светлая/тёмная + токены корпуса из конфига.
import { applyHalo } from "./halo.js";

const KEY = "theme";

export function initTheme() {
  const root = document.documentElement;
  // Тёмная — тема сайта по умолчанию, системная настройка не спрашивается:
  // светлая пока сыровата, и включать её надо осознанно, кнопкой. Атрибут
  // ставим ВСЕГДА — иначе кнопка не знает текущую тему и первый клик уходит
  // впустую, а иконка показывает солнце при тёмной теме.
  const saved = localStorage.getItem(KEY);
  root.setAttribute("data-theme", saved === "light" ? "light" : "dark");

  document.getElementById("theme")?.addEventListener("click", () => {
    const current = root.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    localStorage.setItem(KEY, next);
  });
}

/** Токены из site.yml переопределяют пресет — так «другой корпус» не требует правок вёрстки. */
export function applyThemeTokens(theme = {}) {
  const root = document.documentElement;
  for (const [name, value] of Object.entries(theme.tokens || {})) {
    if (typeof value === "string") root.style.setProperty(`--${name}`, value);
  }
  const fonts = theme.fonts || {};
  if (fonts.display) root.style.setProperty("--serif", `"${fonts.display}", Georgia, serif`);
  if (fonts.data) root.style.setProperty("--mono", `"${fonts.data}", ui-monospace, Menlo, monospace`);
  useSectionColors(theme.sections || {});
  // Перелив подсветки знака и заставки — узор/палитра/цель из конфига; без секции — как было.
  applyHalo(theme.halo || null);
}

// --- цвет раздела (ветки) ---------------------------------------------------
//
// У каждой ветки корпуса свой цвет (владелец, 14.09), и им запись маркируется ВЕЗДЕ: чип
// раздела в фильтрах, рамка карточки в списке, день в календаре, плеер и кнопки на странице
// записи. Карта «ветка → цвет» — конфиг пространства (`theme.sections` в site.yml), не код:
// названия веток — доменное, во фронте им не место (гейт `leak_check --web`), а другой корпус
// раскрасит свои ветки сам. Механизм один: узлу ставятся ЧЕТЫРЕ токена акцента, и всё, что
// внутри него нарисовано акцентом (заливка кнопки, полоса, чипы, подписи), само становится
// цветом ветки — отдельных правил «покрасить кнопку play в цвет ветки» нет и не нужно.
let SECTION_COLORS = {};

/** Принять карту «ветка → цвет»; значения не-строки отбрасываются. */
export function useSectionColors(map = {}) {
  SECTION_COLORS = {};
  for (const [name, value] of Object.entries(map || {})) {
    if (typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value.trim())) SECTION_COLORS[name] = value.trim();
  }
}

/** Цвет ветки или пустая строка, если у ветки цвета нет (тогда остаётся акцент сайта). */
export function sectionColor(section) {
  return SECTION_COLORS[section] || "";
}

/** Цвет надписи ПОВЕРХ заливки: тёмный на светлом (жёлтый), белый на остальных. Считается по
 * относительной яркости, а не задаётся в конфиге — конфигу хватает одного цвета на ветку. */
export function readableInk(hex) {
  const n = parseInt(hex.slice(1), 16);
  const lin = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const lum = 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
  return lum > 0.45 ? "#1A1A1A" : "#FFFFFF";
}

const TOKENS = ["--accent", "--accent-fill", "--accent-ink", "--accent-wash"];

/** Покрасить узел в цвет ветки: четыре токена акцента и класс `sec`. Ветка без цвета — токены
 * снимаются (узел живёт дольше записи: страница читалки одна на все записи). */
export function paintSection(node, section) {
  if (!node) return;
  const color = sectionColor(section);
  if (!color) {
    for (const name of TOKENS) node.style.removeProperty(name);
    node.classList.remove("sec");
    return;
  }
  node.style.setProperty("--accent", color);
  node.style.setProperty("--accent-fill", color);
  node.style.setProperty("--accent-ink", readableInk(color));
  node.style.setProperty("--accent-wash", `color-mix(in srgb, ${color} 14%, transparent)`);
  node.classList.add("sec");
}
