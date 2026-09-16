// Склонение при числе — правило неочевидное ровно на 11-14 («11 выпусков»,
// хотя кончается на 1). Проверяем обе формы, которыми пользуется бренд-строка.
//     node tests/js/plural.test.mjs
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

// dom.js трогает document на уровне модуля только внутри функций, но matchMedia
// нужен для reducedMotion — подставляем заглушки, чтобы импорт прошёл в node
globalThis.document = { querySelector: () => null, createElement: () => ({}) };
globalThis.matchMedia = () => ({ matches: false });

const { plural, countOf } = await import(join(repo, "web/js/ui/dom.js"));

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

const EP = ["выпуск", "выпуска", "выпусков"];
const say = (n) => countOf(n, ...EP);

console.log("счётная форма при числе:");
check("1, 21, 101 — единственное", () => {
  assert.equal(say(1), "1 выпуск");
  assert.equal(say(21), "21 выпуск");
  assert.equal(say(101), "101 выпуск");
});
check("2-4, 54 — «выпуска»", () => {
  assert.equal(say(2), "2 выпуска");
  assert.equal(say(4), "4 выпуска");
  assert.equal(say(54), "54 выпуска"); // то, ради чего всё затевалось
  assert.equal(say(123), "123 выпуска");
});
check("5-20 — «выпусков»", () => {
  assert.equal(say(5), "5 выпусков");
  assert.equal(say(20), "20 выпусков");
});
check("11-14 не идут по последней цифре", () => {
  // главная ловушка: 11 кончается на 1, но это «выпусков», а не «выпуск»
  for (const n of [11, 12, 13, 14, 111, 112]) {
    assert.ok(say(n).endsWith("выпусков"), `${n} → ${say(n)}`);
  }
});
check("ноль и отрицательные не роняют", () => {
  assert.equal(say(0), "0 выпусков");
  assert.equal(plural(-3, ...EP), "выпуска");
});

console.log("\nродительный после предлога («любой из …»):");
const GEN = ["выпуска", "выпусков", "выпусков"];
const from = (n) => `любой из ${countOf(n, ...GEN)}`;
check("54 → «из 54 выпусков», 21 → «из 21 выпуска»", () => {
  assert.equal(from(54), "любой из 54 выпусков");
  assert.equal(from(21), "любой из 21 выпуска");
  assert.equal(from(2), "любой из 2 выпусков");
  assert.equal(from(11), "любой из 11 выпусков");
});

console.log("\nсезоны той же функцией:");
check("2 сезона / 5 сезонов / 1 сезон", () => {
  const S = ["сезон", "сезона", "сезонов"];
  assert.equal(countOf(1, ...S), "1 сезон");
  assert.equal(countOf(2, ...S), "2 сезона");
  assert.equal(countOf(5, ...S), "5 сезонов");
});

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
