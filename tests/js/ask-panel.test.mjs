// Кнопки вопросов к записи: какой набор показывать.
//
//     node tests/js/ask-panel.test.mjs
//
// ⚠️ Наборы приходят ИЗ КОНФИГА корпуса, который пишет человек руками, и ветка у записи может
// быть любой. Здесь проверяется правило выбора: своя строка ветки ЗАМЕНЯЕТ общую целиком, «*» —
// общая, мусор отброшен. Доменных названий тут нет намеренно: их место в `corpora/*/site.yml`.
import { strict as assert } from "node:assert";

// ⚠️ Заглушка DOM нужна ДО импорта: панель тянет ход диалога, тот — карточки моментов, а те —
// плеер, который создаёт единственный `<video>` ПРЯМО НА ИМПОРТЕ модуля. Проверяем здесь чистое
// правило выбора кнопок, но без этих трёх строк модуль не загрузится вовсе.
globalThis.document = {
  createElement: () => ({
    style: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, append() {}, addEventListener() {},
  }),
  addEventListener() {},
};

const { presetsFor } = await import("../../web/js/records/ask-panel.js");

const общие = [{ label: "Краткое содержание", question: "О чём эта запись?" }];
const ветка = [
  { label: "Резюме встречи", question: "Что обсудили?" },
  { label: "Открытые вопросы", question: "Что осталось без ответа?" },
];
const map = { "*": общие, Планёрка: ветка };

// своя строка ветки заменяет общую целиком
assert.deepEqual(presetsFor(map, "Планёрка"), ветка);
assert.equal(presetsFor(map, "Планёрка").length, 2, "общие к своим не подмешиваются");

// ветка без своей строки — общий набор
assert.deepEqual(presetsFor(map, "Основы"), общие);
assert.deepEqual(presetsFor(map, ""), общие, "у записи вне веток тоже есть кнопки");

// набора нет вовсе — пусто, а не падение
assert.deepEqual(presetsFor(null, "Основы"), []);
assert.deepEqual(presetsFor({}, "Основы"), []);
assert.deepEqual(presetsFor("не словарь", "Основы"), []);

// мусор из конфига до кнопок не доезжает
const грязный = { "*": [{ label: "Без вопроса" }, { question: "Без подписи" }, null, "строка",
                        { label: "Годная", question: "Вопрос?" }] };
assert.deepEqual(presetsFor(грязный, "Основы"), [{ label: "Годная", question: "Вопрос?" }]);

console.log("ask-panel: ок");
