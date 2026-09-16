// Субтитры: нарезка титров из пословных времён и сборка дорожки WebVTT.
//     node tests/js/subs.test.mjs
//
// Проверяется класс ошибки, а не буква формата: титр во весь экран, титр-мельканье, склеенные
// говорящие, `Speaker_N` в кадре и дорожка, которую браузер молча не разберёт.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const { cues, toVtt } = await import(join(repo, "web/js/records/subs.js"));

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

/** Реплика из слов «слово, от, до» — ровно так их отдаёт `/api/records/<id>/words`. */
const turn = (speaker, words, t0 = 0, step = 0.4) => ({
  speaker,
  words: words.map((w, i) => [w, +(t0 + i * step).toFixed(2), +(t0 + i * step + step * 0.8).toFixed(2)]),
});

console.log("нарезка титров:");

check("длинная реплика режется по концу предложения", () => {
  const [a, b] = cues([turn("Кузнецова", "Это сказ о том. Первый разметчик шёл".split(" "))]);
  assert.ok(a.text.endsWith("сказ о том."), `первый титр: ${a.text}`);
  assert.equal(b.text, "Первый разметчик шёл");
});

check("точка в сокращении границей НЕ считается", () => {
  // «т.е.» носит точку, но фраза продолжается — правило то же, что у разреза на абзацы.
  const [первый] = cues([turn("Кузнецова", "мы берём т.е. половину списка".split(" "))]);
  assert.ok(первый.text.includes("половину"), `порезали на сокращении: ${первый.text}`);
});

check("без знаков препинания режем по паузе", () => {
  const words = [["раз", 0, 0.3], ["два", 0.4, 0.7], ["три", 3.0, 3.3]];
  const list = cues([{ speaker: "Кузнецова", words }]);
  assert.equal(list.length, 2, "пауза в две секунды не разорвала титр");
});

check("ни один титр не длиннее двух строк", () => {
  const many = Array.from({ length: 60 }, (_, i) => `слово${i}`);
  for (const cue of cues([turn("Кузнецова", many)])) {
    assert.ok(cue.text.length <= 96, `титр во весь экран: ${cue.text.length} знаков`);
  }
});

check("растянутый звук обрезается: один токен на 200 знаков", () => {
  // Проверяем САМ ТОКЕН, а не длину титра: к титру ещё добавляется имя говорящего, и порог
  // «титр короче N» ловил бы не то правило.
  const [первый] = cues([turn("Кузнецова", ["Ры" + "-и".repeat(100), "дальше"])]);
  const самое = первый.text.split(" ").reduce((a, b) => (a.length > b.length ? a : b));
  assert.ok(самое.length <= 48, `титр-стена: токен в ${самое.length} знаков`);
  assert.ok(самое.endsWith("…"), "обрезали без многоточия — выглядит как обрыв данных");
});

console.log("говорящие:");

check("Speaker_N в кадр не попадает", () => {
  // Это идентификатор реестра голосов; посетителю он не говорит ничего — то же правило, что
  // в промпте агента.
  for (const cue of cues([turn("Speaker_314", ["раз", "два"])])) {
    assert.ok(!/Speaker_/.test(cue.text), `в титре служебная метка: ${cue.text}`);
  }
});

check("имя ставится при СМЕНЕ говорящего и не повторяется", () => {
  const list = cues([
    turn("Кузнецова", ["раз."], 0),
    turn("Кузнецова", ["два."], 10),
    turn("Ковалёв", ["три."], 20),
  ]);
  assert.ok(list[0].text.startsWith("Кузнецова: "));
  assert.ok(!list[1].text.includes("Кузнецова"), "имя повторили — это шум");
  assert.ok(list[2].text.startsWith("Ковалёв: "));
});

check("титр не склеивает двух говорящих", () => {
  const list = cues([turn("Кузнецова", ["раз"], 0), turn("Ковалёв", ["два"], 0.5)]);
  assert.equal(list.length, 2, "слова разных людей попали в один титр");
});

console.log("времена:");

check("титры идут по возрастанию и не наезжают друг на друга", () => {
  const list = cues([turn("Кузнецова", "раз. два. три. четыре.".split(" "))]);
  for (let i = 0; i < list.length; i += 1) {
    assert.ok(list[i].end > list[i].start, "конец титра раньше начала");
    if (list[i + 1]) {
      assert.ok(list[i].end <= list[i + 1].start + 1e-9,
                "титры наехали — браузер покажет их двумя этажами разом");
    }
  }
});

check("короткий титр растягивается, но не за счёт следующего", () => {
  const list = cues([{ speaker: "Кузнецова", words: [["да.", 0, 0.2], ["нет.", 5, 5.2]] }]);
  assert.ok(list[0].end - list[0].start >= 1, "титр мелькнул на две десятых секунды");
  assert.ok(list[0].end <= list[1].start);
});

console.log("дорожка:");

check("пустая строка между титрами — без неё браузер МОЛЧА не разберёт файл", () => {
  const text = toVtt([{ start: 1.5, end: 2.25, text: "раз" }, { start: 3, end: 4, text: "два" }]);
  assert.ok(text.startsWith("WEBVTT\n\n"), "нет заголовка дорожки");
  assert.ok(text.includes("\n\n00:00:03.000"), "титры слиплись без пустой строки");
});

check("метка времени — часы:минуты:секунды.миллисекунды", () => {
  const text = toVtt([{ start: 3671.5, end: 3672, text: "раз" }]);
  assert.ok(text.includes("01:01:11.500 --> 01:01:12.000"), text.split("\n")[2]);
});

check("пустой корпус даёт пустую, но ПРАВИЛЬНУЮ дорожку", () => {
  assert.equal(cues([]).length, 0);
  assert.ok(toVtt([]).startsWith("WEBVTT"));
});

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
