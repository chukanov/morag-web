// Стенд переливов: плитка на каждый узор из `ui/halo.js`, общие палитра/цель/период.
import { renderMark, renderText, startIdle, layout } from "../js/ui/mark.js";
import * as boxfont from "../js/ui/boxfont.js";
import { DEFAULT_WORDMARK } from "../js/ui/brand.js";
import { playTopicIntro } from "../js/ui/ascii.js";
import { PALETTES, PATTERNS, TARGETS, drive } from "../js/ui/halo.js";

// Знак — тот же, что в шапке: бренд корпуса (рисунок и слово) от сервера входа — ручка открыта
// без сессии; нет бренда — слово платформы. Раскладка та же (`layout`), чтобы вертушка
// крутилась вокруг всего знака, как на сайте.
const SCALE = 11 / 4;
let BRAND = {};
try {
  BRAND = (await (await fetch("/api/auth/state")).json()).brand || {};
} catch {
  /* стенд без сервера — слово платформы */
}
const ART = BRAND.mark && Array.isArray(BRAND.mark.lines) ? BRAND.mark : null;
const WORD = boxfont.render(BRAND.wordmark || DEFAULT_WORDMARK);
const GEO = layout(ART, WORD, ART ? SCALE : 1);
const FIELD = GEO.field;

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v);
  }
  n.append(...kids.filter(Boolean));
  return n;
};

const opts = { palette: "aurora", target: "halo", period: 3 };
let hoverOnly = false;
let withScene = false;
const tiles = [];

function fillSelect(sel, items, current) {
  sel.replaceChildren(...items.map((x) => el("option", { value: x.id, text: `${x.name} — ${x.note || ""}`.replace(/ — $/, "") })));
  sel.value = current;
}

function makeTile(pat) {
  const mark = el("span", { class: "logo-mark" });
  const word = el("span", { class: "lab-word" });
  const logo = el("div", { class: "lab-logo", "data-cols": FIELD.cols, "data-rows": FIELD.rows }, mark, word);
  const acts = renderMark(mark, FIELD, ART);
  mark.hidden = !ART;
  renderText(word, WORD, FIELD, { ox: GEO.ox, oy: GEO.oy, sx: GEO.sx, sy: GEO.sy });
  startIdle(acts);
  const scene = el("pre", { class: "lab-scene", hidden: "" });
  const cfg = el("p", { class: "cfg" });
  const tile = el("div", { class: `tile${pat.id === "spin" ? " now" : ""}` },
    el("h3", { text: pat.name }, pat.id === "spin" ? el("small", { text: "как сейчас" }) : null),
    el("p", { class: "note", text: pat.note }),
    logo, scene, cfg);
  if (pat.random) {
    const again = el("button", { class: "again", type: "button", text: "ещё раз" });
    again.addEventListener("click", () => t.restart());
    tile.append(again);
  }
  const t = { pat, tile, logo, scene, cfg, run: null, sceneRun: null, sceneDrive: null,
    start() {
      this.stop();
      this.run = drive(this.logo, { pattern: pat.id, palette: opts.palette, target: opts.target, period: opts.period });
      if (withScene && this.scene.dataset.cols) {
        this.sceneDrive = drive(this.scene, { pattern: this.run.pattern, palette: this.run.palette, target: opts.target === "glyph" ? "halo" : opts.target, period: opts.period });
      }
      const chosen = { pattern: this.run.pattern, palette: this.run.palette };
      this.cfg.textContent = `theme.halo: {pattern: ${pat.random ? "random" : chosen.pattern}, palette: ${opts.palette}, target: ${opts.target}, period: ${opts.period}}`
        + (pat.random ? `   ← сейчас выпало ${chosen.pattern} / ${chosen.palette}` : "");
    },
    stop() {
      this.run?.stop(); this.run = null;
      this.sceneDrive?.stop(); this.sceneDrive = null;
    },
    restart() { this.start(); },
    showScene(on) {
      if (on && !this.sceneRun) {
        this.scene.hidden = false;
        this.sceneRun = playTopicIntro(this.scene, {});
      } else if (!on && this.sceneRun) {
        this.sceneRun.stop(); this.sceneRun = null;
        this.scene.hidden = true;
      }
    },
  };
  logo.addEventListener("mouseenter", () => { if (hoverOnly || pat.random) t.start(); });
  logo.addEventListener("mouseleave", () => { if (hoverOnly) t.stop(); });
  return t;
}

function applyAll() {
  for (const t of tiles) {
    t.showScene(withScene);
    if (hoverOnly) t.stop(); else t.start();
  }
}

fillSelect($("#palette"), PALETTES, opts.palette);
fillSelect($("#target"), TARGETS, opts.target);
$("#palette").addEventListener("change", (e) => { opts.palette = e.target.value; applyAll(); });
$("#target").addEventListener("change", (e) => { opts.target = e.target.value; applyAll(); });
$("#period").addEventListener("input", (e) => {
  opts.period = Number(e.target.value);
  $("#period-v").textContent = `${opts.period} с`;
  applyAll();
});
$("#scene").addEventListener("change", (e) => { withScene = e.target.checked; applyAll(); });
$("#hover").addEventListener("change", (e) => { hoverOnly = e.target.checked; applyAll(); });
$("#reshuffle").addEventListener("click", () => tiles.filter((t) => t.pat.random).forEach((t) => t.restart()));

const grid = $("#grid");
for (const pat of PATTERNS) {
  const t = makeTile(pat);
  tiles.push(t);
  grid.append(t.tile);
}
applyAll();
