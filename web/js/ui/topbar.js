// Высота шапки — В ПЕРЕМЕННУЮ СТИЛЕЙ, а не константой в двух местах. Липкие блоки под ней
// обязаны отступать НА ФАКТ: замерено 10.09 — со знаком-рисунком шапка выросла до 71px, а в
// CSS и в читалке стояло 56, и кадр с пультом молча заезжали под неё на 15 пикселей.
export function measureTopbar() {
  const h = document.querySelector(".topbar")?.getBoundingClientRect().height;
  if (h) document.documentElement.style.setProperty("--topbar", `${Math.round(h)}px`);
}
