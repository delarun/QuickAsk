# QuickAsk — https://github.com/delarun/quickask — MIT
"""Оформление: CSS-шаблон и сетка отступов (ui.density масштабирует её целиком)."""
from __future__ import annotations

CSS = """
/* @edge@ — внешнее поле полос, @pad@/@padv@ — поля внутри карточек,
   @band@ — вертикальные поля шапки/футера. Числа подставляет build_css() из ui.density.

   Catppuccin Mocha: base #1e1e2e · mantle #181825 · surface0 #313244 · surface1 #45475a
   text #cdd6f4 · subtext #a6adc8 · overlay #6c7086 · blue #89b4fa · green #a6e3a1
   yellow #f9e2af · red #f38ba8 · mauve #cba6f7 */

window.quickask { background-color: transparent; }

.qa-root {
  background-color: alpha(#1e1e2e, 0.97);
  border: 1px solid alpha(#cdd6f4, 0.10);
  border-radius: 18px;
  padding: 0;
  box-shadow: 0 18px 50px alpha(#000000, 0.55);
}

/* ── шапка: режим + контекст + действия ───────────────────────────────── */
.qa-header {
  padding: @band@px @edge@px @bandb@px @edge@px;
  border-bottom: 1px solid alpha(#cdd6f4, 0.07);
}
.qa-chip {
  font-size: 11px; font-weight: bold; padding: 2px 9px; border-radius: 7px;
  background-color: alpha(#89b4fa, 0.18); color: #89b4fa;
}
@chips@
.qa-context { font-size: 12px; color: #a6adc8; }
.qa-context.mono { font-family: monospace; font-size: 11.5px; }

.qa-btn {
  font-size: 11.5px; padding: 3px 10px; min-height: 0; border: none; border-radius: 8px;
  background-color: alpha(#cdd6f4, 0.08); color: #a6adc8; box-shadow: none;
}
.qa-btn:hover  { background-color: alpha(#cdd6f4, 0.16); color: #cdd6f4; }
.qa-btn.accent { background-color: alpha(#89b4fa, 0.16); color: #89b4fa; }
.qa-btn.accent:hover { background-color: alpha(#89b4fa, 0.28); color: #b4c8fb; }
.qa-btn.danger { background-color: alpha(#f38ba8, 0.13); color: #f38ba8; }
.qa-btn.danger:hover { background-color: alpha(#f38ba8, 0.26); }

/* ── поле ввода ───────────────────────────────────────────────────────── */
.qa-field {
  background-color: #313244; border-radius: 14px; padding: 3px @pad@px;
  border: 1px solid transparent; margin: @gap@px @edge@px 0 @edge@px;
}
.qa-field:focus-within { border-color: alpha(#89b4fa, 0.55); background-color: #363a4f; }
.qa-field.asking:focus-within { border-color: alpha(#f9e2af, 0.6); }
.qa-glyph { font-size: 15px; color: #6c7086; }
.qa-field:focus-within .qa-glyph { color: #89b4fa; }
.qa-entry, .qa-entry text {
  font-size: 19px; background-color: transparent; background-image: none; color: #cdd6f4;
  caret-color: #f5e0dc; border: none; box-shadow: none; outline: none; padding: 9px 0;
  min-height: 0;
}
.qa-entry text selection { background-color: alpha(#89b4fa, 0.35); color: #ffffff; }

/* ── строка состояния и футер ─────────────────────────────────────────── */
.qa-status { font-size: 11.5px; color: #a6adc8; padding: @band@px @edge@px 0 @edge@px; }
.qa-status.err { color: #f38ba8; }
.qa-footer { padding: @band@px @edge@px @bandb@px @edge@px; border-top: 1px solid alpha(#cdd6f4, 0.06); }
.qa-pill {
  font-size: 10.5px; padding: 1px 8px; border-radius: 6px;
  background-color: alpha(#cdd6f4, 0.06); color: #7f849c;
}
.qa-pill.on { background-color: alpha(#a6e3a1, 0.15); color: #a6e3a1; }
.qa-pill.warn { background-color: alpha(#f9e2af, 0.15); color: #f9e2af; }
.qa-keys { font-size: 10.5px; color: #585b70; }

/* ── карточка вопроса ─────────────────────────────────────────────────── */
.qa-question {
  background-color: alpha(#f9e2af, 0.10); border-left: 3px solid #f9e2af;
  border-radius: 12px; padding: @padv@px @pad@px; margin: @gap@px @edge@px 0 @edge@px;
}
.qa-question .q { color: #f9e2af; font-size: 14.5px; font-weight: bold; }
.qa-question button {
  font-size: 13px; padding: 4px 14px; min-height: 0; border: none; border-radius: 9px;
  background-color: alpha(#f9e2af, 0.16); color: #f5e0dc; box-shadow: none;
}
.qa-question button:hover { background-color: alpha(#f9e2af, 0.32); color: #ffffff; }

/* ── вложение ─────────────────────────────────────────────────────────── */
.qa-attach {
  background-color: alpha(#313244, 0.7); border-radius: 12px; padding: 10px @pad@px;
  margin: @gap@px @edge@px 0 @edge@px;
}
.qa-attach label { font-size: 11.5px; color: #a6adc8; }

/* ── тело: ответ, рассуждения, список агентов ─────────────────────────── */
.qa-body { padding: @bodyt@px @edge@px @bodyb@px @edge@px; }

/* карточка ответа: тот же язык, что у карточек агента — фон, скругление, поля */
.qa-msg {
  background-color: alpha(#cdd6f4, 0.04);
  border: 1px solid alpha(#cdd6f4, 0.06);
  border-radius: 14px;
  padding: @padv@px @pad@px;
}
/* строка вопроса — якорь, по которому глаз находит начало обмена при скролле;
   её ❯ стоит ровно под ❯ поля ввода */
.qa-q { font-size: 13px; color: #89b4fa; padding-left: @pad@px; }
.qa-answer, .qa-answer text { background-color: transparent; color: #cdd6f4; font-size: 15px; }
.qa-answer text selection { background-color: alpha(#89b4fa, 0.35); color: #ffffff; }

.qa-think-expander { margin-left: @pad@px; }
.qa-think-expander > title {
  padding: 2px 8px 2px 0; border-radius: 8px; min-height: 0;
}
.qa-think-expander > title:hover { background-color: alpha(#cdd6f4, 0.06); }
.qa-think-expander > title label { font-size: 11.5px; color: #7f849c; }
.qa-think-expander > title:hover label { color: #a6adc8; }
.qa-think, .qa-think text {
  background-color: alpha(#181825, 0.8); color: #9399b2;
  font-size: 12.5px; font-style: italic; border-radius: 10px;
}

/* ── страницы MCP-серверов (quickask.sdk): списки и карточки ─────────────
   Имена классов — это стили из протокола: row → .v-row, card(style=…) → .v-card.<style>,
   line(style=…) → .l-<style>. Сервер выбирает стиль, как он выглядит — решает тема. */
.v-list { background-color: transparent; }
.v-list > row {
  border-radius: 14px; padding: @padv@px @pad@px; margin: 5px 0;
  background-color: alpha(#313244, 0.45); border: 1px solid transparent;
}
.v-list > row:hover { background-color: alpha(#89b4fa, 0.14); border-color: alpha(#89b4fa, 0.3); }
.v-list > row.inert:hover { background-color: alpha(#313244, 0.45); border-color: transparent; }
.r-title { font-size: 13.5px; font-weight: bold; color: #cdd6f4; }
.r-meta  { font-size: 10.5px; color: #6c7086; }
.r-dot   { font-size: 12px; }
.v-empty { font-size: 13px; color: #6c7086; padding: 14px 4px; }

.v-card {
  background-color: alpha(#181825, 0.75); border-radius: 12px; padding: 11px @pad@px;
  border-left: 2px solid alpha(#cdd6f4, 0.10);
}
.v-card.info, .v-card.warn, .v-card.error {
  background-color: alpha(#89b4fa, 0.10); border-radius: 14px; padding: @padv@px @pad@px;
  border-left: 3px solid #89b4fa;
}
.v-card.warn  { background-color: alpha(#f9e2af, 0.10); border-left-color: #f9e2af; }
.v-card.error { background-color: alpha(#f38ba8, 0.10); border-left-color: #f38ba8; }
.v-card.live  { border-left-color: #a6e3a1; background-color: alpha(#a6e3a1, 0.07); }
.v-card.say   { background-color: transparent; border-left-color: alpha(#cba6f7, 0.5); }
.v-card.me    { background-color: alpha(#cba6f7, 0.09); border-left-color: #cba6f7; }
.v-card.bad   { border-left-color: #f38ba8; }

.l-text { font-size: 14px; color: #cdd6f4; }
.l-hint { font-size: 11.5px; color: #7f849c; }
.l-say  { font-size: 13px; color: #cdd6f4; }
.l-cmd  { font-size: 12.5px; font-family: monospace; color: #a6e3a1; }
.l-out  { font-size: 11.5px; font-family: monospace; color: #7f849c; }
.l-time { font-size: 10px; color: #585b70; font-family: monospace; }
.l-task { font-size: 12.5px; color: #a6adc8; }
.l-ask  { font-size: 12.5px; color: #f9e2af; }
.l-now  { font-size: 11.5px; font-family: monospace; color: #a6e3a1; }
.l-done { font-size: 12px; color: #7f849c; }

/* действия, которые сервер приложил к ответу модели («Открыть агента») */
.qa-turn-actions { padding-left: @pad@px; }

scrollbar { background-color: transparent; }
scrollbar slider { background-color: alpha(#cdd6f4, 0.18); border-radius: 8px; min-width: 6px; }
scrollbar slider:hover { background-color: alpha(#cdd6f4, 0.3); }
"""

# Базовые отступы (px) при density = 1.0. Всё, что задаёт «воздух» интерфейса, — здесь.
SPACING = {
    "edge":  20,   # внешнее поле полос: шапка, поле ввода, лента, футер
    "pad":   16,   # горизонтальное поле внутри карточек (и отступ строки вопроса)
    "padv":  14,   # вертикальное поле внутри карточек
    "band":  12,   # верхнее поле шапки/футера/статуса
    "bandb": 13,   # нижнее поле шапки/футера
    "gap":   14,   # отступ поля ввода (и карточки вопроса) от того, что выше
    "bodyt": 18,   # от поля ввода до ленты
    "bodyb": 12,   # от ленты до футера
    "turn":  12,   # между вопросом, рассуждениями и ответом внутри обмена
    "turns": 26,   # между обменами
    "steps": 10,   # между блоками страницы MCP-сервера
}


def spacing(cfg: dict) -> dict:
    """Отступы с учётом ui.density: 1.0 — как задумано, 1.4 — заметно просторнее."""
    k = float(cfg["ui"].get("density", 1.0))
    return {name: max(0, round(v * k)) for name, v in SPACING.items()}


# Палитра, из которой серверы выбирают цвет чипа и меток (protocol.COLORS) — Catppuccin Mocha.
PALETTE = {"blue": "#89b4fa", "green": "#a6e3a1", "yellow": "#f9e2af", "red": "#f38ba8",
           "mauve": "#cba6f7", "peach": "#fab387", "teal": "#94e2d5", "grey": "#6c7086"}


def build_css(sp: dict) -> str:
    chips = "\n".join(f".qa-chip.c-{n} {{ background-color: alpha({c}, 0.18); color: {c}; }}"
                       for n, c in PALETTE.items())
    css = CSS.replace("@chips@", chips)
    for name, v in sp.items():
        css = css.replace(f"@{name}@", str(v))
    return css
