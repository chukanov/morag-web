// На этом домене раньше жил Open WebUI, и у вернувшихся посетителей остался
// его service worker: он перехватывает запросы, ломится в исчезнувшие пути и
// уводит на свою страницу /error. Своего service worker у нас нет — этот файл
// существует ровно затем, чтобы вычистить чужой и самоудалиться.
//
// Браузер сам перепроверяет скрипт worker'а при заходе на сайт, поэтому лечение
// происходит без участия посетителя: один заход — и наваждение снято.

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) await caches.delete(key);
      await self.registration.unregister();
      // перезагружаем открытые вкладки — иначе они доживут со старым worker'ом
      for (const client of await self.clients.matchAll({ type: "window" })) {
        client.navigate(client.url);
      }
    })()
  );
});

// пока worker жив, ничего не перехватываем — все запросы идут в сеть напрямую
self.addEventListener("fetch", () => {});
