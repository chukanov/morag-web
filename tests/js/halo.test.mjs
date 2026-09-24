import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const { pickTarget, TARGETS, PALETTES } = await import(join(repo, "web/js/ui/halo.js"));

// Цель «random» — из пула; повтор в пуле — вес (владелец, 15.09: «символы» чаще остальных).
const ids = new Set(TARGETS.map((t) => t.id));
assert.equal(pickTarget("glyph"), "glyph", "не random — как назвали");
const counts = {};
for (let i = 0; i < 3000; i += 1) {
  const t = pickTarget("random", ["glyph", "glyph", "glyph", "halo"]);
  assert.ok(ids.has(t), `неизвестная цель ${t}`);
  counts[t] = (counts[t] || 0) + 1;
}
assert.ok(counts.glyph > counts.halo * 2, `glyph трижды в пуле — обязан выпадать заметно чаще: ${JSON.stringify(counts)}`);
assert.ok(!counts.ink, "чего нет в пуле — не выпадает");
for (const gone of ["lift", "both"]) assert.ok(!TARGETS.some((t) => t.id === gone), `цель ${gone} снята целиком (владелец, 15-16.09)`);
for (const gone of ["ember", "rainbow"]) assert.ok(!PALETTES.some((p) => p.id === gone), `палитра ${gone} снята целиком (владелец, 16.09)`);
assert.equal(pickTarget("random", ["lift"]), "halo", "снятая цель в конфиге — как неизвестная: ореол");
// Сломанный конфиг не гасит перелив: неизвестные цели отсеиваются, пустой пул — ореол.
assert.equal(pickTarget("random", ["nope"]), "halo");
assert.equal(pickTarget("random", []) !== undefined, true);
for (let i = 0; i < 200; i += 1) assert.ok(ids.has(pickTarget("random")), "без пула — любая известная");
console.log("halo: всё зелёное");

// --- светлая тема: без теней (владелец, 16.09) ------------------------------------------
{
  const { withoutShadow } = await import(join(repo, "web/js/ui/halo.js"));
  assert.equal(withoutShadow(null), null);
  assert.equal(withoutShadow({ target: "halo" }), null, "ореол — это тень, играть нечем");
  const r = withoutShadow({ target: "random", targets: ["glyph", "glyph", "halo"], period: 1.5 });
  assert.deepEqual(r.targets, ["glyph", "glyph"], "ореол выпал из пула, веса остальных целы");
  assert.equal(r.shadow, false);
  assert.equal(r.period, 1.5, "прочие настройки не тронуты");
  assert.equal(withoutShadow({ target: "random", targets: ["halo", "halo"] }), null, "пул из одних теней пуст");
  assert.equal(withoutShadow({ target: "glyph" }).shadow, false);
  assert.equal(withoutShadow({ target: "random" }).targets.includes("halo"), false, "без пула — все цели, кроме ореола");
  console.log("halo без теней: ок");
}

// --- заставка темы со своими настройками: theme.halo.scene (владелец, 18.09) ---------------
{
  const props = {};
  globalThis.document = {
    documentElement: { style: { setProperty: (k, v) => { props[k] = v; } }, getAttribute: () => null },
    querySelector: () => null,
  };
  globalThis.requestAnimationFrame = () => 1;
  globalThis.cancelAnimationFrame = () => {};
  globalThis.performance = globalThis.performance ?? { now: () => Date.now() };
  const { applyHalo, haloOptions, sceneOptions, live, drive, withoutShadow } = await import(join(repo, "web/js/ui/halo.js"));

  applyHalo({ pattern: "random", palette: "aurora", target: "random", targets: ["glyph", "glyph", "halo"], period: 1.5 });
  assert.deepEqual(sceneOptions(), haloOptions(), "без scene заставка живёт общими настройками");

  applyHalo({ pattern: "random", palette: "aurora", target: "random", targets: ["glyph", "glyph", "halo"], period: 1.5,
              scene: { target: "ink", shadow: false } });
  const mark = haloOptions();
  assert.equal(mark.target, "random", "знак — прежний: scene его не трогает");
  assert.equal(mark.shadow, undefined);
  const scene = sceneOptions();
  assert.equal(scene.target, "ink", "заставка — цвет самих символов");
  assert.equal(scene.shadow, false, "и без тени");
  assert.equal(scene.period, 1.5, "остальное — из общих");
  assert.equal(scene.pattern, "random");
  assert.ok(live(scene), "заставке нужен живой перелив");
  // 24.09: заставке — без перелива (`none`) и иногда «матрица»; доля показов прижата к 0..1
  applyHalo({ target: "random", scene: { target: "none", shadow: false, matrix: 0.3 } });
  assert.equal(sceneOptions().target, "none");
  assert.equal(sceneOptions().matrix, 0.3);
  assert.equal(haloOptions().matrix, undefined, "у знака матрицы нет");
  applyHalo({ target: "random", scene: { matrix: 5 } });
  assert.equal(sceneOptions().matrix, 1);
  applyHalo({ target: "random", scene: { matrix: "a" } });
  assert.equal(sceneOptions().matrix, undefined);
  applyHalo({ pattern: "random", palette: "aurora", target: "random", targets: ["glyph", "glyph", "halo"], period: 1.5,
              scene: { target: "ink", shadow: false } });
  const noShadow = withoutShadow(scene);
  assert.equal(noShadow.target, "ink", "заливка без тени остаётся заливкой");
  assert.equal(noShadow.shadow, false);

  // drive без тени вешает класс, по которому CSS гасит и статичное свечение контейнера
  const classes = new Set();
  const host = { dataset: { cols: 2, rows: 1 }, querySelectorAll: () => [], classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c) } };
  const run = drive(host, { ...noShadow, pattern: "spin" });
  assert.ok(classes.has("halo-noshadow"), "класс без тени поставлен");
  run.stop();
  assert.ok(!classes.has("halo-noshadow"), "и снят на остановке");
  drive(host, { pattern: "spin", target: "halo" }).stop();
  assert.ok(!classes.has("halo-noshadow"), "с тенью класса нет");

  // scene может и цель случайную оставить, но со своим пулом
  applyHalo({ pattern: "spin", target: "halo", scene: { target: "random", targets: ["ink", "ink"] } });
  assert.deepEqual(sceneOptions().targets, ["ink", "ink"]);
  assert.equal(haloOptions().targets, undefined, "общий пул не появился из ниоткуда");
  console.log("halo scene: ок");
}
