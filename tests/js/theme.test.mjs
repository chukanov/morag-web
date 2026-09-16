// Цвет ветки: карта из конфига → токены акцента на узле. Без DOM: узел — заглушка со `style`
// и `classList`, как у настоящего элемента.
import test from "node:test";
import assert from "node:assert/strict";

import { paintSection, readableInk, sectionColor, useSectionColors } from "../../web/js/ui/theme.js";

function fakeNode() {
  const props = new Map();
  const classes = new Set();
  return {
    props,
    classes,
    style: {
      setProperty: (k, v) => props.set(k, v),
      removeProperty: (k) => props.delete(k),
    },
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
  };
}

test("карта принимает только шестизначные hex-цвета; незнакомая ветка — без цвета", () => {
  useSectionColors({ alpha: "#3F7FB5", beta: " #e3b92b ", broken: "blue", nope: 12 });
  assert.equal(sectionColor("alpha"), "#3F7FB5");
  assert.equal(sectionColor("beta"), "#e3b92b");
  assert.equal(sectionColor("broken"), "");
  assert.equal(sectionColor("nope"), "");
  assert.equal(sectionColor("gamma"), "");
});

test("надпись поверх заливки: тёмная на светлом, белая на остальных", () => {
  assert.equal(readableInk("#E3B92B"), "#1A1A1A", "жёлтый: белая надпись на нём не читается");
  assert.equal(readableInk("#3F7FB5"), "#FFFFFF");
  assert.equal(readableInk("#3FA66B"), "#FFFFFF");
  assert.equal(readableInk("#E07A2F"), "#FFFFFF");
  assert.equal(readableInk("#2BB3A8"), "#FFFFFF");
  assert.equal(readableInk("#9B6BD6"), "#FFFFFF");
});

test("узел получает четыре токена акцента и класс, а ветка без цвета их снимает", () => {
  useSectionColors({ alpha: "#E07A2F" });
  const node = fakeNode();
  paintSection(node, "alpha");
  assert.equal(node.props.get("--accent"), "#E07A2F");
  assert.equal(node.props.get("--accent-fill"), "#E07A2F");
  assert.equal(node.props.get("--accent-ink"), "#FFFFFF");
  assert.match(node.props.get("--accent-wash"), /color-mix.*#E07A2F/);
  assert.ok(node.classes.has("sec"));

  // Корень читалки один на все записи: следующая запись без цвета не должна донашивать чужой.
  paintSection(node, "gamma");
  assert.equal(node.props.size, 0);
  assert.ok(!node.classes.has("sec"));
});
