// Воспроизведение ленты событий и ЧЕСТНОСТЬ подписи. Обе вещи — чистые функции, поэтому их
// можно проверить тестом и покрутить на стенде без единого прогона расшифровки.
//
// Зачем вообще притормаживать: события приходят ПАЧКАМИ. Финал-раунд считает шесть реплик разом
// (плюс добивочный проход), пасс-2 отдаёт примерно кусок в секунду. Если валить всё в DOM по
// приходу, картинка будет дёргаться: то ничего, то двенадцать карточек за кадр. Поэтому буфер и
// темп — но темп только РАСПРЕДЕЛЯЕТ то, что уже случилось, и ничего не придумывает.

export const TARGET_SEC = 1.5;   // за сколько стараемся разобрать накопившееся
export const MIN_RATE = 6;       // событий в секунду, когда их мало: живо, но читаемо
export const MAX_RATE = 120;     // потолок: пачка в 500 событий разберётся за 4 с, а не за минуту

/** Сколько событий выпустить за кадр длиной `dt` при очереди `queued`. */
export function budget(queued, dt, { reduced = false } = {}) {
  if (reduced || queued <= 0) return queued;          // «меньше движения» — сразу всё
  const rate = Math.min(MAX_RATE, Math.max(MIN_RATE, queued / TARGET_SEC));
  return Math.max(1, Math.ceil(rate * dt));
}

// --- честность --------------------------------------------------------------------------------
// ⚠️ Полоса двигается ТОЛЬКО по настоящему счётчику или по концу стадии. Никакой интерполяции
// «чтобы не стояло»: прежняя полоса как раз и врала — она замерла на 30 % на всю расшифровку,
// потому что искала в логе процент, которого адаптер не присылает.

export const SILENT_AFTER = 3;    // столько секунд тишины — и мы уже не говорим «идёт»
export const LOST_AFTER = 45;     // столько — и честно говорим, что не понимаем, что происходит

// Замерено на живой записи 50 минут: сколько какая стадия занимает. Нужно не для полосы, а чтобы
// в тишине сказать «обычно около минуты» вместо спиннера.
export const STAGE = {
  diarize: { w: 11, say: "делю по голосам", typical: 120 },
  pass1: { w: 6, say: "слушаю целиком — черновик", typical: 60 },
  glossary: { w: 6, say: "собираю термины", typical: 60 },
  pass2: { w: 17, say: "распознаю по кускам", typical: 180 },
  "final-round": { w: 40, say: "правлю сущности", typical: 570 },
  speakers: { w: 2, say: "узнаю голоса", typical: 10 },
  naming: { w: 1, say: "ищу имена", typical: 10 },
  align: { w: 4, say: "расставляю слова по времени", typical: 45 },
};
const TOTAL = Object.values(STAGE).reduce((s, x) => s + x.w, 0);

/**
 * Состояние показа по событиям. `now` инжектируется — иначе ни тест, ни стенд не построить.
 * Возвращает `{pct, mood, say}`, где mood ∈ работает | считает | молчит.
 */
export function state({ stage = "", done = [], counter = null, lastAt = 0, now = 0, error = "" }) {
  if (error) return { pct: null, mood: "ошибка", say: error };

  let pct = done.reduce((s, name) => s + (STAGE[name]?.w || 0), 0);
  const cur = STAGE[stage];
  if (cur && counter && counter.n > 0) pct += cur.w * Math.min(1, counter.i / counter.n);
  pct = Math.round((pct / TOTAL) * 100);

  const quiet = now - lastAt;
  const label = cur ? cur.say : (stage || "готовлюсь");
  if (quiet <= SILENT_AFTER) {
    const tail = counter && counter.n ? ` · ${counter.i} из ${counter.n}` : "";
    return { pct, mood: "работает", say: label + tail };
  }
  if (quiet < LOST_AFTER && cur) {
    // Стадия молчит по своей природе: пасс-1 — один блокирующий вызов, глоссарий — два запроса
    // к модели. Говорим, сколько уже идёт и сколько обычно занимает, а не крутим спиннер.
    return { pct, mood: "считает",
             say: `${label} · ${Math.round(quiet)} с, обычно около ${human(cur.typical)}` };
  }
  return { pct, mood: "молчит",
           say: `${Math.round(quiet)} с без вестей${stage ? ` (стадия «${label}»)` : ""} — `
                + "работа, вероятно, идёт" };
}

function human(sec) {
  if (sec >= 90) return `${Math.round(sec / 60)} мин`;
  return `${sec} с`;
}
