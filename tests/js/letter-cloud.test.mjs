// Облако в форме буквы: раскладка слов по маске — чистая геометрия без канваса.
//     node tests/js/letter-cloud.test.mjs
//
// Проверяется то, из-за чего облако перестало бы быть буквой: слово целиком внутри маски,
// слова не наезжают друг на друга, крупный вес — крупный кегль, углы только из набора, тот же
// сид — та же картинка. Маска здесь — кольцо (как «О»): у неё есть и край снаружи, и дыра.
import assert from "node:assert/strict";
import { ANGLES, hashSeed, layoutWords, maskFromPredicate, seeded } from "../../web/js/records/letter-cloud.js";

let failures = 0;
const check = (name, fn) => {
  try {
    fn();
    console.log("  ✓", name);
  } catch (error) {
    failures += 1;
    console.log("  ✗", name, "\n     ", error.message.split("\n")[0]);
  }
};

const W = 400;
const H = 400;
const CELL = 3;
// кольцо: центр (200,200), внешний радиус 195, внутренний 78 — площадь ≈ 100k px²
const ring = maskFromPredicate(W, H, CELL, (x, y) => {
  const d = Math.hypot(x - 200, y - 200);
  return d <= 195 && d >= 78;
});
const measure = (text, size) => ({ w: text.length * size * 0.55, h: size * 1.12 });
const words = Array.from({ length: 36 }, (_, i) => ({ value: `слово${i}`, weight: (36 - i) / 36 }));

/** Клетки, накрытые повёрнутым прямоугольником слова (та же геометрия, что в раскладке, но
 *  посчитана здесь заново — иначе тест повторял бы проверяемый код). */
function cellsOf(p, mask) {
  const { w, h } = measure(p.value, p.size);
  const rad = (p.angle * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const out = new Set();
  for (let r = 0; r < mask.rows; r++) {
    for (let c = 0; c < mask.cols; c++) {
      const dx = (c + 0.5) * mask.cell - p.x;
      const dy = (r + 0.5) * mask.cell - p.y;
      const lx = dx * cos + dy * sin;
      const ly = -dx * sin + dy * cos;
      if (Math.abs(lx) <= w / 2 && Math.abs(ly) <= h / 2) out.add(r * mask.cols + c);
    }
  }
  return out;
}

console.log("облако в форме буквы:");

const { placed, dropped } = layoutWords(ring, words, measure, { seed: 7 });

check("все слова разложились в кольцо", () => {
  assert.equal(dropped.length, 0, `не влезли: ${dropped.join(", ")}`);
  assert.equal(placed.length, words.length);
});

check("каждое слово целиком внутри маски", () => {
  for (const p of placed) {
    for (const i of cellsOf(p, ring)) assert.ok(ring.grid[i], `«${p.value}» вылезло за букву`);
  }
});

check("слова не наезжают друг на друга", () => {
  const taken = new Map();
  for (const p of placed) {
    for (const i of cellsOf(p, ring)) {
      assert.ok(!taken.has(i), `«${p.value}» легло на «${taken.get(i)}»`);
      taken.set(i, p.value);
    }
  }
});

check("вес крупнее — кегль не меньше", () => {
  const bySize = [...placed].sort((a, b) => b.weight - a.weight);
  for (let i = 1; i < bySize.length; i++) {
    assert.ok(bySize[i].size <= bySize[i - 1].size + 1e-9, `${bySize[i].value} крупнее ${bySize[i - 1].value}`);
  }
  assert.ok(bySize[0].size > bySize.at(-1).size, "кегль вообще не меняется");
});

check("углы только из набора, и не все горизонтальные", () => {
  for (const p of placed) assert.ok(ANGLES.includes(p.angle), `угол ${p.angle}`);
  assert.ok(placed.some((p) => p.angle !== 0), "ни одного вертикального или наклонного слова");
});

check("тот же сид — та же картинка, другой — другая", () => {
  const again = layoutWords(ring, words, measure, { seed: 7 });
  assert.deepEqual(again.placed, placed);
  const other = layoutWords(ring, words, measure, { seed: 8 });
  assert.notDeepEqual(other.placed.map((p) => [p.x, p.y]), placed.map((p) => [p.x, p.y]));
});

check("не влезающее слово не теряется молча — оно в dropped", () => {
  const tiny = maskFromPredicate(30, 30, CELL, () => true);
  const out = layoutWords(tiny, [{ value: "оченьдлинноеслововообще", weight: 1 }], measure, { seed: 1 });
  assert.deepEqual(out.dropped, ["оченьдлинноеслововообще"]);
});

check("сид от состава слов стабилен и не зависит от порядка вызова", () => {
  assert.equal(hashSeed(["а", "б"]), hashSeed(["а", "б"]));
  assert.notEqual(hashSeed(["а", "б"]), hashSeed(["а", "в"]));
  const r = seeded(3);
  assert.ok(r() >= 0 && r() < 1);
});

console.log(failures ? `\nпровалов: ${failures}` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
