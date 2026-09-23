#!/usr/bin/env python3
"""Окно приложения: страница загрузки внутри нативного окна, файл — перетаскиванием.

    python3 tools/ingest.py app          # окно (или браузер, если нет PyObjC)

Зачем окно, если есть страница в браузере. Тому, кто выкладывает свой доклад раз в месяц, вкладка
с локальным адресом и токеном в строке — лишний слой: её теряют среди других вкладок, случайно
закрывают, а перетащить в неё файл нельзя (браузер даёт имя файла, но не путь, и гигабайты
пришлось бы копировать через localhost). Окно решает ровно это: живёт отдельно, файл берётся с
диска по-настоящему.

Как устроено: локальный сервер (`ingest_ui`) поднимается в потоке, окно показывает его страницу
через `WKWebView`, а перетаскивание ловится НАТИВНО и путь отдаётся странице вызовом JS.

⚠️ Перетаскивание перехватывается у веб-вида, а не у окна: WebKit регистрируется на файловые
типы сам и, если не вмешаться, попытается ОТКРЫТЬ брошенный файл вместо страницы (видео на пол-
экрана вместо формы). Поэтому свой подкласс `WKWebView` объявляет себя приёмником первым.

⚠️ Окно живёт, пока жив процесс: закрытие во время работы спрашивает подтверждение, иначе
расшифровка оборвётся на середине и человек об этом не узнает.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ingest_ui  # noqa: E402

TITLE = "Загрузить запись"
WIDTH, HEIGHT = 980, 720


def available() -> bool:
    """Есть ли чем рисовать окно. Нет — зовущий откроет браузер (ingest.py::cmd_app)."""
    try:
        import Cocoa  # noqa: F401, PLC0415
        import WebKit  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001 — на не-маке это ImportError, в урезанном питоне бывает и другое
        return False
    return True


def run(port: int = 8099) -> int:
    import objc
    from Cocoa import (NSApplication, NSApplicationActivationPolicyRegular, NSAlert, NSBackingStoreBuffered,
                       NSMakeRect, NSObject, NSURL, NSURLRequest, NSWindow, NSWindowStyleMaskClosable,
                       NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
                       NSPasteboardTypeFileURL)
    from WebKit import WKWebView, WKWebViewConfiguration

    server, url = ingest_ui.start_server(port)

    class DropWebView(WKWebView):
        """Веб-вид, который сам принимает файлы: путь уходит в страницу, а не открывается в окне."""

        def initWithFrame_configuration_(self, frame, config):  # noqa: N802 — имя из Cocoa
            self = objc.super(DropWebView, self).initWithFrame_configuration_(frame, config)
            if self is not None:
                self.registerForDraggedTypes_([NSPasteboardTypeFileURL])
            return self

        def draggingEntered_(self, sender):  # noqa: N802
            return 1 if self._paths_(sender) else 0   # NSDragOperationCopy / None

        def draggingUpdated_(self, sender):  # noqa: N802
            return 1 if self._paths_(sender) else 0

        def prepareForDragOperation_(self, sender):  # noqa: N802
            return bool(self._paths_(sender))

        def performDragOperation_(self, sender):  # noqa: N802
            paths = self._paths_(sender)
            if not paths:
                return False
            js = f"window.dropVideo && window.dropVideo({paths[0]!r})".replace("'", '"')
            self.evaluateJavaScript_completionHandler_(js, None)
            return True

        def _paths_(self, sender):  # noqa: N802
            """Пути перетаскиваемых видеофайлов (чужие расширения не принимаем вовсе)."""
            out = []
            for item in sender.draggingPasteboard().pasteboardItems() or []:
                raw = item.stringForType_(NSPasteboardTypeFileURL)
                if not raw:
                    continue
                path = NSURL.URLWithString_(raw).path()
                if path and Path(path).suffix.lower().lstrip(".") in ingest_ui.ingest.VIDEO_EXT:
                    out.append(str(path))
            return out

    class Delegate(NSObject):
        """Закрытие окна во время работы спрашивает подтверждение, иначе гасит приложение."""

        def windowShouldClose_(self, window):  # noqa: N802
            if ingest_ui.STATE.get("stage") != "running":
                return True
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Запись ещё обрабатывается")
            alert.setInformativeText_("Если закрыть окно, расшифровка прервётся. Продолжить работу?")
            alert.addButtonWithTitle_("Продолжить")
            alert.addButtonWithTitle_("Закрыть и прервать")
            return alert.runModal() != 1000   # 1000 — первая кнопка

        def windowWillClose_(self, note):  # noqa: N802
            server.shutdown()
            NSApplication.sharedApplication().terminate_(None)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
             | NSWindowStyleMaskResizable)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, WIDTH, HEIGHT), style, NSBackingStoreBuffered, False)
    window.setTitle_(TITLE)
    window.setMinSize_(NSMakeRect(0, 0, 720, 560).size)
    window.center()

    view = DropWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, WIDTH, HEIGHT), WKWebViewConfiguration.alloc().init())
    window.setContentView_(view)
    view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    delegate = Delegate.alloc().init()
    window.setDelegate_(delegate)
    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    print(f"окно открыто; страница — {url}")
    app.run()
    return 0
