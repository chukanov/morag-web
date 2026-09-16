// Сессия на фронте: возврат после входа, права, инициалы, тексты ошибок.
//     node tests/js/session.test.mjs
//
// Главное здесь — `nextFrom`: параметр `next` приходит из адреса, то есть от кого угодно, и
// форма входа, послушно уводящая на `//evil`, — классическая дыра. Остальное — чтобы шапка и
// читалка читали одни и те же флаги.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const s = await import(join(repo, "web/js/session.js"));

// --- возврат после входа: только свой относительный путь ----------------------
assert.equal(s.nextFrom("?next=%2Fdemo%2Frec%2Fx%3Fy%3D1"), "/demo/rec/x?y=1");
assert.equal(s.nextFrom(""), "/", "дом по умолчанию — корень, пока сервер не сказал иного");
assert.equal(s.nextFrom("?next="), "/");
assert.equal(s.nextFrom("?next=%2F%2Fevil.example"), "/", "протокольно-относительный адрес — чужой сайт");
assert.equal(s.nextFrom("?next=https%3A%2F%2Fevil.example%2F"), "/");
assert.equal(s.nextFrom("?next=%2F%5Cevil.example"), "/", "браузеры читают /\\evil как //evil");
assert.equal(s.nextFrom("?next=%2Fsignin%3Fnext%3D%2Fx"), "/", "с формы на форму — кольцо");
assert.equal(s.nextFrom("?next=%2Fsigninx"), "/signinx", "похожий путь — не форма");
// Дом сайта — пространство, не корень домена (там может жить чужой сайт): и явным аргументом, и из `me`.
assert.equal(s.nextFrom("", "/demo"), "/demo");
assert.equal(s.nextFrom("?next=%2F%2Fevil.example", "/demo"), "/demo", "кривой next — домой, не на корень");
s.useSession({ login: "k", can: {}, home: "/demo" });
assert.equal(s.homePath(), "/demo");
assert.equal(s.nextFrom(""), "/demo", "после `me` запасной адрес — дом из ответа сервера");
assert.equal(s.speakerName(), "");
s.useSession({ login: "k", can: {}, speaker_name: "Мария Кузнецова" });
assert.equal(s.speakerName(), "Мария Кузнецова");
assert.equal(s.homePath(), "/demo", "me без home не сбрасывает дом");
s.useSession(null);

// --- адрес формы с возвратом ----------------------------------------------------
assert.equal(s.loginPath("/demo/rec/x?y=1"), "/signin?next=%2Fdemo%2Frec%2Fx%3Fy%3D1");
assert.equal(s.loginPath("/"), "/signin", "с главной хвост не нужен");
assert.equal(s.loginPath(""), "/signin");

// --- права: одна модульная переменная ---------------------------------------------
assert.equal(s.can("edit"), false, "до ответа сервера прав нет");
s.useSession({ login: "k", can: { edit: true, voices: false } });
assert.equal(s.can("edit"), true);
assert.equal(s.can("voices"), false);
assert.equal(s.can("anything"), false);
assert.equal(s.current().login, "k");
s.useSession({ login: null, can: { edit: true, voices: true } }); // вход выключен, правка включена
assert.equal(s.can("voices"), true, "без входа флаги идут по одному editing.enabled — как раньше");
s.useSession(null);
assert.equal(s.current(), null);

// --- инициалы и роли -------------------------------------------------------------
assert.equal(s.initialsOf("Мария Кузнецова"), "МК");
assert.equal(s.initialsOf("ковалёв"), "КО");
assert.equal(s.initialsOf("", "petrov"), "P");
assert.equal(s.initialsOf("  Пётр   Ковалёв  "), "ПК");
assert.equal(s.roleName("admin"), "администратор");
assert.equal(s.roleName("editor"), "редактор");
assert.equal(s.roleName("x"), "x");

// --- тексты ошибок ---------------------------------------------------------------
assert.match(s.errorMessage(401, "что угодно"), /Неверный логин/);
assert.match(s.errorMessage(429), /подождите/);
assert.equal(s.errorMessage(403, "Только для сотрудников отдела"), "Только для сотрудников отдела", "текст правила доступа — с сервера");
assert.match(s.errorMessage(403), /Нет доступа/);
assert.match(s.errorMessage(503), /недоступен/);
assert.equal(s.errorMessage(500, "сломалось"), "сломалось");
assert.equal(s.errorMessage(500), "Не удалось войти");

console.log("session: ок");
