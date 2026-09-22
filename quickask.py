#!/usr/bin/env python3
# QuickAsk — https://github.com/delarun/quickask — MIT
"""
QuickAsk — всплывающее окно (Spotlight-style) для локальной LLM под Wayland.

  • одна резидентная копия (Gtk.Application): повторный запуск бинаря = toggle окна,
    поэтому хоткей в компоузиторе — просто `exec quickask`
  • стрим ответа из OpenAI-совместимого /v1/chat/completions (llama-server, ai-gate, …)
  • рассуждения модели (reasoning_content / <think>…</think>) стримятся в сворачиваемый блок 💭
  • Markdown в ответе рендерится TextTag'ами (заголовки, списки, код, таблицы, кликабельные ссылки)
  • mcps/desk.py — MCP управления рабочим столом: окна/воркспейсы niri, вкладки Chrome (CDP),
    файлы (поиск, каталоги, размеры), URL, запуск приложений, буфер обмена; после focus_* окно само прячется
  • картинка из буфера обмена (Ctrl+V) уходит в запрос как image_url (нужна VLM, см. vision_model)
  • mcps/agent.py — фоновые агенты в Docker (start_agent): «скачай плейлист…» → контейнер с docker/agent/runner.py
    сам пишет код и выполняет; /agents, /log, /status, /stop, /reply — прямые команды без модели
  • инструменты через MCP (stdio JSON-RPC 2.0): native function calling
    + принудительный веб-поиск префиксом  "/s вопрос"  или Ctrl+S
  • gtk4-layer-shell (если установлен) — окно поверх всего с захватом клавиатуры,
    иначе обычное окно (сделай его floating правилом компоузитора)

Клавиши в окне:
  Enter          отправить
  Esc            скрыть окно (и прервать генерацию)
  Ctrl+L         очистить диалог и ответ
  Ctrl+S         переключить режим «всегда искать в интернете»
  Ctrl+Shift+C   скопировать ответ в буфер (исходный markdown)
  Ctrl+V         если в буфере картинка — прикрепить её к вопросу (Ctrl+Shift+V — принудительно)

Запуск:
  quickask --daemon      резидентно, окно не показывать (для systemd --user)
  quickask               показать/скрыть окно (для хоткея)
  quickask --mcp desk    встроенный MCP-сервер на stdio (desk, agent)
  quickask --version     версия, GTK, наличие gtk4-layer-shell

Этот файл — запуск из исходников (под симлинк ~/.local/bin/quickask). Сам код — в src/quickask,
MCP-серверы — в mcps/, сборка бинарника — packaging/quickask.spec.
"""
from __future__ import annotations

import os
import sys

# Файл обычно лежит под симлинком (~/.local/bin/quickask) — пакеты ищем рядом с оригиналом.
ROOT = os.path.dirname(os.path.realpath(__file__))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

from quickask.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
