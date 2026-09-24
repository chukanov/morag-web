// Переливание подсветки: УЗОРЫ фазы, ПАЛИТРЫ и ЦЕЛИ воздействия — один набор на знак в шапке
// и на заставку темы. Владелец (14.09) попросил 5-10 вариантов «как по-разному организовать
// перелив, включая случайно», от эффекта «Матрицы» до чересстрочных и фрактальных узоров, и
// не только на ореол. Лаборатория `/lab/halo.html` показывает всё это на живом знаке и на
// заставке; выбранное потом закрепляется конфигом темы.
//
// Модель одна для всех: у символа с координатами (c, r) в поле {cols, rows} есть ФАЗА 0..1 —
// узор решает, какая; цвет в момент t — палитра в точке frac(p + t / period). Узоры с `live`
// зависят и от времени (шум плывёт, дождь падает) — их фаза пересчитывается каждый кадр.
// Статичные узоры (фаза не зависит от t) сайт может гнать чистым CSS: фаза — отрицательная
// задержка анимации, как сейчас у вертушки.
//
// ⚠️ Клетка вдвое выше, чем шире (AR 0.5): без поправки круг вырождается в плоский овал.

import { rephase, usePattern } from "./mark.js";

const AR = 0.5;
const TAU = Math.PI * 2;
const frac = (x) => x - Math.floor(x);

const hash2 = (c, r) => {
  const x = Math.sin(c * 12.9898 + r * 78.233) * 43758.5453;
  return x - Math.floor(x);
};

/** Гладкий шум на решётке (value noise) и его сумма по трём октавам — «облако». */
function vnoise(x, y) {
  const x0 = Math.floor(x), y0 = Math.floor(y);
  const fx = x - x0, fy = y - y0;
  const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
  const a = hash2(x0, y0), b = hash2(x0 + 1, y0), c = hash2(x0, y0 + 1), d = hash2(x0 + 1, y0 + 1);
  return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
}
function fbm(x, y) {
  return (vnoise(x, y) * 0.55 + vnoise(x * 2.1 + 7, y * 2.1 + 3) * 0.3 + vnoise(x * 4.3 + 19, y * 4.3 + 11) * 0.15);
}

/** Геометрия точки относительно центра поля: смещения с поправкой на клетку, угол, радиус. */
function geo(c, r, { cols, rows }) {
  const dx = (c - (cols - 1) / 2) * AR;
  const dy = r - (rows - 1) / 2;
  let a = Math.atan2(dx, -dy);
  if (a < 0) a += TAU;
  const R = Math.hypot((cols - 1) / 2 * AR, (rows - 1) / 2) || 1;
  return { dx, dy, a: a / TAU, d: Math.hypot(dx, dy) / R };
}

export const PATTERNS = [
  { id: "spin", name: "Вертушка", note: "как сейчас: фаза — угол вокруг центра, волна крутится",
    phase: (c, r, f) => geo(c, r, f).a },
  { id: "rings", name: "Кольца", note: "фаза — расстояние от центра: круги расходятся от середины",
    phase: (c, r, f) => frac(geo(c, r, f).d * 1.4) },
  { id: "sweep", name: "Диагональ", note: "плоская волна наискосок, как блик по стеклу",
    phase: (c, r, f) => frac((c * AR + r) / (f.cols * AR + f.rows) * 1.6) },
  { id: "stars", name: "Искры", note: "случайная фаза у каждого символа — мерцание звёздного неба",
    phase: (c, r) => hash2(c, r) },
  { id: "matrix", name: "Матрица", note: "дождь по колонкам: у каждой своё начало, головка бежит вниз",
    phase: (c, r, f) => frac(hash2(c, 0) * 3 - (r / f.rows) * 1.6) },
  { id: "interlace", name: "Чересстрочно", note: "чётные строки слева направо, нечётные — навстречу",
    phase: (c, r, f) => frac((r % 2 ? 1 - c / f.cols : c / f.cols) * 1.2 + (r / f.rows) * 0.15) },
  { id: "star5", name: "Пятиконечная", note: "кольца в форме звезды: радиус дышит с пятью лучами",
    phase: (c, r, f) => { const g = geo(c, r, f); return frac(g.d / (1 + 0.42 * Math.cos(5 * g.a * TAU)) * 1.2); } },
  { id: "clouds", name: "Облака", note: "фрактальный шум, плывёт со временем — пятна, а не полосы", live: true,
    phase: (c, r, f, t) => frac(fbm(c * AR / 7 + t * 0.12, r / 7 + t * 0.05) * 2.2) },
  { id: "waves", name: "Волны", note: "интерференция двух источников по краям — муар, рябь",
    phase: (c, r, f) => {
      const R = f.rows || 1;
      const d1 = Math.hypot((c * AR) - 0, r - R / 2) / R;
      const d2 = Math.hypot((c * AR) - f.cols * AR, r - R / 2) / R;
      return frac(0.5 + (Math.sin(d1 * 5.5) + Math.sin(d2 * 5.5)) / 4);
    } },
  { id: "spiral", name: "Спираль", note: "угол плюс радиус: рукава закручиваются к центру",
    phase: (c, r, f) => { const g = geo(c, r, f); return frac(g.a + g.d * 1.3); } },
  { id: "random", name: "Случайно", note: "каждый раз новый узор и палитра — сайт выбирает сам при наведении",
    phase: (c, r, f, t) => 0, random: true },
];

/** Палитры — четыре опорных цвета по кругу; `aurora` — нынешние цвета ореола. */
export const PALETTES = [
  { id: "aurora", name: "Аврора", stops: ["#B14FE0", "#EC4899", "#F6F7FB", "#7FB0FF"] },
  { id: "matrix", name: "Матрица", stops: ["#0B3D1E", "#22C55E", "#D9FFE3", "#15803D"] },
  { id: "ocean", name: "Океан", stops: ["#0E4C6B", "#22B8CF", "#E0FBFF", "#3B82F6"] },
  { id: "brass", name: "Латунь", stops: ["#8A5A1B", "#E4A04B", "#FFF3D6", "#57B4A9"] },
  { id: "mono", name: "Моно", stops: ["#334155", "#94A3B8", "#F8FAFC", "#64748B"] },
];

/** Цель: на что ложится цвет. Ореол — как сейчас; заливка съедает буквы на тёмных участках
 *  (ловилось: «O» в слове читалась как «I»), поэтому в чистом виде — только чтобы посмотреть. */
// ⓘ Сняты владельцем 16.09: цели «Волна вверх-вниз» и «Ореол + заливка», палитры «Угли» и
// «Радуга»; в конфиге их имена читаются как неизвестные (цель → ореол, палитра → первая).
export const TARGETS = [
  { id: "halo", name: "Ореол", note: "свечение вокруг символа, буква остаётся своей" },
  { id: "ink", name: "Заливка", note: "цвет самой буквы" },
  { id: "glyph", name: "Символы", note: "на гребне волны символ на миг подменяется случайным — «Матрица»" },
];

const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];

/** Цвет палитры в точке u∈[0,1): линейная смесь соседних опор по кругу. */
export function colorAt(stops, u) {
  const n = stops.length;
  const x = frac(u) * n;
  const i = Math.floor(x), k = x - i;
  const a = hex(stops[i % n]), b = hex(stops[(i + 1) % n]);
  const m = a.map((v, j) => Math.round(v + (b[j] - v) * k));
  return `rgb(${m[0]},${m[1]},${m[2]})`;
}

const GLYPHS = "01ｱｲｳｴｵｶｷｸ#*+=%$@&";

// --- закрепление на сайте ---------------------------------------------------------------------
//
// `theme.halo: {pattern, palette, target, period}` в site.yml. Статичный узор с целью «ореол»
// ложится в CSS: фаза символа — узор (`usePattern`), палитра — переменные `--h1..--h4` у
// keyframes; поведение без конфига — прежнее байт в байт. Живые узоры (`live`), «случайно» и
// цели кроме ореола требуют пересчёта кадров — их ведёт `drive()` на наведении (знак) и на
// время сцены (заставка); включает это `live()`.
let HALO = null;

export function applyHalo(cfg) {
  HALO = cfg && typeof cfg === "object" ? { ...cfg } : null;
  const pat = PATTERNS.find((p) => p.id === HALO?.pattern);
  usePattern(pat && !pat.live && !pat.random ? (c, r, f) => pat.phase(c, r, f, 0) : null);
  const pal = PALETTES.find((p) => p.id === HALO?.palette) || PALETTES[0];
  const root = document.documentElement;
  pal.stops.forEach((color, i) => root.style.setProperty(`--h${i + 1}`, color));
  // знак нарисован до прихода конфига — фазы под новый узор ставятся заново
  rephase(document.querySelector(".logo"));
  return HALO;
}

/** Нужен ли живой перелив (иначе всё делает CSS). `opts` — чьи настройки: знака (по умолчанию)
 *  или заставки (`sceneOptions()`), у них может быть разная цель. */
export function live(opts = haloOptions()) {
  if (!HALO || !opts) return false;
  const pat = PATTERNS.find((p) => p.id === opts.pattern);
  return Boolean((pat && (pat.live || pat.random)) || (opts.target && opts.target !== "halo") || opts.shadow === false);
}

function fields(src) {
  return {
    pattern: src.pattern, palette: src.palette, target: src.target || "halo",
    period: Number(src.period) || 3, targets: Array.isArray(src.targets) ? src.targets : undefined,
    ...(typeof src.shadow === "boolean" ? { shadow: src.shadow } : {}),
    ...(Number(src.matrix) > 0 ? { matrix: Math.min(1, Number(src.matrix)) } : {}),
  };
}

export function haloOptions() {
  return HALO ? fields(HALO) : {};
}

/** Настройки заставки темы: общие, а поверх — `theme.halo.scene` (владелец, 18.09: заставке —
 *  только цвет символов, без теней, знаку — прежний перелив). Ключ, которого в `scene` нет,
 *  берётся из общих. Только здесь имеют смысл цель `none` (сцена своим цветом, без перелива)
 *  и `matrix` — доля показов, в которых символы сцены на миг подменяются случайными. */
export function sceneOptions() {
  if (!HALO) return {};
  const base = fields(HALO);
  if (!HALO.scene || typeof HALO.scene !== "object") return base;
  return fields({ ...base, ...HALO.scene });
}

/** Цель «random» — случайная из пула; повтор в пуле — вес («символы» чаще: владелец, 15.09).
 *  Неизвестные и пустой пул — в ореол: сломанный конфиг не должен гасить перелив. */
export function pickTarget(target, targets) {
  if (target !== "random") return target;
  const known = new Set(TARGETS.map((t) => t.id));
  const pool = (Array.isArray(targets) && targets.length ? targets : TARGETS.map((t) => t.id)).filter((t) => known.has(t));
  return pool.length ? pool[Math.floor(Math.random() * pool.length)] : "halo";
}

/** Те же настройки, но БЕЗ теней (светлая тема, владелец 16.09: «переливы с тёмной тенью
 *  смотрятся грязно» на белой шапке). Ореол — это и есть тень, поэтому он выпадает из пула;
 *  «символы» остаются как чистая подмена символов, «заливка» — как цвет буквы. Возвращает null,
 *  если без теней играть нечем (цель — ореол или пул опустел): тогда перелив не запускается. */
export function withoutShadow(opts) {
  if (!opts) return null;
  const pool = (Array.isArray(opts.targets) && opts.targets.length ? opts.targets : TARGETS.map((t) => t.id))
    .filter((t) => t !== "halo");
  const target = opts.target || "halo";
  if (target === "halo" || (target === "random" && !pool.length)) return null;
  return { ...opts, targets: pool, shadow: false };
}

/**
 * Живой перелив: раскрашивает символы контейнера каждый кадр. Символы — `<i data-c data-r>`
 * (их так помечают `renderMark`, `renderText` и сетка заставки), поле — `data-cols`/`data-rows`
 * контейнера. Возвращает остановку, которая снимает все инлайновые стили.
 * `shadow: false` — без свечения вообще (см. `withoutShadow`).
 */
export function drive(host, { pattern = "spin", palette = "aurora", target = "halo", period = 3, fps = 30, targets, shadow = true } = {}) {
  target = pickTarget(target, targets);
  const field = { cols: Number(host.dataset.cols) || 1, rows: Number(host.dataset.rows) || 1 };
  const cells = [...host.querySelectorAll("i[data-c]")].map((node) => ({
    node, c: Number(node.dataset.c), r: Number(node.dataset.r), swapped: false, orig: "",
  }));
  let pat = PATTERNS.find((p) => p.id === pattern) || PATTERNS[0];
  let pal = PALETTES.find((p) => p.id === palette) || PALETTES[0];
  if (pat.random) {
    const pool = PATTERNS.filter((p) => !p.random);
    pat = pool[Math.floor(Math.random() * pool.length)];
    pal = PALETTES[Math.floor(Math.random() * PALETTES.length)];
  }
  const phases = pat.live ? null : cells.map((x) => pat.phase(x.c, x.r, field, 0));
  const t0 = performance.now();
  let last = 0;
  let raf = 0;
  const step = 1000 / fps;
  const seed = Math.random() * 1000;
  // Без теней — и статичное свечение контейнера из CSS тоже снимаем (класс, не инлайн: у
  // заставки оно на самом `.fx-art`, а не на символах).
  if (!shadow) host.classList.add("halo-noshadow");

  function paint(now) {
    raf = requestAnimationFrame(paint);
    if (now - last < step) return;
    last = now;
    const t = (now - t0) / 1000;
    const shift = t / period;
    for (let k = 0; k < cells.length; k += 1) {
      const x = cells[k];
      const p = phases ? phases[k] : pat.phase(x.c, x.r, field, t + seed);
      const u = frac(p + shift);
      const col = colorAt(pal.stops, u);
      const s = x.node.style;
      if (shadow && (target === "halo" || target === "glyph")) {
        s.textShadow = `0 0 3px ${col}, 0 0 9px ${col}`;
      }
      if (target === "ink") s.color = col;
      if (target === "glyph") {
        // гребень (u около 0.5, где палитра светлее всего) — символ на миг чужой.
        // ⚠️ Подменяем и возвращаем ТОЛЬКО своё: символ запоминается в момент подмены, а не на
        // старте. Иначе перелив дрался бы с чужой анимацией той же ячейки — «нюх» знака ставит
        // на нос `O` за миг до старта, и перелив возвращал бы `O` вместо `p` (ловилось 15.09).
        const hot = Math.abs(u - 0.5) < 0.04;
        if (hot) {
          if (!x.swapped) { x.orig = x.node.textContent; x.swapped = true; }
          x.node.textContent = GLYPHS[Math.floor(hash2(x.c + Math.floor(t * 8), x.r) * GLYPHS.length)];
        } else if (x.swapped) {
          x.node.textContent = x.orig;
          x.swapped = false;
        }
      }
    }
  }
  raf = requestAnimationFrame(paint);
  return {
    pattern: pat.id, palette: pal.id, target,
    stop() {
      cancelAnimationFrame(raf);
      host.classList.remove("halo-noshadow");
      for (const x of cells) {
        x.node.style.textShadow = "";
        x.node.style.color = "";
        if (x.swapped) { x.node.textContent = x.orig; x.swapped = false; }
      }
    },
  };
}
