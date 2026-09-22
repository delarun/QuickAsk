# QuickAsk — https://github.com/delarun/quickask — MIT
"""Конструкторы интерфейса: страницы, блоки, кнопки и действия.

Каждая функция возвращает обычный dict — ровно то, что уйдёт в QuickAsk по JSON-RPC.
Писать словари руками тоже можно; эти функции только проверяют стили и убирают пустые поля.

    from quickask.sdk import ui

    ui.page("Агенты", color="peach", refresh_ms=3000, blocks=[
        ui.rows([ui.row("0922-1350-yt", meta="done · 4 мин назад", dot=ui.dot("●", "blue"),
                        lines=[ui.line("скачать плейлист", "task")],
                        action=ui.open("agent", id="0922-1350-yt"))]),
    ])
"""
from __future__ import annotations

from typing import Any

from . import protocol as P

Action = dict[str, Any]
Block = dict[str, Any]


def _check(value: str | None, allowed: frozenset, what: str) -> None:
    if value is not None and value not in allowed:
        raise ValueError(f"{what} {value!r}: допустимо {', '.join(sorted(allowed))}")


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# ── действия ────────────────────────────────────────────────────────────────

def open(view: str, *, replace: bool = False, label: str = "", server: str = "", **args) -> Action:  # noqa: A001
    """Открыть страницу `view` этого сервера. replace — не класть текущую в историю «назад».
    label — подпись кнопки, если действие пришло из ответа модели и QuickAsk предложит его кнопкой."""
    return _clean({"type": "view", "view": view, "args": args, "replace": replace or None,
                   "label": label, "server": server})


def call(tool: str, *, then: list[Action] | None = None, server: str = "", **args) -> Action:
    """Вызвать инструмент этого сервера. Первая строка результата уйдёт в строку состояния,
    действия из его _meta выполнятся. then — что сделать после; по умолчанию обновить страницу.
    В аргументах можно писать "$text" — подставится текст из поля ввода страницы."""
    a = _clean({"type": "call", "tool": tool, "args": args, "server": server})
    if then is not None:
        a["then"] = then
    return a


def refresh() -> Action:
    return {"type": "refresh"}


def back() -> Action:
    return {"type": "back"}


def chat() -> Action:
    return {"type": "chat"}


def hide() -> Action:
    """Спрятать окно QuickAsk — например, после того как инструмент переключил окно или вкладку."""
    return {"type": "hide"}


def status(text: str) -> Action:
    return {"type": "status", "text": text}


def show(markdown: str, title: str = "") -> Action:
    """Показать Markdown отдельным обменом в ленте чата. title — строка вопроса над ним;
    по умолчанию — команда, которая это вызвала."""
    return _clean({"type": "show", "markdown": markdown, "title": title})


# ── блоки страницы ──────────────────────────────────────────────────────────

def line(text: str, style: str = "text", *, wrap: bool | None = None) -> dict:
    """Строка текста. Стили: text, hint, say, cmd, out, time — в карточках; task, ask, now, done — в списке.
    wrap — переносить строки; по умолчанию переносится всё, кроме out и строк списка."""
    _check(style, P.LINE_STYLES, "стиль строки")
    d: dict = {"text": text, "style": style}
    if wrap is not None:
        d["wrap"] = wrap
    return d


def dot(text: str = "●", color: str = "grey") -> dict:
    """Цветная метка слева от строки списка. Цвета: blue, green, yellow, red, mauve, peach, teal, grey."""
    _check(color, P.COLORS, "цвет")
    return {"text": text, "color": color}


def row(title: str, *, meta: str = "", dot: dict | None = None, lines: list[dict] = (),  # noqa: A002
        action: Action | None = None) -> dict:
    """Строка списка: заголовок, справа meta, под ними lines. С action — кликабельна."""
    return _clean({"title": title, "meta": meta, "dot": dot, "lines": list(lines), "action": action})


def rows(items: list[dict]) -> Block:
    """Список строк (см. row)."""
    return {"type": "list", "rows": list(items)}


def card(*lines: dict, style: str = "plain", time: str = "") -> Block:
    """Карточка из строк. Стили: plain, info, warn, error — сводки; live, say, me, bad — шаги.
    time — метка слева от первой строки (моноширинным, серым)."""
    _check(style, P.CARD_STYLES, "стиль карточки")
    return _clean({"type": "card", "style": style, "time": time, "lines": list(lines)})


def empty(text: str) -> Block:
    """Заглушка «ничего нет» — серым текстом с отступами."""
    return {"type": "empty", "text": text}


def markdown(text: str) -> Block:
    """Markdown в карточке, тем же рендером, что и ответы модели."""
    return {"type": "markdown", "text": text}


def button(label: str, action: Action, style: str | None = None) -> dict:
    """Кнопка в шапке. Стили: accent, danger или без стиля."""
    _check(style, P.BUTTON_STYLES, "стиль кнопки")
    return _clean({"label": label, "style": style, "action": action})


def entry(placeholder: str, submit: Action, glyph: str = "") -> dict:
    """Поле ввода страницы: Enter выполнит submit, "$text" в его аргументах заменится введённым."""
    return _clean({"placeholder": placeholder, "submit": submit, "glyph": glyph})


def page(title: str, *, color: str | None = None, context: str = "", mono: bool = False,
         buttons: list[dict] = (), blocks: list[Block] = (), input: dict | None = None,  # noqa: A002
         keys: str = "", refresh_ms: int = 0, scroll: str = "top") -> dict:
    """Страница целиком.

    title, color   — чип в шапке;          context, mono — текст рядом с ним
    buttons        — кнопки в шапке;       blocks        — содержимое
    input          — что делает Enter (ui.entry); без него текст уходит в чат как обычный вопрос
    keys           — подсказка в футере;   refresh_ms    — перерисовывать каждые N мс (0 — нет)
    scroll         — "bottom": держаться низа, как лог; "top" — как список
    """
    _check(color, P.COLORS, "цвет")
    if scroll not in ("top", "bottom"):
        raise ValueError("scroll: top или bottom")
    return _clean({"title": title, "color": color, "context": context, "mono": mono or None,
                   "buttons": list(buttons), "blocks": list(blocks), "input": input, "keys": keys,
                   "refresh_ms": refresh_ms or None, "scroll": None if scroll == "top" else scroll})
