// Витрина: с чего начинается сайт из нескольких пространств.
//
// Пространство — не «раздел списка», а самостоятельный сайт: свой поиск, своя база, свой
// префикс в адресе, свой акцент. Поэтому карточка ведёт ОБЫЧНОЙ ссылкой, а не переключением
// экрана: конфиг, индекс записей, проигрыватель и история чата у каждого пространства свои, и
// склеивать их в одном документе — источник тихих ошибок вида «показал записи соседа».
import { TITLE_FALLBACK } from "./ui/brand.js";
import { el, countOf } from "./ui/dom.js";
import { applyThemeTokens } from "./ui/theme.js";

function hours(value) {
  if (!value) return "";
  // Часы округляем до целых: «61 час» читается, «61.2 часа» — уже отчёт.
  const whole = Math.round(value);
  return whole ? countOf(whole, "час", "часа", "часов") : "";
}

export function renderHub(data, { mount }) {
  const hub = data.hub || {};
  applyThemeTokens(hub.theme || {});
  document.title = hub.title || TITLE_FALLBACK;

  const title = document.querySelector("#hub-title");
  if (title) title.textContent = hub.title || TITLE_FALLBACK;
  const tagline = document.querySelector("#hub-tagline");
  const total = (data.corpora || []).reduce((sum, s) => sum + (s.records_count || 0), 0);
  if (tagline) tagline.textContent = withCount(hub.tagline || "", total);
  const about = document.querySelector("#hub-about");
  // Разметка как есть: текст пишет владелец в hub.yml — файл в git, доверенный наравне с кодом.
  if (about && hub.about) about.innerHTML = withCount(hub.about, total);

  mount.replaceChildren(
    ...(data.corpora || []).map((space) =>
      el(
        "a",
        {
          class: "space-card",
          href: `/${encodeURIComponent(space.slug)}`,
          // Акцент пространства — прямо на карточке: у витрины он и есть главный опознавательный
          // знак, а внутри пространства тем же цветом покрашен весь интерфейс.
          style: space.accent ? `--card-accent:${space.accent}` : "",
        },
        el("span", { class: "space-name", text: space.title || space.slug }),
        el("span", {
          class: "space-meta",
          text: [
            countOf(space.records_count || 0, "запись", "записи", "записей"),
            hours(space.hours),
          ].filter(Boolean).join(" · "),
        }),
        el("span", { class: "space-tagline", text: withCount(space.tagline || "", space.records_count) }),
        // Пространство без поиска говорит об этом на витрине: иначе отсутствие кнопки
        // «Спросить» внутри выглядит поломкой, а не решением.
        space.chat_enabled ? null : el("span", { class: "space-note", text: "без поиска" })
      )
    )
  );
}

/** Те же плейсхолдеры, что и у пространства: число в конфиге писать нельзя — устареет. */
function withCount(text, count) {
  if (!text) return text;
  const gen = count ? countOf(count, "записи", "записей", "записей") : "записей";
  const nom = count ? countOf(count, "запись", "записи", "записей") : "записей";
  return text.replace(/\{записей-род\}/g, gen).replace(/\{записей\}/g, nom);
}
