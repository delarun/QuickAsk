# QuickAsk — https://github.com/delarun/quickask — MIT
"""Стрим из OpenAI-совместимого /v1/chat/completions и разбор <think>…</think>."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from .config import DEFAULT_USER_AGENT

class ThinkSplitter:
    """Разбирает поток текста на <think>…</think> и обычный контент.

    Нужен, если сервер запущен с --reasoning-format none и рассуждения приходят
    прямо в content. Частичный тег на границе чанков буферизуется.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self, on_think, on_text):
        self.on_think, self.on_text = on_think, on_text
        self.inside = False
        self.buf = ""

    def feed(self, s: str) -> None:
        self.buf += s
        while self.buf:
            tag = self.CLOSE if self.inside else self.OPEN
            i = self.buf.find(tag)
            if i >= 0:
                self._emit(self.buf[:i])
                self.buf = self.buf[i + len(tag):]
                self.inside = not self.inside
                continue
            # придерживаем хвост, который может оказаться началом тега
            hold = 0
            for n in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-n:]):
                    hold = n
                    break
            self._emit(self.buf[: len(self.buf) - hold] if hold else self.buf)
            self.buf = self.buf[len(self.buf) - hold:] if hold else ""
            break

    def flush(self) -> None:
        self._emit(self.buf)
        self.buf = ""

    def _emit(self, s: str) -> None:
        if not s:
            return
        (self.on_think if self.inside else self.on_text)(s)


def stream_chat(llm: dict, messages: list[dict], tools: list[dict] | None,
                on_token, on_reasoning, cancel: threading.Event, model: str | None = None) -> tuple[str, str, list[dict]]:
    """SSE-стрим из /chat/completions. Возвращает (текст, рассуждения, tool_calls)."""
    body: dict = {
        "model": model or llm["model"],
        "messages": messages,
        "stream": True,
        "max_tokens": llm["max_tokens"],
        "temperature": llm["temperature"],
    }
    if llm.get("thinking") is not None:
        body["chat_template_kwargs"] = {"enable_thinking": bool(llm["thinking"])}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
               "User-Agent": llm.get("user_agent") or DEFAULT_USER_AGENT}
    if llm.get("api_key"):
        headers["Authorization"] = f"Bearer {llm['api_key']}"
    req = urllib.request.Request(
        llm["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    acc = {"content": "", "reasoning": ""}

    def _text(s: str) -> None:
        acc["content"] += s
        on_token(s)

    def _think(s: str) -> None:
        acc["reasoning"] += s
        on_reasoning(s)

    splitter = ThinkSplitter(_think, _text)
    calls: dict[int, dict] = {}
    try:
        with urllib.request.urlopen(req, timeout=llm["timeout"]) as r:
            for raw in r:
                if cancel.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"].get("message", str(chunk["error"])))
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                # llama.cpp (--reasoning-format deepseek, по умолчанию) → reasoning_content;
                # некоторые прокси → reasoning
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    _think(reasoning)
                if delta.get("content"):
                    splitter.feed(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    d = calls.setdefault(idx, {"id": tc.get("id") or f"call_{idx}", "name": "", "args": ""})
                    if tc.get("id"):
                        d["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        d["name"] = fn["name"]
                    if fn.get("arguments"):
                        d["args"] += fn["arguments"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM недоступна ({llm['base_url']}): {e.reason}") from e
    splitter.flush()
    return acc["content"], acc["reasoning"], [calls[i] for i in sorted(calls)]
