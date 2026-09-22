# QuickAsk — https://github.com/delarun/quickask — MIT
"""Встроенные MCP-серверы QuickAsk.

Каждый — отдельный процесс на stdio (JSON-RPC 2.0), только стандартная библиотека.
Запуск: `quickask --mcp <имя>`, в config.toml — `command = ["@self", "--mcp", "<имя>"]`.
"""
from __future__ import annotations

import importlib
import sys

SERVERS = {
    "desk": "mcps.desk",     # окна niri, вкладки Chrome, файлы, буфер обмена
    "agent": "mcps.agent",   # фоновые агенты в Docker
}


def run(name: str) -> int:
    if name not in SERVERS:
        print(f"unknown MCP server {name!r}; available: {', '.join(SERVERS)}", file=sys.stderr)
        return 2
    for stream in (sys.stdin, sys.stdout):
        if stream is not None:
            stream.reconfigure(encoding="utf-8")   # на Windows по умолчанию cp1252 — кириллица бы рассыпалась
    importlib.import_module(SERVERS[name]).main()
    return 0
