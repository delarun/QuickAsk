# QuickAsk — https://github.com/delarun/quickask — MIT
"""Окно QuickAsk: лента диалога, поле ввода и место под страницы MCP-серверов."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import traceback

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from . import HAVE_LAYER_SHELL, LayerShell
from .markdown import MdRenderer
from .theme import spacing
from .views import ViewHost
from .worker import QueryWorker


class QuickAskWindow(Gtk.ApplicationWindow):
    PLACEHOLDER = "Спроси что-нибудь…"
    PLACEHOLDER_ASK = "Ответ… (или выбери вариант)"
    KEYS_CHAT = "/s поиск · /help команды · Ctrl+V картинка · Ctrl+L очистить"
    KEYS_VIEW = "Ctrl+L — выйти в чат"

    def __init__(self, app: QuickAskApp):
        super().__init__(application=app, title="QuickAsk", decorated=False, resizable=False)
        self.app_ref = app
        self.cfg = app.cfg
        self.sp = spacing(self.cfg)      # отступы, которые не выражаются в CSS (spacing у Gtk.Box)
        self.history: list[dict] = []
        self.worker: QueryWorker | None = None
        self.always_search = False
        self._history_note = ""
        self._pending: list[str] = []
        self._pending_think: list[str] = []
        self._flush_scheduled = False

        self.add_css_class("quickask")
        self.set_default_size(self.cfg["ui"]["width"], -1)
        self.set_hide_on_close(True)

        self.layer = HAVE_LAYER_SHELL and LayerShell.is_supported()
        if self.layer:
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, "quickask")
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_margin(self, LayerShell.Edge.TOP, self._margin_top())
            # отступ в процентах считается от высоты монитора — пересчитать, когда станет ясно, на каком мы
            self.connect("realize", lambda *_: self.get_surface().connect(
                "enter-monitor", lambda _s, mon: LayerShell.set_margin(self, LayerShell.Edge.TOP,
                                                                        self._margin_top(mon))))
        elif sys.platform == "win32":
            from . import win32              # поверх всех, без кнопки на панели задач, по ui.margin_top
            win32.attach(self)

        # прятаться, когда окно теряет фокус, — как лаунчеры на Windows; на Linux по умолчанию нет
        blur = self.cfg["ui"].get("hide_on_blur")
        self._hide_on_blur = (sys.platform == "win32") if blur is None else bool(blur)
        self._blur_hidden_at = 0.0
        self.connect("notify::is-active", self._on_active_changed)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("qa-root")

        # ── шапка: чип режима · контекст · кнопки серверов · ✕ ───────────
        self.chip = Gtk.Label(label="Чат")
        self.chip.add_css_class("qa-chip")
        self.ctx = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.ctx.add_css_class("qa-context")
        self.busy = Gtk.Spinner()
        self.busy.set_visible(False)

        def button(text, css, cb):
            b = Gtk.Button(label=text)
            b.add_css_class("qa-btn")
            if css:
                b.add_css_class(css)
            b.connect("clicked", cb)
            return b

        self.btn_close = button("✕", None, lambda *_: self.back_to_chat())
        # кнопки в шапке задают серверы: в чате — из манифестов, на странице — сама страница
        self.hbtns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._hbtns_key: str | None = None

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("qa-header")
        for w in (self.chip, self.ctx, self.busy, self.hbtns, self.btn_close):
            header.append(w)

        # ── карточка вопроса (ask_user) ──────────────────────────────────
        self._question: dict | None = None
        self.q_label = Gtk.Label(xalign=0.0, wrap=True)
        self.q_label.add_css_class("q")
        self.q_options = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                     row_spacing=6, column_spacing=6, margin_top=8)
        self.q_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.q_box.add_css_class("qa-question")
        self.q_box.append(self.q_label)
        self.q_box.append(self.q_options)
        self.q_box.set_visible(False)

        # ── поле ввода: глиф + entry ─────────────────────────────────────
        self.glyph = Gtk.Label(label="❯")
        self.glyph.add_css_class("qa-glyph")
        self.entry = Gtk.Entry(placeholder_text=self.PLACEHOLDER, hexpand=True)
        self.entry.add_css_class("qa-entry")
        self.entry.set_has_frame(False)
        self.entry.connect("activate", self.on_submit)
        self.field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.field.add_css_class("qa-field")
        self.field.append(self.glyph)
        self.field.append(self.entry)

        # ── вложение из буфера (Ctrl+V) ──────────────────────────────────
        self.image_url: str | None = None
        self.attach_pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, halign=Gtk.Align.START)
        self.attach_pic.set_size_request(-1, 56)
        self.attach_label = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.attach_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.attach_box.add_css_class("qa-attach")
        self.attach_box.append(self.attach_pic)
        self.attach_box.append(self.attach_label)
        self.attach_box.set_visible(False)
        rm = Gtk.GestureClick(button=1)
        rm.connect("released", lambda *_: self.clear_image())
        self.attach_box.add_controller(rm)

        # ── строка состояния ─────────────────────────────────────────────
        self.status = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("qa-status")
        self.status.set_visible(False)

        # ── тело: лента диалога · страница MCP-сервера ───────────────────
        # Каждый обмен — отдельный блок (вопрос · рассуждения · ответ) в chat_box;
        # предыдущие остаются на месте, их видно скроллом.
        self.chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.sp["turns"])
        self.chat_box.set_visible(False)
        self.turns: list[Gtk.Widget] = []
        self._think_chars = 0
        self._think_started = 0.0
        self._answer_started = False
        self.raw_answer = ""
        self._new_turn()                          # пустой текущий обмен: есть куда стримить

        self.views = ViewHost(self)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.add_css_class("qa-body")
        body.append(self.views.box)
        body.append(self.chat_box)

        self.scroll = Gtk.ScrolledWindow(child=body, propagate_natural_height=True,
                                         max_content_height=self.cfg["ui"]["max_height"])
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._stick = True
        self.scroll.get_vadjustment().connect("notify::upper", self._on_upper)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self._on_wheel)
        self.scroll.add_controller(wheel)
        self.scroll.set_visible(False)

        # ── футер: модель · инструменты · подсказки ──────────────────────
        self.pill_model = Gtk.Label()
        self.pill_model.add_css_class("qa-pill")
        self.pill_tools = Gtk.Label()
        self.pill_tools.add_css_class("qa-pill")
        self.pill_mode = Gtk.Label()
        self.pill_mode.add_css_class("qa-pill")
        self.pill_mode.set_visible(False)
        self.keys = Gtk.Label(xalign=1.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.keys.add_css_class("qa-keys")
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.add_css_class("qa-footer")
        for w in (self.pill_model, self.pill_tools, self.pill_mode, self.keys):
            footer.append(w)

        root.append(header)
        root.append(self.q_box)
        root.append(self.field)
        root.append(self.attach_box)
        root.append(self.status)
        root.append(self.scroll)
        root.append(footer)
        self.set_child(root)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self._update_status_idle()

    def _margin_top(self, monitor: Gdk.Monitor | None = None) -> int:
        """ui.margin_top: число — пиксели, строка «25%» — доля высоты монитора."""
        v = self.cfg["ui"]["margin_top"]
        if isinstance(v, str) and v.strip().endswith("%"):
            if monitor is None:                    # до показа окна монитор неизвестен — берём первый
                monitors = Gdk.Display.get_default().get_monitors()
                monitor = monitors.get_item(0) if monitors.get_n_items() else None
            height = monitor.get_geometry().height if monitor else 1080
            return round(height * float(v.strip()[:-1]) / 100)
        return int(v)

    # -- показ/скрытие ------------------------------------------------------
    def toggle(self) -> None:
        if self.get_visible():
            self.hide_window()
        elif time.monotonic() - self._blur_hidden_at < 0.4:
            pass    # окно только что спряталось из-за этого же клика по трею — не открывать обратно
        else:
            self.present()
            self.entry.grab_focus()

    def _on_active_changed(self, *_) -> None:
        if self._hide_on_blur and self.get_visible() and not self.is_active() and self._question is None:
            self._blur_hidden_at = time.monotonic()
            self.set_visible(False)          # без hide_window: идущий ответ не прерываем, он дождётся

    def hide_if_idle(self) -> bool:
        """Спрятаться после действия (focus_window и т.п.), если не начали печатать новый вопрос."""
        if self.cfg["ui"]["hide_after_action_ms"] and self.get_visible() \
                and not self.entry.get_text() and not (self.worker and self.worker.is_alive()):
            self.hide_window()
        return False

    def hide_window(self) -> None:
        self._cancel()
        self.hide_question()
        self.clear_image()
        if self.cfg["ui"]["clear_on_hide"]:
            self.clear_all()
        self.set_visible(False)

    # -- клавиши -------------------------------------------------------------
    # ЙЦУКЕН → позиции латинских клавиш, на случай если map_keycode не даст латинского уровня
    _CYR_FALLBACK = str.maketrans("йцукенгшщзхъфывапролджэячсмитьбю", "qwertyuiop[]asdfghjkl;'zxcvbnm,.")

    def _latin_key(self, keyval: int, keycode: int) -> str:
        """Латинская буква для физической клавиши независимо от текущей раскладки."""
        ch = chr(Gdk.keyval_to_unicode(keyval) or 0)
        if "a" <= ch.lower() <= "z":
            return ch.lower()
        try:  # все keyval этой клавиши по всем группам раскладки; ищем латиницу на уровне 0
            ok, _keys, keyvals = Gdk.Display.get_default().map_keycode(keycode)
            if ok:
                for kv in keyvals:
                    c = chr(Gdk.keyval_to_unicode(kv) or 0)
                    if "a" <= c.lower() <= "z":
                        return c.lower()
        except Exception:  # noqa: BLE001
            pass
        return ch.lower().translate(self._CYR_FALLBACK) if ch else ""

    def on_key(self, _ctrl, keyval, keycode, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Escape:
            self.hide_window()
            return True
        if keyval in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down) and self.scroll.get_visible():
            adj = self.scroll.get_vadjustment()   # строка ввода всегда в фокусе, листаем ленту отсюда
            page = adj.get_page_size() * 0.9
            adj.set_value(adj.get_value() + (page if keyval == Gdk.KEY_Page_Down else -page))
            self._stick = self._at_bottom()
            return True
        if not ctrl:
            return False
        key = self._latin_key(keyval, keycode)  # 'l' и на русской раскладке ('д')
        if key == "l":
            if self.views.active:
                self.views.close()            # со страницы — в чат, ленту не трогаем
            else:
                self.clear_all()
            return True
        if key == "s":
            self.always_search = not self.always_search
            self.set_status("🔎 поиск включён для каждого вопроса" if self.always_search else "")
            self._update_status_idle()
            return True
        if key == "v":
            if self._clipboard_has_image() or shift:
                self.paste_image()
                return True
            return False  # обычная вставка текста — GTK4 сам матчит Ctrl+V сквозь раскладки
        if key == "c" and shift:
            Gdk.Display.get_default().get_clipboard().set(self.raw_answer)
            self.set_status("📋 скопировано (markdown)")
            return True
        return False

    # -- запрос ------------------------------------------------------------
    def on_submit(self, _entry) -> None:
        text = self.entry.get_text().strip()
        if self._question is not None:  # ответ на вопрос модели/агента
            if text:
                self.answer_question(text)
            return
        submit = ((self.views.page or {}).get("input") or {}).get("submit") if self.views.active else None
        if submit and text and not text.startswith("/"):
            self.entry.set_text("")          # поле ввода страницы: Enter выполняет её действие
            self.views.run(self.views.server, [submit], text=text)
            return
        if not text and not self.image_url:
            return
        if not text:
            text = "Опиши, что на изображении."
        if self._slash_command(text):
            return
        force = self.always_search
        if text.startswith("/s "):
            force, text = True, text[3:].strip()
        image, self._history_note = self.image_url, (" [изображение]" if self.image_url else "")
        self._cancel()
        self.views.close()
        self.entry.set_text("")
        self.clear_image()
        self._new_turn(text)
        self.chat_box.set_visible(True)
        self.scroll.set_visible(True)
        self.set_busy(True)
        self.set_status("… отправляю")
        self.worker = QueryWorker(self.cfg, self.app_ref.hub, list(self.history), text, force, self,
                                  image_url=image)
        self.worker.start()

    # -- вопрос пользователю (ask_user от модели) ----------------------------------------
    def show_question(self, question: str, options: list[str], box: dict) -> bool:
        self._question = box
        self.q_label.set_text(question)
        child = self.q_options.get_first_child()
        while child:
            self.q_options.remove(child)
            child = self.q_options.get_first_child()
        for opt in options:
            b = Gtk.Button(label=opt)
            b.connect("clicked", lambda _b, o=opt: self.answer_question(o))
            self.q_options.append(b)
        self.q_options.set_visible(bool(options))
        self.q_box.set_visible(True)
        self.field.add_css_class("asking")
        self.set_busy(False)
        self.set_status("")
        self._update_header()
        self.entry.grab_focus()
        self.present()
        return False

    def answer_question(self, text: str) -> None:
        box, self._question = self._question, None
        self.hide_question()
        self.entry.set_text("")
        if box is not None:
            box["answer"] = text
            box["event"].set()
        self.set_busy(True)
        self.set_status("… генерирую")

    def hide_question(self) -> bool:
        self._question = None
        self.q_box.set_visible(False)
        self.field.remove_css_class("asking")
        self._update_header()
        return False

    # -- страницы MCP-серверов ----------------------------------------------------------
    def enter_view(self) -> None:
        """ViewHost открыл страницу: прячем ленту, показываем место под страницу."""
        self._cancel()
        self.chat_box.set_visible(False)
        self.views.box.set_visible(True)
        self.scroll.set_visible(True)
        self.set_status("")
        self._update_status_idle()
        self.present()
        self.entry.grab_focus()

    def leave_view(self) -> None:
        self.views.box.set_visible(False)
        self.chat_box.set_visible(self._has_chat())
        self.scroll.set_visible(self._has_chat())
        self._stick = True
        self.set_status("")
        self._update_status_idle()

    # -- слэш-команды: свои и те, что объявили серверы ---------------------------------
    CORE_COMMANDS = {"/s": "<вопрос> — найти в интернете и ответить со ссылками",
                     "/help": "команды всех серверов",
                     "/chat": "закрыть страницу, вернуться в чат"}

    def _slash_command(self, text: str) -> bool:
        name, _, rest = text.partition(" ")
        if name == "/chat":
            self.entry.set_text("")
            self.views.close()
            return True
        if name == "/help":
            self.entry.set_text("")
            self.show_markdown("/help", self._help())
            return True
        hub = self.app_ref.hub
        if hub is None:
            if name != "/s":
                self.set_status("⏳ MCP ещё поднимается — команды серверов будут через пару секунд")
                return True
            return False
        found = hub.commands().get(name)
        if not found:
            return False                      # не команда — пусть уйдёт модели как обычный текст
        self.entry.set_text("")
        self.views.command(found[0], name, rest.strip(), origin=text)
        return True

    def _help(self) -> str:
        out = ["**QuickAsk**", ""] + [f"- `{n}` {d}" for n, d in self.CORE_COMMANDS.items()]
        hub = self.app_ref.hub
        by_server: dict[str, list[str]] = {}
        for name, (srv, spec) in (hub.commands() if hub else {}).items():
            usage = f" {spec['usage']}" if spec.get("usage") else ""
            desc = f" — {spec['description']}" if spec.get("description") else ""
            by_server.setdefault(srv.name, []).append(f"- `{name}{usage}`{desc}")
        for srv, lines in by_server.items():
            out += ["", f"**{srv}**", ""] + lines
        if not by_server:
            out += ["", "_Серверы с командами не подключены — см. [[mcp]] в config.toml._"]
        return "\n".join(out)

    def show_markdown(self, title: str, md: str) -> bool:
        """Отдельный обмен в ленте: строка вопроса title, ответ md (команды, /help, show от сервера)."""
        self._cancel()
        self.views.close()                    # показываем в ленте — значит, со страницы уходим
        self._new_turn(title)
        self.chat_box.set_visible(True)
        self.scroll.set_visible(True)
        self.raw_answer = md
        self.md.render(md)
        self.answer_card.set_visible(True)
        self._update_status_idle()
        return False

    def offer_actions(self, found: list[tuple]) -> bool:
        """Действия, которые серверы приложили к ответам на вызовы модели: «hide» уже выполнен,
        переходы на страницы показываем кнопками под ответом — сам по себе ответ не пропадает."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("qa-turn-actions")
        for server, action in found:
            b = Gtk.Button(label=action.get("label") or "Открыть")
            b.add_css_class("qa-btn")
            b.add_css_class("accent")
            b.connect("clicked", lambda _b, s=server, a=action: self.views.run(s, [a]))
            box.append(b)
        if box.get_first_child() and self.turns:
            self.turns[-1].append(box)
        return False

    # -- картинка из буфера ---------------------------------------------------
    # Основной путь — wl-paste (data-control протокол, не зависит от фокуса и от того,
    # что GTK думает про data offer у layer-surface). GTK-clipboard — запасной.
    IMAGE_MIMES = ("image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif", "image/tiff")

    def _clipboard_image_mime(self) -> str | None:
        try:
            r = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=1.5)
            types = r.stdout.split()
            for m in self.IMAGE_MIMES:
                if m in types:
                    return m
            return next((t for t in types if t.startswith("image/")), None)
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            return None
        formats = Gdk.Display.get_default().get_clipboard().get_formats()  # без wl-clipboard
        try:
            if formats.contain_gtype(Gdk.Texture.__gtype__):
                return "image/png"
        except Exception:  # noqa: BLE001
            pass
        return next((m for m in self.IMAGE_MIMES if formats.contain_mime_type(m)), None)

    def _clipboard_has_image(self) -> bool:
        return self._clipboard_image_mime() is not None

    def paste_image(self) -> None:
        self.set_status("🖼 читаю буфер…")
        threading.Thread(target=self._read_clipboard_image, daemon=True, name="clip-image").start()

    def _read_clipboard_image(self) -> None:
        """В фоне: wl-paste → байты → в main loop. Если wl-paste нет — GTK-путь."""
        try:
            r = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=3)
            types = r.stdout.split()
            mime = next((m for m in self.IMAGE_MIMES if m in types), None) \
                or next((t for t in types if t.startswith("image/")), None)
            if not mime:
                GLib.idle_add(self.set_status, "в буфере нет картинки" +
                              (f" (типы: {', '.join(types[:4])})" if types else " (буфер пуст)"))
                return
            r = subprocess.run(["wl-paste", "--no-newline", "--type", mime], capture_output=True, timeout=10)
            if r.returncode != 0 or not r.stdout:
                GLib.idle_add(self.set_status, f"wl-paste не отдал {mime}: {r.stderr.decode(errors='replace').strip()[:120]}")
                return
            GLib.idle_add(self._image_from_bytes, r.stdout, mime)
        except FileNotFoundError:
            GLib.idle_add(self._paste_image_gtk)
        except subprocess.TimeoutExpired:
            GLib.idle_add(self.set_status, "wl-paste не ответил за 10 с — источник буфера уже закрыт?")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            GLib.idle_add(self.set_status, f"ошибка чтения буфера: {e}")

    def _paste_image_gtk(self) -> bool:
        clip = Gdk.Display.get_default().get_clipboard()
        cancel = Gio.Cancellable()
        state = {"done": False}

        def done(_clip, res) -> None:
            state["done"] = True
            try:
                texture = clip.read_texture_finish(res)
            except GLib.Error as e:
                self.set_status(f"GTK не смог прочитать картинку: {e.message}")
                return
            if texture is None:
                self.set_status("в буфере нет картинки")
                return
            try:
                self._image_from_bytes(bytes(texture.save_to_png_bytes().get_data()), "image/png")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.set_status(f"ошибка обработки картинки: {e}")

        def timeout() -> bool:
            if not state["done"]:
                cancel.cancel()
                self.set_status("буфер не отвечает (GTK). Поставь wl-clipboard: pacman -S wl-clipboard")
            return False

        clip.read_texture_async(cancel, done)
        GLib.timeout_add(4000, timeout)
        return False

    def _image_from_bytes(self, data: bytes, mime: str) -> bool:
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pb = loader.get_pixbuf()
            if pb is None:
                raise RuntimeError(f"не декодировалось ({mime}, {len(data)} байт)")
            pb = pb.apply_embedded_orientation() or pb
            self.set_image_pixbuf(pb)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.set_status(f"ошибка картинки: {e}")
        return False

    def set_image_pixbuf(self, pb: GdkPixbuf.Pixbuf) -> None:
        llm = self.cfg["llm"]
        w, h = pb.get_width(), pb.get_height()
        mx = int(llm["image_max_px"])
        if max(w, h) > mx:
            k = mx / max(w, h)
            pb = pb.scale_simple(max(1, int(w * k)), max(1, int(h * k)), GdkPixbuf.InterpType.BILINEAR)
        fmt = "png" if llm["image_format"] == "png" else "jpeg"
        if fmt == "jpeg" and pb.get_has_alpha():
            # composite_color_simple сохраняет альфу → glycin JPEG (Rgba8) падает; кладём на белый RGB
            flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pb.get_width(), pb.get_height())
            flat.fill(0xFFFFFFFF)
            pb.composite(flat, 0, 0, pb.get_width(), pb.get_height(), 0, 0, 1.0, 1.0,
                         GdkPixbuf.InterpType.NEAREST, 255)
            pb = flat
        try:
            if fmt == "jpeg":
                ok, data = pb.save_to_bufferv("jpeg", ["quality"], ["85"])
            else:
                ok, data = pb.save_to_bufferv("png", [], [])
        except GLib.Error as e:  # энкодер не осилил — пробуем PNG
            print(f"[quickask] {fmt} encode failed: {e.message}; falling back to png", file=sys.stderr)
            fmt = "png"
            ok, data = pb.save_to_bufferv("png", [], [])
        if not ok:
            self.set_status("не удалось закодировать картинку")
            return
        data = bytes(data)
        self.image_url = f"data:image/{fmt};base64," + base64.b64encode(data).decode()
        th = pb.scale_simple(max(1, int(pb.get_width() * 64 / max(pb.get_height(), 1))), 64,
                             GdkPixbuf.InterpType.BILINEAR)
        self.attach_pic.set_paintable(Gdk.Texture.new_for_pixbuf(th))
        self.attach_label.set_text(f"🖼 {w}×{h} → {pb.get_width()}×{pb.get_height()} {fmt}, "
                                   f"{len(data) // 1024} KB  ·  клик — убрать")
        self.attach_box.set_visible(True)
        self.entry.grab_focus()
        self.set_status("картинка прикреплена — Enter отправит" +
                        (f" в {llm['vision_model']}" if llm.get("vision_model") else ""))

    def clear_image(self) -> None:
        self.image_url = None
        self.attach_box.set_visible(False)
        self.attach_pic.set_paintable(None)

    def _cancel(self) -> None:
        if self.worker and self.worker.is_alive():
            self.worker.cancel.set()
        self.worker = None

    def clear_all(self) -> None:
        self._cancel()
        self.set_busy(False)
        self.set_status("")
        self.hide_question()
        self.views.close()
        self.history.clear()
        self._clear_turns()
        self.scroll.set_visible(False)
        self._update_status_idle()

    # -- лента диалога: обмен = вопрос · рассуждения · ответ ------------------
    def _new_turn(self, question: str = "") -> None:
        """Завести блок под новый обмен и сделать его текущим для стриминга."""
        if self.turns and not self.raw_answer and not self._think_chars:
            self.q_line.set_label("❯ " + question if question else "")   # блок ещё пуст — берём его
            self.q_line.set_visible(bool(question))
            return
        turn = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.sp["turn"])

        q = Gtk.Label(xalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                      label="❯ " + question if question else "")
        q.add_css_class("qa-q")
        q.set_visible(bool(question))
        turn.append(q)

        think_view = Gtk.TextView(editable=False, cursor_visible=False,
                                  wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                  left_margin=10, right_margin=10, top_margin=8, bottom_margin=8)
        think_view.add_css_class("qa-think")
        expander = Gtk.Expander(label="💭 Рассуждения", child=think_view)
        expander.add_css_class("qa-think-expander")
        expander.set_visible(False)
        turn.append(expander)

        view = Gtk.TextView(editable=False, cursor_visible=False,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            left_margin=0, right_margin=0, top_margin=0, bottom_margin=0)
        view.add_css_class("qa-answer")
        view.set_pixels_above_lines(1)
        view.set_pixels_below_lines(4)
        view.set_pixels_inside_wrap(3)
        motion = Gtk.EventControllerMotion()       # ссылки: курсор-рука и открытие по клику
        motion.connect("motion", self._on_motion)
        view.add_controller(motion)
        click = Gtk.GestureClick(button=1)
        click.connect("released", self._on_click)
        view.add_controller(click)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("qa-msg")
        card.append(view)
        card.set_visible(False)                    # показываем, только когда есть что показать
        turn.append(card)

        self.chat_box.append(turn)
        self.turns.append(turn)
        self.q_line, self.think_view, self.think_buf = q, think_view, think_view.get_buffer()
        self.think_expander, self.view, self.buf = expander, view, view.get_buffer()
        self.answer_card, self.md = card, MdRenderer(view.get_buffer())
        self.raw_answer = ""
        self._think_chars = 0
        self._think_started = 0.0
        self._answer_started = False
        self._trim_turns()
        self._stick = True                          # новый обмен всегда показываем целиком
        if getattr(self, "scroll", None) is not None:   # в __init__ скролла ещё нет
            GLib.idle_add(self._scroll_to_bottom)       # лента не выросла (сработала обрезка) — доводим сами

    def _has_chat(self) -> bool:
        """Есть ли в ленте хоть что-то (первый блок заводится пустым и ждёт вопроса)."""
        return len(self.turns) > 1 or bool(self.raw_answer) or self.q_line.get_visible()

    def _trim_turns(self) -> None:
        """Ленту режем по той же глубине, что и историю для модели."""
        keep = max(int(self.cfg["llm"]["history_turns"]), 1)
        while len(self.turns) > keep:
            self.chat_box.remove(self.turns.pop(0))

    def _clear_turns(self) -> None:
        for t in self.turns:
            self.chat_box.remove(t)
        self.turns.clear()
        self.chat_box.set_visible(False)
        self.raw_answer = ""
        self._new_turn()

    def _on_wheel(self, _c, _dx, _dy) -> bool:
        GLib.idle_add(self._recheck_stick)      # колесо уже применено — смотрим, где оказались
        return False

    def _recheck_stick(self) -> bool:
        self._stick = self._at_bottom()
        return False

    def _on_upper(self, adj, _param) -> None:
        """Лента подросла — держим низ, пока пользователь не отлистал вверх."""
        if self._stick and (self.chat_box.get_visible() or self.views.active):
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def _scroll_to_bottom(self) -> bool:
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _at_bottom(self) -> bool:
        adj = self.scroll.get_vadjustment()
        return adj.get_value() >= adj.get_upper() - adj.get_page_size() - 40

    # -- колбэки из воркера (main loop) ------------------------------------
    def append_token(self, text: str) -> bool:
        if not self._answer_started:
            self._answer_started = True
            self._on_answer_start()
        self._pending.append(text)
        self._schedule_flush()
        return False

    def append_reasoning(self, text: str) -> bool:
        if not self.cfg["ui"]["show_reasoning"]:
            return False
        if self._think_chars == 0:
            self._think_started = time.monotonic()
            self.think_expander.set_visible(True)
            self.think_expander.set_expanded(True)
            self.set_status("💭 думаю…")
        self._think_chars += len(text)
        self._pending_think.append(text)
        self._schedule_flush()
        return False

    def _on_answer_start(self) -> None:
        self.set_status("")
        if self._think_chars:
            secs = time.monotonic() - self._think_started
            self.think_expander.set_label(f"💭 Рассуждения ({secs:.1f} с, {self._think_chars} симв.)")
            if self.cfg["ui"]["collapse_reasoning"]:
                self.think_expander.set_expanded(False)

    def _schedule_flush(self) -> None:
        if not self._flush_scheduled:
            self._flush_scheduled = True
            GLib.timeout_add(30, self._flush)

    def _flush(self) -> bool:
        self._flush_scheduled = False
        at_bottom = self._stick or self._at_bottom()   # _stick взводит новый обмен, снимает колесо/PgUp
        if self._pending_think:
            self.think_buf.insert(self.think_buf.get_end_iter(), "".join(self._pending_think))
            self._pending_think.clear()
        if self._pending:
            self.raw_answer += "".join(self._pending)
            self._pending.clear()
            self.md.render(self.raw_answer)
            self.answer_card.set_visible(True)
        self._stick = at_bottom                     # отлистал вверх — не дёргаем обратно
        if at_bottom:
            self._scroll_to_bottom()
        return False

    def finish(self, question: str, answer: str, error: str | None) -> bool:
        self._flush()
        self.set_busy(False)
        if self._think_chars and not self._answer_started:
            self._on_answer_start()
        if error:
            self.raw_answer += f"\n\n> ⚠ {error}"
            self.md.render(self.raw_answer)
            self.answer_card.set_visible(True)
            self.set_status(f"⚠ {error.splitlines()[0][:150]}")
            return False
        self.history += [{"role": "user", "content": question + getattr(self, "_history_note", "")},
                         {"role": "assistant", "content": answer}]
        self._history_note = ""
        keep = self.cfg["llm"]["history_turns"] * 2
        if len(self.history) > keep:
            del self.history[:-keep]
        self.set_status("")
        self._update_status_idle()
        return False

    def _on_motion(self, _c, x: float, y: float) -> None:
        self.view.set_cursor_from_name("pointer" if self.md.url_at(self.view, x, y) else "text")

    def _on_click(self, gesture, _n: int, x: float, y: float) -> None:
        if self.buf.get_has_selection():
            return
        url = self.md.url_at(self.view, x, y)
        if url:
            try:
                Gio.AppInfo.launch_default_for_uri(url, None)
                self.set_status(f"↗ {url}")
            except GLib.Error as e:
                self.set_status(f"не открыть: {e.message}")

    def _update_status_idle(self) -> None:
        """Футер (модель · инструменты · режим) + шапка. Строку состояния не трогает."""
        llm = self.cfg["llm"]
        self.pill_model.set_text(llm["model"] or "модель не задана")
        hub = self.app_ref.hub
        if hub is None:
            self.pill_tools.set_text("MCP запускается…")
            self.pill_tools.remove_css_class("on")
            self.pill_tools.add_css_class("warn")
        else:
            n = sum(len(sv.tools) for sv in hub.servers)
            self.pill_tools.set_text(f"{n} инстр." + (f" · ошибки: {len(hub.errors)}" if hub.errors else ""))
            self.pill_tools.remove_css_class("warn" if not hub.errors else "on")
            self.pill_tools.add_css_class("warn" if hub.errors else "on")
        marks = []
        if self.always_search:
            marks.append("🔎 всегда искать")
        if self.history:
            marks.append(f"💬 {len(self.history) // 2}")
        self.pill_mode.set_text("  ·  ".join(marks))
        self.pill_mode.set_visible(bool(marks))
        self._update_header()

    def _update_header(self) -> None:
        page = self.views.page if self.views.active else None
        for css in [c for c in (self.chip.get_css_classes() or []) if c.startswith("c-")]:
            self.chip.remove_css_class(css)
        self.ctx.remove_css_class("mono")
        if page is not None:                  # страница сервера: шапку, поле и подсказку задаёт она
            self.chip.set_text(page.get("title") or self.views.server.name)
            if page.get("color"):
                self.chip.add_css_class(f"c-{page['color']}")
            self.ctx.set_text(page.get("context") or "")
            if page.get("mono"):
                self.ctx.add_css_class("mono")
            inp = page.get("input") or {}
            self.entry.set_placeholder_text(inp.get("placeholder") or self.PLACEHOLDER)
            self.glyph.set_text(inp.get("glyph") or "❯")
            self.keys.set_text(page.get("keys") or self.KEYS_VIEW)
            server = self.views.server
            self._set_buttons([(server, b) for b in page.get("buttons") or []])
        else:
            self.chip.set_text("Чат")
            self.ctx.set_text("")
            self.entry.set_placeholder_text(self.PLACEHOLDER_ASK if self._question else self.PLACEHOLDER)
            self.glyph.set_text("❓" if self._question else ("🔎" if self.always_search else "❯"))
            self.keys.set_text(self.KEYS_CHAT)
            hub = self.app_ref.hub
            self._set_buttons(hub.buttons() if hub else [])
        self.btn_close.set_visible(page is not None)

    def _set_buttons(self, buttons: list[tuple]) -> None:
        """Пересобрать кнопки в шапке — только если набор изменился (страница обновляется каждые пару секунд)."""
        key = json.dumps([(s.name, b) for s, b in buttons], ensure_ascii=False, sort_keys=True)
        if key == self._hbtns_key:
            return
        self._hbtns_key = key
        child = self.hbtns.get_first_child()
        while child:
            self.hbtns.remove(child)
            child = self.hbtns.get_first_child()
        for server, b in buttons:
            btn = Gtk.Button(label=b.get("label", "?"))
            btn.add_css_class("qa-btn")
            if b.get("style") in ("accent", "danger"):
                btn.add_css_class(b["style"])
            btn.connect("clicked", lambda _b, s=server, a=b.get("action"): a and self.views.run(s, [a]))
            self.hbtns.append(btn)

    def set_status(self, text: str) -> bool:
        self.status.set_text(text)
        self.status.set_visible(bool(text))
        self.status.remove_css_class("err")
        if text.startswith(("⚠", "❌", "ошибка", "не удалось")):
            self.status.add_css_class("err")
        return False

    def set_busy(self, on: bool) -> None:
        self.busy.set_visible(on)
        self.busy.start() if on else self.busy.stop()

    def back_to_chat(self) -> None:
        """Кнопка ✕ в шапке: со страницы сервера — обратно в чат."""
        self.views.close()
        self.entry.grab_focus()
