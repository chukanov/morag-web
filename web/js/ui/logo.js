// Знак в шапке: рисунок корпуса (если дал) + слово рамочным шрифтом + фазы для переливания.
//
// Знак — часть БРЕНДА (`brand.mark`, `brand.wordmark` из `/api/site`, витрины или сервера
// входа): у платформы он — одно слово, у корпуса — его зверь. Поэтому монтируется ПОСЛЕ
// прихода конфига, а до того в шапке пусто: запасной текст из index.html снимается сразу —
// он для мира без скриптов. Поле общее на рисунок и слово, поэтому волна крутится вокруг
// всего знака.
import { $, reducedMotion } from "./dom.js";
import { renderMark, renderText, startIdle, layout } from "./mark.js";
import * as boxfont from "./boxfont.js";
import { DEFAULT_WORDMARK } from "./brand.js";
import { drive as driveHalo, haloOptions, live as haloLive, withoutShadow } from "./halo.js";
import { measureTopbar } from "./topbar.js";

// Слово набрано кеглем 11, рисунок — 4: клетка слова в 2.75 раза крупнее, и координаты слова
// пересчитываются в клетки рисунка (`layout`), иначе вертушка на слове крутилась бы своим,
// более быстрым кругом.
const SCALE = 11 / 4;

let stopIdle = () => {};
let acts = null;

/** Смонтировать знак по бренду. Можно звать повторно — прежний покой снимается. */
export function showMark(brand = {}) {
  const markHost = $("#logo-mark"), wordHost = $("#logo-word");
  if (!markHost || !wordHost) return null;
  stopIdle();
  const art = brand.mark && Array.isArray(brand.mark.lines) ? brand.mark : null;
  const lines = boxfont.render(brand.wordmark || DEFAULT_WORDMARK);
  const { field, ox, oy, sx, sy } = layout(art, lines, art ? SCALE : 1);
  acts = renderMark(markHost, field, art);
  markHost.hidden = !art;
  renderText(wordHost, lines, field, { ox, oy, sx, sy });
  // Подпись под словом: у корпуса со своим словом — «morag» (на чём сделано), у платформы — «web».
  const sub = $(".logo-word em");
  if (sub) sub.textContent = brand.wordmark ? "morag" : "web";
  stopIdle = startIdle(acts, { disabled: reducedMotion() });
  const logo = $(".logo");
  if (logo) {
    logo.dataset.cols = field.cols;
    logo.dataset.rows = field.rows;
    logo.classList.toggle("no-art", !art);
  }
  measureTopbar(); // высота шапки зависит от того, есть ли рисунок
  return acts;
}

/** Слушатели наведения — один раз; знак под ними может пересобираться. */
export function initLogo() {
  const wordHost = $("#logo-word");
  if (wordHost) wordHost.textContent = "";
  const logo = $(".logo");
  if (!logo) return;
  // Живой перелив (узор или цель из `theme.halo`, которым нужен пересчёт кадров) — только на
  // наведении, как и CSS-ореол; без такого конфига всё делает CSS, как раньше.
  let run = null;
  logo.addEventListener("mouseenter", () => {
    // Наведение — повод нюхнуть: жест по действию человека, а не по таймеру.
    acts?.sniff();
    if (haloLive() && !reducedMotion()) {
      run?.stop();
      // Светлая тема — без теней (владелец, 16.09): на белой шапке свечение читается как
      // грязь. Ореол тогда выпадает вовсе, «символы» играют без свечения.
      const light = document.documentElement.getAttribute("data-theme") === "light";
      const opts = light ? withoutShadow(haloOptions()) : haloOptions();
      logo.classList.add("halo-live");
      run = opts ? driveHalo(logo, opts) : null;
    }
  });
  logo.addEventListener("mouseleave", () => {
    run?.stop();
    run = null;
    logo.classList.remove("halo-live");
  });
}
