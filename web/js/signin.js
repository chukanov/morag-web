// Форма входа: `/signin`. Своя, а не 401-челлендж браузера — прокси-помощники у коллег
// отвечают на челлендж паролем от прокси и уводят вход в цикл (deploy/server/README.md).
//
// Сюда приводит сервер (страницу без сессии он перенаправляет сам, с `next=`) или api.js
// (401 из уже открытого приложения). После входа — полная перезагрузка на `next`: конфиг,
// корпус и права читаются с нуля, как при переходе между пространствами.
import { $ } from "./ui/dom.js";
import { getAuthState, login } from "./api.js";
import { nextFrom, errorMessage } from "./session.js";
import { showMark } from "./ui/logo.js";

export async function renderSignin() {
  // Сторож в index.html считает приложение мёртвым, если экран не назван за 4 с: назовём
  // сразу, ещё до ответа сервера.
  document.body.setAttribute("data-view", "signin");
  const form = $("#signin-form");
  const error = $("#signin-error");
  const title = $("#signin-title");
  if (!form) return;

  let state = null;
  try {
    state = await getAuthState();
  } catch {
    // сервер не ответил — форму всё равно показываем, ошибку скажет отправка
  }
  // Дом сайта — пространство по умолчанию (`/demo`), не корень домена: там может жить другой сайт.
  const home = state?.home || "/";
  if (state && !state.enabled) {
    location.replace(home);
    return;
  }
  if (state?.logged_in) {
    location.replace(nextFrom(location.search, home));
    return;
  }
  if (state?.title && title) {
    title.textContent = state.title;
    document.title = `Вход · ${state.title}`;
  }
  showMark(state?.brand || {});
  // Подпись под полем логина: у каталога — доменная учётка, у одних локальных — просто логин.
  const hint = $("#signin-hint");
  if (hint) hint.textContent = state?.providers?.includes("ldap") ? "Доменная учётная запись" : "";

  if (form.dataset.bound) return; // роутер может показать экран повторно — обработчик один
  form.dataset.bound = "1";
  const user = $("#signin-login");
  const pass = $("#signin-password");
  const button = form.querySelector("button[type=submit]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (error) error.hidden = true;
    const who = user.value.trim();
    if (!who || !pass.value) {
      (who ? pass : user).focus();
      return;
    }
    button.disabled = true;
    try {
      await login(who, pass.value);
      location.assign(nextFrom(location.search, home));
    } catch (failure) {
      if (error) {
        error.textContent = errorMessage(failure.status, failure.message);
        error.hidden = false;
      }
      pass.value = "";
      pass.focus();
    } finally {
      button.disabled = false;
    }
  });
  user.focus();
}
