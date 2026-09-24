// Сцена 1: волна звука, раскрашенная диаризацией, и бегущая метка распознавания.
//
// Почему canvas, а не DOM: 1200 столбиков, перекрашиваемых по ходу работы, — ровно тот случай,
// про который сказано, что DOM не тянет (в символьной сцене сайта узлом сделан блок из восьми
// знаков именно поэтому). Здесь три слоя, и каждый перерисовывается только когда испачкан:
//   1) пики — один раз, когда пришла огибающая;
//   2) лента голосов — один раз на событие диаризации, с проявлением слева направо;
//   3) живой слой — на каждый кусок пасса-2 закрашивается ТОЛЬКО его окно.
//
// ⚠️ Цвета берём из токенов страницы (`--accent`, `--sonar`, `--ink-faint`) и разводим ПОВОРОТОМ
// тона, а не своей палитрой: окно должно выглядеть продолжением сайта, а не чужой утилитой.
// ⚠️ Цвет — не единственный канал: рядом легенда с подписями голосов. Иначе сцена бесполезна
// тому, кто цвета различает плохо.

import { el, reduced } from "./dom.js";

const REVEAL_MS = 600;        // проявление ленты голосов: одно движение, не мигание

/** Цветовой круг: 12 тонов по четыре кольца — от светлого к глубокому.
 *
 * Цвета сняты ПИКСЕЛЯМИ с круга, который дал владелец (24.09), а не сочинены формулой:
 * выведенные поворотом тона оттенки смотрелись бедно и плоско. Это ЕДИНСТВЕННОЕ место в окне со
 * своими цветами — здесь картинка, а не интерфейс; всё остальное по-прежнему токенами сайта.
 */
const WHEEL = [
  ["#BDB0D7", "#8671B2", "#622F92", "#4C1D73"],   //  0 фиолетовый
  ["#9197C7", "#6D6CB1", "#2F4298", "#1F2E7A"],   //  1 сине-фиолетовый
  ["#A9BCDE", "#5F8AC3", "#1F63A2", "#044A87"],   //  2 синий
  ["#B2D8DE", "#5FC0C8", "#03AAB1", "#01838F"],   //  3 сине-зелёный
  ["#BEDDD1", "#67C3A3", "#04A663", "#018A55"],   //  4 зелёный
  ["#C2E2C6", "#AFD198", "#74BB61", "#589948"],   //  5 жёлто-зелёный
  ["#F5F1CA", "#F5F58C", "#F1F02B", "#C5BC29"],   //  6 жёлтый
  ["#FDEEC8", "#FCD388", "#F9AA1B", "#C48A10"],   //  7 жёлто-оранжевый
  ["#F9DFC9", "#F5C57A", "#F59025", "#C36E17"],   //  8 оранжевый
  ["#F9D1C3", "#F49677", "#ED4B3E", "#BD372F"],   //  9 красно-оранжевый
  ["#F8C6C1", "#F48F76", "#ED2D31", "#BA1820"],   // 10 красный
  ["#E2C0D4", "#D67EB3", "#A72290", "#87126F"],   // 11 красно-фиолетовый
];
// Голоса берут тоны не подряд, а ЧЕРЕЗ СЕМЬ (взаимно просто с 12): соседние по порядку
// голоса оказываются на противоположных сторонах круга и не путаются. Начало — синий (владелец).
const FIRST = 2;
const STEP = 7;

export function wave(root) {
  const canvas = el("canvas", { class: "wv-c" });
  const legend = el("div", { class: "wv-legend" });
  root.append(canvas, legend);

  let peaks = null;           // Uint8Array огибающей
  let audioSec = 0;
  let speakers = [];          // метки голосов в порядке появления
  let spans = [];             // [[from, to, idx], …]
  let revealFrom = 0;         // когда началось проявление ленты
  let live = [];              // закрашенные окна: [[from, to, idx], …]
  let cursor = null;          // бегущая метка: секунда
  let dirty = true;
  let raf = null;

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  /** Разбор `#rrggbb` и смешение двух цветов — всё, что нужно для ступеней. */
  function rgb(hex) {
    const h = String(hex).trim().replace("#", "");
    const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
    const n = parseInt(full, 16);
    return Number.isFinite(n) && full.length === 6
      ? [(n >> 16) & 255, (n >> 8) & 255, n & 255] : null;
  }

  function mix(a, b, t) {
    const x = rgb(a);
    const y = rgb(b);
    if (!x || !y) return a;
    const v = x.map((c, i) => Math.round(c + (y[i] - c) * t));
    return `rgb(${v[0]} ${v[1]} ${v[2]})`;
  }

  /** Цвет голоса — ТОН с круга, один на весь голос.
   *
   * ⚠️ Ступеней по громкости больше НЕТ (владелец, 24.09: «цвета норм, не надо грубой
   * лесенкой градиента»): дробление одного голоса на оттенки читалось как рябь, а громкость
   * и так видна — высотой столбиков. Объём даёт ПЛАВНОЕ затенение к низу, а не смена цвета.
   * На светлой теме берётся кольцо глубже: чистый тон на белом выцветает.
   */
  function colour(idx) {
    const light = document.documentElement.getAttribute("data-theme") === "light";
    const lap = Math.min(1, Math.floor(idx / WHEEL.length));
    const tone = WHEEL[(FIRST + idx * STEP) % WHEEL.length];
    return tone[Math.min(3, (light ? 3 : 2) - (light ? lap : -lap))];
  }

  /** Вертикальный перелив от тона к его тени: плоская заливка смотрится бедно. */
  function shaded(ctx, idx, y0, y1) {
    const base = colour(idx);
    const g = ctx.createLinearGradient(0, y0, 0, y1);
    g.addColorStop(0, mix(base, "#FFFFFF", 0.14));
    g.addColorStop(0.45, base);
    g.addColorStop(1, mix(base, "#000000", 0.45));
    return g;
  }

  function fit() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(320, root.clientWidth);
    const h = 132;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    dirty = true;
    draw();
  }

  function draw() {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { width: W, height: H } = canvas;
    const waveH = Math.round(H * 0.74);
    const ribbonY = waveH + Math.round(H * 0.06);
    const ribbonH = H - ribbonY;
    ctx.clearRect(0, 0, W, H);

    // --- слой 1: пики. Нет огибающей — ровная линия, и это честно: шкала не построилась.
    const n = peaks ? peaks.length : 0;
    const bw = n ? W / n : W;
    ctx.fillStyle = css("--ink-faint") || "#66788a";
    ctx.globalAlpha = 0.38;
    if (!n) {
      ctx.fillRect(0, waveH / 2 - 1, W, 2);
    } else {
      for (let i = 0; i < n; i++) {
        const v = (peaks[i] / 255) * waveH;
        ctx.fillRect(i * bw, (waveH - v) / 2, Math.max(1, bw - 0.6), Math.max(1, v));
      }
    }
    ctx.globalAlpha = 1;

    // --- слой 2: лента голосов, проявляется слева направо
    if (spans.length && audioSec > 0) {
      const done = reduced() || !revealFrom
        ? 1
        : Math.min(1, (performance.now() - revealFrom) / REVEAL_MS);
      const eased = 1 - (1 - done) ** 3;
      // ⚠️ Между репликами — белая граница в два пикселя (владелец, 24.09): без неё соседние
      // куски одного цвета сливаются в одно пятно и по ленте не видно, где менялись реплики.
      const seam = Math.max(1, Math.round(2 * (canvas.width / (canvas.clientWidth || canvas.width))));
      for (const [a, b, idx] of spans) {
        const x0 = (a / audioSec) * W;
        const x1 = Math.min((b / audioSec) * W, W * eased);
        if (x0 > W * eased) break;
        ctx.fillStyle = shaded(ctx, idx, ribbonY, ribbonY + ribbonH);
        ctx.fillRect(x0, ribbonY, Math.max(1, x1 - x0), ribbonH);
        if (x0 > 0.5) {
          ctx.fillStyle = "rgba(255,255,255,.92)";
          ctx.fillRect(x0 - seam / 2, ribbonY, seam, ribbonH);
        }
      }
      if (done < 1) dirty = true;
    }

    // --- слой 3: живой слой — окна, уже распознанные пассом-2
    if (n && audioSec > 0) {
      for (const [a, b, idx] of live) {
        const i0 = Math.max(0, Math.floor((a / audioSec) * n));
        const i1 = Math.min(n, Math.ceil((b / audioSec) * n));
        ctx.fillStyle = shaded(ctx, idx, 0, waveH);
        for (let i = i0; i < i1; i++) {
          const v = (peaks[i] / 255) * waveH;
          ctx.fillRect(i * bw, (waveH - v) / 2, Math.max(1, bw - 0.6), Math.max(1, v));
        }
      }
      if (cursor != null) {
        const x = (cursor / audioSec) * W;
        ctx.fillStyle = css("--accent") || "#E7A857";
        ctx.fillRect(Math.min(W - 2, x), 0, 2, waveH);
      }
    }
  }

  function tick() {
    raf = null;
    if (!dirty) return;
    dirty = false;
    draw();
    if (dirty) raf = requestAnimationFrame(tick);   // проявление ленты продолжается
  }

  function paint() {
    dirty = true;
    if (raf === null) raf = requestAnimationFrame(tick);
  }

  function showLegend() {
    legend.replaceChildren(...speakers.map((name, idx) =>
      el("span", { class: "wv-who" },
         el("i", { style: `background:${colour(idx, 2)}` }), name)));
  }

  return {
    /** Одно событие меняет состояние сцены; рисование — отдельно и по кадрам. */
    apply(e) {
      if (e.t === "job.meta") { audioSec = e.audio_sec || 0; paint(); return; }
      if (e.t === "wave.peaks") {
        peaks = Uint8Array.from(atob(e.b64), (c) => c.charCodeAt(0));
        paint();
        return;
      }
      if (e.t === "diar.spans") {
        speakers = e.speakers || [];
        spans = e.spans || [];
        revealFrom = performance.now();
        showLegend();
        paint();
        return;
      }
      if (e.t === "chunk.start") {
        const idx = Math.max(0, speakers.indexOf(e.spk));
        live.push([e.from, e.to, idx < 0 ? 0 : idx]);
        cursor = e.to;
        paint();
      }
    },
    reset() {
      peaks = null; audioSec = 0; speakers = []; spans = []; live = []; cursor = null;
      revealFrom = 0;
      legend.replaceChildren();
      paint();
    },
    fit,
    /** Для тестов и стенда: что сцена считает своим состоянием. */
    state: () => ({ audioSec, speakers, spans: spans.length, live: live.length, cursor }),
  };
}
