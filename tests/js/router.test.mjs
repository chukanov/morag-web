// Адреса: сборка ссылок и переезд со старых хеш-ссылок.
//     node tests/js/router.test.mjs
//
// Почему это отдельный тест: ссылками делятся, они уходят в чужие чаты и статьи,
// и «молча перестало открываться» здесь — самая дорогая поломка.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const { buildPath, parsePath, createRouter } = await import(join(repo, "web/js/router.js"));

// --- сборка адреса ----------------------------------------------------------

assert.equal(buildPath({ slug: "captainsbridge", view: "home" }), "/captainsbridge");
assert.equal(buildPath({ slug: "captainsbridge", view: "chat" }), "/captainsbridge/chat");
// ⚠️ Своего экрана у списка записей больше нет — он и есть главная (10.09). Старый адрес
// остаётся РАБОЧИМ: по нему уже ходили ссылки, и отдавать на нём пустоту хуже, чем показать
// то же самое. Собирать его больше не из чего, а разбираться он обязан.
assert.equal(parsePath("/captainsbridge/records", "captainsbridge").view, "home");

// Диалог — адрес (11.09): у чата нет пустого экрана, на него попадают с вопросом с главной
// или по ссылке на конкретный разговор. Голый `/chat` — старая ссылка на прежний пустой
// экран, ведёт на вход, а не на пустую страницу.
{
  const dialog = parsePath("/captainsbridge/chat/abc-123", "captainsbridge");
  assert.equal(dialog.view, "chat");
  assert.equal(dialog.id, "abc-123", "идентификатор разговора обязан доехать до обработчика");
  assert.equal(dialog.slug, "captainsbridge");
  assert.equal(parsePath("/captainsbridge/chat", "captainsbridge").view, "home",
    "пустого экрана чата больше нет — старая ссылка ведёт на вход");
  assert.equal(buildPath({ slug: "captainsbridge", view: "chat", id: "abc-123" }), "/captainsbridge/chat/abc-123");
  assert.equal(buildPath({ slug: "captainsbridge", view: "chat", id: "a b" }), "/captainsbridge/chat/a%20b");
}

// Форма входа — вне пространства, как голоса: вход один на весь сайт. `/signin`, а не
// `/login`: на общем с чужим сайтом домене `/login` бывает занят (docs/auth.md).
{
  const signin = parsePath("/signin", "captainsbridge");
  assert.equal(signin.view, "signin");
  assert.equal(signin.slug, null, "у формы входа нет пространства");
  assert.equal(buildPath({ slug: "captainsbridge", view: "signin" }), "/signin");
  assert.equal(parsePath("/signin/anything", null).view, "signin", "хвост не делает из формы витрину");
}

// Слаг подкаста обязателен: движок generic, «Мостик» — первый из многих.
assert.equal(buildPath({ slug: "other", view: "reader", id: "2026-07-17-spark", sec: 1234 }), "/other/rec/2026-07-17-spark/1234");

// Секунда — целая: дробная в адресе выглядит мусором и ничего не даёт.
assert.equal(buildPath({ slug: "m", view: "reader", id: "1-3", sec: 12.7 }), "/m/rec/1-3/13");

// Ноль секунд не пишем: «начало выпуска» и «выпуск» — одно и то же.
assert.equal(buildPath({ slug: "m", view: "reader", id: "1-3", sec: 0 }), "/m/rec/1-3");

// Без слага (подкаст ещё не загрузился) адрес всё равно рабочий.
assert.equal(buildPath({ view: "home" }), "/");
assert.equal(buildPath({ view: "reader", id: "2-1" }), "/rec/2-1");

// Идентификаторы кодируются: они приходят из данных, а не из кода.
assert.ok(buildPath({ slug: "m", view: "reader", id: "a b" }).includes("a%20b"));

// --- разбор адреса ----------------------------------------------------------
// Часть проверяем через сборку и обратный разбор его же адресов, часть — разбором напрямую:
// ошибка бывает не в форме пути, а в ПОРЯДКЕ веток, и круговой обход её не видит.

const cases = [
  { slug: "captainsbridge", view: "reader", id: "2-31", sec: 90 },
  { slug: "captainsbridge", view: "chat", id: "s1" },
  { slug: "captainsbridge", view: "chat" },
];
for (const c of cases) {
  const path = buildPath(c);
  assert.ok(path.startsWith("/captainsbridge/"), `слаг потерян в ${path}`);
}

// Зарезервированные слова не должны съедаться как слаг: /chat без подкаста —
// это чат подкаста по умолчанию, а не подкаст с именем «chat».
assert.equal(buildPath({ view: "chat" }), "/chat");


// ⚠️ Регрессия 08.09: `/voices/Speaker_165` открывал ВИТРИНУ. «voices» лежит в
// зарезервированных, слаг из адреса не берётся, и ранний возврат «нет слага — значит витрина»
// срабатывал РАНЬШЕ ветки голосов. Клик по метке говорящего в читалке уводил на главную, и
// выглядело это так, будто верстака нет вовсе.
{
  const route = parsePath("/voices/Speaker_165", null);
  assert.equal(route.view, "voices", "адрес голоса обязан открывать верстак, а не витрину");
  assert.equal(route.id, "Speaker_165");
  assert.equal(route.slug, null, "у голоса нет пространства: он общий на весь корпус");

  // То же самое, когда пространство уже известно — переход из читалки.
  const fromReader = parsePath("/voices/Speaker_165", "demo");
  assert.equal(fromReader.view, "voices");
  assert.equal(fromReader.slug, null);

  // Список голосов без идентификатора.
  const all = parsePath("/voices", null);
  assert.equal(all.view, "voices");
  assert.equal(all.id, "");

  // Витрина при этом никуда не делась.
  assert.equal(parsePath("/", null).view, "hub");

  assert.equal(buildPath({ view: "voices", id: "Speaker_9" }), "/voices/Speaker_9");
  assert.equal(buildPath({ view: "voices" }), "/voices");
}

// ⚠️ Регрессия 08.09, вторая по тому же адресу: маршрут разбирался верно, но `apply` отдавал
// параметры ТОЛЬКО читалке — остальные обработчики звались без аргументов. Верстак голосов,
// открытый по `/voices/Speaker_165`, показывал весь список: идентификатор до него не доезжал.
// Круговым обходом адресов это не ловится: путь-то строился и разбирался правильно.
{
  const seen = [];
  const stub = () => {};
  globalThis.document = {
    getElementById: () => ({ classList: { toggle: stub } }),
    body: { setAttribute: stub },
    querySelectorAll: () => [],
    addEventListener: stub,
  };
  globalThis.addEventListener = stub;
  globalThis.dispatchEvent = stub;
  globalThis.history = { pushState: stub, replaceState: stub };
  globalThis.CustomEvent = class { constructor(name, opts) { this.detail = opts?.detail; } };

  const run = (pathname) => {
    seen.length = 0;
    globalThis.location = { pathname };
    createRouter({
      voices: (id) => seen.push(["voices", id]),
      reader: (id, sec) => seen.push(["reader", id, sec]),
      chat: (id) => seen.push(["chat", id]),
      hub: () => seen.push(["hub"]),
    }).start();
    return seen[0];
  };

  assert.deepEqual(run("/voices/Speaker_165"), ["voices", "Speaker_165"],
    "верстак обязан получить голос из адреса, иначе покажет весь список");
  assert.deepEqual(run("/voices"), ["voices", ""], "список голосов — без выделенного");
  assert.deepEqual(run("/demo/rec/2026-07-17-spark/90"), ["reader", "2026-07-17-spark", 90],
    "читалка по-прежнему получает запись и секунду");
  assert.deepEqual(run("/"), ["hub"]);
  assert.deepEqual(run("/demo/chat/abc-123"), ["chat", "abc-123"],
    "диалог обязан получить свой идентификатор, иначе откроет чужой разговор");
}

// Тумблер календаря и «Назад» (владелец, 15.09): кнопка в шапке на своём экране стоит нажатой
// и следующим нажатием возвращает ТУДА, ОТКУДА ПРИШЛИ; смена года историю не засоряет; а с
// календаря, открытого по прямой ссылке (истории нет), «назад» ведёт на главную, не на чужой сайт.
{
  const calls = [];
  const stub = () => {};
  let clickHandler = null;
  const toggleBtn = {
    cls: new Set(), attrs: { "data-go": "calendar", "data-toggle": "home" },
    classList: { toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, _s: null },
    getAttribute(k) { return this.attrs[k] ?? null; },
    hasAttribute(k) { return k in this.attrs; },
    setAttribute(k, v) { this.attrs[k] = v; },
  };
  toggleBtn.classList._s = toggleBtn.cls;
  const body = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] ?? null; } };
  globalThis.document = {
    getElementById: () => ({ classList: { toggle: stub } }),
    body,
    querySelectorAll: (sel) => (sel === "[data-toggle]" ? [toggleBtn] : []),
    addEventListener: (type, fn) => { if (type === "click") clickHandler = fn; },
  };
  globalThis.addEventListener = stub;
  globalThis.dispatchEvent = stub;
  globalThis.location = { pathname: "/demo" };
  globalThis.history = {
    state: null,
    pushState(state, _t, path) { calls.push(["push", path, state?.depth]); this.state = state; globalThis.location = { pathname: path }; },
    replaceState(state, _t, path) { calls.push(["replace", path, state?.depth]); this.state = state; globalThis.location = { pathname: path }; },
    back() { calls.push(["back"]); },
  };
  const router = createRouter({ home: stub, calendar: stub, reader: stub }, { getSlug: () => "demo" });
  router.start();
  assert.ok(!toggleBtn.cls.has("on") && toggleBtn.attrs["aria-pressed"] === "false", "на главной тумблер отжат");

  router.go({ view: "calendar" });
  assert.deepEqual(calls.at(-1), ["push", "/demo/calendar", 1], "переход кладёт глубину 1");
  assert.ok(toggleBtn.cls.has("on") && toggleBtn.attrs["aria-pressed"] === "true", "на календаре тумблер нажат");

  router.go({ view: "calendar", id: "2025" }, { replace: true });
  assert.deepEqual(calls.at(-1), ["replace", "/demo/calendar/2025", 1], "год — replace, глубина та же");

  // Клик по тумблеру на его же экране — назад по истории, не второй переход.
  const click = (link) => clickHandler({ preventDefault: stub, target: { closest: (sel) => (sel === "[data-go]" ? link : null) } });
  click(toggleBtn);
  assert.deepEqual(calls.at(-1), ["back"], "тумблер на своём экране зовёт history.back()");

  // Истории нет (прямая ссылка на календарь) — «назад» ведёт на главную, а не на чужой сайт.
  globalThis.history.state = null;
  globalThis.location = { pathname: "/demo/calendar" };
  body.setAttribute("data-view", "calendar");
  router.back({ view: "home" });
  assert.deepEqual(calls.at(-1), ["push", "/demo", 1], "без глубины back() — это переход на запасной экран");

  // «Назад» на странице (data-back) — тот же механизм.
  globalThis.history.state = { depth: 2 };
  const backLink = { getAttribute: (k) => (k === "data-back" ? "home" : null) };
  clickHandler({ preventDefault: stub, target: { closest: (sel) => (sel === "[data-back]" ? backLink : null) } });
  assert.deepEqual(calls.at(-1), ["back"], "data-back при глубине — history.back()");
}

console.log("\nвсё зелёное");
