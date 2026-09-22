# QuickAsk — https://github.com/delarun/quickask — MIT
"""Клиент MCP поверх stdio (JSON-RPC 2.0) и хаб над несколькими серверами."""
from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import threading

from . import self_command
from .sdk import protocol as P

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # Windows: не мигать консолью у uvx/python


def resolve_command(cmd: list[str]) -> list[str]:
    """`@self` в начале команды — этот же QuickAsk: так встроенные серверы запускаются
    одинаково из исходников и из собранного бинарника (`["@self", "--mcp", "desk"]`)."""
    cmd = list(cmd)
    if cmd and cmd[0] == "@self":
        cmd[:1] = self_command()
    return cmd

class McpServer:
    """Минимальный MCP-клиент поверх stdio: initialize → tools/list → tools/call,
    плюс расширение интерфейса QuickAsk (quickask/ui/*), если сервер его объявил."""

    def __init__(self, spec: dict):
        self.name: str = spec["name"]
        self.search_tool: str | None = spec.get("search_tool")
        self.max_chars: int = int(spec.get("max_chars", 6000))
        # для сторонних серверов без SDK: после этих инструментов окно прячется
        self.hide_on_tools: set[str] = set(spec.get("hide_on_tools") or [])
        self.info: dict = {}                     # serverInfo из initialize
        self.has_ui = False
        self.manifest: dict = {"buttons": [], "commands": []}
        env = os.environ.copy()
        env.update(spec.get("env") or {})
        self.proc = subprocess.Popen(
            resolve_command(spec["command"]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",        # JSON-RPC с кириллицей; на Windows локаль по умолчанию — cp1252
            bufsize=1,
            env=env,
            creationflags=NO_WINDOW,
        )
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._plock = threading.Lock()
        self._wlock = threading.Lock()
        self.tools: list[dict] = []
        threading.Thread(target=self._reader, daemon=True, name=f"mcp-{self.name}").start()

    # -- транспорт --------------------------------------------------------
    def _reader(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._plock:
                    slot = self._pending.pop(msg["id"], None)
                if slot:
                    slot[1].append(msg)
                    slot[0].set()
            elif msg.get("method") == "ping" and "id" in msg:
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        with self._plock:  # EOF — сервер умер
            for ev, box in self._pending.values():
                box.append({"error": {"message": "MCP server exited"}})
                ev.set()
            self._pending.clear()

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin
        with self._wlock:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        rid = next(self._ids)
        ev, box = threading.Event(), []
        with self._plock:
            self._pending[rid] = (ev, box)
        msg: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        if not ev.wait(timeout):
            with self._plock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"{self.name}: {method} timed out")
        resp = box[0]
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"{self.name}: {err.get('message', err) if isinstance(err, dict) else err}")
        return resp.get("result") or {}

    # -- протокол ---------------------------------------------------------
    def initialize(self) -> None:
        res = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "quickask", "version": "0.1"},
            },
            timeout=120,  # uvx при первом запуске может качать пакет
        )
        self.info = res.get("serverInfo") or {}
        self.has_ui = P.CAPABILITY in ((res.get("capabilities") or {}).get("experimental") or {})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self.request("tools/list").get("tools", [])
        if self.has_ui:
            m = self.request(P.MANIFEST, {})
            self.manifest = {"buttons": m.get("buttons") or [], "commands": m.get("commands") or []}
        if not self.search_tool:
            for t in self.tools:
                if "search" in t["name"].lower():
                    self.search_tool = t["name"]
                    break

    def call_ex(self, name: str, args: dict, timeout: float = 60) -> tuple[str, list[dict]]:
        """(текст для модели, действия для интерфейса из _meta)."""
        res = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout)
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        out = "\n".join(texts).strip() or json.dumps(res, ensure_ascii=False)
        actions = (res.get("_meta") or {}).get(P.META_ACTIONS) or []
        return out[: self.max_chars], [a for a in actions if isinstance(a, dict)]

    def call(self, name: str, args: dict, timeout: float = 60) -> str:
        return self.call_ex(name, args, timeout)[0]

    # -- расширение интерфейса -----------------------------------------------
    def ui_render(self, view: str, args: dict) -> dict:
        return self.request(P.RENDER, {"view": view, "args": args or {}}, timeout=30)

    def ui_command(self, name: str, text: str) -> list[dict]:
        return self.request(P.COMMAND, {"name": name, "text": text}, timeout=120).get("actions") or []

    def close(self) -> None:
        try:
            self.proc.terminate()
        except OSError:
            pass


class McpHub:
    """Все MCP-серверы из конфига + маппинг их инструментов в OpenAI tools."""

    def __init__(self, specs: list[dict]):
        self.servers: list[McpServer] = []
        self.errors: list[str] = []
        self._tool_map: dict[str, tuple[McpServer, str]] = {}
        self._tools_cache: list[dict] | None = None
        for spec in specs:
            try:
                s = McpServer(spec)
                s.initialize()
                self.servers.append(s)
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"{spec.get('name')}: {e}")

    def openai_tools(self) -> list[dict]:
        if self._tools_cache is not None:
            return self._tools_cache
        tools = []
        for s in self.servers:
            for t in s.tools:
                fname = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{s.name}__{t['name']}")[:64]
                self._tool_map[fname] = (s, t["name"])
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": fname,
                            "description": (t.get("description") or "")[:1024],
                            "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                        },
                    }
                )
        self._tools_cache = tools
        return tools

    def call_ex(self, fname: str, args: dict) -> tuple[McpServer, str, list[dict]]:
        """Вызов инструмента модели: (сервер, текст, действия для интерфейса)."""
        if fname not in self._tool_map:
            raise KeyError(f"неизвестный инструмент {fname}")
        s, tname = self._tool_map[fname]
        text, actions = s.call_ex(tname, args)
        if tname in s.hide_on_tools and text.startswith("OK"):   # сторонний сервер без SDK
            actions = actions + [{"type": "hide"}]
        return s, text, actions

    def call(self, fname: str, args: dict) -> str:
        return self.call_ex(fname, args)[1]

    # -- расширение интерфейса -----------------------------------------------
    def server_by(self, name: str) -> McpServer | None:
        """Сервер по имени из config.toml, а если такого нет — по имени, которое он сам себе дал
        (serverInfo.name): так deep link `agent:agent?id=…` работает при любом name в конфиге."""
        return (next((s for s in self.servers if s.name == name), None)
                or next((s for s in self.servers if s.info.get("name") == name), None))

    def buttons(self) -> list[tuple[McpServer, dict]]:
        """Кнопки серверов для шапки чата — в порядке серверов в config.toml."""
        return [(s, b) for s in self.servers if s.has_ui for b in s.manifest["buttons"]]

    def commands(self) -> dict[str, tuple[McpServer, dict]]:
        """Слэш-команды серверов; при совпадении имён побеждает сервер выше в config.toml."""
        out: dict[str, tuple[McpServer, dict]] = {}
        for s in self.servers:
            for c in s.manifest["commands"] if s.has_ui else []:
                out.setdefault(c["name"], (s, c))
        return out

    def search(self, query: str) -> str:
        for s in self.servers:
            if not s.search_tool:
                continue
            schema = next((t.get("inputSchema") or {} for t in s.tools if t["name"] == s.search_tool), {})
            props = schema.get("properties") or {}
            arg = "query" if "query" in props else next(
                (k for k, v in props.items() if v.get("type") == "string"), "query"
            )
            return s.call(s.search_tool, {arg: query})
        raise RuntimeError("нет MCP-сервера с инструментом поиска")

    def close(self) -> None:
        for s in self.servers:
            s.close()
