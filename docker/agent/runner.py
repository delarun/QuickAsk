#!/usr/bin/env python3
# QuickAsk — https://github.com/delarun/quickask — MIT
"""
agent runner — автономный агент, живёт ВНУТРИ docker-контейнера.

Получает задачу из /work/.agent/task.txt, крутит цикл «LLM → tool_calls → bash/файлы»
пока модель не вызовет finish(). Всё состояние — в /work/.agent/:
  task.txt      задача
  status.json   {state, step, summary, question, started, updated}
  log.jsonl     по строке на событие (llm/tool/result/error)
  inbox.txt     сообщение от пользователя: ответ на ask_user или заметка по ходу работы (пишет mcps/agent.py)
  messages.json история диалога с LLM — по ней агент продолжает работу после finish()
  followup.txt  «доделай ещё …»: если файл есть при старте, история загружается и задача продолжается

Окружение (ставит mcps/agent.py при docker run):
  LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, MAX_STEPS, TOOL_TIMEOUT, WORK (=/work), OUT (=/out)

Stdlib only. Работает и без контейнера (WORK=./job python3 runner.py) — так его и тестируем.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

WORK = os.environ.get("WORK", "/work")
OUT = os.environ.get("OUT", "/out")
STATE = os.path.join(WORK, ".agent")
LLM_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "")
API_KEY = os.environ.get("LLM_API_KEY", "")
USER_AGENT = os.environ.get("LLM_USER_AGENT",
                            "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "60"))
TOOL_TIMEOUT = int(os.environ.get("TOOL_TIMEOUT", "900"))
MAX_TOOL_OUT = 6000        # символов вывода команды, что видит модель
CONTEXT_BUDGET = 60_000    # символов; старые tool-выводы ужимаются при превышении

SYSTEM = f"""Ты автономный агент в изолированном Linux-контейнере (Debian, python3, pip, ffmpeg, yt-dlp,
curl, jq, git). Сети есть. Рабочая папка {WORK} (сохраняется на хосте пользователя). Папка {OUT} — это
папка пользователя для результатов (например ~/Downloads): готовые файлы клади туда, если она есть.

Правила:
- Работай сам, шаг за шагом: выполни команду, посмотри вывод, исправь, продолжай. Не спрашивай разрешения на
  рутинные шаги. Один bash-вызов — одна логическая операция; длинные задачи запускай в foreground, не в фоне.
- Проверяй результат (ls -la, размеры файлов, коды возврата), прежде чем объявлять задачу выполненной.
- Не переспрашивай очевидное. ask_user — только если задача действительно неоднозначна или нужны учётные данные.
- Скрипты пиши через write_file, потом запускай. pip: `pip install --user ...` (HOME={WORK}).
- Вывод команды виден пользователю по ходу выполнения — печатай прогресс. Команда без вывода дольше нескольких
  минут выглядит как зависание; лимит на команду {TOOL_TIMEOUT} с (можно задать timeout явно).
- Тяжёлые вычисления — векторизованно (numpy/scipy, fft-корреляция, ffmpeg-фильтры), не python-циклами по
  миллионам сэмплов. Прикинь объём работы до запуска; для проверки хватает выборки, а не всего файла.
- Когда всё готово — finish(summary) с кратким отчётом: что сделано, где лежат файлы, что не получилось.
Отвечай на языке задачи."""

def _fn(name: str, desc: str, props: dict, req: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": req}}}


TOOLS = [
    _fn("bash", "Выполнить команду в shell (bash -lc). Возвращает stdout+stderr и код возврата.",
        {"command": {"type": "string"},
         "timeout": {"type": "integer", "description": f"секунды, по умолчанию {TOOL_TIMEOUT}"}}, ["command"]),
    _fn("write_file", "Записать файл (перезаписывает). Относительные пути — от рабочей папки.",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _fn("read_file", "Прочитать текстовый файл (до 8000 символов).", {"path": {"type": "string"}}, ["path"]),
    _fn("ask_user", "Задать пользователю вопрос и ждать ответа (блокирует агента). "
        "Только когда без ответа продолжать нельзя.", {"question": {"type": "string"}}, ["question"]),
    _fn("finish", "Завершить задачу с итоговым отчётом.",
        {"summary": {"type": "string"}, "success": {"type": "boolean"}}, ["summary"]),
]

FENCE_RE = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.S)


# ─────────────────────────── state ───────────────────────────

def log(kind: str, **data) -> None:
    rec = {"t": time.strftime("%H:%M:%S"), "kind": kind, **data}
    with open(os.path.join(STATE, "log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    short = {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in data.items()}
    print(f"[{rec['t']}] {kind} {json.dumps(short, ensure_ascii=False)}", flush=True)


_status: dict = {}


def repair(messages: list[dict]) -> None:
    """Каждому tool_call в assistant-сообщении нужен ответ role=tool — иначе API отвергнет историю
    (wrong_api_format). Дописываем заглушки за вызовы, ответ на которые не успели добавить
    (finish, прерывание, несколько вызовов в одном сообщении)."""
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            need = [tc["id"] for tc in m["tool_calls"] if tc.get("id")]
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                if messages[j].get("tool_call_id") in need:
                    need.remove(messages[j]["tool_call_id"])
                j += 1
            for k, cid in enumerate(need):
                messages.insert(j + k, {"role": "tool", "tool_call_id": cid, "content": "(вызов завершён)"})
            i = j + len(need)
        else:
            i += 1


def save_messages(messages: list[dict]) -> None:
    repair(messages)
    tmp = os.path.join(STATE, "messages.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(STATE, "messages.json"))


def take_inbox() -> str | None:
    """Забрать сообщение пользователя, если есть (ответ на вопрос или заметка по ходу)."""
    inbox = os.path.join(STATE, "inbox.txt")
    if not os.path.exists(inbox):
        return None
    with open(inbox, encoding="utf-8") as f:
        text = f.read().strip()
    os.remove(inbox)
    return text or None


def set_status(**kw) -> None:
    _status.update(kw, updated=time.time())
    tmp = os.path.join(STATE, "status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_status, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(STATE, "status.json"))


def truncate(s: str, n: int = MAX_TOOL_OUT) -> str:
    if len(s) <= n:
        return s
    half = n // 2
    return s[:half] + f"\n…[обрезано {len(s) - n} символов]…\n" + s[-half:]


# ─────────────────────────── tools ───────────────────────────

def _path(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(WORK, p)


LIVE = os.path.join(STATE, "live.txt")           # вывод текущей команды по ходу — читает mcps/agent.py / UI
INTERRUPT = os.path.join(STATE, "interrupt")     # файл-флаг: убить текущую команду, агент продолжит


def t_bash(command: str, timeout: int | None = None) -> str:
    timeout = int(timeout or TOOL_TIMEOUT)
    env = dict(os.environ, PYTHONUNBUFFERED="1")  # чтобы print внутри heredoc-питона шёл сразу
    with open(LIVE, "w", encoding="utf-8") as f:
        f.write(f"$ {command}\n")
    set_status(current=command[:300], current_started=time.time())
    if os.path.exists(INTERRUPT):
        os.remove(INTERRUPT)
    proc = subprocess.Popen(["bash", "-lc", command], cwd=WORK, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True)
    chunks: list[str] = []
    done = threading.Event()

    def pump() -> None:
        assert proc.stdout
        with open(LIVE, "a", encoding="utf-8") as live:
            for line in proc.stdout:
                chunks.append(line)
                live.write(line)
                live.flush()
        done.set()

    threading.Thread(target=pump, daemon=True).start()
    started = time.time()
    tail = ""
    while not done.wait(0.5):
        if os.path.exists(INTERRUPT):
            os.remove(INTERRUPT)
            _kill(proc)
            tail = "\n[прервано пользователем]"
            break
        if time.time() - started > timeout:
            _kill(proc)
            tail = f"\n[timeout {timeout}s — команда убита]"
            break
    done.wait(5)
    proc.wait()
    set_status(current="", current_started=0)
    out = "".join(chunks).strip() or "(нет вывода)"
    return truncate(out) + tail + f"\n[exit {proc.returncode}]"


def _kill(proc: subprocess.Popen) -> None:
    import signal
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def t_write_file(path: str, content: str) -> str:
    p = _path(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return f"записано {len(content)} символов в {p}"


def t_read_file(path: str) -> str:
    p = _path(path)
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return truncate(f.read(8000 * 2), 8000)
    except OSError as e:
        return f"ошибка: {e}"


def t_ask_user(question: str) -> str:
    set_status(state="waiting", question=question)
    log("ask_user", question=question)
    while True:
        time.sleep(2)
        ans = take_inbox()
        if ans:
            set_status(state="running", question="")
            log("user_reply", text=ans)
            return f"Ответ пользователя: {ans}"
        if os.path.exists(os.path.join(STATE, "stop")):
            raise SystemExit("stopped")


# ─────────────────────────── LLM ───────────────────────────

def chat(messages: list[dict]) -> dict:
    body = {"model": MODEL, "messages": messages, "tools": TOOLS, "tool_choice": "auto"}
    req = urllib.request.Request(LLM_URL + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT,
                                          **({"Authorization": f"Bearer {API_KEY}"} if API_KEY else {})})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"LLM HTTP {e.code}: {err}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 4:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"LLM недоступна: {e}") from e
    raise RuntimeError("LLM: исчерпаны попытки")


def compact(messages: list[dict]) -> None:
    """Ужать старые tool-выводы, когда контекст разрастается."""
    total = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
    if total < CONTEXT_BUDGET:
        return
    for m in messages[2:-6]:
        if m.get("role") == "tool" and len(m.get("content", "")) > 400:
            m["content"] = m["content"][:300] + "\n…[ужато]"


def parse_calls(msg: dict) -> list[tuple[str, str, dict]]:
    """→ [(id, name, args)]. Если модель не умеет tool_calls — берём ```bash``` из текста."""
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {"command": fn.get("arguments", "")} if fn.get("name") == "bash" else {}
        calls.append((tc.get("id") or f"call_{len(calls)}", fn.get("name", ""), args if isinstance(args, dict) else {}))
    if not calls and msg.get("content"):
        for i, block in enumerate(FENCE_RE.findall(msg["content"])):
            calls.append((f"fence_{i}", "bash", {"command": block.strip()}))
    return calls


# ─────────────────────────── main loop ───────────────────────────

def main() -> int:
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "task.txt"), encoding="utf-8") as f:
        task = f.read().strip()
    for stale in ("stop",):
        if os.path.exists(os.path.join(STATE, stale)):
            os.remove(os.path.join(STATE, stale))

    followup_path = os.path.join(STATE, "followup.txt")
    history_path = os.path.join(STATE, "messages.json")
    messages: list[dict]
    if os.path.exists(followup_path) and os.path.exists(history_path):
        # продолжение: та же папка, та же история, новое поручение
        with open(followup_path, encoding="utf-8") as f:
            followup = f.read().strip()
        os.remove(followup_path)
        with open(history_path, encoding="utf-8") as f:
            messages = json.load(f)
        repair(messages)
        messages[0] = {"role": "system", "content": SYSTEM}
        messages.append({"role": "user", "content": f"Продолжение от пользователя: {followup}\n\n"
                                                     "Рабочая папка и файлы сохранились. Сделай это и снова вызови finish()."})
        set_status(state="running", step=0, summary="", question="", started=time.time(),
                   task=(task[:120] + " → " + followup)[:200])
        log("followup", text=followup, model=MODEL)
    else:
        set_status(state="running", step=0, summary="", question="", started=time.time(), task=task[:200])
        log("start", task=task, model=MODEL, llm=LLM_URL)
        out_note = (f"Папка {OUT} смонтирована." if os.path.isdir(OUT)
                    else f"Папки {OUT} нет — результаты оставляй в {WORK}.")
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"Задача: {task}\n\n{out_note}"}]
    idle = 0
    for step in range(1, MAX_STEPS + 1):
        if os.path.exists(os.path.join(STATE, "stop")):
            set_status(state="stopped", summary="остановлен пользователем")
            save_messages(messages)
            return 0
        note = take_inbox()  # заметка пользователя по ходу работы
        if note:
            log("user_note", text=note)
            messages.append({"role": "user", "content": f"Сообщение от пользователя по ходу работы: {note}"})
        set_status(step=step)
        compact(messages)
        save_messages(messages)
        try:
            msg = chat(messages)
        except RuntimeError as e:
            log("error", text=str(e))
            set_status(state="failed", summary=str(e))
            save_messages(messages)
            return 1
        content = msg.get("content") or ""
        calls = parse_calls(msg)
        log("llm", content=content, calls=[n for _, n, _ in calls])
        messages.append({"role": "assistant", "content": content or None,
                         **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})})

        if not calls:
            idle += 1
            if idle >= 3:  # модель просто болтает — считаем текст финальным отчётом
                set_status(state="done", summary=content[:2000])
                log("finish", summary=content, forced=True)
                save_messages(messages)
                return 0
            messages.append({"role": "user", "content": "Продолжай: вызови инструмент (bash/write_file/…) "
                                                        "или finish(summary), если всё готово."})
            continue
        idle = 0

        for cid, name, args in calls:
            if name == "finish":
                summary = args.get("summary") or content
                ok = args.get("success", True)
                set_status(state="done" if ok else "failed", summary=summary[:4000])
                log("finish", summary=summary, success=ok)
                messages.append({"role": "tool", "tool_call_id": cid, "content": f"Задача завершена. Отчёт: {summary[:500]}"})
                save_messages(messages)
                return 0 if ok else 2
            log("tool_start", name=name, args=args)
            try:
                if name == "bash":
                    result = t_bash(args.get("command", ""), args.get("timeout"))
                elif name == "write_file":
                    result = t_write_file(args["path"], args.get("content", ""))
                elif name == "read_file":
                    result = t_read_file(args["path"])
                elif name == "ask_user":
                    result = t_ask_user(args.get("question", "?"))
                else:
                    result = f"неизвестный инструмент {name}"
            except SystemExit:
                set_status(state="stopped", summary="остановлен пользователем")
                return 0
            except Exception as e:  # noqa: BLE001
                result = f"ошибка инструмента: {e}"
            log("tool", name=name, args=args, result=result)
            if cid.startswith("fence_"):  # фолбэк без tool_calls — отдаём результат как user-сообщение
                messages.append({"role": "user", "content": f"Результат `{args.get('command', '')[:80]}`:\n{result}"})
            else:
                messages.append({"role": "tool", "tool_call_id": cid, "content": result})

    set_status(state="failed", summary=f"лимит шагов {MAX_STEPS} исчерпан")
    log("error", text="max steps")
    save_messages(messages)
    return 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        set_status(state="stopped", summary="прерван")
