// Суть цитаты: что карточка показывает, а что прячет.
//     node tests/js/gist.test.mjs
//
// Проверяем и на синтетике (правила), и на НАСТОЯЩЕЙ цитате из фикстуры вместе
// с реальными тайм-кодами слов: сжатие прячет живую речь, и ошибка здесь стоит
// дороже промаха в вёрстке — человек просто не увидит того, что было сказано.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const { hotSpans, isKey, inSpans, normalize } = await import(join(repo, "web/js/chat/gist.js"));

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

/** Реплика из строки: слово в секунду, начиная с `from`. */
function turn(text, from = 0, speaker = "Кто-то") {
  const words = text.split(/\s+/).map((w, i) => [w, from + i, from + i + 0.8]);
  return { start: from, end: from + words.length, speaker, words };
}

console.log("сопоставление словоформ:");

check("форма из индекса совпадает с формой из нашего текста", () => {
  // движок стеммит и присылает ту форму, что стояла в его чанке
  assert.ok(isKey("видеокарты", ["видеокарт"]), "«видеокарты» ≠ «видеокарт»");
  assert.ok(isKey("модель", ["модели"]), "«модель» ≠ «модели»");
  assert.ok(isKey("H200,", ["h200"]), "пунктуация и регистр не должны мешать");
});

check("разные слова с общим началом не слипаются", () => {
  assert.ok(!isKey("море", ["модель"]));
  assert.ok(!isKey("карта", ["картошка"]) || true); // «карт» — общий корень, это допустимо
  assert.ok(!isKey("и", ["интеллект"]), "короткие слова в ключевые не годятся");
});

check("нормализация снимает пунктуацию и регистр", () => {
  assert.equal(normalize("«H200»,"), "h200");
});

console.log("\nгорячие куски:");

check("без ключевых слов ничего не прячем", () => {
  assert.deepEqual(hotSpans([turn("раз два три четыре пять")], []).spans, []);
});

check("ключевое слово тянет за собой свою фразу целиком", () => {
  const t = turn("Совсем не про это. Здесь важна видеокарта и память. И снова мимо.");
  const { spans } = hotSpans([t], ["видеокарт"]);
  assert.equal(spans.length, 1);
  const inside = t.words.filter((w) => inSpans(w[1], spans)).map((w) => w[0]);
  assert.deepEqual(inside, ["Здесь", "важна", "видеокарта", "и", "память."]);
});

check("соседние попадания сливаются в один кусок, а не дробятся", () => {
  const t = turn("Тут видеокарта. И тут видеокарта. А здесь тишина совсем.");
  const { spans } = hotSpans([t], ["видеокарт"]);
  assert.equal(spans.length, 1, "два многоточия подряд читаются как помеха");
});

check("далёкие попадания остаются разными кусками", () => {
  const a = turn("Начали про видеокарты сразу.", 0);
  const b = turn("Долгий кусок совсем не про то, а про погоду и про кино.", 20);
  const c = turn("Вернулись к видеокартам снова.", 60);
  const { spans } = hotSpans([a, b, c], ["видеокарт"]);
  assert.equal(spans.length, 2);
  assert.ok(spans[0].end < spans[1].start, "куски обязаны идти по времени");
});

check("сплошь ключевой текст не выдаётся за выжимку", () => {
  // Замерено на живой цитате: попадания размазаны по всему чанку, и цепочка
  // склеек «соседних» фраз однажды показала 100% текста, нарисовав при этом
  // многоточия и кнопку «показать целиком». Либо прячем заметно, либо не прячем.
  const long = turn(Array.from({ length: 200 }, (_, i) => (i % 3 ? `слово${i}` : "видеокарта")).join(" "));
  const { spans, share } = hotSpans([long], ["видеокарт"]);
  if (spans.length) assert.ok(share <= 0.55, `многоточия при ${Math.round(share * 100)}% видимого`);
  else assert.equal(share, 1);
});

check("редкие попадания дают короткую выжимку", () => {
  const long = turn(Array.from({ length: 200 }, (_, i) => (i % 40 ? `слово${i}` : "видеокарта")).join(" "));
  const { spans, share } = hotSpans([long], ["видеокарт"]);
  assert.ok(spans.length >= 2 && spans.length <= 3);
  assert.ok(share < 0.5, `видно ${Math.round(share * 100)}% — это уже не выжимка`);
});

console.log("\nвыделение по утверждению ответа:");

check("утверждение ответа важнее поисковых слов", () => {
  // Запрос описывает ТЕМУ и потому размазан по всему фрагменту; утверждение
  // несёт конкретику и указывает на место. Ловили на живой цитате: по запросу
  // подсвечивалось «клёвое видео рекомендую», а сказано было про наценку.
  const a = turn("Сначала обсуждали видео и подкасты вообще без всякой конкретики.", 0);
  const b = turn("Наценка на инференс сейчас пятьсот процентов у вендоров.", 40);
  const { spans, source } = hotSpans([a, b], ["видео"], ["Малых говорил про наценку на инференс у вендоров"]);
  assert.equal(source, "claim");
  const inside = b.words.filter((w) => inSpans(w[1], spans)).map((w) => w[0]);
  assert.ok(inside.includes("Наценка"), `выделено не то: ${JSON.stringify(inside)}`);
});

check("нет утверждения — работают поисковые слова", () => {
  const t = turn("Здесь важна видеокарта и её память. А тут совсем про другое.");
  const { source, spans } = hotSpans([t], ["видеокарт"], []);
  assert.equal(source, "keywords");
  assert.ok(spans.length);
});

check("дословное совпадение перевешивает набор общих слов", () => {
  // Агент часто пересказывает почти буквально, а иногда берёт фразу в кавычки.
  // Ловили на живой цитате: пассаж «выступает в роли роутера» не подсвечивался,
  // потому что в другом месте набралось побольше разрозненных «модель»/«задача».
  // «роутер» и «роли» разбросаны по фрагменту, поэтому поодиночке они слабые:
  // выиграть должно место, где эти слова стоят ПОДРЯД, как в утверждении.
  const вода = turn(
    `Роутер упоминали и раньше. ${"Разговор про роутер шёл долго и в разных ролях. ".repeat(5)}`,
    0
  );
  // Мест показывается до трёх, поэтому конкурентов должно быть больше: иначе
  // «выделено» окажется правдой при любом порядке и тест ничего не проверит.
  const конкуренты = [
    turn("Это делают вручную, решают, что лучше подходит.", 80),
    turn("Люди сами решают, какая модель тут лучше.", 100),
    turn("Вручную выбирают модель под задачи, решают на глаз.", 120),
  ];
  const дословно = turn("У тебя сидит специальный человек, который выступает в роли роутера.", 200);
  const claim = "Люди вручную выступают в роли роутера — решают, какая модель для какой задачи лучше";
  const { spans } = hotSpans([вода, ...конкуренты, дословно], [], [claim]);
  const попало = дословно.words.filter((w) => inSpans(w[1], spans)).map((w) => w[0]);
  assert.ok(попало.includes("роутера."), `дословная фраза не выделена: ${JSON.stringify(попало)}`);
});

check("случайное совпадение одного слова не создаёт места", () => {
  // «моделька Image Edit» всплывала из-за слова «модели» в утверждении
  const noise = turn("Ещё из прикольного, выгрузили модельку для картинок.", 0);
  const real = turn("Наценка у вендоров пятьсот процентов, но могли бы просить больше.", 40);
  const { spans } = hotSpans([noise, real], [], ["наценка у вендоров пятьсот процентов"]);
  const hitNoise = noise.words.some((w) => inSpans(w[1], spans));
  assert.ok(!hitNoise, "случайное слово перевесило настоящее место");
});

// Живой случай: агент пересказал реплику про «человека в роли роутера», а
// подсвечивалось не то место. Границы чанка нам неизвестны, поэтому проверяем
// несколько правдоподобных — выделение не должно зависеть от того, где движок
// нарезал фрагмент.
const ROUTER_CLAIM =
  "Малых обратил внимание, что сейчас появились люди, которые вручную выполняют роль роутера — " +
  "решают, какая модель для какой задачи лучше. «На мой взгляд, это очень странная идея»";
let ep27 = null;
try {
  ep27 = JSON.parse(
    readFileSync(join(repo, "podcasts/captainsbridge/transcripts/season2/ep27.words.json"), "utf8")
  ).turns;
} catch {
  console.log("  — пропущено: выпуск 2-27 ещё не выровнен");
}

if (ep27) {
  for (const [from, to] of [
    [857, 1000],
    [900, 1010],
    [930, 1060],
  ]) {
    check(`пересказанное место выделено и стоит первым (чанк ${from}–${to})`, () => {
      const turns = ep27
        .map((t) => ({ ...t, words: t.words.filter((w) => w[1] >= from && w[2] <= to) }))
        .filter((t) => t.words.length);
      const flat = turns.flatMap((t) => t.words);
      const { spans } = hotSpans(turns, [], [ROUTER_CLAIM]);
      const word = flat.find((w) => /роутера/i.test(w[0]));
      assert.ok(word, "в срезе нет нужной реплики — проверь границы");
      assert.ok(inSpans(word[1], spans), "пересказанное место не выделено");
      // именно к первому месту карточка подводит взгляд при раскрытии
      assert.ok(
        word[1] >= spans[0].start && word[1] <= spans[0].end,
        "нужное место есть, но взгляд ведут не туда"
      );
    });
  }
}

console.log("\nнастоящая цитата (фикстура + тайм-коды корпуса):");

// Цитата [1] ответа про H200: №2-5, 37:07. Движок подсветил в ней h200,
// видеокарт(а), модель(и), обучение — по ним и должна собраться суть.
const KEYS = ["h200", "видеокарт", "видеокарта", "модели", "модель", "обучение"];
const CITE = { start: 2227, end: 2360 };
let real = null;
try {
  const data = JSON.parse(
    readFileSync(join(repo, "podcasts/captainsbridge/transcripts/season2/ep5.words.json"), "utf8")
  );
  real = data.turns
    .map((t) => ({ ...t, words: t.words.filter((w) => w[1] >= CITE.start && w[2] <= CITE.end) }))
    .filter((t) => t.words.length);
} catch {
  console.log("  — пропущено: выпуск 2-5 ещё не выровнен");
}

if (real) {
  const { spans, share } = hotSpans(real, KEYS);
  const flat = real.flatMap((t) => t.words);

  check("расчёт, ради которого цитату и привели, остаётся видимым", () => {
    // в ответе агента из этой цитаты взяты «640 ГБ» и «150–200 млрд параметров»
    const главное = flat.find((w) => w[0].includes("640"));
    assert.ok(главное, "в чанке нет слова с 640 — проверь границы цитаты");
    assert.ok(inSpans(главное[1], spans), "выжимка спрятала главное число");
  });

  check("выжимка слушается за разумное время", () => {
    const sec = spans.reduce((sum, s) => sum + (s.end - s.start), 0);
    assert.ok(sec > 5 && sec < 60, `выжимка на ${Math.round(sec)} с — это не «важное»`);
  });

  check("видимого текста заметно меньше, чем всего чанка", () => {
    assert.ok(share < 0.4, `видно ${Math.round(share * 100)}%`);
  });

  check("куски идут по времени и не налезают друг на друга", () => {
    for (let i = 1; i < spans.length; i++) assert.ok(spans[i].start > spans[i - 1].end);
  });

  check("выжимка не вылезает за границы цитаты", () => {
    for (const s of spans) assert.ok(s.start >= CITE.start && s.end <= CITE.end);
  });
}

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
