// Облако слов В ФОРМЕ БУКВЫ: темы — большая «М», метки — большая «О», рядом они читаются как
// «МО» (владелец, 15.09). Часть слов ложится вертикально и под углом — так заполняются штрихи
// буквы, и сама буква читается издалека.
//
// Две половины. `letterMask` растрирует глиф на канвасе и отдаёт сетку «внутри/снаружи» —
// это только браузер. `layoutWords` — чистая геометрия: маска, слова с весами и измеритель
// текста на входе, координаты с углами на выходе; её и проверяет node-тест. Раскладка —
// классическое «облако по маске»: слова от крупных к мелким, каждому — случайные (по сиду)
// клетки внутри буквы и набор углов; ложится там, где повёрнутый прямоугольник целиком внутри
// маски и не задевает уже положенное; не влезло — кегль ужимается на 15 % (до трёх раз).
//
// ⓘ Сид — от состава слов: тот же набор даёт ту же картинку, и облако не «прыгает» при
// перерисовке панели; сменился отбор (веса) — раскладка честно другая.

export const ANGLES = [0, 90, -90, 45, -45];

/** Детерминированный генератор — тот же mulberry32, что у волны карточки-момента. */
export function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Сид из строк: раскладка держится за состав слов, а не за порядок вызова. */
export function hashSeed(parts) {
  let h = 2166136261;
  for (const ch of parts.join("")) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return h >>> 0;
}

/** Маска из предиката (для тестов и любой формы, не только буквы). */
export function maskFromPredicate(width, height, cell, inside) {
  const cols = Math.ceil(width / cell);
  const rows = Math.ceil(height / cell);
  const grid = new Uint8Array(cols * rows);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (inside((c + 0.5) * cell, (r + 0.5) * cell)) grid[r * cols + c] = 1;
    }
  }
  return { width, height, cell, cols, rows, grid };
}

/**
 * Маска буквы: глиф вписывается в поле и растрируется. Только браузер.
 * Нет канваса (старый движок, тесты) — null, и вызывающий рисует обычное облако.
 *
 * `stroke` — обводка той же краской поверх заливки: штрих раздаётся на полтолщины в обе стороны
 * (владелец, 15.09: «толщину букв тоже сделать больше») — самый жирный вес шрифта всё равно
 * тоньше, чем нужно облаку, а толще штрих — больше места под слова.
 */
export function letterMask(letter, { width, height, cell = 3, family = "sans-serif", weight = 900, stroke = 0,
                                     stretch = false }) {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext?.("2d");
  if (!ctx || !ctx.measureText) return null;
  // Кегль подбираем по фактическому боксу глифа: буква занимает почти всё поле — площадь
  // буквы и есть место под слова, а полей вокруг у облака нет.
  const probe = 100;
  ctx.font = `${weight} ${probe}px ${family}`;
  const m = ctx.measureText(letter);
  const glyphH = (m.actualBoundingBoxAscent || probe * 0.72) + (m.actualBoundingBoxDescent || 0);
  const glyphW = (m.actualBoundingBoxLeft || 0) + (m.actualBoundingBoxRight || m.width);
  // Глиф в своей пропорции, вписан в поле по обеим осям. `stretch` растягивает его по
  // вертикали до высоты поля (кегль тогда по ширине) — пробовали 15.09 ради площади под слова
  // (высокое поле само не помогает: «М» упирается в ширину, площадь от высоты не росла —
  // 15 459 клеток при 425×425 и 15 462 при 425×574), но владелец увидел: «некрасиво», буквы
  // стали узкими. Выключено; место под слова добирается ужиманием кегля в `list.js`.
  let size = Math.floor(probe * Math.min((height * 0.97 - stroke) / glyphH, (width * 0.98 - stroke) / glyphW));
  let sy = 1;
  if (stretch) size = Math.floor(probe * ((width * 0.98 - stroke) / glyphW));
  ctx.font = `${weight} ${size}px ${family}`;
  const mm = ctx.measureText(letter);
  const ascent = mm.actualBoundingBoxAscent || size * 0.72;
  const descent = mm.actualBoundingBoxDescent || 0;
  if (stretch) sy = Math.max(1, (height * 0.97 - stroke) / (ascent + descent));
  ctx.fillStyle = "#000";
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.save();
  ctx.scale(1, sy);
  const baseline = (height / sy - (ascent + descent)) / 2 + ascent;
  ctx.fillText(letter, width / 2, baseline);
  if (stroke > 0) {
    // Обводка под растяжением: по горизонтали — `stroke`, по вертикали — в `sy` раз толще.
    // Это нам на руку: у вытянутой буквы горизонтальные штрихи иначе выглядели бы тоньше.
    ctx.lineJoin = "round";
    ctx.lineWidth = stroke;
    ctx.strokeStyle = "#000";
    ctx.strokeText(letter, width / 2, baseline);
  }
  ctx.restore();
  const data = ctx.getImageData(0, 0, width, height).data;
  return maskFromPredicate(width, height, cell, (x, y) => {
    const px = Math.min(width - 1, Math.floor(x));
    const py = Math.min(height - 1, Math.floor(y));
    return data[(py * width + px) * 4 + 3] > 128;
  });
}

/**
 * Разложить слова по маске.
 * @param mask     {width, height, cell, cols, rows, grid}
 * @param words    [{value, weight 0..1}]
 * @param measure  (text, size) → {w, h} в пикселях
 * @returns {placed: [{value, weight, size, x, y, angle}], dropped: [value]}
 */
export function layoutWords(mask, words, measure, {
  seed = 1, minSize = 10, maxSize = 22, angles = ANGLES, tries = 1500, shrinks = 3, pad = 0.35,
} = {}) {
  const { cell, cols, rows, grid } = mask;
  const occupied = new Uint8Array(cols * rows);
  const rng = seeded(seed);
  // Кандидаты — клетки внутри буквы, перемешанные один раз; каждое слово идёт по ним со своего
  // сдвига и с шагом, чтобы не обходить все семь тысяч клеток ради одного места.
  const cells = [];
  for (let i = 0; i < grid.length; i++) if (grid[i]) cells.push(i);
  for (let i = cells.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [cells[i], cells[j]] = [cells[j], cells[i]];
  }
  const placed = [];
  const dropped = [];
  const sorted = [...words].sort((a, b) => b.weight - a.weight || a.value.localeCompare(b.value, "ru"));
  const stride = Math.max(1, Math.floor(cells.length / tries));

  /** Клетки, которые накрывает повёрнутый прямоугольник с центром в (cx, cy); null — не влезает. */
  function footprint(cx, cy, w, h, angle) {
    const rad = (angle * Math.PI) / 180;
    const cos = Math.cos(rad);
    const sin = Math.sin(rad);
    const hw = w / 2 + pad * cell;
    const hh = h / 2 + pad * cell;
    // Габарит повёрнутого прямоугольника — какие клетки вообще смотреть.
    const ex = Math.abs(hw * cos) + Math.abs(hh * sin);
    const ey = Math.abs(hw * sin) + Math.abs(hh * cos);
    const c0 = Math.floor((cx - ex) / cell);
    const c1 = Math.floor((cx + ex) / cell);
    const r0 = Math.floor((cy - ey) / cell);
    const r1 = Math.floor((cy + ey) / cell);
    if (c0 < 0 || r0 < 0 || c1 >= cols || r1 >= rows) return null;
    const out = [];
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const dx = (c + 0.5) * cell - cx;
        const dy = (r + 0.5) * cell - cy;
        // в систему координат прямоугольника — поворот на −angle
        const lx = dx * cos + dy * sin;
        const ly = -dx * sin + dy * cos;
        if (Math.abs(lx) > hw || Math.abs(ly) > hh) continue;
        const i = r * cols + c;
        if (!grid[i] || occupied[i]) return null;
        out.push(i);
      }
    }
    return out;
  }

  for (const [n, word] of sorted.entries()) {
    let size = minSize + (maxSize - minSize) * Math.max(0, Math.min(1, word.weight));
    let done = null;
    const prefer = pickAngles(rng, angles);
    for (let s = 0; s <= shrinks && !done; s++) {
      const { w, h } = measure(word.value, size);
      for (const angle of prefer) {
        const start = Math.floor(rng() * stride);
        for (let k = start; k < cells.length; k += stride) {
          const idx = cells[k];
          const cx = ((idx % cols) + 0.5) * cell;
          const cy = (Math.floor(idx / cols) + 0.5) * cell;
          const cover = footprint(cx, cy, w, h, angle);
          if (!cover) continue;
          for (const i of cover) occupied[i] = 1;
          done = { value: word.value, weight: word.weight, size, x: cx, y: cy, angle, order: n };
          break;
        }
        if (done) break;
      }
      if (!done) size = Math.max(minSize * 0.8, size * 0.85);
    }
    if (done) placed.push(done);
    else dropped.push(word.value);
  }
  return { placed, dropped };
}

/** Порядок углов для слова: чаще горизонталь, реже вертикаль и диагональ — иначе буква рябит. */
function pickAngles(rng, angles) {
  const roll = rng();
  const rest = angles.filter((a) => a !== 0);
  // перемешанные остальные — чтобы вертикаль и диагонали чередовались
  for (let i = rest.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [rest[i], rest[j]] = [rest[j], rest[i]];
  }
  if (roll < 0.62) return [0, ...rest];
  const first = rest[0];
  return [first, 0, ...rest.slice(1)];
}

/** Измеритель текста канвасом — тем же шрифтом, что у слов облака. Только браузер. */
export function canvasMeasurer(font) {
  if (typeof document === "undefined") return null;
  const ctx = document.createElement("canvas").getContext?.("2d");
  if (!ctx || !ctx.measureText) return null;
  return (text, size) => {
    ctx.font = `600 ${size}px ${font}`;
    return { w: ctx.measureText(text).width, h: size * 1.12 };
  };
}
