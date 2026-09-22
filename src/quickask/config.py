# QuickAsk — https://github.com/delarun/quickask — MIT
"""Значения по умолчанию и чтение ~/.config/quickask/config.toml."""
from __future__ import annotations

import json
import os
import tomllib

APP_ID = "org.homelab.QuickAsk"
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
CONFIG_PATH = os.path.expanduser("~/.config/quickask/config.toml")

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
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            user = tomllib.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg
