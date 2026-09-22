# QuickAsk — https://github.com/delarun/quickask — MIT
"""QuickAsk — всплывающее окно к LLM для Wayland.

Ядро без GUI: конфиг, клиент LLM, клиент MCP. Всё, что тянет GTK, — в quickask.ui,
поэтому `quickask --mcp …` и `--version` поднимаются без инициализации дисплея.
"""
from __future__ import annotations

import os
import sys

__version__ = "0.1.0"


def self_command() -> list[str]:
    """Как запустить этот же QuickAsk ещё раз: бинарник, quickask.py из исходников или `python -m`."""
    if getattr(sys, "frozen", False):          # PyInstaller
        return [sys.executable]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = os.path.join(root, "quickask.py")
    if os.path.isfile(script):
        return [sys.executable, script]
    return [sys.executable, "-m", "quickask"]
