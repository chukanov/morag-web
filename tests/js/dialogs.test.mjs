// Список диалогов в браузере: сохранение, чтение, удаление, лимиты.
//     node tests/js/dialogs.test.mjs
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

const { saveDialog, listDialogs, loadDialog, deleteDialog, newSessionId, formatWhen } = await import(
  join(repo, "web/js/chat/history.js")
);

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

const turn = (q, a, cits = []) => ({ question: q, answer: a, citations: cits });

console.log("диалоги в браузере:");

check("новый id уникален", () => assert.notEqual(newSessionId(), newSessionId()));

check("пустой диалог не сохраняется", () => {
  saveDialog({ id: "empty", turns: [] });
  assert.equal(listDialogs().length, 0);
});

check("диалог сохраняется и читается", () => {
  saveDialog({
    id: "d1",
    title: "Спор про H200",
    topic: { title: "Спор про H200", summary: "О железе" },
    turns: [turn("Что про H200?", "Ответ про память", [{ n: 1, rec: "2026-07-17-spark", sec: 2227, url: "/api/media/spark.mp4", label: "…" }])],
  });
  const saved = loadDialog("d1");
  assert.equal(saved.turns.length, 1);
  assert.equal(saved.turns[0].citations[0].rec, "2026-07-17-spark", "цитаты должны сохраняться — по ним рисуются карточки");
  assert.equal(saved.topic.title, "Спор про H200");
});

check("без темы заголовок берётся из первого вопроса", () => {
  saveDialog({ id: "d2", turns: [turn("Кто был гостем в №9?", "Гостья")] });
  assert.ok(listDialogs().find((d) => d.id === "d2").title.startsWith("Кто был гостем"));
});

check("свежие сверху", () => {
  const ids = listDialogs().map((d) => d.id);
  assert.deepEqual(ids, ["d2", "d1"]);
});

check("повторное сохранение обновляет, а не плодит", () => {
  saveDialog({ id: "d1", title: "Спор про H200", turns: [turn("q1", "a1"), turn("q2", "a2")] });
  const all = listDialogs().filter((d) => d.id === "d1");
  assert.equal(all.length, 1);
  assert.equal(all[0].count, 2);
});

check("удаление работает", () => {
  deleteDialog("d2");
  assert.equal(loadDialog("d2"), null);
  assert.ok(!listDialogs().some((d) => d.id === "d2"));
});

check("хранится не больше 20 диалогов", () => {
  for (let i = 0; i < 25; i++) saveDialog({ id: `x${i}`, turns: [turn(`q${i}`, "a")] });
  assert.ok(listDialogs().length <= 20, `в списке ${listDialogs().length}`);
});

check("битое хранилище не роняет список", () => {
  store.set("dialogs", "{не json");
  assert.deepEqual(listDialogs(), []);
});

check("время показывается коротко", () => {
  assert.match(formatWhen(Date.now()), /^\d{1,2}:\d{2}$/); // сегодня — часы
  assert.ok(formatWhen(Date.now() - 5 * 864e5).length <= 12); // раньше — дата
});

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
