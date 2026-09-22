# QuickAsk

Всплывающее окно к локальной LLM для Wayland — Spotlight, но для вопросов, окон, файлов и фоновых задач.

Одна резидентная копия висит в памяти, хоткей показывает и прячет окно. Ответ стримится с разметкой Markdown, а модель может звать инструменты через MCP: искать в интернете, переключать окна niri и вкладки Chrome, читать файлы, запускать фоновых агентов в Docker и следить за ними.

Чистый Python + GTK4, без Electron и без WebKit. Из зависимостей — только PyGObject.

---

## Что умеет

**Диалог**

- стрим ответа из любого OpenAI-совместимого `/v1/chat/completions` — llama-server, ai-gate, Cerebras, OpenRouter;
- рассуждения модели (`reasoning_content` или `<think>…</think>`) идут в отдельный сворачиваемый блок 💭;
- Markdown рендерится тегами `Gtk.TextBuffer`: заголовки, списки, цитаты, таблицы, блоки кода и кликабельные ссылки;
- предыдущие обмены остаются лентой со скроллом — глубина совпадает с тем, что помнит модель (`history_turns`);
- картинка из буфера обмена по `Ctrl+V` уходит в запрос как `image_url` (нужна VLM, см. `vision_model`).

**Инструменты (MCP, stdio JSON-RPC 2.0)**

- native function calling: модель сама выбирает инструмент и получает результат;
- принудительный веб-поиск префиксом `/s вопрос` или тумблером `Ctrl+S`;
- встроенный `ask_user` — модель задаёт уточняющий вопрос и показывает кнопки-варианты;
- **серверы расширяют интерфейс**: кнопки в шапке, слэш-команды, свои страницы. Для этого есть [SDK](src/quickask/sdk/), и оба встроенных сервера написаны на нём — окно само про агентов и окна ничего не знает.

**Фоновые агенты**

- «скачай этот плейлист и разбей по трекам» → отдельный контейнер, который сам пишет код и выполняет его;
- список агентов, живой лог, прерывание текущей команды, ответ на вопрос агента, продолжение задачи;
- `notify-send` с кнопкой «Открыть диалог», когда агент закончил или о чём-то спрашивает.

---

## Требования

Готовому бинарнику не нужны ни Python, ни GTK — всё внутри. Остальное зависит от того, какие возможности нужны:

| | Пакет | Зачем |
|---|---|---|
| **желательно** | `gtk4-layer-shell` | окно поверх всего с захватом клавиатуры (только Linux/Wayland, в бинарник для Linux уже вшит); без него — обычное окно, которому нужно правило «floating» в компоузиторе |
| | `uv` | `uvx` поднимает внешние MCP-серверы (поиск, fetch) |
| | `wl-clipboard` | вставка картинки из буфера (`wl-paste`); без него — через буфер GTK |
| | `libnotify` | уведомления от фоновых агентов |
| **для агентов** | `docker` (или Docker Desktop) | песочница, в которой агент выполняет свой код |
| | Chrome с `--remote-debugging-port=9222` | список и переключение вкладок |
| | `fd` | ускоряет `find_files` (иначе `find`) |
| **из исходников** | `python` ≥ 3.11, `gtk4`, `python-gobject` | интерфейс |

Arch, всё сразу:

```sh
sudo pacman -S python gtk4 python-gobject gtk4-layer-shell uv wl-clipboard libnotify fd docker
```

### Платформы

| | Linux x86_64 / aarch64 | Windows x86_64 | macOS arm64 |
|---|---|---|---|
| окно, стрим, Markdown, MCP | ✓ | ✓ | ✓ |
| где живёт | резидентно, хоткей в компоузиторе | в трее, глобальный хоткей `ui.hotkey` | обычное окно |
| окно поверх всего, по `ui.margin_top` | ✓ под Wayland (layer-shell) | ✓ | — |
| повторный запуск — показать/спрятать | ✓ (D-Bus) | ✓ | ✓ |
| `desk`: окна и воркспейсы | ✓ под niri | — | — |
| `desk`: вкладки Chrome, файлы, буфер | ✓ | ✓ | ✓ |
| фоновые агенты | ✓ | через Docker Desktop | через Docker Desktop |

Linux-бинарник собирается на Ubuntu 24.04 и требует glibc ≥ 2.39 (Arch, Fedora 40+, Ubuntu 24.04+, Debian 13+).

---

## Установка

### Вариант А: готовый бинарник

Скачайте файл под свою систему со страницы [Releases](https://github.com/delarun/quickask/releases) и положите в `PATH`:

```sh
install -Dm755 quickask-linux-x86_64 ~/.local/bin/quickask
quickask --version        # quickask 0.1.0  GTK 4.x  layer-shell: yes
```

Встроенные MCP-серверы едут внутри: `quickask --mcp desk`, `quickask --mcp agent`. Дальше — с шага **2** ниже.

### Вариант Б: из исходников

Симлинк на `quickask.py` — правки в репозитории видны сразу, без переустановки:

```sh
git clone https://github.com/delarun/quickask.git ~/Projects/QuickAsk
cd ~/Projects/QuickAsk
ln -sf "$PWD/quickask.py" ~/.local/bin/quickask
quickask --version
```

Или `pip install --user .` — поставит команду `quickask` из [`pyproject.toml`](pyproject.toml) (PyGObject и GTK4 при этом всё равно нужны системные).

### Дальше — для обоих вариантов

**1. Конфиг.** На первом запуске QuickAsk сам создаёт `config.toml` из [примера](src/quickask/config.example.toml) — останется вписать свою модель. Где он лежит, смотрите в разделе [Где лежит конфиг](#где-лежит-конфиг) или спросите у самого QuickAsk:

```sh
quickask --version
# quickask 0.1.0  GTK 4.22.5  layer-shell: 1.3.0 (active)
# config: /home/you/.config/quickask/config.toml
```

Минимум — секция `[llm]`:

```toml
[llm]
base_url = "http://127.0.0.1:8080/v1"   # свой llama-server
model    = "MiniCPM5-2B-Q8_0"
api_key  = ""                            # для облачных гейтов
```

Встроенные серверы в примере подключены как `command = ["@self", "--mcp", "desk"]`. `@self` — это сам QuickAsk: бинарник или `quickask.py`, смотря что запущено. Пути прописывать не нужно.

**2. Проверить запуск** — `quickask`. Должно появиться окно; если нет — запустите из терминала и посмотрите вывод.

**3. Повесить хоткей** в компоузиторе (Linux). Повторный запуск — это toggle окна, отдельной команды «спрятать» не нужно. На Windows хоткей свой — см. [ниже](#windows).

niri (`~/.config/niri/config.kdl`):

```kdl
binds {
    Mod+Z hotkey-overlay-title="QuickAsk" repeat=false { spawn "quickask"; }
}
```

Hyprland / Sway:

```
bind = SUPER, Z, exec, quickask
```

**4. Держать копию резидентной** (необязательно, но первый показ окна станет мгновенным) — systemd `--user`:

```ini
# ~/.config/systemd/user/quickask.service
[Unit]
Description=QuickAsk
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=%h/.local/bin/quickask --daemon
Restart=on-failure

[Install]
WantedBy=graphical-session.target
```

```sh
systemctl --user enable --now quickask
```

**5. Образ для агентов** — только если нужны фоновые задачи. По умолчанию берётся готовый из GHCR. Если его нет, первый `start_agent` сам запустит `docker pull` в фоне и попросит повторить задачу, — проще скачать заранее:

```sh
docker pull ghcr.io/delarun/quickask-agent:latest
```

### Windows

QuickAsk живёт в трее. Первый запуск `quickask-windows-x86_64.exe` показывает окно и значок рядом с часами. Дальше окно открывается и прячется:

- глобальным сочетанием `ui.hotkey` — по умолчанию `Alt+Space`;
- кликом по значку в трее;
- повторным запуском `.exe` — он передаст команду уже запущенной копии и выйдет.

Окно встаёт поверх всех, по центру монитора с курсором, на высоте `ui.margin_top`. На панели задач кнопки у него нет, а при потере фокуса оно прячется — это отключается строкой `hide_on_blur = false`.

В меню значка (правая кнопка):
- **кнопки серверов** — например, «Агенты»;
- **«Открыть config.toml»** и **«Папка конфига»**;
- **«Запускать при входе в Windows»** — пишет `quickask --daemon` в автозагрузку текущего пользователя (`HKCU\…\Run`);
- **«Перезапустить»** — перечитать конфиг после правки;
- **«Выход»**.

Если `Alt+Space` уже занят другой программой, QuickAsk скажет об этом в окне — поменяйте `ui.hotkey`: `"Ctrl+Alt+Space"`, `"Win+Shift+Q"`, `"Ctrl+Alt+F12"`. Буквы пишутся латиницей, как на клавише: сочетание привязано к физической клавише, а не к раскладке.

> **Важно про перезапуск.** QuickAsk — одна резидентная копия, и Esc её не закрывает, а только прячет окно. После обновления бинарника, правки кода или конфига нового запуска мало: он лишь покажет окно уже запущенной копии. На Windows — пункт «Перезапустить» в трее, на Linux процесс нужно убить:
>
> ```sh
> pkill -f 'bin/quickask'
> ```

---

## Клавиши

| Клавиша | Действие |
|---|---|
| `Enter` | отправить вопрос (в режиме агента — сообщение агенту) |
| `Esc` | спрятать окно и прервать генерацию |
| `PageUp` / `PageDown` | листать ленту диалога |
| `Ctrl+L` | очистить диалог; со страницы сервера — вернуться в чат |
| `Ctrl+S` | тумблер «всегда искать в интернете» |
| `Ctrl+Shift+C` | скопировать ответ как исходный Markdown |
| `Ctrl+V` | вставить картинку из буфера (`Ctrl+Shift+V` — принудительно) |

Клавиши работают и на русской раскладке: `Ctrl+Д` — это `Ctrl+L`.

## Слэш-команды

Команды объявляют серверы — полный список по `/help`. Своих у QuickAsk три:

| Команда | Что делает |
|---|---|
| `/s <вопрос>` | найти в интернете и ответить со ссылками |
| `/help` | команды всех подключённых серверов |
| `/chat` | закрыть страницу сервера, вернуться в чат |

От встроенных серверов:

| Команда | Сервер | Что делает |
|---|---|---|
| `/agents` | agent | список фоновых агентов |
| `/agent <задача>` | agent | запустить агента и открыть его страницу |
| `/open <id>` | agent | открыть страницу агента |
| `/status <id>`, `/log <id> [строк]` | agent | состояние и хвост лога |
| `/reply <id> <текст>`, `/continue <id> <текст>` | agent | ответить агенту или дать продолжение задачи |
| `/stop <id>` | agent | остановить агента |
| `/windows [фильтр]`, `/tabs [фильтр]` | desk | список окон niri и вкладок Chrome |

Неизвестная `/команда` уходит модели обычным текстом.

---|---|
| `/s <вопрос>` | найти в интернете и ответить со ссылками |
| `/agents` | меню фоновых агентов |
| `/agent <задача>` | запустить фонового агента |
| `/open <id>` | открыть диалог с агентом |
| `/status <id>`, `/log <id> [строк]` | состояние и хвост лога |
| `/reply <id> <текст>`, `/continue <id> <текст>` | ответить агенту или дать продолжение задачи |
| `/stop <id>` | остановить агента |
| `/windows [фильтр]`, `/tabs [фильтр]` | список окон niri и вкладок Chrome |
| `/chat` | выйти из режима агента |

---

## Настройка

### Где лежит конфиг

| ОС | Путь |
|---|---|
| Linux | `~/.config/quickask/config.toml`, а если задан `$XDG_CONFIG_HOME` — `$XDG_CONFIG_HOME/quickask/config.toml` |
| Windows | `%APPDATA%\QuickAsk\config.toml` — обычно `C:\Users\<имя>\AppData\Roaming\QuickAsk\config.toml` |
| macOS | `~/Library/Application Support/QuickAsk/config.toml` |

- **Свой путь** — переменная `QUICKASK_CONFIG=/путь/к/config.toml`.
- **Какой файл прочитан** — показывает вторая строка `quickask --version`. На Windows быстрее через трей: «Открыть config.toml».
- **Если файла нет,** QuickAsk создаёт его на первом запуске из [`src/quickask/config.example.toml`](src/quickask/config.example.toml) — там все ключи с комментариями.
- **Старый путь на Windows и macOS:** если там раньше лежал `~/.config/quickask/config.toml`, а на родном месте файла ещё нет, читается старый.

Всё в конфиге необязательно — чего нет, берётся из `DEFAULTS` в [`src/quickask/config.py`](src/quickask/config.py). После правки QuickAsk нужно перезапустить.

### `[llm]`

| Ключ | По умолчанию | Смысл |
|---|---|---|
| `base_url` | `http://127.0.0.1:8080/v1` | адрес OpenAI-совместимого API |
| `model`, `api_key` | — | имя модели и ключ, если гейт его требует |
| `max_tokens` | `512` | `-1` — без ограничения |
| `temperature`, `timeout` | `0.7`, `120` | |
| `system_prompt` | см. код | короткий промпт = меньше префилла, llama.cpp его закэширует |
| `native_tools` | `true` | отдавать MCP-инструменты как OpenAI tools; нужен `--jinja` у llama-server. Если маленькая модель галлюцинирует вызовы — `false` и пользуйтесь `/s` |
| `max_tool_rounds` | `3` | сколько раундов вызова инструментов допустимо в одном ответе |
| `history_turns` | `6` | пар вопрос/ответ в контексте; столько же обменов видно в ленте |
| `thinking` | не задан | `true`/`false` → `chat_template_kwargs.enable_thinking`; выключение заметно ускоряет ответ |
| `ask_user_tool` | `true` | встроенный `ask_user` с кнопками-вариантами |
| `vision_model` | `""` | модель для картинок; пусто — картинка уходит в основную |
| `image_max_px`, `image_format` | `1024`, `jpeg` | длинная сторона и формат перекодировки перед отправкой |

### `[ui]`

| Ключ | По умолчанию | Смысл |
|---|---|---|
| `width`, `max_height` | `720`, `520` | размеры окна; по высоте лента упирается в `max_height` и дальше скроллится |
| `margin_top` | `"25%"` | отступ от верха экрана: пиксели (`64`) или доля высоты монитора (`"25%"`). На Linux — с gtk4-layer-shell, на Windows — всегда |
| `hotkey` | `"Alt+Space"` | Windows: глобальное сочетание, открывающее окно. Модификаторы `Ctrl` `Alt` `Shift` `Win`, клавиши — буквы, цифры, `Space`, `F1`–`F24`, стрелки. На Linux сочетание задаётся в компоузиторе |
| `hide_on_blur` | да на Windows, нет на Linux | прятать окно, когда оно теряет фокус; идущий ответ при этом не прерывается |
| `density` | `1.0` | множитель **всех** отступов: `1.2–1.5` просторнее, `0.8` плотнее |
| `show_reasoning`, `collapse_reasoning` | `true`, `true` | показывать блок 💭 и сворачивать его, когда пошёл ответ |
| `clear_on_hide` | `false` | сбрасывать диалог при Esc |
| `hide_after_action_ms` | `700` | через сколько прятать окно после `focus_window` и подобных (`0` — не прятать) |

### `[[mcp]]`

Серверов может быть сколько угодно, инструменты всех попадут в модель.

```toml
[[mcp]]
name        = "ddg"
command     = ["uvx", "duckduckgo-mcp-server"]
search_tool = "search"     # что дёргать по "/s" и Ctrl+S
max_chars   = 6000         # обрезка результата, чтобы не раздувать контекст

[[mcp]]
name    = "desk"
command = ["@self", "--mcp", "desk"]   # встроенный сервер — тот же бинарник
```

Кнопки, команды и страницы серверы на [SDK](src/quickask/sdk/) объявляют сами — в конфиге их не перечисляют. Для стороннего сервера без SDK осталась одна ручка: `hide_on_tools = ["focus_window", …]` — после этих инструментов, если ответ начинается с `OK`, окно прячется.

Переменные окружения сервера задаются подтаблицей сразу после его блока:

```toml
[[mcp]]
name    = "agent"
command = ["@self", "--mcp", "agent"]

[mcp.env]
AGENT_HOME    = "/home/USER/Agents"
AGENT_OUT     = "/home/USER/Downloads"
AGENT_LLM_URL = "http://127.0.0.1:8080/v1"
AGENT_MODEL   = "qwen2.5-coder-7b"
```

`@self` в начале `command` заменяется на сам QuickAsk: собранный бинарник или `python3 quickask.py` из исходников. Внешние серверы подключаются обычной командой.

---

## MCP-серверы в комплекте

Лежат в [`mcps/`](mcps/), написаны на [SDK](src/quickask/sdk/) и запускаются отдельными процессами: `quickask --mcp <имя>`. Можно подключить и к другому MCP-клиенту — Claude Desktop, Cursor, чему угодно со stdio: там будут те же инструменты, только без страниц.

### `desk` — рабочий стол

[`mcps/desk.py`](mcps/desk.py). Окна и воркспейсы через `niri msg`, вкладки через Chrome DevTools Protocol.

`list_windows` · `focus_window` · `close_window` · `list_workspaces` · `focus_workspace` · `list_chrome_tabs` · `focus_chrome_tab` · `close_chrome_tab` · `open_url` · `list_dir` · `dir_size` · `find_files` · `file_info` · `read_file` · `open_file` · `launch_app` · `get_clipboard` · `set_clipboard` · `notify`

В QuickAsk: команды `/windows` и `/tabs`; удачные `focus_*`, `open_*` и `launch_app` прячут окно, чтобы оно не висело поверх найденного.

Файловые инструменты работают только внутри `DESK_MCP_ROOTS` — список через `:` (на Windows через `;`), по умолчанию `~`, `/mnt`, `/media`, `/run/media`, `/tmp`.

Для вкладок Chrome браузер должен быть запущен с remote debugging:

```sh
google-chrome-stable --remote-debugging-port=9222
```

### `agent` — фоновые агенты

[`mcps/agent.py`](mcps/agent.py). `start_agent` · `list_agents` · `agent_status` · `agent_log` · `agent_message` · `interrupt_agent` · `stop_agent`

В QuickAsk: кнопка **Агенты** в шапке, страницы списка и диалога с агентом, команды `/agents`, `/agent` и остальные из таблицы выше.

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `AGENT_HOME` | `~/Agents` | папка задачи, монтируется в `/work` |
| `AGENT_OUT` | `~/Downloads` | монтируется в `/out`, сюда агент кладёт результат; `""` — не монтировать |
| `AGENT_IMAGE` | `ghcr.io/delarun/quickask-agent:latest` | образ из [`docker/agent`](docker/agent/), его собирает CI |
| `AGENT_RUNNER` | `docker/agent/runner.py` из исходников | монтируется поверх копии в образе — правки агента без пересборки; в бинарнике его нет, и работает runner из образа |
| `AGENT_LLM_URL`, `AGENT_MODEL`, `AGENT_API_KEY` | — | своя LLM для агента; 2B-модели не хватит, нужен кодер от 7B |
| `AGENT_NET` | `host` | сеть контейнера; `host` даёт доступ к localhost хоста |
| `AGENT_MAX_STEPS`, `AGENT_MEM`, `AGENT_CPUS` | `60`, `4g`, `2` | лимиты |

### Внешние

```toml
[[mcp]]
name    = "fetch"
command = ["uvx", "mcp-server-fetch"]     # модель откроет найденную ссылку целиком
```

---

## Фоновые агенты

Скажите в чате «скачай плейлист … и разбей по трекам» — модель вызовет `start_agent`, и дальше:

1. создаётся `~/Agents/<дата>-<slug>/`, задача кладётся в `.agent/task.txt`;
2. поднимается контейнер из `AGENT_IMAGE` с [`docker/agent/runner.py`](docker/agent/runner.py) внутри;
3. агент сам пишет код и выполняет его, статус и лог пишет в ту же папку;
4. QuickAsk следит за файлами и шлёт `notify-send`, когда агент закончил или задал вопрос;
5. клик по уведомлению → `quickask --open agent:agent?id=<id>` → окно открывается сразу на странице агента.

На странице агента видно задачу, итог, живой вывод текущей команды и карточки шагов. Кнопка **⏭ Команду** прерывает зависшую команду, не убивая агента, — он увидит `[прервано пользователем]` и решит, что делать дальше. Сообщение агенту работает по-разному в зависимости от состояния: `waiting` — это ответ на его вопрос, `running` — заметка к следующему шагу, `done`/`failed` — продолжение задачи в той же папке с той же историей.

Агент работает в контейнере с примонтированными `/work` и `/out`, лимитами по памяти и CPU и потолком шагов. Ключей хоста и домашнего каталога он не видит. Но код он пишет сам и выполняет без подтверждения — не давайте ему `AGENT_OUT`, который жалко.

---

## Структура

```
quickask.py                запуск из исходников (под симлинк ~/.local/bin/quickask)
pyproject.toml             метаданные, версия, команда quickask для pip install
src/quickask/              ядро без GUI — поднимается и без дисплея
  __init__.py                версия, self_command() для @self
  __main__.py                режимы запуска: окно, --mcp, --version
  config.py                  DEFAULTS, где лежит config.toml на каждой ОС, его чтение
  config.example.toml        пример конфига — из него создаётся config.toml на первом запуске
  llm.py                     стрим /chat/completions, разбор <think>…</think>
  mcp.py                     клиент MCP (stdio JSON-RPC) и хаб над серверами
  sdk/                       SDK для серверов, расширяющих интерфейс — см. его README
    protocol.py                методы, стили, действия — общий словарь с клиентом
    server.py                  Server, Result: цикл stdio, регистрация декораторами
    ui.py                      конструкторы страниц, блоков и действий
  ui/                        всё, что на GTK
    __init__.py                инициализация GTK и опционального gtk4-layer-shell
    app.py                     Gtk.Application, резидентность, аргументы
    window.py                  окно: лента диалога, шапка, поле ввода, слэш-команды
    views.py                   страницы серверов: JSON → виджеты, действия, автообновление
    instance.py                одна копия на Windows и macOS, где нет D-Bus
    win32.py                   Windows: трей, глобальный хоткей, окно поверх всех
    quickask.ico               значок трея и .exe
    worker.py                  поток одного запроса: раунды инструментов, ask_user
    markdown.py                Markdown → Gtk.TextBuffer на TextTag-ах
    theme.py                   CSS-шаблон и сетка отступов (ui.density)

mcps/                      встроенные MCP-серверы на SDK
  desk.py                    окна niri, вкладки Chrome, файлы, буфер обмена; /windows, /tabs
  agent.py                   фоновые агенты в Docker, их страницы и команды

docker/agent/              песочница агента
  Dockerfile
  runner.py                  цикл агента внутри контейнера

packaging/                 сборка бинарника
  quickask.spec              спека PyInstaller
  entry.py                   точка входа бинарника
  hooks/                     хук для gtk4-layer-shell
  build-gtk4-layer-shell.sh  сборка gtk4-layer-shell из исходников для Ubuntu 24.04, где его нет в пакетах
  smoke.py                   проверка собранного бинарника без дисплея
  THIRD_PARTY_NOTICES.md     вшитые в бинарник компоненты и их лицензии — едет в каждый релиз

.github/workflows/
  build.yml                  бинарники под Linux, Windows, macOS; релиз по тегу
  docker.yml                 образ агента в GHCR
```

Отступы интерфейса заданы одной сеткой в `theme.py`: внешний край полос — `edge`, поля внутри карточек — `pad`/`padv`, вертикальные разрывы — `turn`/`turns`. `ui.density` умножает их все разом, так что подогнать плотность под свой экран можно одним числом.

---

## Свой сервер

Любой MCP-сервер подключается в `config.toml` и сразу даёт модели свои инструменты. Если написать его на [`quickask.sdk`](src/quickask/sdk/), он сможет ещё и добавить в окно кнопку, команду и страницу:

```python
from quickask.sdk import Result, Server, ui

srv = Server("notes")

@srv.tool("add_note", "Сохранить заметку", {"text": {"type": "string"}}, ["text"])
def add_note(text: str) -> Result:
    NOTES.append(text)
    return Result("OK: сохранил", [ui.open("notes", label="Открыть заметки")])

@srv.view("notes")
def notes():
    return ui.page("Заметки", color="teal", blocks=[ui.card(ui.line(n)) for n in NOTES],
                   input=ui.entry("Новая заметка…", ui.call("add_note", text="$text")))

srv.button("Заметки", ui.open("notes"))
srv.run()
```

Полный пример, справочник и описание протокола для серверов на других языках — в [`src/quickask/sdk/README.md`](src/quickask/sdk/README.md).

---

## Сборка

### Бинарник локально

Нужны GTK4 и PyGObject из системы — PyInstaller вшивает их в файл:

```sh
python3 -m venv --system-site-packages .venv       # PyGObject — системный
.venv/bin/pip install pyinstaller pyinstaller-hooks-contrib
.venv/bin/pyinstaller --noconfirm --clean packaging/quickask.spec
.venv/bin/python packaging/smoke.py dist/quickask
```

Получится `dist/quickask` — около 60 МБ, один файл. Smoke-тест проверяет, что GTK вшит (`--version`) и что оба встроенных сервера отвечают на `initialize` и `tools/list`. Дисплей для этого не нужен.

### CI

| Workflow | Когда | Что делает |
|---|---|---|
| [`build.yml`](.github/workflows/build.yml) | push, PR | собирает бинарники под Linux x86_64 и aarch64, Windows x86_64, macOS arm64 и гоняет на каждом smoke-тест |
| | тег `v*` | то же + GitHub Release с бинарниками и `SHA256SUMS` |
| [`docker.yml`](.github/workflows/docker.yml) | push в `main`, тег `v*` | образ агента под amd64 и arm64 → `ghcr.io/<owner>/quickask-agent` (`:latest`, `:sha-…`, `:1.2.3`) |
| | PR с правками `docker/agent/` | только сборка, без публикации |

GTK под каждую ОС берётся оттуда, где он живёт нативно: Linux — apt на Ubuntu 24.04, Windows — MSYS2 UCRT64, macOS — Homebrew.

Выпустить релиз:

```sh
# версия — в src/quickask/__init__.py
git tag v0.1.0 && git push --tags
```

---

## Лицензия

MIT.
