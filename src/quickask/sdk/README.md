# quickask.sdk

SDK для MCP-серверов, которые не только дают модели инструменты, но и добавляют что-то в окно QuickAsk: кнопки в шапке, слэш-команды и целые страницы — списки, карточки, Markdown, с кнопками, полем ввода и автообновлением.

MCP-сервер — отдельный процесс, и виджеты GTK он передать не может. Поэтому интерфейс он **описывает** JSON-ом, а QuickAsk его рисует. Описание идёт расширением MCP через `capabilities.experimental`. Другие клиенты — Claude Desktop, Cursor и прочие — его просто не заметят и увидят обычный MCP-сервер с инструментами.

Оба встроенных сервера написаны на этом SDK: [`mcps/agent.py`](../../../mcps/agent.py) — страницы фоновых агентов, [`mcps/desk.py`](../../../mcps/desk.py) — команды `/windows`, `/tabs` и «спрятать окно после переключения». В самом QuickAsk про агентов и окна нет ни строчки.

Только стандартная библиотека Python.

---

## Пример: заметки

Сервер целиком — инструменты для модели, страница, команда и кнопка:

```python
import time

from quickask.sdk import Result, Server, ui

srv = Server("notes")
NOTES: list[dict] = []


@srv.tool("add_note", "Сохранить заметку пользователя", {"text": {"type": "string"}}, ["text"])
def add_note(text: str) -> Result:
    NOTES.append({"text": text, "at": time.strftime("%H:%M")})
    return Result(f"OK: заметка №{len(NOTES)} сохранена", [ui.open("notes", label="Открыть заметки")])


@srv.tool("clear_notes", "Удалить все заметки")
def clear_notes() -> str:
    NOTES.clear()
    return "OK: заметки удалены"


@srv.view("notes")
def notes() -> dict:
    cards = [ui.card(ui.line(n["text"]), time=n["at"]) for n in reversed(NOTES)]
    return ui.page(
        "Заметки", color="teal", context=f"{len(NOTES)} шт.",
        buttons=[ui.button("Очистить", ui.call("clear_notes"), style="danger")] if NOTES else [],
        blocks=cards or [ui.empty("Заметок пока нет — напиши что-нибудь ниже.")],
        input=ui.entry("Новая заметка…", ui.call("add_note", text="$text"), glyph="📝"),
    )


@srv.command("/note", "сохранить заметку", "<текст>")
def cmd_note(text: str):
    return add_note(text).actions if text else "использование: /note <текст>"


srv.button("Заметки", ui.open("notes"))

if __name__ == "__main__":
    srv.run()
```

Подключить в `~/.config/quickask/config.toml`:

```toml
[[mcp]]
name    = "notes"
command = ["python3", "/путь/к/notes.py"]
env     = { PYTHONPATH = "/путь/к/QuickAsk/src" }   # не нужно, если QuickAsk стоит через pip
```

Что получится:

- в шапке чата появится кнопка **Заметки**, в `/help` — команда `/note`;
- на странице Enter в поле ввода вызовет `add_note` с введённым текстом (`$text`) и перерисует страницу;
- если модель сама вызовет `add_note` в ответ на «запомни, что…», под её ответом появится кнопка **Открыть заметки**. Ответ при этом не пропадёт — переход по кнопке, а не принудительно.

---

## API

### `Server(name, version="0.1")`

`name` уходит в `serverInfo.name`. По нему работают deep link'и (`quickask --open notes:notes`), даже если в `config.toml` сервер назван иначе.

| | |
|---|---|
| `@srv.tool(name, description, params=None, required=None)` | Инструмент для модели. `params` — JSON Schema свойств. Функция возвращает `str` или `Result`; исключение уйдёт модели как `isError`. |
| `@srv.view(name)` | Страница. Функция получает `args` из `ui.open(name, **args)` и возвращает `ui.page(...)`. Аргументы из deep link приходят строками и приводятся к типу значения по умолчанию: `def view(id: str, lines: int = 14)`. |
| `@srv.command("/name", description="", usage="")` | Слэш-команда. Функция получает текст после имени и возвращает действие, список действий, строку (станет строкой состояния) или `None`. Разбор аргументов — на стороне сервера. |
| `srv.button(label, action, style=None)` | Кнопка в шапке, пока открыт чат. |
| `srv.run()` | Цикл stdio. Каждый запрос обрабатывается в своём потоке: медленный инструмент не тормозит перерисовку страницы. |

### `Result(text, actions=())`

Результат инструмента: `text` видит модель, `actions` — интерфейс.

```python
return Result("OK: переключил окно", [ui.hide()])
```

Из ответа модели QuickAsk выполняет только `hide` (после паузы `ui.hide_after_action_ms`). Переходы — `view`, `call`, `show` — он предлагает кнопками под ответом, с подписью из `label`. Когда тот же инструмент вызывают кнопкой или командой, выполняется всё сразу.

### Действия — `ui.*`

| | |
|---|---|
| `ui.open(view, replace=False, label="", server="", **args)` | Открыть страницу. `replace` — не класть текущую в историю. Переход на уже открытую страницу просто обновляет её. |
| `ui.call(tool, then=None, server="", **args)` | Вызвать инструмент. Первая строка результата уйдёт в строку состояния, его `actions` выполнятся. `then` — что сделать после; по умолчанию — обновить страницу. |
| `ui.refresh()` · `ui.back()` · `ui.chat()` · `ui.hide()` | Перерисовать страницу · назад (из первой — в чат) · в чат · спрятать окно |
| `ui.status(text)` | Строка состояния |
| `ui.show(markdown, title="")` | Отдельный обмен в ленте чата. `title` — строка вопроса; по умолчанию — команда, которая это вызвала. |

`server` — имя другого сервера, если действие адресовано не себе.

В аргументах `ui.call` и `ui.open` можно писать `"$text"` — подставится текст из поля ввода страницы.

### Страница — `ui.page`

```python
ui.page(title, color=None, context="", mono=False, buttons=(), blocks=(), input=None,
        keys="", refresh_ms=0, scroll="top")
```

| | |
|---|---|
| `title`, `color` | Чип в шапке. Цвета: `blue` `green` `yellow` `red` `mauve` `peach` `teal` `grey` |
| `context`, `mono` | Текст рядом с чипом; `mono` — моноширинным (для id) |
| `buttons` | Кнопки в шапке — `ui.button(label, action, style)`, стили `accent` и `danger` |
| `blocks` | Содержимое, см. ниже |
| `input` | `ui.entry(placeholder, submit, glyph="")`: что делает Enter. Без `input` текст уходит в чат обычным вопросом |
| `keys` | Подсказка в футере |
| `refresh_ms` | Перерисовывать каждые N мс, пока окно видно |
| `scroll` | `"bottom"` — держаться низа, пока пользователь не отлистал вверх (лог); `"top"` — остаться на месте (список) |

### Блоки

| | |
|---|---|
| `ui.rows([ui.row(title, meta="", dot=None, lines=(), action=None)])` | Список. `dot=ui.dot("●", "green")` — метка слева; строка с `action` кликабельна |
| `ui.card(*lines, style="plain", time="")` | Карточка. Стили: `info` `warn` `error` — сводки с цветной полосой; `plain` `live` `say` `me` `bad` — шаги. `time` — метка слева от первой строки |
| `ui.line(text, style="text", wrap=None)` | Строка. В карточках: `text` `hint` `say` `cmd` `out` `time`; в списке: `task` `ask` `now` `done`. По умолчанию переносится всё, кроме `out` и строк списка |
| `ui.empty(text)` | Заглушка «ничего нет» |
| `ui.markdown(text)` | Markdown тем же рендером, что и ответы модели |

Стили — фиксированный набор, у каждого свой CSS-класс в теме QuickAsk. Сервер выбирает смысл («предупреждение», «команда», «вывод»), а цвета, шрифты и отступы решает тема, в том числе `ui.density`. Поэтому страницы сторонних серверов выглядят так же, как встроенные.

---

## Протокол

Для сервера на другом языке или без SDK. Константы — в [`protocol.py`](protocol.py).

Сервер объявляет поддержку в ответе на `initialize`:

```json
{"capabilities": {"tools": {}, "experimental": {"quickask/ui": {"version": 1}}},
 "serverInfo": {"name": "notes", "version": "0.1"}}
```

После этого QuickAsk вызывает три метода сверх обычного MCP:

| Метод | Параметры | Ответ | Когда |
|---|---|---|---|
| `quickask/ui/manifest` | `{}` | `{"buttons": [Button], "commands": [{name, usage, description}]}` | один раз после `initialize` |
| `quickask/ui/render` | `{"view": "notes", "args": {}}` | `Page` | при открытии страницы и каждые `refresh_ms` |
| `quickask/ui/command` | `{"name": "/note", "text": "купить молоко"}` | `{"actions": [Action]}` | пользователь ввёл команду |

Действия из результата инструмента — в `_meta`:

```json
{"content": [{"type": "text", "text": "OK: сохранил"}],
 "_meta": {"quickask/actions": [{"type": "view", "view": "notes", "label": "Открыть заметки"}]}}
```

Формы объектов — ровно то, что возвращают функции `ui.*`:

```jsonc
// Page
{"title": "Заметки", "color": "teal", "context": "2 шт.", "mono": false,
 "buttons": [{"label": "Очистить", "style": "danger", "action": {...}}],
 "blocks": [...], "input": {"placeholder": "…", "glyph": "📝", "submit": {...}},
 "keys": "…", "refresh_ms": 3000, "scroll": "bottom"}

// Block
{"type": "list", "rows": [{"title": "…", "meta": "…", "dot": {"text": "●", "color": "green"},
                           "lines": [{"text": "…", "style": "task"}], "action": {...}}]}
{"type": "card", "style": "info", "time": "09:57", "lines": [{"text": "…", "style": "cmd", "wrap": true}]}
{"type": "empty", "text": "…"}
{"type": "markdown", "text": "…"}

// Action
{"type": "view", "view": "notes", "args": {}, "replace": false, "label": "…", "server": "…"}
{"type": "call", "tool": "add_note", "args": {"text": "$text"}, "then": [...], "server": "…"}
{"type": "refresh"} {"type": "back"} {"type": "chat"} {"type": "hide"}
{"type": "status", "text": "…"}
{"type": "show", "markdown": "…", "title": "…"}
```

Незнакомые поля и стили QuickAsk пропускает: блок неизвестного типа не рисуется, неизвестный стиль строки заменяется на `text`.

## Deep link

```sh
quickask --open notes:notes
quickask --open agent:agent?id=0922-1350-yt
```

Открывает страницу в уже запущенной копии, а если её нет — запускает. Так агент по клику на уведомление открывает свою страницу, ничего не зная о том, как устроено окно.
