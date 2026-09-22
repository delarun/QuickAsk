# QuickAsk — https://github.com/delarun/quickask — MIT
"""Значения по умолчанию, где лежит config.toml и как он читается."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib

APP_ID = "org.homelab.QuickAsk"
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
EXAMPLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.example.toml")


def config_path() -> str:
    """Где искать config.toml — там, где принято на этой ОС:

        Linux    $XDG_CONFIG_HOME/quickask/config.toml, по умолчанию ~/.config/quickask/
        Windows  %APPDATA%\\QuickAsk\\config.toml
        macOS    ~/Library/Application Support/QuickAsk/config.toml

    QUICKASK_CONFIG переопределяет путь целиком. На Windows и macOS ~/.config/quickask/ тоже
    понимается, если конфиг уже лежит там: так работали ранние сборки."""
    if os.environ.get("QUICKASK_CONFIG"):
        return os.path.expanduser(os.environ["QUICKASK_CONFIG"])
    home = os.path.expanduser("~")
    legacy = os.path.join(home, ".config", "quickask", "config.toml")
    if sys.platform == "win32":
        native = os.path.join(os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"),
                              "QuickAsk", "config.toml")
    elif sys.platform == "darwin":
        native = os.path.join(home, "Library", "Application Support", "QuickAsk", "config.toml")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        return os.path.join(base, "quickask", "config.toml")
    return legacy if os.path.exists(legacy) and not os.path.exists(native) else native


def ensure_config() -> str:
    """Путь к конфигу; если его ещё нет — создать из примера, чтобы было что открыть и поправить."""
    path = config_path()
    if not os.path.exists(path) and os.path.exists(EXAMPLE_PATH):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(EXAMPLE_PATH, path)
        except OSError:
            pass                         # только для чтения и т.п. — работаем на значениях по умолчанию
    return path

DEFAULTS: dict = {
    "llm": {
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "MiniCPM5-2B-Q8_0",
        "api_key": "",
        "user_agent": DEFAULT_USER_AGENT,  # некоторые прокси/гейты режут «неизвестные» клиенты
        "max_tokens": 512,
        "temperature": 0.7,
        "timeout": 120,
        # Короткий system prompt = меньше префилла; llama.cpp закэширует его (cache_prompt).
        "system_prompt": (
            "Ты быстрый локальный ассистент. Отвечай кратко, по делу, на языке вопроса, "
            "без вступлений. Если вопрос требует актуальных данных из интернета — "
            "вызови инструмент поиска, затем ответь и укажи источники. Если просят найти, "
            "открыть или переключиться на окно/вкладку/приложение — используй инструменты "
            "управления рабочим столом (focus_window, focus_chrome_tab и т.п.), а не описывай, как это сделать. "
            "Длинные задачи (скачать плейлист/видео, сконвертировать файлы, написать и выполнить скрипт, обработать "
            "данные) — отдавай фоновому агенту через start_agent, передав задачу целиком со всеми ссылками; "
            "сам такие задачи не выполняй и не пиши для них код в ответе."
        ),
        # Отдавать модели список MCP-инструментов как OpenAI tools (нужен llama-server --jinja).
        # Если 2B-модель начинает галлюцинировать вызовы — поставь false и пользуйся "/s".
        "native_tools": True,
        "max_tool_rounds": 3,
        "history_turns": 6,  # сколько пар вопрос/ответ держать в контексте
        # Режим рассуждений (thinking). true/false → chat_template_kwargs.enable_thinking,
        # None → не трогать, как решит шаблон модели. Выключение заметно ускоряет ответ.
        #"thinking": True,
        # Картинки из буфера (Ctrl+V). MiniCPM5-2B текстовая — для картинок нужна VLM
        # (напр. MiniCPM-V с --mmproj в llama-server, или гейт, который роутит по имени модели).
        # Пусто → запрос с картинкой уходит в тот же model.
        "ask_user_tool": True,  # встроенный инструмент ask_user: модель задаёт уточняющий вопрос с кнопками-вариантами
        "vision_model": "",
        "image_max_px": 1024,   # длинная сторона; уменьшает число image-токенов
        "image_format": "jpeg",  # jpeg|png
    },
    "ui": {
        "width": 720,
        "max_height": 520,
        "margin_top": "25%",            # от верха экрана: пиксели (140) или доля высоты монитора ("25%")
        "hotkey": "Alt+Space",          # Windows: глобальное сочетание, открывающее окно из трея
        "hide_on_blur": None,           # прятать при потере фокуса; None — да на Windows, нет на Linux
        "clear_on_hide": False,
        "show_reasoning": True,          # показывать блок 💭 Рассуждения
        "collapse_reasoning": True,      # сворачивать его, когда пошёл сам ответ
        "hide_after_action_ms": 700,     # спрятать окно после инструмента из hide_on_tools (0 = не прятать)
        "density": 1.0,                  # множитель всех отступов: 1.2–1.5 — просторнее, 0.8 — плотнее
    },
    "mcp": [
        {
            "name": "ddg",
            "command": ["uvx", "duckduckgo-mcp-server"],
            "search_tool": "search",  # какой инструмент дёргать для "/s"
            "max_chars": 6000,        # обрезать результат инструмента до N символов
        }
    ],
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    path = ensure_config()
    cfg["_path"] = path                  # откуда прочитан — для --version и пункта «Настройки» в трее
    if os.path.exists(path):
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg
