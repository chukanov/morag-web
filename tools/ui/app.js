// Окно загрузки записи: форма, вход, настройки — и ЖИВОЙ показ работы.
//
// Было: полоса прогресса, которая выковыривала процент регэкспом из лога и поэтому стояла на 30 %
// всю расшифровку, плюс свёрнутый лог. Стало: лента событий стадий (`/api/events`, курсор),
// буфер, кадровое воспроизведение и две сцены — волна с диаризацией и живые исправления.
//
// ⚠️ Сеть трогает ТОЛЬКО цикл опроса. Сцены получают `apply(event)` и ничего не запрашивают — без
// этого правила стенд (`bench.html`), на котором анимация настраивается по записанной трассе,
// построить нельзя.

import { $, el, reduced } from "./dom.js";
import { budget, state as showState } from "./play.js";
import { textScene } from "./text.js";
import { wave } from "./wave.js";

const T = new URLSearchParams(location.search).get("t") || "";
const api = async (path, body) => {
  const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(T);
  const r = await fetch(url, body ? {method: "POST", headers: {"Content-Type": "application/json"},
                                     body: JSON.stringify(body)} : {});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `ошибка ${r.status}`);
  return data;
};
const id = (x) => document.getElementById(x);
const size = (n) => n >= 1e9 ? (n / 1e9).toFixed(1) + " ГБ" : Math.round(n / 1e6) + " МБ";
const when = (t) => new Date(t * 1000).toLocaleDateString("ru-RU", {day: "numeric", month: "short"});

let picked = null;       // {path, name, size}
let titleTouched = false;
let lastLogLen = -1;
let videos = [];

// --- показ работы -----------------------------------------------------------------------------

const scenes = { wave: wave(id("scene-wave")), text: textScene(id("scene-text")) };
const queue = [];        // события, пришедшие, но ещё не показанные
let cursor = 0;
let noEvents = false;    // старый адаптер/сервер без ленты — падаем обратно на лог
let raf = null;
let last = 0;
const show = { stage: "", done: [], counter: null, lastAt: 0, error: "" };

function dispatch(e) {
  scenes.wave.apply(e);
  scenes.text.apply(e);
  show.lastAt = performance.now() / 1000;
  if (e.t === "stage.start") { show.stage = e.stage; show.counter = null; }
  if (e.t === "stage.end" && !show.done.includes(e.stage)) show.done.push(e.stage);
  if (e.t === "chunk.done") show.counter = { i: e.i, n: show.counter?.n || 0 };
  if (e.t === "chunk.start") show.counter = { i: e.i, n: e.n };
  if (e.t === "turn.done") show.counter = { i: e.done, n: e.n };
  if (e.t === "client.step") show.stage = e.step;
}

function drain(now) {
  raf = null;
  const dt = last ? Math.min(0.25, (now - last) / 1000) : 0.016;
  last = now;
  let n = budget(queue.length, dt, { reduced: reduced() });
  while (n-- > 0 && queue.length) dispatch(queue.shift());
  paintState();
  if (queue.length) raf = requestAnimationFrame(drain);
  else last = 0;
}

function pump() {
  // ⚠️ Цикл живёт только пока есть что показывать — та же дисциплина, что у плеера сайта: на
  // паузе кадры не просят вовсе, иначе окно греет батарею коллеги все двадцать минут.
  if (raf === null && queue.length) raf = requestAnimationFrame(drain);
}

function paintState() {
  const s = showState({ ...show, now: performance.now() / 1000 });
  const bar = id("bar");
  if (s.pct != null) bar.firstElementChild.style.width = `${Math.min(100, s.pct)}%`;
  bar.classList.toggle("err", s.mood === "ошибка");
  const line = id("work-mood");
  line.textContent = s.say;
  line.className = "mood " + s.mood;
}

async function pollEvents() {
  if (noEvents) return;
  try {
    const r = await api(`/api/events?since=${cursor}`);
    cursor = r.cursor ?? cursor;
    for (const e of r.events || []) queue.push(e);
    pump();
  } catch (err) {
    // 404 — сервер без ленты (разъезд версий). Не шумим: лог на месте, показ просто беднее.
    if (String(err.message).includes("404")) noEvents = true;
  }
}

// --- форма (порт прежнего окна, поведение не менялось) ----------------------------------------

function pick(v) {
  picked = v;
  const drop = id("drop");
  drop.classList.add("has");
  drop.replaceChildren(el("div", { class: "file" },
    el("span", { class: "name", text: v.name }),
    el("span", { class: "meta", text: v.size ? size(v.size) : "" }),
    el("button", { class: "ghost", style: "margin-left:auto",
                   onclick: (e) => { e.stopPropagation(); picked = null; resetDrop(); } }, "другой файл")));
  id("fields").hidden = false;
  id("recent").hidden = true;
  if (!titleTouched) {
    // Название из имени файла — подсказка. Сервер знает, что оно подставлено, и заменит
    // придуманным по расшифровке, если человек не станет править (title_auto).
    id("title").value = v.name.replace(/\.[^.]+$/, "").replace(/[_]+/g, " ").trim();
  }
  if (!id("date").value) {
    const d = v.mtime ? new Date(v.mtime * 1000) : new Date();
    id("date").value = d.toISOString().slice(0, 10);
  }
}

function resetDrop() {
  const drop = id("drop");
  drop.classList.remove("has");
  drop.replaceChildren(
    el("p", { class: "big", text: "Перетащите сюда запись" }),
    el("p", { class: "hint", text: "или выберите из недавних файлов ниже · mp4, mov, webm, mkv" }));
  id("fields").hidden = true;
  id("recent").hidden = false;
}

function renderRecent(list) {
  videos = list;
  id("recent").replaceChildren(...list.slice(0, 8).map((v) =>
    el("button", { onclick: () => pick(v) },
       el("b", { text: v.name }),
       el("i", { text: `${v.folder} · ${size(v.size)} · ${when(v.mtime)}` }))));
}

/** Нативное окно отдаёт сюда путь брошенного файла (tools/upload_app.py). */
window.dropVideo = (path) => {
  const known = videos.find((v) => v.path === path);
  pick(known || {path, name: path.split("/").pop(), size: 0, mtime: Date.now() / 1000});
};

const drop = id("drop");
["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => {
  ev.preventDefault(); drop.classList.add("hot");
}));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("hot")));
drop.addEventListener("drop", (ev) => {
  ev.preventDefault();
  const f = ev.dataTransfer.files[0];
  if (!f) return;
  // В браузере пути нет, но файл обычно лежит в тех же папках — ищем по имени и размеру.
  const hit = videos.find((v) => v.name === f.name && Math.abs(v.size - f.size) < 2);
  if (hit) pick(hit);
  else id("msg").textContent = `не нашёл «${f.name}» в Загрузках, на Рабочем столе и в Movies — выберите из списка`;
});
drop.addEventListener("click", () => { if (!picked && videos.length) id("recent").scrollIntoView({block: "nearest"}); });

id("title").addEventListener("input", () => { titleTouched = true; });

// --- состояние окна ---------------------------------------------------------------------------

async function tick() {
  let s;
  try { s = await api("/api/state"); } catch (e) { id("env").textContent = e.message; return; }
  const site = s.site || {};
  const logged = Boolean(site.logged && site.site);
  id("login").hidden = logged;
  id("form").hidden = !logged || (s.job.stage === "running" || s.job.stage === "done");
  if (!id("site").value) id("site").value = site.site || "";

  const events = site.events || [];
  const sel = id("event");
  if (sel.options.length !== events.length + 1) {
    // Заглушка видна (иначе непонятно, что поле не заполнено), но выбрать её нельзя: сервер
    // без рубрики запись не примет — она решает ветку и год.
    const empty = new Option("— выберите рубрику —", "");
    empty.disabled = true;
    sel.replaceChildren(empty, ...events.map((e) => new Option(e, e)));
  }
  if (!picked) renderRecent(s.videos || []);

  const job = s.job || {};
  id("work").hidden = job.stage !== "running" && job.stage !== "error";
  id("ready").hidden = job.stage !== "done";
  if (s.log && s.log.length !== lastLogLen) {
    lastLogLen = s.log.length;
    id("log").textContent = s.log.join("\n");
    id("log").scrollTop = id("log").scrollHeight;
  }
  id("go").disabled = job.stage === "running";
  if (job.stage === "running") scenes.wave.fit();

  show.error = job.stage === "error" ? `Не получилось: ${job.error}` : "";
  if (job.stage === "running" || job.stage === "error") paintState();

  if (job.stage === "running") {
    const min = Math.max(1, Math.round((Date.now() / 1000 - job.started) / 60));
    id("work-msg").textContent = `Идёт ${min} мин. Окно можно свернуть — работа не прервётся, но закрывать его нельзя.`;
    id("work-msg").className = "msg";
  } else if (job.stage === "error") {
    id("work-msg").textContent = "";
    id("work").hidden = false;
    id("form").hidden = false;
  } else if (job.stage === "done") {
    id("ready-msg").textContent = "Расшифровка, слайды и поля уже на сайте — запись читается и играет. "
      + (job.search === "later" ? "В поиске она появится после ближайшей плановой индексации. " : "")
      + "Голоса подписываются там же, в режиме правки.";
    id("open-record").onclick = () => { if (job.url) window.open(job.url, "_blank"); };
  }

  id("env").innerHTML = [
    site.site ? `сайт ${site.site}${site.who ? ` — ${site.who}` : ""}` : "сайт не выбран",
    s.stack ? '<span class="ok">стек транскрибации поднят</span>' : "стек погашен — поднимется сам",
    llmLine(s.llm),
    `рабочая папка ${s.home}`,
    site.error ? `<span class="bad">сайт не отвечает: ${site.error}</span>` : "",
    site.note ? `<span class="bad">${site.note}</span>` : "",
  ].filter(Boolean).join(" · ");
  // Настройки сами раскрываются только если ходить в шлюз нечем: это единственное, что человек
  // обязан сделать руками, — и то лишь когда сайт не умеет ходить за него.
  if (!s.llm?.ready && !id("settings").open && job.stage === "idle") id("settings").open = true;
  id("llm-site").hidden = !!s.llm?.via_site;
  id("llm-own").hidden = !s.llm?.via_site;
  if (!id("llm-msg").dataset.touched) {
    id("llm-msg").textContent = s.llm?.via_site
      ? "Ключ не нужен: стадии с ИИ идут через сайт, от вашего имени. Работает, пока вы залогинены."
      : (s.llm?.ready ? "Стадии с ИИ идут вашим ключом напрямую в шлюз."
                      : "Войдите на сайт — и ключ не понадобится: ИИ-стадии пойдут через него.");
  }
}

/** Чем ходим в шлюз — одной строкой в состоянии. */
function llmLine(llm) {
  if (!llm) return "";
  if (llm.via_site) return "ИИ-стадии через сайт (ключ не нужен)";
  if (llm.ready) return "ИИ-стадии своим ключом";
  return '<span class="bad">ИИ-стадии некуда отправить: войдите на сайт</span>';
}

id("do-login").onclick = async () => {
  id("login-msg").textContent = "…";
  id("login-msg").className = "msg";
  try {
    await api("/api/login", {site: id("site").value.trim(), login: id("login-name").value.trim(),
                             password: id("password").value});
    id("password").value = "";
    id("login-msg").textContent = "";
    await tick();
  } catch (e) { id("login-msg").textContent = e.message; id("login-msg").className = "msg bad"; }
};

id("llm-own").onclick = () => { id("key-box").hidden = false; id("key").focus(); };
id("llm-site").onclick = async () => {
  const msg = id("llm-msg");
  msg.dataset.touched = "1";
  msg.textContent = "настраиваю…";
  msg.className = "msg";
  try {
    const r = await api("/api/llm", {});
    msg.textContent = r.via_site
      ? (r.checked ? "Готово: ИИ-стадии идут через сайт." : "Настроено, но сайт не ответил на проверку.")
      : "Этот сайт не умеет ходить в шлюз за вас — нужен свой ключ.";
    msg.className = r.via_site ? "msg ok" : "msg bad";
    id("key-box").hidden = !!r.via_site;
  } catch (e) { msg.textContent = e.message; msg.className = "msg bad"; }
};

id("save-key").onclick = async () => {
  id("key-msg").textContent = "проверяю…";
  id("key-msg").className = "msg";
  try {
    const r = await api("/api/key", {key: id("key").value.trim()});
    id("key").value = "";
    id("key-msg").textContent = r.checked ? "ключ работает" : "сохранено";
    id("key-msg").className = "msg ok";
    await tick();
  } catch (e) { id("key-msg").textContent = e.message; id("key-msg").className = "msg bad"; }
};

id("go").onclick = async () => {
  id("msg").textContent = "";
  id("msg").className = "msg";
  if (!picked) { id("msg").textContent = "сначала перетащите видео"; id("msg").className = "msg bad"; return; }
  try {
    await api("/api/start", {
      video: picked.path, title: id("title").value.trim(), date: id("date").value,
      event: id("event").value, speakers: id("speakers").value, tags: id("tags").value,
      summary: id("summary").value, slides: id("slides").value.trim(),
      no_screen: !id("screen").checked, stack: true, title_auto: !titleTouched,
    });
    lastLogLen = -1;
    cursor = 0;
    queue.length = 0;
    show.stage = ""; show.done = []; show.counter = null; show.error = "";
    show.lastAt = performance.now() / 1000;
    scenes.wave.reset();
    scenes.text.reset();
    await tick();
  } catch (e) { id("msg").textContent = e.message; id("msg").className = "msg bad"; }
};

id("again").onclick = async () => {
  await api("/api/reset", {});
  picked = null; titleTouched = false; lastLogLen = -1;
  ["title", "speakers", "summary", "tags", "slides"].forEach((x) => { id(x).value = ""; });
  resetDrop();
  await tick();
};

id("theme").onclick = () => {
  const now = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", now);
  try { localStorage.setItem("morag-upload-theme", now); } catch { /* приватное окно — не беда */ }
};
try {
  const saved = localStorage.getItem("morag-upload-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
} catch { /* тоже не беда */ }

window.addEventListener("resize", () => scenes.wave.fit());
scenes.wave.fit();
tick();
setInterval(tick, 2000);
setInterval(pollEvents, 1000);
// Подпись обновляется и без событий: «считает» и «молчит» это про ТИШИНУ, её надо чем-то мерить.
setInterval(() => { if (!id("work").hidden) paintState(); }, 1000);
