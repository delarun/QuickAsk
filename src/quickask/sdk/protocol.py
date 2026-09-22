# QuickAsk — https://github.com/delarun/quickask — MIT
"""Протокол расширения интерфейса QuickAsk поверх MCP, версия 1.

Общий словарь для SDK (сторона сервера) и для QuickAsk (сторона клиента). Только константы,
без зависимостей, — чтобы им мог пользоваться и сервер, написанный не на этом SDK.

Сервер объявляет поддержку в ответе на initialize:

    "capabilities": {"tools": {}, "experimental": {"quickask/ui": {"version": 1}}}

и тогда QuickAsk вызывает у него три метода сверх обычного MCP:

    quickask/ui/manifest  {}               → {"buttons": [Button], "commands": [Command]}
    quickask/ui/render    {view, args}     → Page
    quickask/ui/command   {name, text}     → {"actions": [Action]}

Результат tools/call может нести действия в `_meta`:

    {"content": [...], "_meta": {"quickask/actions": [Action]}}

Формат Page, Block, Action — в README рядом с этим файлом.
"""

CAPABILITY = "quickask/ui"
VERSION = 1

MANIFEST = "quickask/ui/manifest"
RENDER = "quickask/ui/render"
COMMAND = "quickask/ui/command"

META_ACTIONS = "quickask/actions"      # ключ в _meta результата tools/call

# Действия, которые умеет выполнять QuickAsk.
ACTIONS = frozenset({
    "view",      # открыть страницу сервера: {view, args, replace?, label?, server?}
    "call",      # вызвать инструмент: {tool, args, then?, server?}; без then — обновить страницу
    "refresh",   # перерисовать текущую страницу
    "back",      # на страницу назад, из первой — в чат
    "chat",      # закрыть страницы, вернуться в чат
    "hide",      # спрятать окно QuickAsk
    "status",    # строка состояния: {text}
    "show",      # показать Markdown отдельным обменом в чате: {markdown, title?}
})

# Блоки страницы.
BLOCKS = frozenset({"list", "card", "empty", "markdown"})

# Оформление — фиксированный набор, у каждого стиля свой CSS-класс в теме QuickAsk.
CARD_STYLES = frozenset({"plain", "info", "warn", "error", "live", "say", "me", "bad"})
LINE_STYLES = frozenset({
    "text", "hint", "say", "cmd", "out", "time",    # в карточках
    "task", "ask", "now", "done",                   # в строках списка
})
BUTTON_STYLES = frozenset({"accent", "danger"})
COLORS = frozenset({"blue", "green", "yellow", "red", "mauve", "peach", "teal", "grey"})

# Подстановка в аргументы действия из поля ввода страницы.
TEXT_PLACEHOLDER = "$text"
