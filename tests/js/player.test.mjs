// Общий проигрыватель: один <video> на всё приложение.
//     node tests/js/player.test.mjs
//
// Здесь проверяется не воспроизведение (его дать может только браузер), а обвязка,
// на которой уже ловились дефекты: media fragment в `src`, отложенная перемотка и
// то, что элемент один и переезжает между страницами.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.kids = [];
    this.parentNode = null;
    this._on = {};
    if (this.tagName === "VIDEO") {
      this.paused = true;
      this.readyState = 4;
      this.currentTime = 0;
      this.duration = 3600;
      this.playbackRate = 1;
      this.play = () => {
        this.paused = false;
        this.fire("play");
        return Promise.resolve();
      };
      this.pause = () => {
        this.paused = true;
        this.fire("pause");
      };
    }
  }
  addEventListener(type, fn) {
    (this._on[type] ||= []).push(fn);
  }
  removeEventListener(type, fn) {
    this._on[type] = (this._on[type] || []).filter((f) => f !== fn);
  }
  fire(type) {
    for (const fn of [...(this._on[type] || [])]) fn({ target: this });
  }
  append(...kids) {
    for (const kid of kids) {
      kid.parentNode?.kids?.splice(kid.parentNode.kids.indexOf(kid), 1);
      kid.parentNode = this;
      this.kids.push(kid);
    }
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.kids = this.parentNode.kids.filter((k) => k !== this);
    this.parentNode = null;
    // ⚠️ Спецификация: медиаэлемент, ИЗЪЯТЫЙ ИЗ ДОКУМЕНТА, браузер ставит на паузу. Без этой
    // строчки тест «уход с читалки не глушит звук» был зелёным вхолостую — а в браузере звук
    // глох (проверено 08.09: после ухода со страницы `querySelector("video")` отдавал null).
    if (this.tagName === "VIDEO" && !this.paused) {
      this.paused = true;
      this.fire("pause");
    }
  }
}

// Документ-заглушка. ⚠️ Слушатели и `fullscreenElement` здесь не для красоты: полноэкранный
// режим живёт на УРОВНЕ ДОКУМЕНТА (событие и текущий развёрнутый элемент), и без них модуль
// просто не грузится — что тест и поймал.
const docOn = {};
globalThis.document = {
  createElement: (t) => new El(t),
  body: new El("body"),
  fullscreenElement: null,
  addEventListener: (type, fn) => ((docOn[type] ||= []).push(fn)),
  removeEventListener: (type, fn) => (docOn[type] = (docOn[type] || []).filter((f) => f !== fn)),
  fire: (type) => [...(docOn[type] || [])].forEach((fn) => fn({ type })),
};
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
};
// Кадр не переспрашиваем: покадровый цикл плеера иначе стал бы бесконечной рекурсией.
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};

const player = await import(join(repo, "web/js/ui/player.js"));
const video = player.element();

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

console.log("проигрыватель:");

check("это <video>, и он играет в потоке страницы", () => {
  assert.equal(video.tagName, "VIDEO");
  // ⚠️ Без playsInline iOS уводит видео в свой полноэкранный плеер, и караоке —
  // ради которого страница и делалась — не видно вовсе.
  assert.equal(video.playsInline, true, "iOS уведёт видео в полный экран");
  assert.equal(video.controls, false, "родные кнопки дублировали бы плеер читалки");
});

check("элемент один и переезжает: attach → detach", () => {
  const screen = new El("div");
  player.attach(screen);
  assert.equal(screen.kids.length, 1, "кадр не встал на страницу");

  const another = new El("div");
  player.attach(another);
  assert.equal(screen.kids.length, 0, "элемент раздвоился — играли бы два сразу");
  assert.equal(another.kids.length, 1);

  player.detach();
  assert.equal(another.kids.length, 0);
});

check("уход с читалки не глушит звук", () => {
  // ⚠️ Элемент обязан быть НА СТРАНИЦЕ до ухода — иначе проверять нечего и тест зелен вхолостую
  // (так и было: `remove()` на уже откреплённом элементе ничего не делает).
  const screen = new El("div");
  player.attach(screen);
  player.play("/api/media/a.mp4", { at: 0, owner: "rd:a" });
  assert.equal(screen.kids.length, 1, "кадр не встал на страницу");
  player.detach();
  assert.equal(screen.kids.length, 0, "кадр остался на покинутой странице");
  assert.ok(player.state().playing, "detach остановил воспроизведение — а человек ушёл читать ответы");
});

check("в src кладём адрес БЕЗ «#t=»", () => {
  // Media fragment браузер отрабатывает сам при загрузке метаданных и затирает нашу
  // перемотку, которая ждёт того же события: первый клик по слову играл с начала чанка,
  // а повторный — уже верно.
  player.play("/api/media/b.mp4#t=120", { at: 120, owner: "rd:b" });
  assert.equal(video.src, "/api/media/b.mp4");
});

check("перемотка ждёт метаданных, если их ещё нет", () => {
  video.readyState = 0;
  video.currentTime = 0;
  player.play("/api/media/c.mp4", { at: 42, owner: "rd:c" });
  assert.equal(video.currentTime, 0, "перемотали вслепую — на свежей загрузке это уедет в ноль");

  video.readyState = 4;
  video.fire("loadedmetadata");
  assert.equal(video.currentTime, 42, "перемотка потерялась");
});

check("запись без видео не роняет страницу", () => {
  const before = video.src;
  player.play("", { at: 10, owner: "rd:пусто" });
  assert.equal(video.src, before, "играть нечего, но текст записи показать обязаны");
});

check("тот же владелец и адрес — пауза, чужой — воспроизведение", () => {
  player.play("/api/media/d.mp4", { at: 0, owner: "rd:d" });
  assert.equal(player.toggle("/api/media/d.mp4", { owner: "rd:d" }), false, "повторное нажатие не поставило паузу");
  assert.equal(player.toggle("/api/media/d.mp4", { owner: "m1" }), true, "чужой владелец должен перехватывать");
});

check("подпись и id записи уезжают в состояние — по ним панель в шапке знает, что играет", () => {
  player.play("/api/media/e.mp4", { at: 0, owner: "rd:e", title: "Kafka без боли", record: "2026-03-12-kafka" });
  const state = player.state();
  assert.equal(state.title, "Kafka без боли");
  assert.equal(state.record, "2026-03-12-kafka");

  // сменилась запись — прежняя подпись не про неё
  player.play("/api/media/f.mp4", { at: 0, owner: "rd:f" });
  assert.equal(player.state().title, "");
});

check("скорость идёт по кругу и запоминается", () => {
  const first = player.cycleRate();
  assert.ok(player.RATES.includes(first));
  assert.equal(store.get("morag.rate"), String(first), "скорость не сохранилась — забудется к следующему заходу");
  const seen = [first];
  for (let i = 1; i < player.RATES.length; i++) seen.push(player.cycleRate());
  assert.deepEqual([...seen].sort(), [...player.RATES].sort(), "по кругу проходят не все скорости");
});

check("подписчик получает состояние на каждое событие", () => {
  const seen = [];
  const off = player.subscribe((s) => seen.push(s.time));
  video.currentTime = 77;
  video.fire("timeupdate");
  off();
  video.fire("timeupdate");
  assert.deepEqual(seen, [77], "подписка не сработала или не снялась");
});

check("пауза и пуск не отматывают запись в начало", () => {
  // ⚠️ Читалка передаёт `at` из адреса, и у записи, открытой из списка, это НОЛЬ. Без оговорки
  // в `toggle` пуск после паузы честно перематывал в начало — на сороковой минуте доклада.
  video.readyState = 4;
  player.play("/api/media/g.mp4", { at: 0, owner: "rd:g" });
  video.currentTime = 1500; // послушали сорок минут
  assert.equal(player.toggle("/api/media/g.mp4", { owner: "rd:g" }), false, "пауза не сработала");
  assert.equal(player.toggle("/api/media/g.mp4", { at: 0, owner: "rd:g" }), true);
  assert.equal(video.currentTime, 1500, "пуск после паузы отмотал запись в начало");
});

// Дальше — про ПЕРВЫЙ клик на свежей странице, поэтому нужен чистый модуль: у здешнего
// проигрывателя запись уже загружена, а весь дефект был именно в состоянии «ещё ничего нет».
const fresh = await import(`${join(repo, "web/js/ui/player.js")}?fresh`);
const freshVideo = fresh.element();

check("prime готовит запись заранее и НЕ начинает её", () => {
  freshVideo.readyState = 0;
  fresh.prime("/api/media/z.mp4");
  assert.equal(freshVideo.src, "/api/media/z.mp4", "адрес не проставлен — первый клик опоздает");
  assert.ok(freshVideo.paused, "prime не имеет права включать звук");
  assert.equal(fresh.state().record, null, "панель в шапке решила бы, что запись играет");
});

check("первый клик по слову не звучит с нуля", () => {
  // ⚠️ Ровно тот дефект, что видел владелец: «первый клик не работает, а если срабатывает —
  // играет с начала». Метаданных нет, перематывать некуда, и браузер идёт с нуля.
  freshVideo.currentTime = 0;
  fresh.play("/api/media/z.mp4", { at: 55, owner: "rd:z" });
  assert.equal(freshVideo.currentTime, 0, "перемотали вслепую — на свежей загрузке это уедет в ноль");
  assert.ok(freshVideo.paused, "звук пошёл С НАЧАЛА, пока метаданные не подъехали");

  freshVideo.readyState = 4;
  freshVideo.fire("loadedmetadata");
  assert.equal(freshVideo.currentTime, 55, "отложенная перемотка потерялась");
  assert.ok(!freshVideo.paused, "после перемотки звук обязан пойти сам, без второго клика");
});

check("переход на другую запись переставляет плеер на неё", () => {
  // ⚠️ Ловилось владельцем: плеер оставался висеть на прежней записи — с её кадром и её
  // длительностью, — и выглядело это так, будто сейчас заиграет старое видео.
  freshVideo.readyState = 4;
  freshVideo.pause();
  fresh.prime("/api/media/другая.mp4");
  assert.equal(freshVideo.src, "/api/media/другая.mp4", "плеер остался на прежней записи");
  assert.equal(fresh.state().owner, null, "прежний владелец не про эту запись");
});

check("та же запись не перезагружается", () => {
  // Сброс src выбросил бы уже забранные метаданные — и первый клик снова играл бы с нуля.
  freshVideo.readyState = 4;
  fresh.prime("/api/media/другая.mp4");
  assert.equal(freshVideo.readyState, 4, "метаданные выброшены зря");
});

check("prime не обрывает ЗВУЧАЩУЮ запись", () => {
  // Звук намеренно не глохнет при уходе с читалки: человек мог включить доклад и пойти читать.
  freshVideo.readyState = 4;
  fresh.play("/api/media/другая.mp4", { at: 5, owner: "rd:другая" });
  assert.ok(!freshVideo.paused, "запись должна звучать");
  fresh.prime("/api/media/третья.mp4");
  assert.equal(freshVideo.src, "/api/media/другая.mp4", "оборвали звук на полуслове");
});

check("клик по другому слову ОТМЕНЯЕТ прежнюю отложенную перемотку", () => {
  // Иначе одноразовые слушатели копились бы очередью и доигрывали чужие места по порядку.
  freshVideo.readyState = 0;
  freshVideo.currentTime = 0;
  fresh.play("/api/media/z.mp4", { at: 10, owner: "rd:z" });
  fresh.play("/api/media/z.mp4", { at: 900, owner: "rd:z" });
  freshVideo.readyState = 4;
  freshVideo.fire("loadedmetadata");
  assert.equal(freshVideo.currentTime, 900, "доехали не до последнего выбранного места");
});

check("мини-плеер считает маршрут по пути, а не по хешу", () => {
  // ⚠️ Дефект, приехавший вместе с каркасом: проверка «виден ли плеер, которым запустили»
  // читала `location.hash`, а адреса давно на путях — хеш всегда пуст, проверка всегда «нет»,
  // и панель показывалась ПРЯМО В ЧИТАЛКЕ, второй полосой перемотки над первой.
  //
  // Смотрим исходник, а не поведение: `ownerOnScreen` не экспортирована, а поднимать ради неё
  // поддельные `location` и DOM дороже и хрупче, чем зафиксировать сам факт «хеша здесь нет».
  // Комментарии выбрасываем: в них дефект как раз ОПИСАН, и без этого тест ловил бы
  // собственное объяснение вместо кода.
  const source = readFileSync(join(repo, "web/js/ui/miniplayer.js"), "utf-8")
    .split("\n").filter((line) => !line.trim().startsWith("//")).join("\n");
  assert.ok(!/location\.hash/.test(source), "мини-плеер всё ещё смотрит в location.hash");
  assert.ok(!/hashchange/.test(source), "мини-плеер всё ещё слушает hashchange");
  assert.ok(/location\.pathname/.test(source), "мини-плеер должен разбирать путь");
  assert.ok(/morag:navigate/.test(source), "переходы внутри приложения — своё событие");

  // ⚠️ Видимость считается по ВЛАДЕЛЬЦУ, а не по адресу. С появлением `prime` адрес есть
  // сразу при открытии читалки, и проверка «есть url — значит играет» выводила панель в
  // шапку на странице, где ничего не звучало.
  assert.ok(/!s\.owner/.test(source), "панель снова смотрит на адрес вместо владельца");
  assert.ok(!/!s\.url\s*\|\|/.test(source), "осталась проверка по адресу");
});

console.log("во весь экран:");

check("разворачивается КОНТЕЙНЕР, а не сам <video>", () => {
  // ⚠️ Это не придирка к форме. Поверх развёрнутого `<video>` браузер не даёт положить ничего:
  // ни своих субтитров, ни подсветки слова. Развернули не тот элемент — и класть их некуда.
  const screen = new El("div");
  let наЧём = null;
  screen.requestFullscreen = function () { наЧём = "контейнер"; return Promise.resolve(); };
  video.requestFullscreen = function () { наЧём = "video"; return Promise.resolve(); };

  player.fullscreen(screen);
  assert.equal(наЧём, "контейнер", "развернули сам кадр — субтитры поверх него не лягут");
  delete video.requestFullscreen;
});

check("где контейнер развернуть нельзя (iOS) — разворачиваем кадр", () => {
  // На iOS полноэкранный режим есть ТОЛЬКО у `<video>`; отказаться от него значит остаться
  // на телефоне вовсе без полного экрана.
  const screen = new El("div");
  let позвали = false;
  video.webkitEnterFullscreen = () => { позвали = true; };
  player.fullscreen(screen);
  assert.ok(позвали, "на iOS кадр остался неразвёрнутым");
  delete video.webkitEnterFullscreen;
});

check("в полном экране отдаём РОДНЫЕ кнопки, после выхода забираем", () => {
  // Наш пульт остаётся на странице и в полном экране не виден: без родных кнопок там нельзя
  // ни перемотать, ни выйти иначе как клавишей.
  document.fullscreenElement = new El("div");
  document.fire("fullscreenchange");
  assert.equal(video.controls, true, "в полном экране нечем управлять");

  document.fullscreenElement = null;
  document.fire("fullscreenchange");
  assert.equal(video.controls, false, "родные кнопки остались и дублируют пульт читалки");
});

check("состояние говорит, есть ли КАРТИНКА: у аудио разворачивать нечего", () => {
  video.videoWidth = 0;
  assert.equal(player.state().picture, false);
  video.videoWidth = 1280;
  assert.equal(player.state().picture, true);
});

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
