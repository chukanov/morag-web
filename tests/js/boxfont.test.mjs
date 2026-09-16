// Слово знака рамочным шрифтом и раскладка знака — чистая арифметика под node.
//     node tests/js/boxfont.test.mjs
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const { render, width, GLYPHS } = await import(join(repo, "web/js/ui/boxfont.js"));
const { layout, artSize } = await import(join(repo, "web/js/ui/mark.js"));

// Шрифт: три строки, у каждой буквы ровная ширина, A–Z и цифры на месте.
for (const [ch, g] of Object.entries(GLYPHS)) {
  assert.equal(g.length, 3, `${ch}: три строки`);
  assert.ok(new Set(g.map((l) => [...l].length)).size === 1, `${ch}: строки одной ширины`);
}
for (const ch of "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") assert.ok(GLYPHS[ch], `нет глифа ${ch}`);

// Слово платформы по умолчанию — три строки рамочного шрифта, прибитые байт в байт: правка
// глифа в шапке видна не глазами, а тестом.
assert.deepEqual(render("MORAG"), ["╔╦╗ ╔═╗ ╔═╗ ╔═╗ ╔═╗", "║║║ ║ ║ ╠╦╝ ╠═╣ ║ ╦", "╩ ╩ ╚═╝ ╩ ╚ ╩ ╩ ╚═╝"]);
assert.deepEqual(render("morag".toLowerCase()), render("MORAG"), "регистр не важен");
assert.equal(width("MORAG"), 19);
assert.equal(render("").join(""), "", "пустое слово — пустые строки");
assert.equal(render("M?").join("\n").includes("?"), false, "незнакомый символ — пробел, не мусор");

// Раскладка: с рисунком слово стоит справа, поле в клетках рисунка; без рисунка — поле = слово.
const art = { lines: ["ab", "cd", "ef", "gh"] };
assert.deepEqual(artSize(art), { cols: 2, rows: 4 });
const g1 = layout(art, render("MO"), 2);
assert.equal(g1.ox, 5, "слово начинается через 3 клетки после рисунка");
assert.equal(g1.sx, 2);
assert.equal(g1.field.rows, 4);
assert.equal(g1.field.cols, 5 + width("MO") * 2);
const g0 = layout(null, render("MORAG"), 2.75);
assert.deepEqual(g0, { field: { cols: width("MORAG"), rows: 3 }, ox: 0, oy: 0, sx: 1, sy: 1 });
assert.deepEqual(artSize(null), { cols: 0, rows: 0 });

console.log("boxfont: ок");
