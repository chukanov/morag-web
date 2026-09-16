// Верстак имён: кто говорит в корпусе и как это исправить.
//
// Почему отдельная страница, а не только правка в читалке: голосов в корпусе сотни, названы
// единицы, и по эфиру они распределены крайне неравно — верхняя треть списка закрывает большую
// часть речи. Из читалки не видно, какой голос важнее; здесь видно сразу, и час работы даёт
// несоизмеримо больше, чем случайные правки по ходу чтения.
//
// Правка идёт в СЛОВАРЬ, а не «на отображение»: имя доезжает до текста расшифровки, до шапки
// записи и оттуда в поиск. Пустое имя снимает правку — сайдкар остался сырым, поэтому запись
// вернётся к исходной метке.
import { el, countOf, toast } from "./ui/dom.js";
import { getVoices, getVoicesQueue, renameVoice } from "./api.js";
import { current } from "./session.js";

const FROM = { tag: "метка", slide: "слайд", post: "пост", owner: "владелец", corpus: "корпус" };

let loaded = null;
let poll = null;

const hours = (sec) => (sec >= 3600 ? `${(sec / 3600).toFixed(1)} ч` : `${Math.round(sec / 60)} мин`);

export async function renderVoices(focusId) {
  const list = document.querySelector("#voices-list");
  if (!list) return;
  if (!loaded) {
    list.replaceChildren(el("p", { class: "rec-sub", text: "Читаю голоса…" }));
    loaded = await getVoices();
  }
  const { voices = [], vocabulary = [], record_space: where = {}, editing, ready, hint } = loaded;

  const head = document.querySelector("#voices-head");
  if (head) {
    const named = voices.filter((v) => v.name).length;
    head.textContent = ready
      ? `${countOf(voices.length, "голос", "голоса", "голосов")} · названы ${named}`
      : hint || "снимок не собран";
  }
  const note = document.querySelector("#voices-note");
  // Кнопки нет — говорим почему. Молчаливо отключённая правка читается как поломка.
  // Причина зависит от того, есть ли вход: вошедшему без права — «только администраторам»,
  // без входа — прежнее «включается в конфиге на машине с корпусом».
  if (note) {
    note.hidden = Boolean(editing);
    if (!editing && current()?.login) {
      note.textContent = "Имена голосов правят только администраторы сайта: имя действует на " +
        "все записи, где звучит голос. Смотреть можно всем.";
    }
  }

  // Словарь имён корпуса — в общий datalist: он один на все поля ввода.
  const dl = document.querySelector("#voice-names");
  if (dl) dl.replaceChildren(...vocabulary.map((n) => el("option", { value: n })));

  list.replaceChildren(...voices.map((v) => row(v, { editing, where })));
  if (focusId) {
    const node = list.querySelector(`[data-voice="${CSS.escape(focusId)}"]`);
    if (node) {
      // ⚠️ У <details> раскрытие — это АТРИБУТ, а не класс: с классом голос был бы найден,
      // но остался бы свёрнутым, и переход по ссылке выглядел бы как «просто открылся список».
      node.open = true;
      node.scrollIntoView({ block: "center" });
      node.classList.add("focused");
    } else {
      toast("такого голоса нет в снимке — пересоберите его");
    }
  }
}

function row(v, { editing, where }) {
  const node = el("details", { class: "voice", "data-voice": v.id });
  const meta = [
    hours(v.sec),
    countOf(v.records.length, "запись", "записи", "записей"),
    v.spaces.join(", "),
  ].filter(Boolean).join(" · ");

  node.append(
    el(
      "summary",
      { class: "voice-head" },
      el("span", { class: "voice-id", text: v.id }),
      el("span", { class: "voice-meta", text: meta }),
      // Разовый голос из зала именем не подписывают: реестр иначе распухнет людьми,
      // которых больше никогда не будет.
      v.solo ? el("span", { class: "voice-tag", text: "разовый" }) : null,
      // Голос представляется разными именами — вероятно, реестр склеил двоих. Это просьба
      // послушать, а не приговор: расшифровка могла и переврать имя.
      v.conflict?.length ? el("span", { class: "voice-tag clash", text: "два имени?" }) : null,
      el("span", { class: v.name ? "voice-name on" : "voice-name", text: v.name || "без имени" })
    ),
    body(v, { editing, where })
  );
  return node;
}

function body(v, { editing, where }) {
  const box = el("div", { class: "voice-body" });

  // 0. Тревога о склейке — раньше всего остального: если голос это два человека, то и общее
  // имя, и кандидаты из меты для него бессмысленны, и знать об этом надо ДО того, как назовёшь.
  if (v.conflict?.length) {
    box.append(el(
      "p",
      { class: "voice-clash" },
      el("b", { text: `Представляется по-разному: ${v.conflict.join(", ")}. ` }),
      "Похоже, реестр голосов склеил двух человек — послушайте, прежде чем называть. " +
      "Правильное лечение склейки в реестре, а не в словаре имён: подписав такой голос " +
      "во всём корпусе, вы подпишете обоих, а следующая запись снова придёт склеенной."
    ));
  }

  // 1. Самопредставление — самый надёжный источник: человек называет себя сам.
  if (v.intros?.length) {
    box.append(el("h4", { text: "Представляется сам" }));
    for (const intro of v.intros) {
      const at = el("button", {
        class: "voice-at",
        text: `${intro.record} · ${Math.floor(intro.sec / 60)}:${String(Math.round(intro.sec) % 60).padStart(2, "0")}`,
      });
      // Полная перезагрузка: читалка живёт внутри пространства, а верстак — вне его.
      // Адрес раздела берём из карты снимка, иначе ссылка была бы битой.
      at.addEventListener("click", () => {
        const slug = where[intro.record];
        if (!slug) return toast("не знаю, в каком разделе эта запись — пересоберите снимок");
        location.assign(`/${encodeURIComponent(slug)}/rec/${encodeURIComponent(intro.record)}/${Math.round(intro.sec)}`);
      });
      box.append(el("p", { class: "voice-intro" }, el("q", { text: intro.raw }), at));
    }
    // ⚠️ Текст СЫРОЙ, до правок модели. Финал-раунд умеет дочинить обрывок до правдоподобного
    // имени, которого в звуке не было — на этом уже обожглись однажды.
    box.append(el("p", { class: "voice-warn", text: "Текст сырой, до правок модели — верить можно ему, а не расшифровке." }));
  }

  // 2. Кандидаты из метаданных тех записей, где голос звучит.
  if (v.candidates?.length) {
    box.append(el("h4", { text: "Из метаданных записей" }));
    box.append(el(
      "div",
      { class: "voice-cands" },
      ...v.candidates.slice(0, 12).map((c) => {
        const chip = el("button", {
          class: "voice-cand",
          text: `${c.name} · ${c.votes} · ${FROM[c.from] || c.from}`,
          title: `${c.name}: встречается в ${c.votes} записях этого голоса, источник — ${FROM[c.from] || c.from}`,
        });
        chip.addEventListener("click", () => {
          const input = box.querySelector("input.voice-input");
          if (input) { input.value = c.name; input.focus(); }
        });
        return chip;
      })
    ));
  }

  // 3. Свободный ввод с автодополнением по всем именам корпуса.
  const input = el("input", {
    class: "voice-input", list: "voice-names", placeholder: "Имя и фамилия",
    value: v.name || "", autocomplete: "off",
  });
  // Область правки. По умолчанию — весь корпус, и так и должно быть: голос ОБЩИЙ, ради этого
  // всё и затевалось. Адресная правка нужна ровно в одном случае — реестр склеил двоих, — и она
  // не лечение, а заплатка: следующая запись с тем же голосом снова будет подписана не тем.
  const scope = el("select", {
    class: "voice-scope",
    title: "Обычно имя ставится во всём корпусе. Отдельная запись — заплатка на случай, " +
           "когда реестр склеил двух людей.",
  },
    el("option", { value: "", text: "во всём корпусе" }),
    ...v.records.map((r) => el("option", { value: r, text: `заплатка: только ${r}` })));
  const save = el("button", { class: "voice-save", text: "Сохранить" });
  const drop = el("button", { class: "voice-drop", text: "Убрать имя" });
  const status = el("span", { class: "voice-status" });

  const apply = async (name) => {
    save.disabled = drop.disabled = true;
    status.textContent = "сохраняю…";
    try {
      const out = await renameVoice(v.id, { name, record: scope.value });
      v.name = name;
      status.textContent = out.records.length
        ? `пересобираю ${countOf(out.records.length, "запись", "записи", "записей")}…`
        : "записей не нашлось";
      watchQueue(status);
      const label = document.querySelector(`[data-voice="${CSS.escape(v.id)}"] .voice-name`);
      if (label) { label.textContent = name || "без имени"; label.classList.toggle("on", Boolean(name)); }
    } catch (error) {
      status.textContent = error.message;
      toast(error.message);
    } finally {
      save.disabled = drop.disabled = false;
    }
  };
  save.addEventListener("click", () => apply(input.value.trim()));
  drop.addEventListener("click", () => { input.value = ""; apply(""); });

  const form = el("div", { class: "voice-form" }, input, scope, save, drop, status);
  if (!editing) {
    for (const control of [input, scope, save, drop]) control.disabled = true;
  }
  box.append(form);
  return box;
}

/** Пока очередь не опустела — правка ещё не доехала до файлов, и об этом надо говорить. */
function watchQueue(status) {
  clearInterval(poll);
  poll = setInterval(async () => {
    const q = await getVoicesQueue().catch(() => null);
    if (!q) return;
    if (!q.pending && !q.current) {
      clearInterval(poll);
      status.textContent = q.failed?.length ? `сорвалось: ${q.failed.join(", ")}` : "готово";
      return;
    }
    status.textContent = `пересобираю: осталось ${q.pending + (q.current ? 1 : 0)}`;
  }, 700);
}
