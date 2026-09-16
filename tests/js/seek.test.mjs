// Перемотка по абзацам: ← сначала в начало текущего, повторно — на предыдущий; → — на следующий.
import { strict as assert } from "node:assert";
import { isPauseKey, isSeekKey, nextTarget, paragraphAt, prevTarget } from "../../web/js/records/seek.js";

const starts = [0, 30.5, 61, 95.2];

// в каком абзаце секунда
assert.equal(paragraphAt(starts, 0), 0);
assert.equal(paragraphAt(starts, 29.9), 0);
assert.equal(paragraphAt(starts, 30.5), 1);
assert.equal(paragraphAt(starts, 200), 3);
assert.equal(paragraphAt([5, 10], 2), -1);

// ← с середины абзаца — в его начало; у самого начала — на предыдущий (как на плеере)
assert.equal(prevTarget(starts, 70), 61);
assert.equal(prevTarget(starts, 62), 30.5);
assert.equal(prevTarget(starts, 64), 30.5);          // ровно на допуске (3 с) — ещё «назад»
assert.equal(prevTarget(starts, 64.01), 61);         // чуть дальше допуска — в начало текущего
assert.equal(prevTarget(starts, 1), 0);              // первый абзац: некуда — остаёмся на нуле
assert.equal(prevTarget([5, 10], 2), 5);             // до первого абзаца — в его начало
assert.equal(prevTarget(starts, 40, 15), 0);         // допуск настраивается

// → — в начало следующего; из последнего — некуда
assert.equal(nextTarget(starts, 0), 30.5);
assert.equal(nextTarget(starts, 30.5), 61);
assert.equal(nextTarget(starts, 100), null);
assert.equal(nextTarget([], 10), null);
assert.equal(prevTarget([], 10), null);

// клавиши: только стрелки без модификаторов и не из поля ввода (режим правки — textarea)
const ev = (key, extra = {}) => ({ key, target: { tagName: "DIV" }, ...extra });
assert.equal(isSeekKey(ev("ArrowLeft")), true);
assert.equal(isSeekKey(ev("ArrowRight")), true);
assert.equal(isSeekKey(ev("ArrowUp")), false);
assert.equal(isSeekKey(ev("ArrowLeft", { metaKey: true })), false);
assert.equal(isSeekKey(ev("ArrowLeft", { shiftKey: true })), false);
assert.equal(isSeekKey({ key: "ArrowLeft", target: { tagName: "TEXTAREA" } }), false);
assert.equal(isSeekKey({ key: "ArrowRight", target: { tagName: "INPUT" } }), false);
assert.equal(isSeekKey({ key: "ArrowRight", target: { tagName: "DIV", isContentEditable: true } }), false);

// пробел — пауза/пуск: не из поля ввода, не с кнопки/ссылки (там пробел — их нажатие), без модификаторов
assert.equal(isPauseKey(ev(" ")), true);
assert.equal(isPauseKey({ key: "Spacebar", target: { tagName: "DIV" } }), true);
assert.equal(isPauseKey({ key: "Unidentified", code: "Space", target: { tagName: "P" } }), true);
assert.equal(isPauseKey(ev("Enter")), false);
assert.equal(isPauseKey(ev(" ", { ctrlKey: true })), false);
assert.equal(isPauseKey({ key: " ", target: { tagName: "BUTTON" } }), false);
assert.equal(isPauseKey({ key: " ", target: { tagName: "A" } }), false);
assert.equal(isPauseKey({ key: " ", target: { tagName: "TEXTAREA" } }), false);
assert.equal(isPauseKey({ key: " ", target: { tagName: "INPUT" } }), false);

console.log("seek: ok");
