# QuickAsk — https://github.com/delarun/quickask — MIT
"""MCP-сервер на stdio (JSON-RPC 2.0) с расширением интерфейса QuickAsk.

Инструменты видят все MCP-клиенты; страницы, команды и кнопки — только QuickAsk,
остальные клиенты про capability quickask/ui не знают и её пропускают.
"""
from __future__ import annotations

import inspect
import json
import sys
import threading
import traceback
from typing import Any, Callable

from . import protocol as P

PROTOCOL_VERSION = "2025-06-18"


class Result:
    """Результат инструмента с действиями для QuickAsk: текст видит модель, действия — интерфейс.

        return Result("OK: переключил окно", [ui.hide()])
    """

    def __init__(self, text: str, actions: list[dict] | None = None):
        self.text, self.actions = str(text), list(actions or [])

    def __str__(self) -> str:
        return self.text


class Server:
    """MCP-сервер. Регистрация — декораторами, запуск — run().

        srv = Server("agent")

        @srv.tool("stop_agent", "Остановить агента", {"id": {"type": "string"}}, ["id"])
        def stop_agent(id: str) -> str: ...

        @srv.view("agents")
        def agents() -> dict: return ui.page("Агенты", blocks=[...])

        @srv.command("/agents", "меню фоновых агентов")
        def cmd_agents(text: str): return ui.open("agents")

        srv.button("Агенты", ui.open("agents"), style="accent")
        srv.run()
    """

    def __init__(self, name: str, version: str = "0.1"):
        self.name, self.version = name, version
        self.tools: dict[str, dict] = {}
        self.views: dict[str, Callable[..., dict]] = {}
        self.commands: dict[str, dict] = {}
        self.buttons: list[dict] = []
        self._out = threading.Lock()

    # ── регистрация ────────────────────────────────────────────────────────
    def tool(self, name: str, description: str, params: dict | None = None, required: list[str] | None = None):
        """Инструмент для модели. params — JSON Schema свойств, required — обязательные.
        Функция возвращает строку или Result; исключение уйдёт модели как isError."""
        def deco(fn):
            self.tools[name] = {"fn": fn, "schema": {
                "name": name, "description": description,
                "inputSchema": {"type": "object", "properties": params or {}, "required": required or []}}}
            return fn
        return deco

    def view(self, name: str):
        """Страница. Функция получает args из действия ui.open(name, **args) и возвращает ui.page(...).
        Строки из deep-link (`quickask --open srv:view?lines=30`) приводятся к типу значения по умолчанию."""
        def deco(fn):
            self.views[name] = fn
            return fn
        return deco

    def command(self, name: str, description: str = "", usage: str = ""):
        """Слэш-команда. Функция получает текст после имени команды и возвращает действие,
        список действий, строку (станет строкой состояния) или None."""
        if not name.startswith("/"):
            raise ValueError(f"команда должна начинаться с /: {name!r}")

        def deco(fn):
            self.commands[name] = {"fn": fn, "spec": {"name": name, "usage": usage, "description": description}}
            return fn
        return deco

    def button(self, label: str, action: dict, style: str | None = None) -> None:
        """Кнопка в шапке QuickAsk, пока открыт чат."""
        from . import ui
        self.buttons.append(ui.button(label, action, style))

    @property
    def has_ui(self) -> bool:
        return bool(self.views or self.commands or self.buttons)

    # ── транспорт ──────────────────────────────────────────────────────────
    def send(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        with self._out:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def _reply(self, mid, result: Any = None, error: str | None = None, code: int = -32000) -> None:
        if mid is None:
            return
        if error is not None:
            self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": error}})
        else:
            self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def run(self) -> None:
        """Читать запросы со stdin до EOF. Каждый запрос — отдельный поток: медленный
        инструмент не задерживает перерисовку страницы, которую QuickAsk просит каждые пару секунд."""
        inflight: list[threading.Thread] = []
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("method") == "initialize":          # до ответа на него ничего другого не придёт
                self._handle(msg)
            else:
                t = threading.Thread(target=self._handle, args=(msg,), daemon=True)
                t.start()
                inflight = [x for x in inflight if x.is_alive()] + [t]
        for t in inflight:            # stdin закрыт — дождаться ответов на уже полученные запросы
            t.join()

    # ── обработка ──────────────────────────────────────────────────────────
    def _handle(self, msg: dict) -> None:
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        try:
            handler = {
                "initialize": self._initialize,
                "ping": lambda p: {},
                "tools/list": lambda p: {"tools": [t["schema"] for t in self.tools.values()]},
                "tools/call": self._call_tool,
                P.MANIFEST: self._manifest,
                P.RENDER: self._render,
                P.COMMAND: self._command,
            }.get(method)
            if handler is None:
                if mid is not None:
                    self._reply(mid, error=f"method not found: {method}", code=-32601)
                return
            self._reply(mid, handler(params))
        except Exception as e:  # noqa: BLE001
            if sys.stderr:                                   # у QuickAsk stderr серверов в /dev/null
                traceback.print_exc(file=sys.stderr)
            self._reply(mid, error=str(e) or type(e).__name__)

    def _initialize(self, params: dict) -> dict:
        caps: dict = {"tools": {}}
        if self.has_ui:
            caps["experimental"] = {P.CAPABILITY: {"version": P.VERSION}}
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": caps, "serverInfo": {"name": self.name, "version": self.version}}

    def _call_tool(self, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in self.tools:
            return _text_result(f"Неизвестный инструмент {name}", error=True)
        try:
            out = self.tools[name]["fn"](**args)
        except TypeError as e:
            return _text_result(f"Неверные аргументы: {e}", error=True)
        except Exception as e:  # noqa: BLE001
            return _text_result(f"Ошибка: {e}", error=True)
        res = _text_result(str(out))
        if isinstance(out, Result) and out.actions:
            res["_meta"] = {P.META_ACTIONS: out.actions}
        return res

    def _manifest(self, _params: dict) -> dict:
        return {"buttons": self.buttons, "commands": [c["spec"] for c in self.commands.values()]}

    def _render(self, params: dict) -> dict:
        name = params.get("view", "")
        if name not in self.views:
            raise ValueError(f"нет страницы {name!r}; есть: {', '.join(self.views) or '—'}")
        fn = self.views[name]
        return fn(**_coerce(fn, params.get("args") or {}))

    def _command(self, params: dict) -> dict:
        name = params.get("name", "")
        if name not in self.commands:
            raise ValueError(f"нет команды {name}")
        return {"actions": _as_actions(self.commands[name]["fn"](params.get("text", "")))}


def _text_result(text: str, error: bool = False) -> dict:
    res: dict = {"content": [{"type": "text", "text": text}]}
    if error:
        res["isError"] = True
    return res


def _as_actions(out) -> list[dict]:
    if out is None:
        return []
    if isinstance(out, str):
        return [{"type": "status", "text": out}]
    if isinstance(out, dict):
        return [out]
    return list(out)


def _coerce(fn: Callable, args: dict) -> dict:
    """Аргументы из deep-link приходят строками — приводим к типу значения по умолчанию (int, float, bool)."""
    sig = inspect.signature(fn)
    out = dict(args)
    for k, v in args.items():
        p = sig.parameters.get(k)
        if p is None or not isinstance(v, str) or p.default is inspect.Parameter.empty:
            continue
        kind = type(p.default)
        try:
            if kind is bool:
                out[k] = v.lower() in ("1", "true", "yes", "on")
            elif kind in (int, float):
                out[k] = kind(v)
        except ValueError:
            pass
    return out
