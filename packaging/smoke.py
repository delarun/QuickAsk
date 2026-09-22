"""Smoke-тест собранного бинарника — без дисплея и без сети.

    python packaging/smoke.py dist/quickask            # бинарник
    python packaging/smoke.py python3 quickask.py      # исходники

Проверяет, что GTK вшит и импортируется (--version), что встроенные MCP-серверы
поднимаются из бинарника, отвечают на initialize и tools/list и отдают манифест
расширения интерфейса (quickask.sdk): кнопки и слэш-команды.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

CMD = sys.argv[1:] or ["dist/quickask"]
EXPECT = {"desk": ("list_windows", "/windows"), "agent": ("start_agent", "/agents")}


def run(args: list[str], stdin: str = "") -> str:
    r = subprocess.run(CMD + args, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        sys.exit(f"FAIL {' '.join(args)}: exit {r.returncode}\n{r.stderr[-2000:]}")
    return r.stdout


version = run(["--version"]).strip()
print("version:", version)
if not version.startswith("quickask ") or "GTK 4." not in version:
    sys.exit(f"FAIL --version: {version!r}")
if os.environ.get("SMOKE_REQUIRE_LAYER_SHELL") and "layer-shell: no" in version:
    sys.exit("FAIL: gtk4-layer-shell not bundled")   # без дисплея будет «(inactive)» — это нормально

for name, (tool, command) in EXPECT.items():
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "quickask/ui/manifest", "params": {}},
    ]
    out = run(["--mcp", name], "".join(json.dumps(r) + "\n" for r in requests))
    replies = {m["id"]: m for m in map(json.loads, filter(None, out.splitlines())) if "id" in m}
    tools = [t["name"] for t in replies.get(2, {}).get("result", {}).get("tools", [])]
    if 1 not in replies or tool not in tools:
        sys.exit(f"FAIL --mcp {name}: replies {sorted(replies)}, tools {tools}")
    caps = replies[1]["result"].get("capabilities", {}).get("experimental", {})
    commands = [c["name"] for c in replies.get(3, {}).get("result", {}).get("commands", [])]
    if "quickask/ui" not in caps or command not in commands:
        sys.exit(f"FAIL --mcp {name}: ui capability {caps}, commands {commands}")
    print(f"mcp {name}: {len(tools)} tools, {tool} present; ui: {len(commands)} commands, {command} present")

print("OK")
