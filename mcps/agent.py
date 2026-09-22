#!/usr/bin/env python3
# QuickAsk — https://github.com/delarun/quickask — MIT
"""
agent — MCP-сервер (stdio) фоновых агентов для QuickAsk.

start_agent("скачай плейлист …") → создаёт ~/Agents/<дата>-<slug>/, кладёт задачу в .agent/task.txt и
запускает docker-контейнер с docker/agent/runner.py внутри. Агент пишет статус и лог в ту же папку;
этот процесс следит за ними и шлёт notify-send, когда агент закончил или задал вопрос.

Окружение (задаётся в [[mcp]] env):
  AGENT_HOME     ~/Agents            корень папок задач (монтируется в /work)
  AGENT_OUT      ~/Downloads         папка результатов (монтируется в /out); "" — не монтировать
  AGENT_IMAGE    ghcr.io/delarun/quickask-agent:latest   образ из docker/agent (собирает CI)
  AGENT_RUNNER   docker/agent/runner.py из исходников — монтируется поверх копии в образе
                 (правки без пересборки); в бинарнике его нет, работает runner из образа
  AGENT_LLM_URL  http://127.0.0.1:8080/v1   LLM для агента (из контейнера, см. AGENT_NET)
  AGENT_MODEL    имя модели агента   (2B-модели для агента мало — см. README)
  AGENT_API_KEY, AGENT_NET (host|bridge), AGENT_MAX_STEPS (60), AGENT_MEM (4g), AGENT_CPUS (2)
  AGENT_QUICKASK quickask            команда, которой открывать диалог по клику на уведомлении

Интерфейс в QuickAsk — тоже отсюда, через quickask.sdk: кнопка «Агенты» в шапке, страницы
списка и диалога с агентом, команды /agents, /agent, /open, /log, /status, /stop, /reply, /continue.

Уведомления: notify-send с кнопкой «Открыть диалог» (нужен демон с поддержкой actions — mako, swaync, dunst),
клик → `quickask --open agent:agent?id=<id>` → окно QuickAsk сразу на странице агента.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time

from quickask.sdk import Result, Server, ui

HOME = os.path.expanduser("~")
AGENT_HOME = os.path.expanduser(os.environ.get("AGENT_HOME", f"{HOME}/Agents"))
AGENT_OUT = os.path.expanduser(os.environ.get("AGENT_OUT", f"{HOME}/Downloads"))
IMAGE = os.environ.get("AGENT_IMAGE", "ghcr.io/delarun/quickask-agent:latest")
_SRC_RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docker", "agent", "runner.py")
RUNNER = os.path.expanduser(os.environ.get("AGENT_RUNNER", _SRC_RUNNER))
LLM_URL = os.environ.get("AGENT_LLM_URL", "http://127.0.0.1:8080/v1")
MODEL = os.environ.get("AGENT_MODEL", "")
API_KEY = os.environ.get("AGENT_API_KEY", "")
USER_AGENT = os.environ.get("AGENT_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0")
NET = os.environ.get("AGENT_NET", "host")
MAX_STEPS = os.environ.get("AGENT_MAX_STEPS", "60")
MEM = os.environ.get("AGENT_MEM", "4g")
CPUS = os.environ.get("AGENT_CPUS", "2")
# чем открывать диалог по клику на уведомление: из бинарника — им же, из исходников — командой в PATH
QUICKASK = os.environ.get("AGENT_QUICKASK") or (sys.executable if getattr(sys, "frozen", False) else "quickask")

srv = Server("agent", version="0.2")
tool = srv.tool


# ─────────────────────────── jobs ───────────────────────────

def slug(text: str, n: int = 40) -> str:
    tr = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeejziyklmnoprstufhccss_y_eua")
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().translate(tr)).strip("-")
    return s[:n].rstrip("-") or "task"


def job_dir(job_id: str) -> str | None:
    if not os.path.isdir(AGENT_HOME):
        return None
    for d in os.listdir(AGENT_HOME):
        if d == job_id or d.startswith(job_id) or d.endswith("-" + job_id):
            return os.path.join(AGENT_HOME, d)
    return None


def read_status(d: str) -> dict:
    try:
        with open(os.path.join(d, ".agent", "status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"state": "unknown"}


def container_name(d: str) -> str:
    return "qa-agent-" + os.path.basename(d)


def container_running(d: str) -> bool:
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container_name(d)],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


def fmt_job(d: str) -> str:
    st = read_status(d)
    state = st.get("state", "?")
    if state == "running" and not container_running(d):
        state = "dead"  # контейнер умер, статус не обновился
    age = ""
    if st.get("updated"):
        m = int((time.time() - st["updated"]) / 60)
        age = f"{m} мин назад" if m < 120 else f"{m // 60} ч назад"
    icon = {"running": "🏃", "waiting": "❓", "done": "✅", "failed": "❌", "stopped": "⏹", "dead": "💀"}.get(state, "•")
    line = f"{icon} [{os.path.basename(d)}] {state}, шаг {st.get('step', '?')}, {age}\n    задача: {st.get('task', '')}"
    if st.get("question"):
        line += f"\n    ❓ вопрос: {st['question']}"
    if st.get("summary") and state in ("done", "failed", "stopped"):
        line += f"\n    итог: {st['summary'][:300]}"
    cmd, secs = current_cmd(st)
    if cmd and state == "running":
        line += f"\n    ⏳ выполняется {secs // 60} мин {secs % 60:02d} с: $ {cmd[:160]}"
    return line


def last_activity(d: str) -> str:
    """Что агент делает сейчас — последняя команда или реплика модели из лога."""
    p = os.path.join(d, ".agent", "log.jsonl")
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        k = rec.get("kind")
        if k == "tool":
            a = rec.get("args") or {}
            return f"$ {a.get('command') or a.get('path') or a.get('question') or ''}"[:120]
        if k == "llm" and rec.get("content"):
            return f"🤖 {rec['content']}"[:120]
        if k == "ask_user":
            return f"❓ {rec.get('question', '')}"[:120]
    return ""


def current_cmd(st: dict) -> tuple[str, int]:
    """(команда, секунд выполняется) для команды, которая идёт прямо сейчас, иначе ('', 0)."""
    if st.get("state") == "running" and st.get("current"):
        return st["current"], int(time.time() - (st.get("current_started") or time.time()))
    return "", 0


def live_tail(d: str, chars: int = 1500) -> str:
    try:
        with open(os.path.join(d, ".agent", "live.txt"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - chars))
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def job_info(d: str) -> dict:
    st = read_status(d)
    state = st.get("state", "?")
    if state == "running" and not container_running(d):
        state = "dead"
    cmd, secs = current_cmd(st)
    return {"id": os.path.basename(d), "state": state, "step": st.get("step", 0), "task": st.get("task", ""),
            "summary": (st.get("summary") or "")[:300], "question": st.get("question") or "",
            "updated": st.get("updated", 0), "last": last_activity(d), "dir": d,
            "current": cmd, "current_secs": secs, "live": live_tail(d, 400) if cmd else ""}


def list_dirs() -> list[str]:
    if not os.path.isdir(AGENT_HOME):
        return []
    ds = [os.path.join(AGENT_HOME, d) for d in os.listdir(AGENT_HOME)
          if os.path.isdir(os.path.join(AGENT_HOME, d, ".agent"))]
    return sorted(ds, key=os.path.getmtime, reverse=True)


# ─────────────────────────── tools ───────────────────────────

_pull: subprocess.Popen | None = None   # фоновый docker pull, чтобы не запускать его дважды


def _ensure_image() -> str | None:
    """Образ есть локально — None. Нет — начинаем docker pull в фоне и объясняем, что делать.

    Синхронно тянуть нельзя: сотни мегабайт не уложатся в таймаут вызова инструмента."""
    global _pull
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0:
        return None
    if _pull is None or _pull.poll() is not None:
        try:
            _pull = subprocess.Popen(["docker", "pull", IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            return "Docker не найден — фоновым агентам он нужен (Docker Engine или Docker Desktop)."
    return (f"Образа {IMAGE} ещё нет — скачиваю его в фоне (docker pull, пара минут). "
            f"Повтори задачу, когда докачается. Свой образ: docker build -t {IMAGE} docker/agent")


@tool("start_agent",
      "Запустить фонового агента в Docker для длинной или многошаговой задачи: скачать плейлист/видео, "
      "сконвертировать файлы, написать и выполнить скрипт, разобрать архив данных и т.п. Агент сам пишет код, "
      "выполняет команды и проверяет результат; результат кладёт в папку результатов пользователя. "
      "Возвращает id задачи — статус потом через agent_status / list_agents.",
      {"task": {"type": "string", "description": "полное описание задачи, со всеми ссылками и деталями, как сказал пользователь"},
       "name": {"type": "string", "description": "короткое имя задачи латиницей (необязательно)"}},
      ["task"])
def start_agent(task: str, name: str = "") -> str | Result:
    err = _ensure_image()
    if err:
        return err
    os.makedirs(AGENT_HOME, exist_ok=True)
    jid = time.strftime("%m%d-%H%M") + "-" + slug(name or task, 30)
    d = os.path.join(AGENT_HOME, jid)
    os.makedirs(os.path.join(d, ".agent"), exist_ok=True)
    with open(os.path.join(d, ".agent", "task.txt"), "w", encoding="utf-8") as f:
        f.write(task.strip() + "\n")
    err = _launch(d, task)
    if err:
        return err
    return Result(f"OK: агент [{jid}] запущен в фоне.\nПапка: {d}\nРезультаты: {AGENT_OUT or d}\n"
                  f"Уведомлю, когда закончит; статус — agent_status(\"{jid}\") или /agents в QuickAsk.",
                  [ui.open("agent", id=jid, label="Открыть агента")])


def _launch(d: str, task: str) -> str | None:
    """docker run для папки задачи. None — ок, иначе текст ошибки."""
    subprocess.run(["docker", "rm", "-f", container_name(d)], capture_output=True)  # хвост от прошлого запуска
    # файлы в /work и /out — от имени хозяина; на Windows uid нет, Docker Desktop мапит владельца сам
    user = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    cmd = ["docker", "run", "-d", "--rm", "--name", container_name(d), "--network", NET,
           *user, "--memory", MEM, "--cpus", CPUS, "--pids-limit", "1024",
           "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
           "-e", "HOME=/work", "-e", f"LLM_BASE_URL={LLM_URL}", "-e", f"LLM_MODEL={MODEL}",
           "-e", f"LLM_API_KEY={API_KEY}", "-e", f"LLM_USER_AGENT={USER_AGENT}",
           "-e", f"MAX_STEPS={MAX_STEPS}", "-e", "WORK=/work", "-e", "OUT=/out",
           "-v", f"{d}:/work"]
    if AGENT_OUT:
        os.makedirs(AGENT_OUT, exist_ok=True)
        cmd += ["-v", f"{AGENT_OUT}:/out"]
    if os.path.isfile(RUNNER):
        cmd += ["-v", f"{RUNNER}:/agent/runner.py:ro"]
    if NET == "bridge":
        cmd += ["--add-host", "host.docker.internal:host-gateway"]
    cmd += [IMAGE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return f"docker run не удался: {r.stderr.strip()[:500]}"
    st = read_status(d)
    st.update(state="running", step=0, task=task[:200], question="", started=time.time(), updated=time.time())
    with open(os.path.join(d, ".agent", "status.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    _watch.add(d)
    return None


@tool("list_agents", "Список фоновых агентов: id, состояние, шаг, задача, итог. Новые первые.",
      {"limit": {"type": "integer"}})
def list_agents(limit: int = 10) -> str:
    ds = list_dirs()[:max(1, limit)]
    return "\n".join(fmt_job(d) for d in ds) if ds else "Агентов ещё не запускали."


def parse_log(d: str, limit: int) -> list[dict]:
    """Структурированные события лога (новые в конце) — для UI."""
    p = os.path.join(d, ".agent", "log.jsonl")
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        k = rec.get("kind")
        if k == "tool_start":
            continue                      # парная запись tool несёт и команду, и результат
        e = {"t": rec.get("t", ""), "kind": k}
        if k == "tool":
            a = rec.get("args") or {}
            e["name"] = rec.get("name", "")
            e["cmd"] = a.get("command") or a.get("path") or a.get("question") or ""
            e["result"] = str(rec.get("result", ""))
        elif k == "llm":
            e["text"] = rec.get("content") or ""
            e["calls"] = rec.get("calls") or []
            if not e["text"] and not e["calls"]:
                continue
        elif k in ("user_reply", "user_note", "followup"):
            e["text"] = rec.get("text", "")
        elif k == "ask_user":
            e["text"] = rec.get("question", "")
        elif k == "finish":
            e["text"] = rec.get("summary", "")
            e["success"] = rec.get("success", True)
        elif k == "error":
            e["text"] = rec.get("text", "")
        elif k == "start":
            e["text"] = rec.get("task", "")
        else:
            continue
        out.append(e)
    return out[-max(1, limit):]


@tool("agent_status", "Состояние агента по id (можно начало id) + последние строки лога.",
      {"id": {"type": "string"}, "log_lines": {"type": "integer", "description": "сколько строк лога, по умолчанию 8"}},
      ["id"])
def agent_status(id: str, log_lines: int = 8) -> str:  # noqa: A002
    d = job_dir(id)
    if not d:
        return f"Агента {id} нет. Есть:\n" + list_agents(5)
    live = ""
    cmd, _ = current_cmd(read_status(d))
    if cmd:
        live = "\n\nвывод текущей команды:\n```\n" + (live_tail(d) or "(пока пусто)") + "\n```"
    return fmt_job(d) + live + "\n\nлог:\n" + agent_log(id, log_lines)


@tool("agent_log", "Хвост лога агента (команды, их вывод, ответы модели).",
      {"id": {"type": "string"}, "lines": {"type": "integer", "description": "по умолчанию 20"}}, ["id"])
def agent_log(id: str, lines: int = 20) -> str:  # noqa: A002
    d = job_dir(id)
    if not d:
        return f"Агента {id} нет."
    p = os.path.join(d, ".agent", "log.jsonl")
    if not os.path.exists(p):
        r = subprocess.run(["docker", "logs", "--tail", str(lines), container_name(d)], capture_output=True, text=True)
        return (r.stdout + r.stderr).strip() or "лог пуст (агент ещё стартует?)"
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f.readlines()[-max(1, lines):]:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            k = rec.get("kind")
            if k == "tool_start":
                continue  # парная запись tool покажет и команду, и результат
            if k == "tool":
                out.append(f"{rec['t']} $ {json.dumps(rec.get('args'), ensure_ascii=False)[:200]}\n"
                           f"    → {str(rec.get('result', ''))[:600].replace(chr(10), ' ⏎ ')}")
            elif k == "llm":
                out.append(f"{rec['t']} 🤖 {(rec.get('content') or '')[:200]} {rec.get('calls') or ''}")
            else:
                rest = {a: b for a, b in rec.items() if a not in ("t", "kind")}
                out.append(f"{rec['t']} {k}: {json.dumps(rest, ensure_ascii=False)[:300]}")
    return "\n".join(out)


@tool("agent_message",
      "Написать агенту. Если он ждёт ответа (waiting) — это ответ на его вопрос; если работает — заметка, которую "
      "он учтёт на следующем шаге; если уже закончил (done/failed/stopped) — продолжение задачи: агент "
      "перезапускается в той же папке с той же историей и делает, что просят («переименуй файлы», «ещё и в flac»).",
      {"id": {"type": "string"}, "message": {"type": "string"}}, ["id", "message"])
def agent_message(id: str, message: str) -> str:  # noqa: A002
    d = job_dir(id)
    if not d:
        return f"Агента {id} нет. Есть:\n" + list_agents(5)
    name = os.path.basename(d)
    st = read_status(d)
    if container_running(d):
        with open(os.path.join(d, ".agent", "inbox.txt"), "w", encoding="utf-8") as f:
            f.write(message.strip() + "\n")
        kind = "ответ на вопрос" if st.get("state") == "waiting" else "заметка по ходу работы"
        return f"OK: {kind} передан агенту [{name}]"
    if not os.path.exists(os.path.join(d, ".agent", "messages.json")):
        return f"У агента [{name}] нет сохранённой истории — запусти новую задачу через start_agent."
    with open(os.path.join(d, ".agent", "followup.txt"), "w", encoding="utf-8") as f:
        f.write(message.strip() + "\n")
    err = _launch(d, st.get("task", ""))
    if err:
        return err
    return f"OK: агент [{name}] продолжает работу в той же папке: {message.strip()[:120]}"


@tool("interrupt_agent",
      "Прервать ТЕКУЩУЮ команду агента (например, зависшую или слишком долгую), не останавливая самого агента: "
      "он увидит «[прервано пользователем]» и решит, что делать дальше. Часто вместе с agent_message «сделай иначе».",
      {"id": {"type": "string"}}, ["id"])
def interrupt_agent(id: str) -> str:  # noqa: A002
    d = job_dir(id)
    if not d:
        return f"Агента {id} нет."
    cmd, secs = current_cmd(read_status(d))
    if not cmd:
        return f"Агент [{os.path.basename(d)}] сейчас не выполняет команду (думает или ждёт)."
    open(os.path.join(d, ".agent", "interrupt"), "w").close()
    return f"OK: прерываю `{cmd[:100]}` ({secs} с) у агента [{os.path.basename(d)}]"


@tool("stop_agent", "Остановить агента (контейнер убивается, папка с результатами остаётся).",
      {"id": {"type": "string"}}, ["id"])
def stop_agent(id: str) -> str:  # noqa: A002
    d = job_dir(id)
    if not d:
        return f"Агента {id} нет."
    open(os.path.join(d, ".agent", "stop"), "w").close()
    subprocess.run(["docker", "kill", container_name(d)], capture_output=True)
    st = read_status(d)
    st.update(state="stopped", summary="остановлен пользователем", updated=time.time())
    with open(os.path.join(d, ".agent", "status.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    return f"OK: агент [{os.path.basename(d)}] остановлен"


# ─────────────────────────── watcher → notify-send ───────────────────────────

class Watcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.dirs: dict[str, str] = {}   # dir → последнее виденное состояние
        self.lock = threading.Lock()

    def add(self, d: str) -> None:
        with self.lock:
            self.dirs[d] = "running"

    def run(self) -> None:
        for d in list_dirs():  # подхватить живые после перезапуска
            if read_status(d).get("state") in ("running", "waiting"):
                self.add(d)
        while True:
            time.sleep(4)
            with self.lock:
                items = list(self.dirs.items())
            for d, seen in items:
                st = read_status(d)
                state = st.get("state", "unknown")
                if state == "running" and not container_running(d):
                    state = "dead"
                if state == seen:
                    continue
                self.dirs[d] = state
                name = os.path.basename(d)
                if state == "waiting":
                    notify(f"❓ Агент {name} спрашивает", st.get("question", ""), job=name, urgency="critical")
                elif state == "done":
                    notify(f"✅ Агент {name} закончил", st.get("summary", "")[:400], job=name)
                elif state in ("failed", "dead"):
                    notify(f"❌ Агент {name}: {state}", st.get("summary", "контейнер завершился без статуса")[:400],
                           job=name, urgency="critical")
                if state in ("done", "failed", "dead", "stopped"):
                    with self.lock:
                        self.dirs.pop(d, None)


def _notify_supports_actions() -> bool:
    try:
        return "--action" in subprocess.run(["notify-send", "--help"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return False


HAS_ACTIONS = _notify_supports_actions()


def notify(title: str, body: str, job: str | None = None, urgency: str = "normal") -> None:
    """notify-send; с job — кнопка «Открыть диалог», по клику — `quickask --open agent:agent?id=<id>`."""
    def run() -> None:
        cmd = ["notify-send", "-a", "QuickAsk", "-u", urgency, "-t", "0" if urgency == "critical" else "15000"]
        if job and HAS_ACTIONS:
            cmd += ["-A", "open=Открыть диалог", "-A", "log=Показать лог"]
        cmd += [title, body]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return
        action = r.stdout.strip()
        if job and action in ("open", "log"):
            subprocess.Popen([QUICKASK, "--open", f"{srv.name}:agent?id={job}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    threading.Thread(target=run, daemon=True).start()


_watch = Watcher()


# ─────────────────────────── интерфейс в QuickAsk ───────────────────────────
# Всё, что QuickAsk показывает про агентов, описано здесь: он сам про агентов ничего не знает.

DOTS = {"running": ("●", "green"), "waiting": ("◆", "yellow"), "done": ("●", "blue"),
        "failed": ("●", "red"), "dead": ("✕", "red"), "stopped": ("■", "grey")}
ICONS = {"running": "🏃", "waiting": "❓", "done": "✅", "failed": "❌", "dead": "💀", "stopped": "⏹"}
HINTS = {"waiting": "агент ждёт ответа — напиши его в строке ниже",
         "running": "сообщение уйдёт заметкой к следующему шагу · «⏭ Команду» прервёт текущую",
         "done": "напиши, что доделать — продолжит в той же папке",
         "failed": "напиши, что исправить — продолжит с той же историей",
         "dead": "контейнер умер — сообщение перезапустит задачу с той же историей",
         "stopped": "сообщение продолжит задачу"}
LOG_LINES, LOG_STEP, LOG_MAX = 14, 30, 200


def _age(ts: float) -> str:
    if not ts:
        return ""
    m = int((time.time() - ts) / 60)
    return f"{m} мин назад" if m < 120 else (f"{m // 60} ч назад" if m < 2880 else f"{m // 1440} д назад")


def _tail(text: str, lines: int) -> str:
    rows = [r for r in (text or "").splitlines() if r.strip()]
    cut = rows[-lines:]
    head = f"…ещё {len(rows) - len(cut)} строк выше\n" if len(rows) > len(cut) else ""
    return head + "\n".join(r[:400] for r in cut)


def _clock(secs: int) -> str:
    return f"{secs // 60}:{secs % 60:02d}"


@srv.view("agents")
def view_agents() -> dict:
    jobs = [job_info(d) for d in list_dirs()[:30]]
    if not jobs:
        return ui.page("Агенты", color="peach", context="пусто", refresh_ms=3000,
                       keys="/agent <задача> — новый агент", blocks=[
                           ui.empty("Фоновых задач пока нет.\n"
                                    "Напиши «скачай …», «сконвертируй …» — или /agent <задача>.")])
    running = sum(1 for a in jobs if a["state"] == "running")
    waiting = sum(1 for a in jobs if a["state"] == "waiting")
    rows = []
    for a in jobs:
        if a["question"]:
            last = ui.line("❓ " + a["question"], "ask")
        elif a["current"]:
            last = ui.line(f"⏳ {_clock(a['current_secs'])}   $ {a['current']}", "now")
        elif a["state"] in ("running", "waiting"):
            last = ui.line(a["last"], "now") if a["last"] else None
        else:
            last = ui.line(a["summary"], "done") if a["summary"] else None
        ch, color = DOTS.get(a["state"], ("•", "grey"))
        rows.append(ui.row(a["id"], meta=f"{a['state']} · шаг {a['step']} · {_age(a['updated'])}",
                           dot=ui.dot(ch, color), action=ui.open("agent", id=a["id"]),
                           lines=[ui.line(a["task"], "task")] + ([last] if last else [])))
    return ui.page("Агенты", color="peach", refresh_ms=3000,
                   context=f"{len(jobs)} задач · {running} в работе" + (f" · {waiting} ждут ответа" if waiting else ""),
                   keys="Клик — открыть диалог · /agent <задача> — новый", blocks=[ui.rows(rows)])


def _log_card(e: dict) -> dict | None:
    kind = e.get("kind")
    if kind == "tool":
        res = e.get("result") or ""
        head = ("$ " if e.get("name") == "bash" else f"{e.get('name')}: ") + (e.get("cmd") or "")
        lines = [ui.line(head, "cmd")] + ([ui.line(_tail(res, 8), "out")] if res else [])
        return ui.card(*lines, time=e.get("t", ""), style="bad" if "exit" in res and "[exit 0]" not in res else "plain")
    if kind == "llm":
        text, calls = (e.get("text") or "").strip(), e.get("calls") or []
        if not text and not calls:
            return None
        return ui.card(ui.line("🤖 " + (text or ", ".join(calls)), "say"), style="say")
    if kind in ("user_reply", "user_note", "followup"):
        prefix = {"user_reply": "💬 ответ: ", "user_note": "💬 заметка: ", "followup": "💬 продолжить: "}[kind]
        return ui.card(ui.line(prefix + e.get("text", ""), "say"), style="me")
    if kind == "ask_user":
        return ui.card(ui.line("❓ " + e.get("text", ""), "say"), style="me")
    if kind in ("finish", "error", "start"):
        icon = {"finish": "✅", "error": "⚠", "start": "▶"}[kind]
        return ui.card(ui.line(f"{icon} {e.get('text', '')}", "say"), style="bad" if kind == "error" else "plain")
    return None


@srv.view("agent")
def view_agent(id: str, lines: int = LOG_LINES) -> dict:  # noqa: A002
    d = job_dir(id)
    back = ui.button("← Список", ui.open("agents", replace=True))
    if not d:
        return ui.page("Агент", color="mauve", context=id, mono=True, buttons=[back],
                       blocks=[ui.empty(f"⚠ Агента {id} нет")])
    a = job_info(d)
    jid, state = a["id"], a["state"]

    buttons = [back]
    if lines < LOG_MAX:
        buttons.append(ui.button("Лог", ui.open("agent", id=jid, lines=min(lines + LOG_STEP, LOG_MAX), replace=True)))
    if a["current"]:
        buttons.append(ui.button("⏭ Команду", ui.call("interrupt_agent", id=jid)))
    if state in ("running", "waiting"):
        buttons.append(ui.button("⏹ Стоп", ui.call("stop_agent", id=jid), style="danger"))

    # сводка: задача, вопрос или итог, подсказка, что сделает Enter
    summary = [ui.line(a["task"])]
    tail = a["question"] or (a["summary"] if state != "running" else "")
    if tail:
        summary.append(ui.line(("❓ " if a["question"] else "") + tail))
    if HINTS.get(state):
        summary.append(ui.line(HINTS[state], "hint"))
    style = {"waiting": "warn", "failed": "error", "dead": "error"}.get(state, "info")
    blocks = [ui.card(*summary, style=style)]
    blocks += [c for c in map(_log_card, parse_log(d, lines)) if c]
    if a["current"]:                           # идущая команда — живой вывод
        blocks.append(ui.card(ui.line(f"⏳ {_clock(a['current_secs'])}   $ {a['current']}", "cmd"),
                              ui.line(_tail(live_tail(d, 4000) or "(вывода пока нет)", 12), "out"), style="live"))

    return ui.page("Агент", color="mauve", context=f"{jid}   {ICONS.get(state, '•')} {state}", mono=True,
                   buttons=buttons, blocks=blocks, refresh_ms=3000, scroll="bottom",
                   input=ui.entry("Сообщение агенту: ответ, заметка или «доделай ещё …»",
                                  ui.call("agent_message", id=jid, message="$text"), glyph="🤖"),
                   keys="Enter — отправить агенту · Ctrl+L — выйти в чат")


# ── команды и кнопка ────────────────────────────────────────────────────────

def _split_id(text: str) -> tuple[str, str]:
    """"<id> <текст>" → (id, текст)."""
    parts = text.split(maxsplit=1)
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


@srv.command("/agents", "меню фоновых агентов")
def cmd_agents(_text: str):
    return ui.open("agents")


@srv.command("/agent", "запустить фонового агента", "<задача>")
def cmd_agent(text: str):
    if not text.strip():
        return "использование: /agent <задача>"
    out = start_agent(text.strip())
    if isinstance(out, Result):                # запустился — сразу на страницу агента
        return [ui.status(out.text.splitlines()[0])] + out.actions
    return ui.show(f"```\n{out}\n```")


@srv.command("/open", "открыть диалог с агентом", "<id>")
def cmd_open(text: str):
    return ui.open("agent", id=text.strip()) if text.strip() else "использование: /open <id>"


@srv.command("/log", "хвост лога агента", "<id> [строк]")
def cmd_log(text: str):
    parts = text.split()
    if not parts:
        return "использование: /log <id> [строк]"
    n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 25
    return ui.show(f"```\n{agent_log(parts[0], n)}\n```")


@srv.command("/status", "состояние агента", "<id>")
def cmd_status(text: str):
    return ui.show(f"```\n{agent_status(text.strip())}\n```") if text.strip() else "использование: /status <id>"


@srv.command("/stop", "остановить агента", "<id>")
def cmd_stop(text: str):
    return stop_agent(text.strip()) if text.strip() else "использование: /stop <id>"


def _message(text: str, cmd: str):
    jid, message = _split_id(text)
    if not message:
        return f"использование: {cmd} <id> <текст>"
    return agent_message(jid, message)


@srv.command("/reply", "ответить агенту на вопрос", "<id> <текст>")
def cmd_reply(text: str):
    return _message(text, "/reply")


@srv.command("/continue", "продолжить задачу агента", "<id> <текст>")
def cmd_continue(text: str):
    return _message(text, "/continue")


srv.button("Агенты", ui.open("agents"), style="accent")


def main() -> None:
    _watch.start()
    srv.run()


if __name__ == "__main__":
    main()
