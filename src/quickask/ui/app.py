# QuickAsk — https://github.com/delarun/quickask — MIT
"""Gtk.Application: одна резидентная копия, CSS, старт MCP, разбор аргументов."""
from __future__ import annotations

import sys
import threading

from gi.repository import Gdk, Gio, GLib, Gtk

from ..config import APP_ID, load_config
from ..mcp import McpHub
from .theme import build_css, spacing
from .window import QuickAskWindow

class QuickAskApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.cfg = load_config()
        self.hub: McpHub | None = None
        self.win: QuickAskWindow | None = None
        self.daemon = False
        self.add_main_option("daemon", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Запустить резидентно без показа окна", None)
        self.add_main_option("open", ord("o"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Открыть страницу MCP-сервера: сервер:страница?ключ=значение", "LINK")
        self._pending_link: str | None = None

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
        if self.hub:
            self.hub.close()
        Gtk.Application.do_shutdown(self)


def main() -> int:
    GLib.set_prgname("quickask")
    return QuickAskApp().run(sys.argv)
