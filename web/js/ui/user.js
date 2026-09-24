// Кто вошёл — кружок в шапке и меню: имя, должность · отдел, роль, «Выйти».
//
// Тот же попап, что у прошлых разговоров (`.dialogs`): закрывается кликом снаружи и Esc.
// Кружок — фото из каталога, если оно есть, иначе инициалы; фото отдаёт BFF по сессии
// (`/api/auth/me/photo`), поэтому в разметку адрес не зашит.
import { $, el } from "./dom.js";
import { logout } from "../api.js";
import { initialsOf, roleName, loginPath, homePath } from "../session.js";

export function mountUserMenu(me) {
  const wrap = $("#user-wrap");
  const btn = $("#user-btn");
  const menu = $("#user-menu");
  if (!wrap || !btn || !menu) return;
  if (!me?.login) {
    wrap.hidden = true; // вход выключен или не спрашивали — кружка нет вовсе
    return;
  }
  wrap.hidden = false;
  btn.title = me.name || me.login;
  btn.setAttribute("aria-label", `Вы вошли как ${me.name || me.login}`);
  const initials = el("span", { class: "user-initials", text: initialsOf(me.name, me.login) });
  btn.replaceChildren(initials);
  if (me.photo) {
    const img = el("img", { class: "user-photo", src: "/api/auth/me/photo", alt: "" });
    // Фото не отдалось — остаются инициалы, а не сломанная картинка.
    img.addEventListener("error", () => img.remove());
    btn.append(img);
  }

  const sub = [me.title, me.department].filter(Boolean).join(" · ");
  const out = el("button", { class: "d-item u-out", type: "button", text: "Выйти" });
  out.addEventListener("click", async () => {
    try {
      await logout();
    } finally {
      // На форму с возвратом ДОМОЙ: без `next` вход вёл бы на корень домена (там может быть чужой сайт).
      location.assign(loginPath(homePath()));
    }
  });
  // Своя запись — только тем, кому можно грузить: у остальных пункт был бы дорогой в никуда
  // (страница спросила бы у сервера зеркало и получила 403). `data-go` ведёт роутером, без
  // перезагрузки, как кнопка календаря.
  const upload = me.can?.upload
    ? el("button", { class: "d-item", type: "button", text: "Загрузить свою запись", "data-go": "upload" })
    : null;
  // ⚠️ Нативный replaceChildren null не пропускает — рисует текст «null».
  menu.replaceChildren(
    ...[
      el("div", { class: "u-name", text: me.name || me.login }),
      sub ? el("div", { class: "u-sub", text: sub }) : null,
      el("div", { class: "u-role", text: `${me.login} · ${roleName(me.role)}` }),
      upload,
      out,
    ].filter(Boolean)
  );

  const show = (on) => {
    menu.hidden = !on;
    btn.setAttribute("aria-expanded", String(on));
  };
  // Клик внутри обёртки меню не закрывает (сторож снаружи), а уехав на другой экран с открытым
  // меню, человек возвращается к висящему попапу — закрываем сами.
  upload?.addEventListener("click", () => show(false));
  btn.addEventListener("click", () => show(menu.hidden));
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !event.target.closest("#user-wrap")) show(false);
  });
  addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) show(false);
  });
}
