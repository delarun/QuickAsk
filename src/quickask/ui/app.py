# QuickAsk — https://github.com/delarun/quickask — MIT
"""Gtk.Application: одна резидентная копия, CSS, старт MCP, разбор аргументов.

Одна копия на Linux — через D-Bus (Gtk.Application). На Windows и macOS сессионной шины нет,
и если оставить поиск копий Gtk.Application, он не сможет зарегистрироваться и окно не откроется:
там приложение уникальностью не занимается (NON_UNIQUE), а копии общаются через instance.py.
На Windows QuickAsk ещё и живёт в трее (win32.py).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

from gi.repository import Gdk, Gio, GLib, Gtk

from .. import self_command
from ..config import APP_ID, load_config
from ..mcp import McpHub
from .theme import build_css, spacing
from .window import QuickAskWindow

UNIQUE_VIA_DBUS = sys.platform.startswith("linux")
WINDOWS = sys.platform == "win32"


class QuickAskApp(Gtk.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.DEFAULT_FLAGS if UNIQUE_VIA_DBUS else Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.cfg = load_config()
        self.hub: McpHub | None = None
        self.win: QuickAskWindow | None = None
        self.daemon = False
        self.add_main_option("daemon", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Запустить резидентно без показа окна", None)
        self.add_main_option("open", ord("o"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Открыть страницу MCP-сервера: сервер:страница?ключ=значение", "LINK")
        self._pending_link: str | None = None
        self.instance = None                 # сервер «одной копии» на Windows/macOS
        self.tray = None

    def do_handle_local_options(self, options) -> int:
        if options.contains("open"):
            link = options.lookup_value("open", GLib.VariantType.new("s")).get_string()
            self.register(None)
            if self.get_is_remote():  # демон жив — попросим его открыть страницу
                self.activate_action("open", GLib.Variant("s", link))
                return 0
            self._pending_link = link
        if options.contains("daemon"):
            self.register(None)
            if self.get_is_remote():  # демон уже жив — ничего не делаем
                return 0
            self.daemon = True
        return -1

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        css = build_css(spacing(self.cfg))
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(css)
        else:  # GTK < 4.12
            provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        act = Gio.SimpleAction.new("open", GLib.VariantType.new("s"))
        act.connect("activate", lambda _a, v: self.open_link(v.get_string()))
        self.add_action(act)
        threading.Thread(target=self._start_mcp, daemon=True).start()
        if not UNIQUE_VIA_DBUS:
            from .instance import Server
            self.instance = Server(lambda m: GLib.idle_add(self._on_instance_message, m))
            self.instance.start()
        if WINDOWS:
            from .win32 import Tray
            self.tray = Tray(self._toggle, self._tray_items, self.cfg["ui"].get("hotkey") or "", self._tray_error)
            self.tray.start()
            self.hold()                      # живём в трее, даже когда окно спрятано

    # -- следующие запуски и трей (Windows/macOS) ------------------------------------------
    def _on_instance_message(self, msg: str) -> bool:
        if msg == "toggle":
            self._toggle()
        elif msg.startswith("open:"):
            self.open_link(msg[5:])
            self.win.present()
        return False

    def _window(self) -> QuickAskWindow:
        if self.win is None:
            self.win = QuickAskWindow(self)
        return self.win

    def _toggle(self) -> bool:
        self._window().toggle()
        return False

    def _tray_error(self, text: str) -> bool:
        w = self._window()
        w.present()
        w.set_status(f"⚠ {text}")
        return False

    def _tray_items(self) -> list[tuple]:
        """Пункты меню трея. Вызывается из потока трея — только чтение, без GTK."""
        from . import win32
        items: list[tuple] = [("Показать / спрятать", self._toggle, False), ("", None, False)]
        buttons = self.hub.buttons() if self.hub else []
        for server, b in buttons:        # кнопки серверов из их манифестов — «Агенты» и т.п.
            items.append((b.get("label", "?"), lambda s=server, a=b.get("action"): self._server_action(s, a), False))
        if buttons:
            items.append(("", None, False))
        items += [("Открыть config.toml", self._open_config, False),
                  ("Папка конфига", self._open_config_dir, False),
                  ("Запускать при входе в Windows", self._toggle_autostart, win32.autostart_enabled()),
                  ("", None, False),
                  ("Перезапустить", self._restart, False),
                  ("Выход", self._quit, False)]
        return items

    def _server_action(self, server, action) -> bool:
        w = self._window()
        w.present()
        if action:
            w.views.run(server, [action])
        return False

    def _open_config(self) -> bool:
        path = self.cfg["_path"]
        try:
            os.startfile(path)               # type: ignore[attr-defined]  # только Windows
        except OSError:                      # у .toml нет программы по умолчанию
            subprocess.Popen(["notepad.exe", path])
        return False

    def _open_config_dir(self) -> bool:
        os.startfile(os.path.dirname(self.cfg["_path"]))  # type: ignore[attr-defined]
        return False

    def _toggle_autostart(self) -> bool:
        from . import win32
        win32.set_autostart(not win32.autostart_enabled())
        return False

    def _restart(self) -> bool:
        """Перечитать config.toml: отпустить канал «одной копии», запустить новую копию, выйти."""
        if self.instance:
            self.instance.close()
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(self_command(), creationflags=flags, close_fds=True)
        return self._quit()

    def _quit(self) -> bool:
        self.quit()
        return False

    def open_link(self, link: str) -> None:
        """Deep link на страницу сервера — например, из уведомления агента."""
        if self.win is None:
            self.win = QuickAskWindow(self)
        if self.hub is None:  # MCP ещё поднимается — откроем, как только будет готов
            GLib.timeout_add(500, lambda: (self.open_link(link), False)[1])
            return
        self.win.views.open_link(link)

    def _start_mcp(self) -> None:
        hub = McpHub(self.cfg.get("mcp") or [])
        self.hub = hub

        def done() -> bool:
            if self.win:
                self.win._update_status_idle()
            return False

        GLib.idle_add(done)

    def do_activate(self) -> None:
        if self._pending_link:
            link, self._pending_link = self._pending_link, None
            self.hold()
            self.open_link(link)
            return
        if self.win is None:
            self.win = QuickAskWindow(self)
            if self.daemon:
                self.hold()
                return  # первый activate в режиме демона — окно не показываем
        self.win.toggle()

    def do_shutdown(self) -> None:
        if self.tray:
            self.tray.stop()
        if self.instance:
            self.instance.close()
        if self.hub:
            self.hub.close()
        Gtk.Application.do_shutdown(self)


def main() -> int:
    GLib.set_prgname("quickask")
    if not UNIQUE_VIA_DBUS:              # копия уже запущена — передать ей команду и выйти
        from .instance import message_for, send
        if send(message_for(sys.argv[1:])):
            return 0
    return QuickAskApp().run(sys.argv)
