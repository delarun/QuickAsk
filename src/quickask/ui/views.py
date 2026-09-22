# QuickAsk — https://github.com/delarun/quickask — MIT
"""Страницы MCP-серверов и их действия (протокол quickask.sdk).

Сервер описывает страницу JSON-ом (quickask/ui/render), здесь она превращается в виджеты.
Про то, что на странице, окно ничего не знает: агенты, окна, заметки — всё приходит от серверов.
"""
from __future__ import annotations

import threading
import urllib.parse

from gi.repository import Gio, GLib, Gtk, Pango

from ..sdk import protocol as P
from .markdown import MdRenderer
from .theme import PALETTE

NO_WRAP = {"out", "task", "ask", "now", "done", "time"}   # эти строки по умолчанию не переносятся


def _subst(value, text: str):
    """"$text" в аргументах действия → текст из поля ввода страницы."""
    if isinstance(value, str):
        return value.replace(P.TEXT_PLACEHOLDER, text)
    if isinstance(value, list):
        return [_subst(v, text) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, text) for k, v in value.items()}
    return value


class ViewHost:
    """Стек открытых страниц, их отрисовка и выполнение действий."""

    def __init__(self, win):
        self.win = win
        self.stack: list[tuple] = []          # (сервер, имя страницы, args)
        self.page: dict | None = None         # последняя полученная страница
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=win.sp["steps"])
        self.box.set_visible(False)
        self._gen = 0                         # номер запроса страницы: опоздавшие ответы отбрасываем
        self._rendering = False
        self._poll: int | None = None
        self._poll_ms = 0

    @property
    def active(self) -> bool:
        return bool(self.stack)

    @property
    def server(self):
        return self.stack[-1][0] if self.stack else None

    # ── навигация ────────────────────────────────────────────────────────────
    def open(self, server, view: str, args: dict | None = None, replace: bool = False) -> None:
        if server is None or not server.has_ui:
            self.win.set_status(f"⚠ у сервера {getattr(server, 'name', '?')} нет страниц")
            return
        entering = not self.stack
        entry = (server, view, dict(args or {}))
        if self.stack and self.stack[-1][0] is server and self.stack[-1][1] == view:
            replace = True                    # уже на этой странице — обновить, а не класть её в стек второй раз
        if replace and self.stack:
            self.stack[-1] = entry            # та же страница с другими args — старая видна, пока грузится новая
        else:
            self.stack.append(entry)
            self.page = {"title": server.info.get("name") or server.name}
            self._clear()
        if entering:                          # из чата — спрятать ленту; между страницами — не трогать статус
            self.win.enter_view()
        self._render(first=not replace)

    def open_link(self, link: str) -> None:
        """Deep link `сервер:страница?ключ=значение` — из `quickask --open …` (клик по уведомлению)."""
        name, _, rest = link.partition(":")
        view, _, query = rest.partition("?")
        server = self.win.app_ref.hub.server_by(name) if self.win.app_ref.hub else None
        if server is None:
            self.win.set_status(f"⚠ нет MCP-сервера {name!r} для {link}")
            return
        self.open(server, view, dict(urllib.parse.parse_qsl(query)))

    def back(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            self._clear()
            self._render(first=True)
        else:
            self.close()

    def close(self) -> None:
        if not self.stack:
            return
        self.stack.clear()
        self.page = None
        self._gen += 1
        self._stop_poll()
        self._clear()
        self.win.leave_view()

    def refresh(self) -> None:
        if self.stack and not self._rendering:
            self._render(first=False)

    # ── запрос и отрисовка страницы ──────────────────────────────────────────
    def _render(self, first: bool) -> None:
        self._gen += 1
        gen, (server, view, args) = self._gen, self.stack[-1]
        self._rendering = True

        def work() -> None:
            try:
                page, err = server.ui_render(view, args), None
            except Exception as e:  # noqa: BLE001
                page, err = None, str(e)
            GLib.idle_add(self._apply, gen, page, err, first)

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, gen: int, page: dict | None, err: str | None, first: bool) -> bool:
        if gen != self._gen or not self.stack:
            return False
        self._rendering = False
        if err or not isinstance(page, dict):
            page = {**(self.page or {}), "blocks": [{"type": "empty", "text": f"⚠ {err or 'пустой ответ'}"}]}
        bottom = page.get("scroll") == "bottom"
        was_at_bottom, value = self.win._at_bottom(), self.win.scroll.get_vadjustment().get_value()
        self.page = page

        self._clear()
        server = self.server
        for block in page.get("blocks") or []:
            w = self._block(server, block)
            if w is not None:
                self.box.append(w)
        self.win._update_header()

        # лог — держаться низа, пока пользователь не отлистал; список — остаться там, где был
        if bottom and (first or was_at_bottom):
            self.win._stick = True
            GLib.idle_add(self.win._scroll_to_bottom)
        else:
            self.win._stick = False
            GLib.idle_add(lambda: (self.win.scroll.get_vadjustment().set_value(0 if first else value), False)[1])
        self._schedule_poll(int(page.get("refresh_ms") or 0))
        return False

    def _clear(self) -> None:
        child = self.box.get_first_child()
        while child:
            self.box.remove(child)
            child = self.box.get_first_child()

    # ── обновление по таймеру ────────────────────────────────────────────────
    def _schedule_poll(self, ms: int) -> None:
        if ms == self._poll_ms and self._poll is not None:
            return
        self._stop_poll()
        self._poll_ms = ms
        if ms > 0:
            self._poll = GLib.timeout_add(max(ms, 500), self._tick)

    def _stop_poll(self) -> None:
        if self._poll is not None:
            GLib.source_remove(self._poll)
        self._poll, self._poll_ms = None, 0

    def _tick(self) -> bool:
        if not self.stack:
            self._poll, self._poll_ms = None, 0
            return False
        if self.win.get_visible():
            self.refresh()
        return True

    # ── блоки ────────────────────────────────────────────────────────────────
    def _block(self, server, b: dict) -> Gtk.Widget | None:
        kind = b.get("type")
        if kind == "list":
            return self._list(server, b.get("rows") or [])
        if kind == "card":
            return self._card(b)
        if kind == "empty":
            lbl = Gtk.Label(label=b.get("text", ""), xalign=0.0, wrap=True, selectable=True)
            lbl.add_css_class("v-empty")
            return lbl
        if kind == "markdown":
            return self._markdown(b.get("text", ""))
        return None

    @staticmethod
    def _line(line: dict) -> Gtk.Label:
        style = line.get("style") if line.get("style") in P.LINE_STYLES else "text"
        lbl = Gtk.Label(label=str(line.get("text", "")), xalign=0.0, selectable=True)
        if line.get("wrap", style not in NO_WRAP):
            lbl.set_wrap(True)
            lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        else:
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.add_css_class(f"l-{style}")
        return lbl

    def _list(self, server, rows: list[dict]) -> Gtk.Widget:
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("v-list")
        for r in rows:
            title = Gtk.Label(label=r.get("title", ""), xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            title.add_css_class("r-title")
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            top.append(title)
            if r.get("meta"):
                meta = Gtk.Label(label=r["meta"], xalign=1.0)
                meta.add_css_class("r-meta")
                top.append(meta)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
            col.append(top)
            for ln in r.get("lines") or []:
                lbl = self._line({"wrap": False, **ln})
                lbl.set_selectable(False)           # иначе клик по строке уходит в выделение текста
                col.append(lbl)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            dot = r.get("dot")
            if dot:
                d = Gtk.Label(valign=Gtk.Align.START)
                d.add_css_class("r-dot")
                color = PALETTE.get(dot.get("color", "grey"), PALETTE["grey"])
                d.set_markup(f'<span color="{color}">{GLib.markup_escape_text(dot.get("text", "●"))}</span>')
                box.append(d)
            box.append(col)
            row = Gtk.ListBoxRow(child=box, activatable=bool(r.get("action")))
            if not r.get("action"):
                row.add_css_class("inert")
            row.action = r.get("action")
            lb.append(row)
        lb.connect("row-activated", lambda _l, row: row.action and self.run(server, [row.action]))
        return lb

    def _card(self, b: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("v-card")
        style = b.get("style") if b.get("style") in P.CARD_STYLES else "plain"
        if style != "plain":
            box.add_css_class(style)
        lines = [self._line(ln) for ln in b.get("lines") or []]
        if b.get("time") and lines:                 # метка времени слева от первой строки
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            t = Gtk.Label(label=b["time"], xalign=0.0, valign=Gtk.Align.START)
            t.add_css_class("l-time")
            top.append(t)
            lines[0].set_hexpand(True)
            top.append(lines[0])
            box.append(top)
            lines = lines[1:]
        for lbl in lines:
            box.append(lbl)
        return box

    def _markdown(self, text: str) -> Gtk.Widget:
        view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.add_css_class("qa-answer")
        md = MdRenderer(view.get_buffer())
        md.render(text)
        click = Gtk.GestureClick(button=1)

        def on_click(_g, _n, x, y):
            url = md.url_at(view, x, y)
            if url and not view.get_buffer().get_has_selection():
                try:
                    Gio.AppInfo.launch_default_for_uri(url, None)
                except GLib.Error as e:
                    self.win.set_status(f"не открыть: {e.message}")

        click.connect("released", on_click)
        view.add_controller(click)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("qa-msg")
        card.append(view)
        return card

    # ── действия ─────────────────────────────────────────────────────────────
    def _target(self, server, a: dict):
        name = a.get("server")
        return (self.win.app_ref.hub.server_by(name) if name and self.win.app_ref.hub else None) or server

    def run(self, server, actions: list[dict], text: str = "", origin: str = "") -> None:
        """Выполнить действия по порядку. text — для "$text"; origin — команда, от которой они пришли
        (станет строкой вопроса у show без title). call асинхронный — остальное продолжится после него."""
        queue = [a for a in actions or [] if isinstance(a, dict)]
        while queue:
            a = _subst(queue.pop(0), text)
            srv, kind = self._target(server, a), a.get("type")
            if kind == "call":
                self._call(srv, a, queue, text, origin)
                return
            if kind == "view":
                self.open(srv, a.get("view", ""), a.get("args") or {}, bool(a.get("replace")))
            elif kind == "refresh":
                self.refresh()
            elif kind == "back":
                self.back()
            elif kind == "chat":
                self.close()
            elif kind == "hide":
                self.win.hide_window()
            elif kind == "status":
                self.win.set_status(str(a.get("text", "")))
            elif kind == "show":
                self.win.show_markdown(a.get("title") or origin, str(a.get("markdown", "")))

    def _call(self, server, a: dict, rest: list[dict], text: str, origin: str) -> None:
        tool, args = a.get("tool", ""), a.get("args") or {}
        self.win.set_busy(True)
        self.win.set_status(f"🔧 {tool}…")

        def work() -> None:
            try:
                out, acts = server.call_ex(tool, args)
                GLib.idle_add(done, out.split("\n")[0][:160], acts)
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(done, f"⚠ {e}", [])

        def done(line: str, acts: list[dict]) -> bool:
            self.win.set_busy(False)
            self.win.set_status(line)
            then = a["then"] if "then" in a else ([{"type": "refresh"}] if self.stack else [])
            self.run(server, acts + list(then) + rest, text, origin)
            return False

        threading.Thread(target=work, daemon=True).start()

    def command(self, server, name: str, text: str, origin: str) -> None:
        """Слэш-команда сервера: он сам разбирает аргументы и возвращает действия."""
        self.win.set_busy(True)

        def work() -> None:
            try:
                acts, err = server.ui_command(name, text), None
            except Exception as e:  # noqa: BLE001
                acts, err = [], str(e)
            GLib.idle_add(done, acts, err)

        def done(acts: list[dict], err: str | None) -> bool:
            self.win.set_busy(False)
            if err:
                self.win.set_status(f"⚠ {name}: {err}")
            else:
                self.run(server, acts, origin=origin)
            return False

        threading.Thread(target=work, daemon=True).start()
