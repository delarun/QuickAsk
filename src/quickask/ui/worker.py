# QuickAsk — https://github.com/delarun/quickask — MIT
"""Фоновый поток одного запроса: стрим ответа, раунды вызова инструментов, ask_user."""
from __future__ import annotations

import json
import threading

from gi.repository import GLib

from ..llm import stream_chat
from ..mcp import McpHub

ASK_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": ("Задать пользователю уточняющий вопрос и получить ответ, прежде чем действовать: когда "
                        "запрос неоднозначен, нашлось несколько подходящих окон/файлов/вкладок, или нужно "
                        "подтверждение необратимого действия (закрыть, удалить). Варианты ответа передавай в options — "
                        "пользователь выберет кнопкой."),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"},
                            "description": "2–6 коротких вариантов (необязательно)"},
            },
            "required": ["question"],
        },
    },
}


class QueryWorker(threading.Thread):
    def __init__(self, cfg: dict, hub: McpHub | None, history: list[dict], question: str,
                 force_search: bool, ui, image_url: str | None = None):
        super().__init__(daemon=True, name="quickask-query")
        self.cfg, self.hub, self.history = cfg, hub, history
        self.question, self.force_search, self.ui = question, force_search, ui
        self.image_url = image_url  # data:image/...;base64,...
        self.cancel = threading.Event()
        self.hide_after = False
        self.offers: list[tuple] = []   # (сервер, действие) — переходы, которые серверы приложили к результатам

    def _status(self, text: str) -> None:
        GLib.idle_add(self.ui.set_status, text)

    def _token(self, text: str) -> None:
        if not self.cancel.is_set():
            GLib.idle_add(self.ui.append_token, text)

    def _reasoning(self, text: str) -> None:
        if not self.cancel.is_set():
            GLib.idle_add(self.ui.append_reasoning, text)

    def _ask_user(self, question: str, options: list) -> str | None:
        """Показать вопрос в окне и ждать ответа (Enter в строке или кнопка). None — отмена."""
        box: dict = {"answer": None, "event": threading.Event()}
        GLib.idle_add(self.ui.show_question, question, [str(o) for o in options][:6], box)
        while not box["event"].wait(0.2):
            if self.cancel.is_set():
                GLib.idle_add(self.ui.hide_question)
                return None
        return f"Ответ пользователя: {box['answer']}"

    def _user_msg(self, text: str) -> dict:
        """user-сообщение; с картинкой — multipart content (OpenAI image_url)."""
        if not self.image_url:
            return {"role": "user", "content": text}
        return {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": self.image_url}},
        ]}

    def run(self) -> None:
        llm = self.cfg["llm"]
        msgs: list[dict] = [{"role": "system", "content": llm["system_prompt"]}]
        msgs += self.history
        model = (llm.get("vision_model") or None) if self.image_url else None
        try:
            if self.force_search:
                if not self.hub:
                    raise RuntimeError("MCP ещё не поднялся / не настроен")
                self._status("🔎 ищу в интернете…")
                results = self.hub.search(self.question)
                msgs.append(self._user_msg(
                    f"{self.question}\n\n[Результаты веб-поиска]\n{results}\n[/Результаты]\n\n"
                    "Ответь на вопрос, опираясь на результаты. Укажи источники ссылками."
                ))
                tools = None
            else:
                msgs.append(self._user_msg(self.question))
                tools = None
                if llm["native_tools"]:
                    tools = list(self.hub.openai_tools()) if self.hub else []
                    if llm.get("ask_user_tool", True):
                        tools.append(ASK_USER_TOOL)
                    tools = tools or None

            self._status("… генерирую")
            final = ""
            for rnd in range(llm["max_tool_rounds"] + 1):
                content, reasoning, calls = stream_chat(
                    llm, msgs, tools, self._token, self._reasoning, self.cancel, model=model)
                if self.cancel.is_set():
                    return
                if not calls or rnd == llm["max_tool_rounds"]:
                    final = content
                    break
                msgs.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                        for c in calls
                    ],
                })
                for c in calls:
                    self._status(f"🔧 {c['name']}…")
                    try:
                        args = json.loads(c["args"]) if c["args"] else {}
                        if not isinstance(args, dict):
                            args = {"input": args}
                    except ValueError:
                        args = {"input": c["args"]}
                    try:
                        if c["name"] == "ask_user":
                            result = self._ask_user(args.get("question") or "?", args.get("options") or [])
                            if result is None:  # отменено (Esc) — выходим тихо
                                return
                        else:
                            server, result, actions = self.hub.call_ex(c["name"], args)  # type: ignore[union-attr]
                            for a in actions:
                                if a.get("type") == "hide":
                                    self.hide_after = True          # переключили окно — спрячемся после ответа
                                elif a.get("type") in ("view", "show", "call"):
                                    self.offers.append((server, a))  # предложим кнопкой под ответом
                    except Exception as e:  # noqa: BLE001
                        result = f"ERROR: {e}"
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                if content:
                    self._token("\n\n")
                if reasoning:
                    self._reasoning("\n— — —\n")
                self._status("… генерирую")

            GLib.idle_add(self.ui.finish, self.question, final, None)
            if self.offers and not self.cancel.is_set():
                GLib.idle_add(self.ui.offer_actions, self.offers)
            if self.hide_after and not self.cancel.is_set():
                GLib.timeout_add(self.cfg["ui"]["hide_after_action_ms"], self.ui.hide_if_idle)
        except Exception as e:  # noqa: BLE001
            GLib.idle_add(self.ui.finish, self.question, "", str(e))
