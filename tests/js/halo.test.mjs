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
