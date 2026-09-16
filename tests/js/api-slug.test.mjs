// Слаг пространства обязан ехать в КАЖДОМ запросе.
//
// ⚠️ Это проверка целого класса ошибок, а не одного вызова. Слаг нужен девяти вызовам в шести
// файлах, и достаточно забыть один, чтобы страница одного раздела молча показала данные другого.
// У подкаста ровно это и было сломано: фронт не слал слаг вообще, и любой адрес отдавал корпус
// по умолчанию — мультикорпусность «работала» ровно до второго корпуса.
import { strict as assert } from "node:assert";
import {
  useCorpus, getRecords, getWords, transcriptUrl, slidesUrl, brandAsset, mediaUrl, ask,
} from "../../web/js/api.js";

const seen = [];
globalThis.fetch = async (url, init) => {
  seen.push({ url: String(url), init });
  // Пустой поток: `ask` сразу за отправкой начинает читать кадры, и без тела падал бы
  // на первом же чтении — а проверяем мы то, что УШЛО, а не то, что вернулось.
  return {
    ok: true,
    json: async () => ({}),
    body: { getReader: () => ({ read: async () => ({ done: true }), cancel() {} }) },
  };
};

async function urlsAfter(fn) {
  seen.length = 0;
  await fn();
  return seen.map((r) => r.url);
}

// --- со слагом ---------------------------------------------------------------
useCorpus("second-corpus");

const built = [
  transcriptUrl("rec-1"),
  slidesUrl("rec-1"),
  brandAsset("cover.jpg"),
  mediaUrl("Каталог/файл.mp4"),
];
for (const url of built) {
  assert.ok(url.includes("slug=second-corpus"), `нет слага в адресе: ${url}`);
}

const fetched = await urlsAfter(async () => {
  await getRecords();
  await getWords("rec-1");
  await getWords("rec-1", { start: 10, end: 20, pad: 2 });
});
for (const url of fetched) {
  assert.ok(url.includes("slug=second-corpus"), `нет слага в запросе: ${url}`);
}
// «шире» не должен терять ни промежуток, ни слаг: раньше строка запроса склеивалась руками.
const wide = fetched.at(-1);
assert.ok(wide.includes("start=10") && wide.includes("end=20") && wide.includes("pad=2"), wide);

// Вопрос уходит с пространством в теле: у каждого свой мораг и своя коллекция.
seen.length = 0;
await ask({ question: "что?" }, undefined).next();
assert.equal(JSON.parse(seen[0].init.body).corpus, "second-corpus");

// --- адрес архива вместо своей раздачи ---------------------------------------
useCorpus("demo", "https://example.invalid/archive/");
const direct = mediaUrl("Каталог/Подкаталог/файл с пробелом.mp4");
assert.equal(direct,
  "https://example.invalid/archive/%D0%9A%D0%B0%D1%82%D0%B0%D0%BB%D0%BE%D0%B3/" +
  "%D0%9F%D0%BE%D0%B4%D0%BA%D0%B0%D1%82%D0%B0%D0%BB%D0%BE%D0%B3/" +
  "%D1%84%D0%B0%D0%B9%D0%BB%20%D1%81%20%D0%BF%D1%80%D0%BE%D0%B1%D0%B5%D0%BB%D0%BE%D0%BC.mp4",
  `посегментное кодирование сломано: ${direct}`);
assert.ok(!direct.includes("%2F"), "слэши пути превратились в %2F — сервер такого не найдёт");

// --- вопрос, ограниченный одной записью --------------------------------------
// Канала «искать в подмножестве» между сайтом и движком нет: ограничение выражается текстом
// вопроса, который собирает СЕРВЕР по этому контексту. Значит клиент обязан прислать `scope`.
useCorpus("demo");
seen.length = 0;
await ask({
  question: "о чём запись?",
  context: { record_id: "2024-01-15-kafka-osnovy", scope: "record" },
  wantTopic: false,
}, undefined).next();
const body = JSON.parse(seen[0].init.body);
assert.deepEqual(body.context, { record_id: "2024-01-15-kafka-osnovy", scope: "record" });
assert.equal(body.want_topic, false, "тема на странице записи не нужна: её заставка — синглтон");

// --- одно пространство без слага ---------------------------------------------
useCorpus("");
assert.equal(transcriptUrl("rec-1"), "/api/records/rec-1/transcript.md");
assert.equal((await urlsAfter(() => getRecords()))[0], "/api/records");

console.log("api-slug: ок");
