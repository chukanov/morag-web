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
const HUES = 8;               // больше восьми оттенков глазом не различаются — дальше светлота
// С какого тона начинается развёртка голосов. Синий — решение владельца (24.09): первый голос
// виден чаще всех и задаёт впечатление от сцены. Значение — тон акцентного синего сайта
// (#3F7FB5 ≈ oklch 246°), чтобы окно читалось продолжением сайта.
const BLUE = 246;

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

  /** Цвет голоса: ЯРКИЙ, а не приглушённый оттенок акцента.
   *
   * ⚠️ Сначала цвета выводились из акцента поворотом тона — и получались блёклыми: у брендового
   * цвета низкая насыщенность, она и наследовалась. Здесь картинка, а не текст: голоса должны
   * различаться с одного взгляда, поэтому насыщенность задаётся прямо, а от темы берётся только
   * светлота (на светлом фоне те же цвета надо темнее, иначе они выцветают).
   */
  function colour(idx) {
    const light = document.documentElement.getAttribute("data-theme") === "light";
    const L = light ? 0.58 : 0.74;
    const hue = (idx * 360) / HUES + BLUE;        // старт от синего — см. BLUE
    const dim = idx >= HUES ? 1 - 0.16 * Math.floor(idx / HUES) : 1;
    return `oklch(${(L * dim).toFixed(3)} 0.19 ${hue % 360})`;
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
      for (const [a, b, idx] of spans) {
        const x0 = (a / audioSec) * W;
        const x1 = (b / audioSec) * W;
        if (x0 > W * eased) break;
        ctx.fillStyle = colour(idx);
        ctx.fillRect(x0, ribbonY, Math.max(1, Math.min(x1, W * eased) - x0), ribbonH);
      }
      if (done < 1) dirty = true;
    }

    // --- слой 3: живой слой — окна, уже распознанные пассом-2
    if (n && audioSec > 0) {
      for (const [a, b, idx] of live) {
        const i0 = Math.max(0, Math.floor((a / audioSec) * n));
        const i1 = Math.min(n, Math.ceil((b / audioSec) * n));
        ctx.fillStyle = colour(idx);
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
         el("i", { style: `background:${colour(idx)}` }), name)));
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
