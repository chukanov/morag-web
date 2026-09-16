// Знак сайта: рисунок в ASCII (если корпус его дал), живой и переливающийся на наведении.
//
// Рисунок и клетки мордочки приезжают из бренда корпуса (`brand.mark` → `/api/site`, файл в
// `brand/`), а не из кода: у платформы знак — слово, у корпуса — его зверь. Здесь только
// механика. Без клеток мордочки знак стоит неподвижно — моргать и нюхать нечем.
//
// ⚠️ `line-height` РОВНО 1. Строки рисунка обязаны касаться друг друга: при 1.35 (как было
//    у прежнего знака) картинка расслаивается в полоски, а рамка распадалась на кирпичи.
//
// ⚠️ Символы разбиты на <i> НЕ ради разметки, а ради переливания подсветки: фаза каждого
//    символа — его угол вокруг центра знака, поэтому волна идёт вертушкой, а не полосой.
//    Сетка строится ОДИН раз; моргание меняет текст двух узлов, нюханье двигает пять.
//    Пересобирать сетку на каждый жест нельзя — это перезапускало бы анимацию ореола
//    прямо под курсором.
//
// ⚠️ Пробелы НЕ оборачиваем. Их в рисунке больше половины, а подсвечивать пустоту незачем:
//    узлов было бы вдвое больше без единого видимого пикселя.

/** Размеры рисунка: `{lines}` → колонки и строки. Пустой рисунок — 0 × 0. */
export function artSize(art) {
  const lines = art?.lines || [];
  return { cols: lines.reduce((m, l) => Math.max(m, [...l].length), 0), rows: lines.length };
}

/**
 * Раскладка знака: где стоит слово относительно рисунка и какое у всего знака поле.
 * Слово набрано кеглем крупнее рисунка в `scale` раз; поле — в клетках РИСУНКА, чтобы вертушка
 * была одна на весь знак (см. `renderText`). Без рисунка поле — само слово.
 */
export function layout(art, wordLines, scale) {
  const { cols, rows } = artSize(art);
  const wordCols = Math.max(0, ...wordLines.map((l) => [...l].length));
  const wordRows = wordLines.length;
  if (!rows) return { field: { cols: wordCols, rows: wordRows }, ox: 0, oy: 0, sx: 1, sy: 1 };
  const ox = cols + 3;
  const oy = Math.max(0, (rows - wordRows * scale) / 2);
  return { field: { cols: ox + wordCols * scale, rows }, ox, oy, sx: scale, sy: scale };
}

/**
 * Собирает знак в контейнере.
 * @param {HTMLElement} host куда рисовать
 * @param {{cols:number, rows:number}} field общее поле знака — по нему считается угол
 *        символа; в него входит и надпись справа, поэтому вертушка крутится вокруг ВСЕГО знака.
 * @param {{lines:string[], eye?:object, nose?:object, snout?:object}} art рисунок и клетки
 *        мордочки из бренда корпуса (`mark_payload` на сервере проверил, что клетки в сетке).
 * @returns {{blink:Function, sniff:Function, live:boolean}}
 */
export function renderMark(host, field, art) {
  host.textContent = "";
  host.classList.add("logo-mark");
  const ART = art?.lines || [];
  const { cols: COLS, rows: ROWS } = artSize(art);
  const EYE = art?.eye || null;
  const NOSE = art?.nose || null;
  const SNOUT = art?.snout || null;

  const eyeCells = [];
  let snoutCell = null;
  for (let r = 0; r < ROWS; r++) {
    const line = document.createElement("span");
    line.className = "logo-row";
    const src = [...ART[r]];
    // Кончик морды уезжает в свой узел: двигать надо ЕГО, а не строку целиком.
    let nose = null;
    for (let c = 0; c < COLS; c++) {
      const inNose = NOSE && r === NOSE.row && c >= NOSE.from && c < NOSE.to;
      if (inNose && !nose) {
        nose = document.createElement("span");
        nose.className = "logo-nose";
        line.append(nose);
      }
      const box = inNose ? nose : line;
      const ch = src[c] ?? " ";
      if (ch === " ") { box.append(" "); continue; }
      const i = document.createElement("i");
      i.textContent = ch;
      i.style.setProperty("--p", phase(c, r, field));
      // Координаты в поле знака — для живого перелива (`ui/halo.js`): узор считает фазу по ним.
      if (i.dataset) {
        i.dataset.c = c;
        i.dataset.r = r;
      }
      box.append(i);
      if (EYE && r === EYE.row && c >= EYE.from && c < EYE.to) eyeCells.push(i);
      if (SNOUT && r === SNOUT.row && c === SNOUT.col) snoutCell = i;
    }
    host.append(line);
  }

  const setEye = s => eyeCells.forEach((n, k) => { n.textContent = s[k] ?? " "; });
  const setSnout = ch => { if (snoutCell) snoutCell.textContent = ch; };
  // Без клеток мордочки жесты — пустые: знак неподвижен, а вызовы снаружи ничего не ломают.
  if (!EYE && !SNOUT) return { blink() {}, sniff() {}, live: false };

  // ⚠️ Жесты накладываются: простой нюхает раз в 3-8 секунд и может совпасть с наведением мыши.
  // Без счётчика второй жест восстановил бы букву на середине первого, и пятачок «сдулся» бы
  // раньше времени. Возвращаем в покой только тот вызов, который был последним.
  let sniffRun = 0;
  const SNIFF_MS = 620;

  // Вдох ДВОЙНОЙ: раздулась, отпустила, раздулась снова. Одиночный «пых» читается как
  // случайный сбой отрисовки, а не как жест.
  // ⚠️ Расписание задано ДОЛЯМИ от длительности, а не миллисекундами: движение носа живёт
  // в CSS (`@keyframes logo-sniff`, выпады на 30% и 78%), и при жёстких числах буква с
  // движением разъедутся, стоит кому-то подправить время в одном месте из двух.
  const calm = SNOUT?.calm ?? "", puff = SNOUT?.sniff ?? "";
  const PUFFS = [[0, puff], [0.34, calm], [0.53, puff], [0.90, calm]];

  return {
    live: true,
    blink() { if (EYE) { setEye(EYE.shut); setTimeout(() => setEye(EYE.open), 120); } },
    sniff() {
      const run = ++sniffRun;
      host.classList.add("sniffing");
      for (const [at, ch] of PUFFS) {
        if (at === 0) { setSnout(ch); continue; }
        setTimeout(() => { if (run === sniffRun) setSnout(ch); }, at * SNIFF_MS);
      }
      setTimeout(() => {
        if (run !== sniffRun) return;   // поверх уже пришёл следующий вдох
        host.classList.remove("sniffing");
        setSnout(calm);
      }, SNIFF_MS);
    },
  };
}

/**
 * Разбирает готовый текст (надпись справа от зверя) на <i> с теми же фазами.
 *
 * ⚠️ Надпись набрана ДРУГИМ кеглем, чем зверь, поэтому её клетки крупнее. Чтобы вертушка
 * была одна на весь знак, координаты надписи пересчитываются в клетки ЗВЕРЯ: `sx`/`sy` —
 * во сколько раз клетка надписи больше, `ox`/`oy` — где надпись начинается. Без пересчёта
 * волна на надписи шла бы своим кругом, вдвое быстрее, и знак распадался бы надвое.
 */
export function renderText(host, lines, field, { ox = 0, oy = 0, sx = 1, sy = 1 } = {}) {
  host.textContent = "";
  lines.forEach((text, r) => {
    const line = document.createElement("span");
    line.className = "logo-row";
    [...text].forEach((ch, c) => {
      if (ch === " ") { line.append(" "); return; }
      const i = document.createElement("i");
      i.textContent = ch;
      i.style.setProperty("--p", phase(ox + c * sx, oy + r * sy, field));
      if (i.dataset) {
        i.dataset.c = (ox + c * sx).toFixed(2);
        i.dataset.r = (oy + r * sy).toFixed(2);
      }
      line.append(i);
    });
    host.append(line);
  });
}

/**
 * Фаза символа — его угол вокруг центра знака, 0..1 по часовой от верха.
 *
 * ⓘ Экспортируется НАМЕРЕННО: этой же формулой переливается заставка темы в чате. Приём один
 * на весь сайт, и копия формулы рядом означала бы две волны, которые разъедутся при первой же
 * правке — а разъезд такого рода замечают не глазами, а через полгода.
 * Отрицательная задержка анимации сдвигает символ ВПЕРЁД по циклу, поэтому узор
 * бежит в сторону убывания фазы, то есть ПРОТИВ часовой стрелки.
 * ⚠️ Клетка вдвое выше, чем шире, — без поправки «круг» вырождается в плоский овал
 * и волна идёт почти горизонтально, как обычный градиент.
 */
export function phase(c, r, field) {
  return Number(current(c, r, field, 0)).toFixed(4);
}

/** Вертушка — узор по умолчанию: угол вокруг центра. */
function spin(c, r, { cols, rows }) {
  const AR = 0.5;
  const dx = (c - (cols - 1) / 2) * AR;
  const dy = r - (rows - 1) / 2;
  let a = Math.atan2(dx, -dy);
  if (a < 0) a += Math.PI * 2;
  return a / (Math.PI * 2);
}
let current = spin;

/** Сменить узор фазы (конфиг темы `theme.halo.pattern`, узоры — `ui/halo.js`). Зовётся ДО
 *  отрисовки знака: фаза ставится символу при сборке сетки. `null` — вертушка. */
export function usePattern(fn) {
  current = typeof fn === "function" ? fn : spin;
}

/** Пересчитать фазы уже собранного знака (после смены узора: конфиг темы приезжает ПОЗЖЕ,
 *  чем рисуется знак). Поле — `data-cols`/`data-rows` контейнера, координаты — у символов. */
export function rephase(host) {
  if (!host) return;
  const field = { cols: Number(host.dataset?.cols) || 1, rows: Number(host.dataset?.rows) || 1 };
  for (const i of host.querySelectorAll("i[data-c]")) {
    i.style.setProperty("--p", phase(Number(i.dataset.c), Number(i.dataset.r), field));
  }
}

/**
 * «Покой»: движение приходит редко и коротко. Постоянно шевелящийся знак читается как
 * ёлочная гирлянда и мешает — решение владельца 10.09.
 */
export function startIdle(acts, { disabled = false } = {}) {
  if (disabled || !acts?.live) return () => {};
  let timer = 0;
  const tick = () => {
    if (Math.random() < 0.6) acts.blink(); else acts.sniff();
    timer = setTimeout(tick, 3400 + Math.random() * 4600);
  };
  timer = setTimeout(tick, 1200 + Math.random() * 2600);
  return () => clearTimeout(timer);
}

