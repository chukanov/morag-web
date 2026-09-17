// Заставка при определении темы вопроса: живое ASCII-море.
// Символы здесь — «пиксели»: каждый кадр считаем яркость клетки процедурно,
// поэтому движение плавное, а сцена каждый раз получается новой.
//
// ⓘ Здесь же жил эквалайзер под обложкой лендинга. Обложку убрали 10.09 — список записей
// переехал на главную, и полноэкранная картинка перед ним означала прокручивать её каждый
// раз. Знак в шапке остался (`ui/mark.js`), фоновая анимация ушла вместе с обложкой.

import { phase } from "./mark.js";
import { TARGETS, drive as driveHalo, live as haloLive, sceneOptions, withoutShadow } from "./halo.js";

const RAMP = " .:-=+*#";
const SKY = " .:-=+*#";
const SEA = " .:~-=+*";
const SCRAMBLE = ".:-=+*#";

// ---------------------------------------------------------------------------
// Заставка «определяю тему»: одна из трёх сцен, случайно, только на появлении.
// Сцена собирается из шума (scramble-in) и рассыпается в заголовок (scatter-out).

const hash2 = (c, r) => {
  const x = Math.sin(c * 12.9898 + r * 78.233) * 43758.5453;
  return x - Math.floor(x);
};
const ease = (p) => (p <= 0 ? 0 : p >= 1 ? 1 : p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const angleDiff = (a, b) => {
  const d = Math.abs(a - b) % (Math.PI * 2);
  return d > Math.PI ? Math.PI * 2 - d : d;
};
// трохоида: острый гребень, широкая пологая впадина — вода, а не синус
const trochoid = (th, p) => Math.pow((Math.sin(th) + 1) * 0.5, p) * 2 - 1;
const rf = (a, b) => a + Math.random() * (b - a);
const rint = (a, b) => a + Math.floor(Math.random() * (b - a + 1));

/**
 * Плотность облака в точке неба (0 — чисто, 1 — гуще некуда).
 * Контур — сумма трёх несоизмеримых синусов: гряда получается клочковатой,
 * а не правильной волной. Отдельной функцией — потому что ровно то же
 * считает вода, отражая небо.
 */
function cloudAt(g, c, r, t) {
  let dense = 0;
  for (const band of g.clouds || []) {
    const x = c * band.scale + t * band.speed + band.phase;
    const shape = 0.45 * Math.sin(x) + 0.32 * Math.sin(x * 2.3 + 1.2) + 0.23 * Math.sin(x * 4.1);
    // смещение отрицательное: гряда местами обрывается в чистое небо,
    // иначе получается сплошная стена во всю ширину
    const thick = band.thick * (shape - 0.05);
    if (thick <= 0) continue;
    const dy = Math.abs(r - band.row);
    if (dy < thick) dense = Math.max(dense, 1 - dy / thick);
  }
  return dense;
}
// gamma<1 поднимает полутона: без этого сцена выглядит серой кашей
const shade = (b, ramp = SKY) => {
  const v = Math.pow(Math.max(0, Math.min(1, b)), 0.72);
  return ramp[Math.min(ramp.length - 1, Math.floor(v * ramp.length))];
};

// Матрица Байера 8×8 — упорядоченный дизеринг. Строится рекурсивно:
// M(2n) = [[4M, 4M+2], [4M+3, 4M+1]].
const BAYER = (() => {
  let m = [[0]];
  while (m.length < 8) {
    const n = m.length;
    const next = Array.from({ length: n * 2 }, () => new Array(n * 2));
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const v = m[y][x] * 4;
        next[y][x] = v;
        next[y][x + n] = v + 2;
        next[y + n][x] = v + 3;
        next[y + n][x + n] = v + 1;
      }
    }
    m = next;
  }
  return m;
})();

/**
 * Как `shade`, но с порогом, своим у каждой клетки.
 *
 * Зачем: небо квантуется в восемь символов, ступень 0.125, а по вертикали
 * яркость меняется всего на 0.04 — то есть меньше ступени. Поэтому при закате
 * целые ряды перещёлкивались почти разом, и переход читался как полоса,
 * ползущая сверху вниз (на рассвете — снизу вверх).
 *
 * Со своим порогом у каждой клетки соседние ячейки переходят на новый символ не
 * одновременно, а по узору: темнота НАСТУПАЕТ сгущающейся сыпью, а свет её
 * развеивает. Матрица 8×8 даёт 64 промежуточных состояния между двумя
 * символами — этого хватает, чтобы переход шёл заметно и плавно.
 *
 * Амплитуда ровно в одну ступень: больше — узор проступит как шахматы поверх
 * картинки, меньше — снова щёлкнет разом.
 */
const shadeDither = (b, ramp, c, r) => {
  // Порог = матрица + шум клетки. Матрица задаёт ПОРЯДОК: клетки гаснут не
  // случайными пятнами, а равномерно сгущающейся сыпью. Шум ломает решётку:
  // на нашей крупной ячейке чистый Байер читается как шахматы поверх картинки,
  // а чистый шум (без матрицы) сбивается в кляксы и выглядит грязью.
  // Вес шума — единственная ручка: больше структуры или больше органики.
  const threshold = (BAYER[r & 7][c & 7] + hash2(c, r) * 12) / 64 - 0.5;
  const v = Math.pow(Math.max(0, Math.min(1, b)), 0.72) + threshold / ramp.length;
  return ramp[Math.max(0, Math.min(ramp.length - 1, Math.floor(v * ramp.length)))];
};

const SCENES = [
  {
    // маяк: луч выходит от своего края и метёт через центр
    kicker: "Разрезаю туман…",
    init(g) {
      g.side = Math.random() < 0.5 ? -1 : 1;
      g.lx = Math.round(g.cols * (g.side < 0 ? rf(0.1, 0.22) : rf(0.78, 0.9)));
      g.tower = [[-1, " _ "], [0, "[#]"], [1, "/#\\"], [2, "|#|"], [3, "|#|"], [4, "/###\\"]];
    },
    cell(c, r, t, g) {
      const seaTop = g.rows - 4;
      const lamp = seaTop - 6;
      for (const [dr, s] of g.tower) {
        if (lamp + dr !== r) continue;
        const idx = c - (g.lx - (s.length >> 1));
        if (idx >= 0 && idx < s.length && s[idx] !== " ") return s[idx];
      }
      let b = 0.07 + 0.1 * (0.5 + 0.5 * Math.sin(c * 0.17 + t * 0.7)) * (0.5 + 0.5 * Math.cos(r * 0.33 - t * 0.45));
      const surf = seaTop + 1.3 + 1.1 * Math.sin(c * 0.3 + t * 2.3) + 0.7 * Math.sin(c * 0.12 - t * 1.6);
      const sea = r >= surf;
      if (sea) b = 0.3 + 0.16 * Math.sin(c * 0.42 + t * 3 + r * 0.6) - (r - surf) * 0.03;
      // Маяк вращается, и мы смотрим на него СБОКУ. Считаем азимут и
      // раскладываем его на две видимые составляющие:
      //   across — «поперёк вида»: узкий луч, уходящий в море;
      //   toward — «на зрителя»: луч разворачивается и заливает кадр светом.
      // Фаза 0 — луч смотрит ВБОК, в открытое море; дальше он разворачивается
      // на зрителя и к ~2-й секунде заливает кадр. Раньше отсчёт начинался с
      // заливки, и сцена сразу уходила в темноту.
      const TURN = 8.5; // секунд на полный оборот
      const phi = (t / TURN) * Math.PI * 2;
      const across = Math.cos(phi) * -g.side; // от башни в открытое море
      const toward = Math.sin(phi); // >0 — светит нам в глаза

      const dx = c - g.lx;
      const dy = (r - lamp) * 1.9;
      const dist = Math.hypot(dx, dy);

      // Видимый луч: чем сильнее он развёрнут на зрителя, тем короче и шире —
      // это и есть перспективное укорочение.
      const span = Math.abs(across);
      if (dist > 1 && span > 0.04) {
        const TILT = 0.16; // чуть вниз, чтобы свет ложился на воду
        const aim = across >= 0 ? TILT : Math.PI - TILT;
        const half = 0.13 + 0.45 * (1 - span);
        const reach = g.cols * 0.72 * span + 6;
        const ad = angleDiff(Math.atan2(dy, dx), aim);
        if (ad < half) b += (1 - ad / half) * Math.max(0, 1 - dist / reach) * 0.95 * (0.35 + 0.65 * span);
      }

      // Заливка: луч смотрит на нас — свет заполняет сцену, слабея к краям.
      const flood = Math.pow(Math.max(0, toward), 2.4);
      if (flood > 0.01) b += flood * 0.6 * Math.max(0.15, 1 - dist / (g.cols * 0.95));

      if (dist < 3.5) b += (0.45 + 0.45 * flood) * (1 - dist / 3.5); // сам фонарь
      return shade(b, sea ? SEA : SKY);
    },
  },
  {
    // рассвет / закат / луна — со световой дорожкой на воде и звёздами
    kicker: "Ловлю свет…",
    init(g) {
      const v = Math.random();
      g.night = v > 0.66;
      g.sunset = !g.night && v > 0.33;
      g.sx = Math.round(g.cols * rf(0.35, 0.65));
      // на закате навстречу солнцу выходит луна — с другой стороны неба
      g.mx = Math.round(g.sx + g.cols * (g.sx < g.cols / 2 ? 0.33 : -0.33));

      // Облака на рассвете: 2-3 гряды на разной высоте. У каждой свой масштаб,
      // скорость и фаза — поэтому они расходятся, а не едут одной стенкой.
      if (!g.night && !g.sunset) {
        const bands = rint(2, 3);
        g.clouds = [];
        for (let i = 0; i < bands; i++) {
          g.clouds.push({
            row: g.rows * rf(0.16, 0.5), // держимся верхней половины неба
            scale: rf(0.09, 0.17), // крупнее/мельче клочья
            speed: rf(0.05, 0.14) * (Math.random() < 0.5 ? 1 : -1),
            thick: rf(1.2, 2.6),
            phase: rf(0, 6.28),
          });
        }
      }
      g.kicker = g.night ? "Иду по лунной дорожке…" : g.sunset ? "Ловлю закат…" : "Ловлю рассвет…";
    },
    cell(c, r, t, g) {
      const seaTop = g.rows - 4;
      const XS = 0.55; // сжатие по горизонтали: символ выше, чем шире
      const DISC = 2.4; // радиус светила
      const DISC_HALF = DISC / XS; // его полуширина В КОЛОНКАХ — с неё стартует дорожка
      // Светило идёт в одну сторону, обратного хода нет. Темп неравномерный:
      // у самой воды движение замедляется, вдали от неё — быстрее. Поэтому
      // закат разгоняется вверху и замирает у кромки моря, а рассвет наоборот
      // тяжело выбирается из воды и дальше ускоряется.
      // Асимптотика: старт в нормальном темпе, дальше всё медленнее и медленнее,
      // до конца путь не «упирается» — сцена может играть сколько угодно.
      const age = Math.max(0, t - 0.3);
      const p = g.night ? ease(Math.min(age / 7, 1)) : 1 - Math.exp(-age / (g.sunset ? 1.9 : 2.4));
      // Луна висит высоко в небе, но НЕ выше зоны, которую гасит маска
      // (сцена уходит под шапку сайта) — иначе её просто не видно.
      const moonBase = Math.max(g.rows * 0.3, seaTop * 0.42);
      const sr = g.night
        ? moonBase - p * 1.2 // едва заметно приподнимается
        : g.sunset
          ? 2 + p * seaTop // уходит ЗА линию воды — море скроет диск
          : seaTop + 2 - p * seaTop; // и выходит ИЗ-ПОД воды — зеркально закату

      // Высота светила над водой: 1 — высоко, 0 — касается, <0 — за горизонтом.
      // Чем ниже светило, тем положе луч и тем шире дорожка на воде — максимум
      // ровно на кромке. Пока светило за горизонтом (закат догорает или рассвет
      // ещё не наступил), дорожка гаснет и сужается.
      const height = (seaTop - sr) / Math.max(1, seaTop - 2);
      const lit = height >= 0 ? 1 : Math.max(0, 1 + height * 3.5);
      // Веер: чем ниже светило, тем положе луч и тем быстрее дорожка расходится
      // к зрителю. На саму ширину у горизонта это не влияет — она всегда равна
      // диску, иначе дорожка «отрывается» от источника.
      const fan = 0.5 + 1.6 * (1 - Math.max(0, Math.min(1, height)));
      const stars = g.night ? 1 : g.sunset ? p : 1 - p;
      // Насколько уже рассвело: 0 — светило у воды, 1 — высоко. Нужно и небу,
      // и воде (отражение разгорается вместе с облаками), поэтому считаем здесь.
      const day = g.night ? 0 : Math.max(0, Math.min(1, height));
      if (r >= seaTop) {
        let b = 0.3 + 0.15 * Math.sin(c * 0.4 + t * 2.6 + r * 0.6) - (r - seaTop) * 0.02;
        if (g.night) b *= 0.72;

        // Отражение облаков. Зеркалим небо относительно горизонта, растягивая
        // к зрителю (перспектива), и ведём отражённую точку по волне — иначе
        // получилась бы вторая, перевёрнутая, но идеально чёткая гряда.
        // Гаснет книзу: у дальней кромки вода зеркалит, вблизи — уже рябь.
        if (g.clouds) {
          const depth = r - seaTop + 0.5;
          const wob = 1.7 * Math.sin(r * 1.1 + t * 1.9) + 0.9 * Math.sin(c * 0.25 - t * 1.3);
          const refl = cloudAt(g, c + wob, seaTop - depth * 1.7, t);
          // Гасим лишь наполовину: высокие гряды зеркалятся у ближней кромки,
          // и при резком затухании они пропадали вовсе — отражалась только
          // та мелочь, что висит у горизонта.
          const fade = 1 - 0.5 * (depth / (g.rows - seaTop));
          b += refl * fade * (0.1 + 0.3 * day);
        }
        // у горизонта — ровно ширина диска, дальше к зрителю только шире
        const width = DISC_HALF + (r - seaTop) * 0.95 * fan;
        const off = Math.abs(c - g.sx);
        if (off < width) {
          const edge = 1 - off / width;
          const glint = 0.5 + 0.5 * Math.sin(c * 0.9 + r * 1.4 + t * 4.5);
          const spark = hash2(c, r);
          // блики держим в полную силу — гаснет только подложка дорожки
          const path = (edge * (0.35 + 0.5 * glint) + (spark > 0.7 ? edge * 0.4 * spark : 0)) * 0.6;
          b = Math.max(b, 0.35 * lit + path);
        }
        return shadeDither(b, SEA, c, r);
      }
      const dx = (c - g.sx) * XS;
      const dist = Math.hypot(dx, r - sr);
      if (g.night) {
        const mx = (c - (g.sx + 1.3)) * XS;
        if (dist < DISC && Math.hypot(mx, r - (sr - 0.4)) > 1.9) return shade(1, SKY);
      } else if (dist < DISC) return shade(1, SKY);

      // Закат: луна ВЫХОДИТ ИЗ МОРЯ навстречу солнцу. Ход подобран так, чтобы
      // к 2-й секунде диск показался ровно наполовину (центр на кромке воды),
      // дальше она всплывает всё медленнее — как и солнце.
      if (g.sunset) {
        const pm = 1 - Math.exp(-age / 2.9); // 0.5 на второй секунде
        const mr = seaTop + 2.2 - pm * 4.4; // центр: из-под воды к горизонту
        const md = Math.hypot((c - g.mx) * 0.55, r - mr);
        const cut = Math.hypot((c - (g.mx + 1.3)) * 0.55, r - (mr - 0.4));
        if (md < 2.1 && cut > 1.8) return shade(1, SKY); // ниже воды её скроет само море
      }
      const cloud = cloudAt(g, c, r, t);
      const glow = Math.max(0, 0.9 - dist * 0.11);
      // Небо светлеет по мере подъёма светила: у воды почти ночь, высоко —
      // день, и тогда небо ЯРЧЕ воды (раньше оно всегда оставалось тёмным).
      // Закат догорает В ЧЁРНОЕ: как только солнце уходит под воду, заливка
      // неба и его ореол гаснут до нуля — остаются одни звёзды на пустоте,
      // а не вечные сумерки. `lit` уже отсчитывает это угасание (тем же
      // множителем гаснет дорожка на воде), так что вторых часов не заводим.
      const dusk = g.sunset ? lit : 1;
      const base = g.night ? 0.02 : (0.05 + 0.58 * Math.pow(day, 0.75) + 0.04 * (1 - r / g.rows)) * dusk;
      const h = hash2(c, r);
      const star = h > 0.972 ? stars * (0.35 + 0.55 * (0.5 + 0.5 * Math.sin(t * 3 + h * 40))) : 0;
      let sky = Math.max(base, glow * (g.night ? 0.5 : 0.85) * dusk, star);

      if (cloud > 0.04) {
        // подсветка сбоку: край, обращённый к солнцу, горит ярче
        const toSun = Math.max(0, 1 - Math.abs(c - g.sx) / (g.cols * 0.5));
        const edge = Math.pow(cloud, 0.6); // кромка плотнее середины
        sky = Math.max(sky, base + edge * (0.14 + 0.5 * toSun * (0.25 + 0.75 * day)));
      }
      return shadeDither(sky, SKY, c, r);
    },
  },
  {
    // многослойные волны: гряды сгущаются к горизонту, ближние катятся быстрее
    kicker: "Поднимаю волну…",
    init(g) {
      g.phase = rf(0, 6.28);
      g.dir = Math.random() < 0.5 ? 1 : -1;
      g.layers = 6 + (Math.random() < 0.5 ? 0 : 1);
    },
    cell(c, r, t, g) {
      const horizon = Math.round(g.rows * 0.2);
      if (r < horizon) return " ";
      const sea = g.rows - horizon;
      const build = ease(Math.min(t / 0.9, 1));
      for (let i = g.layers - 1; i >= 0; i--) {
        const f = (i + 0.5) / g.layers;
        const base = horizon + Math.pow(f, 1.7) * sea;
        const amp = (0.35 + 1.35 * f) * build;
        const freq = 0.05 + 0.018 * f;
        const speed = (0.6 + 1.1 * f) * g.dir;
        const ph = g.phase + i * 1.3;
        const crest = trochoid(freq * c + speed * t + ph, 1.6);
        const row = base - amp * crest;
        if (Math.abs(r - row) < 0.5) {
          const slope = base - amp * trochoid(freq * (c + 1) + speed * t + ph, 1.6) - row;
          if (f > 0.6 && crest > 0.55 && hash2(c, r) > 0.62) return "#";
          if (slope < -0.4) return "/";
          if (slope > 0.4) return "\\";
          return f > 0.45 ? "~" : "-";
        }
      }
      return hash2(c, r) > 0.93 ? "." : " ";
    },
  },
];

// Ширина блока переливания. ⚠️ Посимвольно нельзя: поле сцены до 320 × 48 — это 15 тысяч
// узлов, пересобираемых 30 раз в секунду. Блок из восьми знаков даёт ~2 тысячи узлов, а волна
// на таком кегле (5.5-8.5 px) всё равно читается сплошной: соседние блоки отличаются фазой на
// сотые доли оборота.
const HALO_BLOCK = 8;

/** Сетка узлов сцены. Строится ОДИН раз — как у знака: пересборка на каждом кадре
 *  перезапускала бы анимацию ореола, и вместо волны вышло бы мерцание. */
function haloGrid(pre, g) {
  pre.textContent = "";
  // Поле и координаты блоков — для живого перелива (`ui/halo.js`). ⓘ Заглушка DOM в тестах
  // `dataset` не знает — потому через проверку, а не напрямую.
  if (pre.dataset) {
    pre.dataset.cols = g.cols;
    pre.dataset.rows = g.rows;
  }
  const rows = [];
  for (let r = 0; r < g.rows; r++) {
    const line = document.createElement("span");
    line.className = "fx-row";
    const cells = [];
    for (let c = 0; c < g.cols; c += HALO_BLOCK) {
      const cell = document.createElement("i");
      // Фаза — угол середины блока вокруг центра сцены. Формула ОДНА со знаком (`mark.js`).
      cell.style.setProperty("--p", phase(c + HALO_BLOCK / 2, r, g));
      if (cell.dataset) {
        cell.dataset.c = c + HALO_BLOCK / 2;
        cell.dataset.r = r;
      }
      line.append(cell);
      cells.push(cell);
    }
    pre.append(line);
    rows.push(cells);
  }
  return rows;
}

/**
 * Проигрывает заставку в <pre>. Возвращает выбранную подпись («Разрезаю туман…»).
 * @param {HTMLElement} pre куда рисовать
 * @param {{onReveal:Function, disabled:boolean}} opts onReveal — когда показывать заголовок темы
 */
export function playTopicIntro(pre, { onReveal, disabled = false } = {}) {
  if (!pre || disabled) {
    onReveal?.();
    return "";
  }
  const scene = SCENES[Math.floor(Math.random() * SCENES.length)];

  // Сетку считаем по реальному размеру символа: чем мельче шрифт, тем больше
  // клеток — а значит выше «разрешение» сцены и разборчивее силуэт.
  const style = getComputedStyle(pre);
  const probe = document.createElement("span");
  probe.textContent = "M".repeat(20);
  probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font-family:${style.fontFamily};font-size:${style.fontSize};line-height:${style.lineHeight}`;
  pre.append(probe);
  const box = probe.getBoundingClientRect();
  const cellWidth = box.width / 20 || 6;
  const cellHeight = box.height || 11;
  probe.remove();

  const areaWidth = pre.clientWidth || pre.parentElement?.clientWidth || innerWidth;
  const areaHeight = pre.parentElement?.clientHeight || 220;
  const g = {
    cols: Math.max(60, Math.min(320, Math.floor(areaWidth / cellWidth))),
    rows: Math.max(12, Math.min(48, Math.floor(areaHeight / cellHeight))),
  };
  scene.init?.(g);

  const grid = haloGrid(pre, g);
  // Живой перелив из конфига темы (`theme.halo`) — на время сцены; иначе CSS-ореол, как раньше.
  let haloRun = null;
  const opts = sceneOptions(); // общие настройки перелива + `theme.halo.scene` поверх
  if (haloLive(opts)) {
    pre.classList.add("halo-live");
    // ⚠️ Цель «символы» здесь невозможна: сцена сама переписывает текст узлов каждый кадр, и
    // подмена символов дралась бы с ней. Фиксированный glyph → ореол; из случайного пула glyph
    // просто выпадает — остальные цели (ореол, заливка) сцене не мешают.
    const pool = (opts.targets || TARGETS.map((t) => t.id)).filter((t) => t !== "glyph");
    let scene = { ...opts, target: opts.target === "glyph" ? "halo" : opts.target, targets: pool };
    // Без теней — светлая тема (владелец, 16.09) или `scene.shadow: false` в конфиге (18.09:
    // «только цветом самих символов»): ореол выпадает, остаётся заливка; нет ничего — сцена
    // играет одной сборкой из шума.
    const light = document.documentElement.getAttribute("data-theme") === "light";
    if (light || opts.shadow === false) scene = withoutShadow(scene);
    haloRun = scene ? driveHalo(pre, scene) : null;
  }
  const started = performance.now();
  const ASSEMBLE = 0.75; // сборка из шума
  const MIN_SHOW = 1.8; // сколько сцена держится минимум, даже если тема пришла мгновенно
  const SCATTER = 0.9; // рассыпание в заголовок
  let running = true;
  let wantFinish = false; // тема пришла — рассыпаемся, как только отработает MIN_SHOW
  let finishAt = 0; // момент начала рассыпания (0 — ещё играем)
  let last = 0;

  function frame(now) {
    if (!running) return;
    if (now - last < 32) return requestAnimationFrame(frame); // ~30 кадров/с достаточно
    last = now;
    const t = (now - started) / 1000;
    const assemble = Math.min(t / ASSEMBLE, 1);
    // держим сцену минимум MIN_SHOW, даже если тема пришла мгновенно
    if (wantFinish && !finishAt && t >= MIN_SHOW) finishAt = now;
    const scatter = finishAt ? Math.min(1, (now - finishAt) / 1000 / SCATTER) : 0;
    const noise = Math.floor(t * 16);

    for (let r = 0; r < g.rows; r++) {
      let line = "";
      for (let c = 0; c < g.cols; c++) {
        let ch = scene.cell(c, r, t, g);
        const ord = hash2(c, r);
        if (scatter > 1 - ord) ch = " ";
        else if (assemble < ord) ch = ord - assemble < 0.18 ? SCRAMBLE[(Math.floor(ord * 100) + noise) % SCRAMBLE.length] : " ";
        line += ch;
      }
      // Меняем ТЕКСТ готовых узлов, а не собираем разметку заново: узлы держат фазу
      // переливания, и пересоздание сбрасывало бы её каждый кадр.
      const cells = grid[r];
      for (let k = 0; k < cells.length; k++) {
        cells[k].textContent = line.slice(k * HALO_BLOCK, (k + 1) * HALO_BLOCK);
      }
    }

    if (finishAt && scatter >= 1) {
      running = false;
      haloRun?.stop();
      pre.textContent = "";
      pre.parentElement?.setAttribute("hidden", "");
      onReveal?.();
      return;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    kicker: g.kicker || scene.kicker,
    /** Тема пришла — доигрываем и рассыпаемся. Момент выбирает сам цикл кадров:
     *  два разных источника времени (таймер и кадры) уже расходились. */
    finish(reveal) {
      onReveal = reveal || onReveal;
      wantFinish = true;
    },
    stop() {
      running = false;
      haloRun?.stop();
      pre.textContent = "";
    },
  };
}
