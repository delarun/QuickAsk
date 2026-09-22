#!/usr/bin/env python3
"""
desk — MCP-сервер (stdio) управления рабочим столом для QuickAsk.

Окна и воркспейсы — через `niri msg` (IPC niri), вкладки Chrome/Chromium —
через Chrome DevTools Protocol (http://127.0.0.1:9222/json/*), файлы и каталоги, плюс мелочи:
открыть URL, запустить приложение, буфер обмена (wl-clipboard), уведомление.

Stdlib + quickask.sdk. Встроен в QuickAsk (`quickask --mcp desk`), в config.toml:

    [[mcp]]
    name    = "desk"
    command = ["@self", "--mcp", "desk"]

Удачные focus_*/open_*/launch_app сами просят QuickAsk спрятаться; /windows и /tabs — команды в окне.

Файлы: list_dir / dir_size / find_files (fd → find) / file_info / read_file / open_file,
только внутри DESK_MCP_ROOTS (по умолчанию ~, /mnt, /media, /run/media, /tmp).
Для вкладок Chrome нужен запущенный с remote debugging браузер — см. README.
"""
from __future__ import annotations

import difflib
import functools
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

from quickask.sdk import Result, Server, ui

CDP = os.environ.get("DESK_MCP_CDP", "http://127.0.0.1:9222")
srv = Server("desk", version="0.2")
tool = srv.tool


def hiding(fn):
    """Удалось переключить окно, вкладку или что-то открыть (ответ «OK: …») — QuickAsk прячется,
    чтобы не висеть поверх найденного. Раньше это перечисляли в config.toml как hide_on_tools."""
    @functools.wraps(fn)
    def wrap(*args, **kwargs):
        out = fn(*args, **kwargs)
        return Result(out, [ui.hide()]) if isinstance(out, str) and out.startswith("OK") else out
    return wrap


# ─────────────────────────── helpers ───────────────────────────


def sh(*args: str, timeout: float = 10) -> str:
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"{args[0]} exit {r.returncode}")
    return r.stdout


def niri_json(what: str) -> list[dict]:
    return json.loads(sh("niri", "msg", "-j", what))


_TR = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})
# частые русские названия → что искать в app_id/заголовке
ALIASES = {
    "телега": "telegram", "телеграм": "telegram", "хром": "chrom", "хромиум": "chromium", "браузер": "chrom firefox",
    "лиса": "firefox", "терминал": "terminal alacritty foot kitty wezterm konsole", "консоль": "terminal alacritty foot kitty",
    "файлы": "files nautilus thunar nemo dolphin", "проводник": "files nautilus thunar", "редактор": "code codium vim nvim zed",
    "код": "code", "вскод": "code", "почта": "mail thunderbird evolution", "музыка": "spotify music", "плеер": "mpv vlc",
    "видео": "mpv vlc", "дискорд": "discord", "слак": "slack", "обсидиан": "obsidian", "стим": "steam",
    "ютуб": "youtube", "гитхаб": "github", "документы": "docs", "таблица": "sheets", "пдф": "pdf",
}


def expand(query: str) -> list[str]:
    """Варианты запроса: как есть, алиас, транслит — берём лучший score."""
    q = query.lower().strip()
    out = [q]
    words = q.split()
    if any(w in ALIASES for w in words):
        out.append(" ".join(ALIASES.get(w, w) for w in words))
    tr = q.translate(_TR)
    if tr != q:
        out.append(tr)
    return out


def score(query: str, *fields: str | None) -> float:
    return max(_score1(q, *fields) for q in expand(query))


def _score1(query: str, *fields: str | None) -> float:
    """0..1: точное вхождение подстроки — высоко, иначе fuzzy по difflib."""
    q = query.lower().strip()
    best = 0.0
    for f in fields:
        if not f:
            continue
        f = f.lower()
        if q == f:
            return 1.0
        if q in f:
            best = max(best, 0.9 - 0.2 * (f.index(q) / max(len(f), 1)))
        else:
            # по словам, чтобы "терминал" находил "Alacritty — терминал ~/Projects"
            for w in q.split():
                if w in f:
                    best = max(best, 0.7)
            best = max(best, difflib.SequenceMatcher(None, q, f).ratio() * 0.8)
    return best


def pick(query: str, items: list[dict], *keys: str, min_score: float = 0.45) -> tuple[dict | None, list[dict]]:
    scored = sorted(((score(query, *[it.get(k) for k in keys]), it) for it in items),
                    key=lambda p: p[0], reverse=True)
    good = [it for s, it in scored if s >= min_score]
    return (good[0] if good else None), good[:8]


def fmt_win(w: dict) -> str:
    flags = ("*" if w.get("is_focused") else "") + ("~" if w.get("is_floating") else "")
    return f"[{w['id']}]{flags} {w.get('app_id') or '?'} — {w.get('title') or ''}  (ws {w.get('workspace_id')})"


def fmt_tab(t: dict) -> str:
    return f"[{t['id'][:8]}] {t.get('title') or ''}  — {t.get('url') or ''}"


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost — мимо http_proxy


def cdp(path: str, method: str = "GET") -> list | dict | str:
    req = urllib.request.Request(CDP + path, method=method)
    try:
        with _NO_PROXY.open(req, timeout=5) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Chrome DevTools недоступен на {CDP} ({e.reason}). Браузер должен быть запущен с "
            "--remote-debugging-port=9222 (и своим --user-data-dir для Chrome ≥136), см. README."
        ) from e
    try:
        return json.loads(body)
    except ValueError:
        return body


def chrome_tabs() -> list[dict]:
    data = cdp("/json/list")
    return [t for t in data if t.get("type") == "page" and not t.get("url", "").startswith("devtools://")]


def focus_chrome_window(title_hint: str = "") -> None:
    """После активации вкладки поднять окно браузера в niri."""
    try:
        wins = [w for w in niri_json("windows")
                if any(k in (w.get("app_id") or "").lower() for k in ("chrom", "brave", "vivaldi", "edge"))]
    except Exception:  # noqa: BLE001
        return
    if not wins:
        return
    best, _ = pick(title_hint, wins, "title", min_score=0.0) if title_hint else (wins[0], wins)
    sh("niri", "msg", "action", "focus-window", "--id", str((best or wins[0])["id"]))


# ─────────────────────────── tools: окна ───────────────────────────


@tool("list_windows",
      "Список открытых окон (id, приложение, заголовок, воркспейс). Необязательный фильтр query "
      "по заголовку/приложению. Звёздочка * — сфокусированное окно.",
      {"query": {"type": "string", "description": "подстрока для фильтра, можно пусто"}})
def list_windows(query: str = "") -> str:
    wins = niri_json("windows")
    if query:
        _, wins = pick(query, wins, "title", "app_id", min_score=0.3)
    if not wins:
        return "Окон не найдено."
    return "\n".join(fmt_win(w) for w in sorted(wins, key=lambda w: (w.get("workspace_id") or 0, w["id"])))


@tool("focus_window",
      "Найти окно по названию приложения или заголовку (нечёткий поиск) и переключиться на него. "
      "Либо передать точный id из list_windows.",
      {"query": {"type": "string", "description": "часть заголовка или имя приложения, напр. 'telegram', 'pdf счёт'"},
       "id": {"type": "integer", "description": "точный id окна (вместо query)"}})
@hiding
def focus_window(query: str = "", id: int | None = None) -> str:  # noqa: A002
    wins = niri_json("windows")
    if id is not None:
        w = next((w for w in wins if w["id"] == id), None)
        if not w:
            return f"Окна с id {id} нет."
    else:
        if not query:
            return "Нужен query или id."
        w, cands = pick(query, wins, "title", "app_id")
        if not w:
            return "Ничего похожего не нашёл. Открытые окна:\n" + "\n".join(fmt_win(x) for x in wins[:15])
        if len(cands) > 1 and score(query, cands[0].get("title"), cands[0].get("app_id")) < 0.75:
            return ("Несколько подходящих, уточни или передай id:\n" + "\n".join(fmt_win(x) for x in cands))
    sh("niri", "msg", "action", "focus-window", "--id", str(w["id"]))
    return f"OK: переключился на {fmt_win(w)}"


@tool("close_window",
      "Закрыть окно по id (сначала найди его через list_windows/focus_window, чтобы не закрыть лишнее).",
      {"id": {"type": "integer"}}, ["id"])
def close_window(id: int) -> str:  # noqa: A002
    sh("niri", "msg", "action", "close-window", "--id", str(id))
    return f"OK: окно {id} закрыто"


@tool("list_workspaces", "Список воркспейсов niri: индекс, имя, монитор, активный ли.")
def list_workspaces() -> str:
    out = []
    for ws in sorted(niri_json("workspaces"), key=lambda w: (w.get("output") or "", w.get("idx", 0))):
        mark = "*" if ws.get("is_focused") else ("+" if ws.get("is_active") else " ")
        out.append(f"{mark} {ws.get('output')}:{ws.get('idx')}  {ws.get('name') or ''}".rstrip())
    return "\n".join(out)


@tool("focus_workspace", "Переключиться на воркспейс по индексу (число) или имени.",
      {"ref": {"type": "string", "description": "индекс, например '3', или имя воркспейса"}}, ["ref"])
@hiding
def focus_workspace(ref: str) -> str:
    sh("niri", "msg", "action", "focus-workspace", str(ref))
    return f"OK: воркспейс {ref}"


# ─────────────────────────── tools: Chrome ───────────────────────────


@tool("list_chrome_tabs",
      "Список открытых вкладок Chrome/Chromium (id, заголовок, url). Необязательный фильтр query.",
      {"query": {"type": "string", "description": "подстрока заголовка или url, можно пусто"}})
def list_chrome_tabs(query: str = "") -> str:
    tabs = chrome_tabs()
    if query:
        _, tabs = pick(query, tabs, "title", "url", min_score=0.3)
    if not tabs:
        return "Вкладок не найдено."
    return "\n".join(fmt_tab(t) for t in tabs)


@tool("focus_chrome_tab",
      "Найти вкладку Chrome по заголовку или url (нечёткий поиск) и переключиться на неё, "
      "подняв окно браузера. Либо передать id из list_chrome_tabs.",
      {"query": {"type": "string", "description": "часть заголовка или url, напр. 'github pull request', 'youtube'"},
       "id": {"type": "string", "description": "id вкладки (полный или первые 8 символов)"}})
@hiding
def focus_chrome_tab(query: str = "", id: str = "") -> str:  # noqa: A002
    tabs = chrome_tabs()
    if id:
        t = next((t for t in tabs if t["id"].startswith(id)), None)
        if not t:
            return f"Вкладки с id {id} нет."
    else:
        if not query:
            return "Нужен query или id."
        t, cands = pick(query, tabs, "title", "url")
        if not t:
            return "Похожих вкладок нет. Открыто:\n" + "\n".join(fmt_tab(x) for x in tabs[:20])
        if len(cands) > 1 and score(query, cands[0].get("title"), cands[0].get("url")) < 0.75:
            return "Несколько подходящих, уточни или передай id:\n" + "\n".join(fmt_tab(x) for x in cands)
    cdp(f"/json/activate/{t['id']}")
    focus_chrome_window(t.get("title") or "")
    return f"OK: вкладка {fmt_tab(t)}"


@tool("close_chrome_tab", "Закрыть вкладку Chrome по id из list_chrome_tabs.",
      {"id": {"type": "string"}}, ["id"])
def close_chrome_tab(id: str) -> str:  # noqa: A002
    tabs = chrome_tabs()
    t = next((t for t in tabs if t["id"].startswith(id)), None)
    if not t:
        return f"Вкладки с id {id} нет."
    cdp(f"/json/close/{t['id']}")
    return f"OK: закрыл {fmt_tab(t)}"


@tool("open_url", "Открыть URL в браузере по умолчанию (новая вкладка).",
      {"url": {"type": "string"}}, ["url"])
@hiding
def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:  # через CDP — сразу и с фокусом окна; иначе xdg-open
        t = cdp(f"/json/new?{url}", method="PUT")
        if isinstance(t, dict) and t.get("id"):
            cdp(f"/json/activate/{t['id']}")
            focus_chrome_window(t.get("title") or "")
            return f"OK: открыл {url}"
    except RuntimeError:
        pass
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"OK: открыл {url} (xdg-open)"


# ─────────────────────────── tools: файлы ───────────────────────────

HOME = os.path.expanduser("~")
# список через os.pathsep: «:» на Linux/macOS, «;» на Windows — иначе C:\… порвётся на части
_DEFAULT_ROOTS = os.pathsep.join([HOME, "/mnt", "/media", "/run/media", "/tmp"]) if os.name == "posix" else HOME
ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in
         os.environ.get("DESK_MCP_ROOTS", _DEFAULT_ROOTS).split(os.pathsep) if p]
SKIP_DIRS = {".git", "node_modules", ".cache", "__pycache__", ".venv", "venv", ".npm", ".cargo", "target", ".local/share/Trash"}


def human(n: int) -> str:
    f = float(n)
    for u in ("B", "K", "M", "G", "T"):
        if f < 1024 or u == "T":
            return f"{f:.0f}{u}" if u == "B" else f"{f:.1f}{u}"
        f /= 1024
    return f"{n}B"


def resolve(path: str) -> str:
    """Развернуть ~ и относительный путь, проверить, что внутри разрешённых корней."""
    p = os.path.realpath(os.path.expanduser(path or "~"))
    if not any(p == r or p.startswith(r + "/") for r in ROOTS):
        raise RuntimeError(f"{path}: вне разрешённых каталогов ({', '.join(ROOTS)}); задай DESK_MCP_ROOTS")
    return p


def fmt_entry(p: str, st: os.stat_result | None = None, base: str | None = None) -> str:
    try:
        st = st or os.lstat(p)
    except OSError as e:
        return f"?  {p}  ({e.strerror})"
    import stat as _s
    import time as _t
    kind = "d" if _s.S_ISDIR(st.st_mode) else ("l" if _s.S_ISLNK(st.st_mode) else "-")
    size = "" if kind == "d" else human(st.st_size).rjust(7)
    when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(st.st_mtime))
    name = os.path.relpath(p, base) if base else p
    return f"{kind} {size:>7}  {when}  {name}{'/' if kind == 'd' else ''}"


@tool("list_dir",
      "Показать содержимое каталога: тип, размер файла, дата изменения, имя. Каталоги без размера — "
      "для размеров каталогов есть dir_size. sort: name|size|mtime.",
      {"path": {"type": "string", "description": "каталог, по умолчанию ~; ~ и относительные пути ок"},
       "sort": {"type": "string", "enum": ["name", "size", "mtime"]},
       "show_hidden": {"type": "boolean"},
       "limit": {"type": "integer", "description": "макс. строк, по умолчанию 60"}})
def list_dir(path: str = "~", sort: str = "name", show_hidden: bool = False, limit: int = 60) -> str:
    d = resolve(path)
    if not os.path.isdir(d):
        return fmt_entry(d) if os.path.exists(d) else f"{path}: нет такого пути"
    entries = []
    with os.scandir(d) as it:
        for e in it:
            if not show_hidden and e.name.startswith("."):
                continue
            try:
                entries.append((e, e.stat(follow_symlinks=False)))
            except OSError:
                continue
    key = {"name": lambda p: (not p[0].is_dir(), p[0].name.lower()),
           "size": lambda p: -p[1].st_size,
           "mtime": lambda p: -p[1].st_mtime}.get(sort, lambda p: p[0].name.lower())
    entries.sort(key=key)
    total = len(entries)
    lines = [fmt_entry(e.path, st, d) for e, st in entries[:max(1, limit)]]
    files_size = sum(st.st_size for e, st in entries if not e.is_dir(follow_symlinks=False))
    head = f"{d}  — {total} записей, файлов на {human(files_size)}" + (f", показано {limit}" if total > limit else "")
    return head + "\n" + "\n".join(lines)


@tool("dir_size",
      "Размер каталога и его подкаталогов (что занимает место). Возвращает топ подкаталогов по размеру.",
      {"path": {"type": "string"}, "depth": {"type": "integer", "description": "глубина, по умолчанию 1"},
       "top": {"type": "integer", "description": "сколько строк, по умолчанию 20"}})
def dir_size(path: str = "~", depth: int = 1, top: int = 20) -> str:
    d = resolve(path)
    try:
        out = sh("du", "-x", "-b", f"--max-depth={max(0, depth)}", d, timeout=120)
    except RuntimeError as e:  # du ругается на недоступные каталоги, но вывод отдаёт
        out = str(e)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1]))
    if not rows:
        return f"du не вернул данных для {d}"
    total = next((n for n, p in rows if p == d), rows[-1][0])
    rows = sorted((r for r in rows if r[1] != d), reverse=True)[:max(1, top)]
    lines = [f"{human(n):>8}  {int(100 * n / total) if total else 0:3d}%  {os.path.relpath(p, d)}/" for n, p in rows]
    return f"{d}: всего {human(total)}\n" + "\n".join(lines)


@tool("find_files",
      "Найти файлы по части имени (регистронезависимо). Использует fd, если установлен, иначе find. "
      "Результат: размер, дата, путь — отсортировано по дате изменения (новые первые).",
      {"query": {"type": "string", "description": "часть имени файла, напр. 'invoice', 'квитанция', '.gguf'"},
       "path": {"type": "string", "description": "где искать, по умолчанию ~"},
       "ext": {"type": "string", "description": "расширение без точки: pdf, jpg, gguf"},
       "days": {"type": "integer", "description": "только изменённые за последние N дней"},
       "include_dirs": {"type": "boolean", "description": "искать и каталоги"},
       "limit": {"type": "integer", "description": "макс. результатов, по умолчанию 40"}},
      ["query"])
def find_files(query: str, path: str = "~", ext: str = "", days: int = 0,
               include_dirs: bool = False, limit: int = 40) -> str:
    root = resolve(path)
    q = query.strip().lstrip("*")
    if ext.startswith("."):
        ext = ext[1:]
    if q.startswith(".") and not ext and " " not in q:  # ".gguf" → расширение
        ext, q = q[1:], ""
    hits: list[str] = []
    fd = "fd" if subprocess.run(["which", "fd"], capture_output=True).returncode == 0 else ""
    cap = max(1, limit) * 5  # с запасом — потом отсортируем по mtime
    try:
        if fd:
            cmd = [fd, "-i", "-a", "-H", "--no-ignore-vcs", "--max-results", str(cap)]
            if not include_dirs:
                cmd += ["-t", "f"]
            if ext:
                cmd += ["-e", ext]
            if days:
                cmd += ["--changed-within", f"{days}d"]
            for sd in SKIP_DIRS:
                cmd += ["-E", sd]
            cmd += [q or ".", root]
            hits = sh(*cmd, timeout=60).splitlines()
        else:
            cmd = ["find", root]
            for sd in SKIP_DIRS:
                cmd += ["-name", os.path.basename(sd), "-prune", "-o"]
            if not include_dirs:
                cmd += ["-type", "f"]
            pat = f"*{q}*" if q else "*"
            if ext:
                pat = f"*{q}*.{ext}" if q else f"*.{ext}"
            cmd += ["-iname", pat]
            if days:
                cmd += ["-mtime", f"-{days}"]
            cmd += ["-print"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            hits = r.stdout.splitlines()[:cap]
    except subprocess.TimeoutExpired:
        return "Поиск не уложился в 60 с — сузь path или запрос."
    if not hits:
        return f"Ничего не найдено по '{query}' в {root}" + (f" (*.{ext})" if ext else "")
    stated = []
    for h in hits:
        try:
            stated.append((os.lstat(h), h))
        except OSError:
            continue
    stated.sort(key=lambda p: -p[0].st_mtime)
    lines = [fmt_entry(h, st) for st, h in stated[:limit]]
    more = f"\n… и ещё {len(stated) - limit}" if len(stated) > limit else ""
    return f"Найдено {len(stated)} в {root}:\n" + "\n".join(lines) + more


@tool("file_info", "Подробности о файле или каталоге: размер, даты, тип (file), для каталога — число записей.",
      {"path": {"type": "string"}}, ["path"])
def file_info(path: str) -> str:
    p = resolve(path)
    if not os.path.exists(p):
        return f"{path}: нет такого пути"
    st = os.stat(p)
    import time as _t
    lines = [fmt_entry(p, st),
             f"размер: {human(st.st_size)} ({st.st_size} байт)",
             f"изменён: {_t.strftime('%Y-%m-%d %H:%M:%S', _t.localtime(st.st_mtime))}",
             f"права: {oct(st.st_mode & 0o777)}"]
    if os.path.isdir(p):
        try:
            lines.append(f"записей: {len(os.listdir(p))}")
        except OSError as e:
            lines.append(f"записей: ? ({e.strerror})")
    else:
        try:
            lines.append("тип: " + sh("file", "-b", p, timeout=5).strip())
        except (RuntimeError, FileNotFoundError):
            pass
    return "\n".join(lines)


@tool("read_file", "Прочитать начало текстового файла (до max_chars символов, по умолчанию 4000).",
      {"path": {"type": "string"}, "max_chars": {"type": "integer"}}, ["path"])
def read_file(path: str, max_chars: int = 4000) -> str:
    p = resolve(path)
    if os.path.isdir(p):
        return f"{path} — каталог, используй list_dir"
    with open(p, "rb") as f:
        data = f.read(max(1, max_chars) * 4)
    if b"\x00" in data[:1024]:
        return f"{path}: бинарный файл ({human(os.path.getsize(p))}), не читаю"
    text = data.decode("utf-8", "replace")[:max_chars]
    return text + ("\n…(обрезано)" if os.path.getsize(p) > len(data) or len(text) == max_chars else "")


@tool("open_file", "Открыть файл или каталог приложением по умолчанию (xdg-open).",
      {"path": {"type": "string"}}, ["path"])
@hiding
def open_file(path: str) -> str:
    p = resolve(path)
    if not os.path.exists(p):
        return f"{path}: нет такого пути"
    subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"OK: открыл {p}"


# ─────────────────────────── tools: разное ───────────────────────────


@tool("launch_app",
      "Запустить приложение по имени .desktop (без .desktop, напр. 'org.telegram.desktop', 'firefox') "
      "или командой. Если приложение уже открыто — лучше focus_window.",
      {"app": {"type": "string", "description": "имя desktop-файла или команда"}}, ["app"])
@hiding
def launch_app(app: str) -> str:
    try:
        sh("gtk-launch", app, timeout=5)
        return f"OK: запустил {app}"
    except (RuntimeError, FileNotFoundError):
        pass
    argv = shlex.split(app)
    if not argv:
        return "Пустая команда."
    subprocess.Popen(["niri", "msg", "action", "spawn", "--", *argv],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"OK: spawn {app}"


@tool("get_clipboard", "Прочитать текст из буфера обмена (wl-paste).")
def get_clipboard() -> str:
    try:
        return sh("wl-paste", "--no-newline", timeout=3)[:8000] or "(пусто)"
    except (RuntimeError, FileNotFoundError) as e:
        return f"буфер недоступен: {e}"


@tool("set_clipboard", "Положить текст в буфер обмена (wl-copy).",
      {"text": {"type": "string"}}, ["text"])
def set_clipboard(text: str) -> str:
    subprocess.run(["wl-copy"], input=text, text=True, check=True, timeout=3)
    return f"OK: скопировано {len(text)} симв."


@tool("notify", "Показать системное уведомление (notify-send).",
      {"title": {"type": "string"}, "body": {"type": "string"}}, ["title"])
def notify(title: str, body: str = "") -> str:
    subprocess.Popen(["notify-send", title, body], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "OK"


# ─────────────────────────── команды в QuickAsk ───────────────────────────


@srv.command("/windows", "список окон niri", "[фильтр]")
def cmd_windows(text: str):
    return ui.show(f"```\n{list_windows(text.strip())}\n```")


@srv.command("/tabs", "список вкладок Chrome", "[фильтр]")
def cmd_tabs(text: str):
    return ui.show(f"```\n{list_chrome_tabs(text.strip())}\n```")


def main() -> None:
    srv.run()


if __name__ == "__main__":
    main()
