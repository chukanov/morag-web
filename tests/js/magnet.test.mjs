import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const { pull, MAGNET } = await import(join(repo, "web/js/records/magnet.js"));

// Вне радиуса — покой, иначе всё облако дышало бы от любого движения мыши по странице.
assert.deepEqual(pull(MAGNET.radius, 0), { x: 0, y: 0, s: 1 });
assert.deepEqual(pull(300, -300), { x: 0, y: 0, s: 1 });
// Под курсором — максимальный рост, но сдвиг ноль: тянуть некуда.
assert.deepEqual(pull(0, 0), { x: 0, y: 0, s: 1 + MAGNET.grow });
// Тянется К курсору (знак сдвига — знак расстояния) и гаснет к краю.
const near = pull(20, -10), far = pull(70, -35);
assert.ok(near.x > 0 && near.y < 0, "сдвиг направлен к курсору");
assert.ok(near.s > far.s && far.s > 1, "рост убывает с расстоянием");
// Сила квадратичная: на середине радиуса — четверть максимума; сдвиг там заметен глазом.
assert.equal(Number((pull(MAGNET.radius / 2, 0).s - 1).toFixed(4)), Number((MAGNET.grow * 0.25).toFixed(4)));
assert.ok(pull(MAGNET.radius / 2, 0).x >= 6, "в середине радиуса тянет хотя бы на 6 px — иначе магнит не читается (15.09: при 2 px не виден)");
// Настройки уважаются.
assert.equal(pull(0, 0, { grow: 0.1 }).s, 1.1);
assert.deepEqual(pull(50, 0, { radius: 40 }), { x: 0, y: 0, s: 1 });
console.log("magnet: всё зелёное");
