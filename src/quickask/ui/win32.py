# QuickAsk — https://github.com/delarun/quickask — MIT
"""Windows: значок в трее, глобальное сочетание клавиш, окно поверх всех без кнопки на панели задач.

На Linux всё это делают компоузитор (хоткей) и gtk4-layer-shell (окно поверх всего). В GTK 4
трея нет вовсе, поэтому здесь — WinAPI через ctypes, без зависимостей:

  • отдельный поток держит скрытое окно-приёмник: значок в трее (Shell_NotifyIcon), хоткей
    (RegisterHotKey) и меню трея живут на его очереди сообщений;
  • всё, что трогает GTK, уходит в главный цикл через GLib.idle_add;
  • окну QuickAsk ставится WS_EX_TOOLWINDOW (нет кнопки на панели задач) и HWND_TOPMOST,
    а позиция — по центру монитора с курсором, с отступом ui.margin_top.
"""
from __future__ import annotations

import ctypes
import os
import threading
import winreg
from ctypes import wintypes as W

from gi.repository import GLib

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
LONG_PTR = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

WM_DESTROY, WM_CLOSE, WM_NULL, WM_HOTKEY = 0x0002, 0x0010, 0x0000, 0x0312
WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0205, 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1                      # сообщения от значка в трее
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0, 0x800, 0x8, 0x1
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
IDI_APPLICATION = 32512
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
HWND_TOPMOST = W.HWND(-1)
SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED, SWP_NOMOVE = 0x1, 0x4, 0x20, 0x2
MONITOR_DEFAULTTONEAREST = 2
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
HOTKEY_ID = 1
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
                ("lpszClassName", W.LPCWSTR)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", W.HICON)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]


def _proto(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, list(argtypes)
    return fn


# Явные типы обязательны: по умолчанию ctypes считает всё int и обрезает 64-битные HWND/HICON.
RegisterClassW = _proto(user32.RegisterClassW, W.ATOM, ctypes.POINTER(WNDCLASSW))
CreateWindowExW = _proto(user32.CreateWindowExW, W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
DestroyWindow = _proto(user32.DestroyWindow, W.BOOL, W.HWND)
DefWindowProcW = _proto(user32.DefWindowProcW, LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
GetMessageW = _proto(user32.GetMessageW, W.BOOL, ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
TranslateMessage = _proto(user32.TranslateMessage, W.BOOL, ctypes.POINTER(W.MSG))
DispatchMessageW = _proto(user32.DispatchMessageW, LRESULT, ctypes.POINTER(W.MSG))
PostMessageW = _proto(user32.PostMessageW, W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
PostQuitMessage = _proto(user32.PostQuitMessage, None, ctypes.c_int)
RegisterWindowMessageW = _proto(user32.RegisterWindowMessageW, W.UINT, W.LPCWSTR)
RegisterHotKey = _proto(user32.RegisterHotKey, W.BOOL, W.HWND, ctypes.c_int, W.UINT, W.UINT)
UnregisterHotKey = _proto(user32.UnregisterHotKey, W.BOOL, W.HWND, ctypes.c_int)
CreatePopupMenu = _proto(user32.CreatePopupMenu, W.HMENU)
AppendMenuW = _proto(user32.AppendMenuW, W.BOOL, W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR)
TrackPopupMenu = _proto(user32.TrackPopupMenu, W.BOOL, W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int,
                        ctypes.c_int, W.HWND, W.LPVOID)
DestroyMenu = _proto(user32.DestroyMenu, W.BOOL, W.HMENU)
GetCursorPos = _proto(user32.GetCursorPos, W.BOOL, ctypes.POINTER(W.POINT))
SetForegroundWindow = _proto(user32.SetForegroundWindow, W.BOOL, W.HWND)
LoadImageW = _proto(user32.LoadImageW, W.HANDLE, W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT)
LoadIconW = _proto(user32.LoadIconW, W.HICON, W.HINSTANCE, ctypes.c_void_p)
EnumWindows = _proto(user32.EnumWindows, W.BOOL, WNDENUMPROC, W.LPARAM)
GetWindowThreadProcessId = _proto(user32.GetWindowThreadProcessId, W.DWORD, W.HWND, ctypes.POINTER(W.DWORD))
GetClassNameW = _proto(user32.GetClassNameW, ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
GetWindowTextW = _proto(user32.GetWindowTextW, ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
GetWindowLongPtrW = _proto(user32.GetWindowLongPtrW, LONG_PTR, W.HWND, ctypes.c_int)
SetWindowLongPtrW = _proto(user32.SetWindowLongPtrW, LONG_PTR, W.HWND, ctypes.c_int, LONG_PTR)
SetWindowPos = _proto(user32.SetWindowPos, W.BOOL, W.HWND, W.HWND, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, W.UINT)
GetWindowRect = _proto(user32.GetWindowRect, W.BOOL, W.HWND, ctypes.POINTER(W.RECT))
MonitorFromPoint = _proto(user32.MonitorFromPoint, W.HMONITOR, W.POINT, W.DWORD)
GetMonitorInfoW = _proto(user32.GetMonitorInfoW, W.BOOL, W.HMONITOR, ctypes.POINTER(MONITORINFO))
Shell_NotifyIconW = _proto(shell32.Shell_NotifyIconW, W.BOOL, W.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
GetModuleHandleW = _proto(kernel32.GetModuleHandleW, W.HMODULE, W.LPCWSTR)
try:                                        # Windows 10 1607+; на старых — считаем 96 dpi
    GetDpiForWindow = _proto(user32.GetDpiForWindow, W.UINT, W.HWND)
except AttributeError:
    GetDpiForWindow = None

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quickask.ico")


# ── сочетание клавиш ─────────────────────────────────────────────────────────

_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT,
         "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN}
_KEYS = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
         "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
         "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
         "`": 0xC0, "grave": 0xC0, "backquote": 0xC0, ";": 0xBA, "=": 0xBB, ",": 0xBC,
         "-": 0xBD, ".": 0xBE, "/": 0xBF, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE}


def parse_hotkey(text: str) -> tuple[int, int]:
    """"Alt+Space", "Ctrl+Alt+Q", "Win+F12" → (модификаторы RegisterHotKey, виртуальная клавиша)."""
    parts = [p.strip().lower() for p in text.split("+")]
    if not parts or not parts[-1]:
        raise ValueError(f"пустая клавиша в {text!r}")
    *mods, key = parts
    flags = MOD_NOREPEAT                     # зажатая клавиша не присылает хоткей повторно
    for m in mods:
        if m not in _MODS:
            raise ValueError(f"неизвестный модификатор {m!r} в {text!r}: {', '.join(_MODS)}")
        flags |= _MODS[m]
    if key in _KEYS:
        vk = _KEYS[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())                # коды букв — латинские: «Щ» на русской раскладке — это O
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"неизвестная клавиша {key!r} в {text!r} (буквы — латиницей, как на клавише)")
    return flags, vk


# ── автозапуск ───────────────────────────────────────────────────────────────

def _autostart_command() -> str:
    from .. import self_command
    return " ".join(f'"{c}"' for c in self_command() + ["--daemon"])


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "QuickAsk")
        return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "QuickAsk", 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(k, "QuickAsk")
            except FileNotFoundError:
                pass


# ── трей и хоткей ────────────────────────────────────────────────────────────

class Tray:
    """Скрытое окно-приёмник со значком в трее и глобальным хоткеем в своём потоке.

    toggle, menu_items — колбэки из главного цикла GTK: toggle() показывает или прячет окно,
    menu_items() → [(подпись, действие | None для разделителя, отмечен)]."""

    def __init__(self, toggle, menu_items, hotkey: str = "", on_error=None):
        self.toggle, self.menu_items, self.hotkey = toggle, menu_items, hotkey
        self.on_error = on_error or (lambda _t: None)
        self.hwnd = None
        self._proc = WNDPROC(self._wndproc)   # держим ссылку: иначе колбэк соберёт GC и Windows вызовет мусор
        self._taskbar_created = 0
        self._icon = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="quickask-tray").start()

    def stop(self) -> None:
        if self.hwnd:
            PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    # -- поток трея ---------------------------------------------------------
    def _run(self) -> None:
        hinst = GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName="QuickAskTray")
        RegisterClassW(ctypes.byref(wc))
        self.hwnd = CreateWindowExW(0, "QuickAskTray", "QuickAsk tray", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            GLib.idle_add(self.on_error, "не удалось создать окно трея")
            return
        # Explorer перезапустился — значки в трее пропали, их надо добавить заново
        self._taskbar_created = RegisterWindowMessageW("TaskbarCreated")
        self._icon = LoadImageW(None, ICON_PATH, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE) \
            if os.path.exists(ICON_PATH) else None
        self._icon = self._icon or LoadIconW(None, IDI_APPLICATION)
        self._notify(NIM_ADD)
        if self.hotkey:
            try:
                mods, vk = parse_hotkey(self.hotkey)
                if not RegisterHotKey(self.hwnd, HOTKEY_ID, mods, vk):
                    GLib.idle_add(self.on_error, f"сочетание {self.hotkey} уже занято другой программой — "
                                                 f"смени ui.hotkey в config.toml")
            except ValueError as e:
                GLib.idle_add(self.on_error, f"ui.hotkey: {e}")
        msg = W.MSG()
        while GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            TranslateMessage(ctypes.byref(msg))
            DispatchMessageW(ctypes.byref(msg))

    def _notify(self, action: int) -> None:
        nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
                              uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP, uCallbackMessage=WM_TRAY,
                              hIcon=self._icon)
        tip = "QuickAsk" + (f" — {self.hotkey}" if self.hotkey else "")
        nid.szTip = tip[:127]
        Shell_NotifyIconW(action, ctypes.byref(nid))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONUP:
                    GLib.idle_add(self.toggle)
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._menu(hwnd)
                return 0
            if msg == WM_HOTKEY and wparam == HOTKEY_ID:
                GLib.idle_add(self.toggle)
                return 0
            if msg == self._taskbar_created and msg:
                self._notify(NIM_ADD)
                return 0
            if msg == WM_CLOSE:
                self._notify(NIM_DELETE)
                UnregisterHotKey(hwnd, HOTKEY_ID)
                DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001 — исключение в оконной процедуре уронило бы весь процесс
            pass
        return DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self, hwnd) -> None:
        items = self.menu_items()
        menu = CreatePopupMenu()
        for i, (label, action, checked) in enumerate(items, start=1):
            if action is None and not label:
                AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                flags = MF_STRING | (MF_CHECKED if checked else 0) | (MF_GRAYED if action is None else 0)
                AppendMenuW(menu, flags, i, label)
        pt = W.POINT()
        GetCursorPos(ctypes.byref(pt))
        SetForegroundWindow(hwnd)             # иначе меню не закроется по клику мимо него
        chosen = TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, hwnd, None)
        PostMessageW(hwnd, WM_NULL, 0, 0)
        DestroyMenu(menu)
        if 0 < chosen <= len(items) and items[chosen - 1][1]:
            GLib.idle_add(items[chosen - 1][1])


# ── окно QuickAsk: поверх всех, без кнопки на панели задач, на своём месте ──

def find_hwnd(title: str) -> int | None:
    """Окно GTK этого процесса (GTK 4 не отдаёт HWND без отдельного typelib GdkWin32):
    по заголовку, а если он ещё не выставлен — по классу, который GTK регистрирует для окон верхнего уровня."""
    pid, by_title, by_class = os.getpid(), [], []

    def check(hwnd, _lparam):
        owner = W.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            GetWindowTextW(hwnd, buf, 256)
            if buf.value == title:
                by_title.append(hwnd)
                return False
            GetClassNameW(hwnd, buf, 256)
            if buf.value == "gdkSurfaceToplevel":
                by_class.append(hwnd)
        return True

    EnumWindows(WNDENUMPROC(check), 0)
    return (by_title or by_class or [None])[0]


def hide_from_taskbar(hwnd) -> None:
    ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)


def place(hwnd, margin_top) -> None:
    """По центру монитора с курсором, сверху — ui.margin_top (пиксели или «25%»), поверх всех окон."""
    pt = W.POINT()
    GetCursorPos(ctypes.byref(pt))
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    GetMonitorInfoW(MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST), ctypes.byref(mi))
    work, rect = mi.rcWork, W.RECT()
    GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = mi.rcMonitor.bottom - mi.rcMonitor.top
    if isinstance(margin_top, str) and margin_top.strip().endswith("%"):
        top = round(height * float(margin_top.strip()[:-1]) / 100)
    else:                                    # пиксели в конфиге — логические, как в GTK
        scale = (GetDpiForWindow(hwnd) / 96) if GetDpiForWindow else 1.0
        top = round(int(margin_top) * scale)
    x = work.left + max(0, (work.right - work.left - width) // 2)
    y = mi.rcMonitor.top + top
    SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE)
    SetForegroundWindow(hwnd)


def attach(win) -> None:
    """Подключить к окну QuickAsk: убрать с панели задач при создании, ставить на место при показе."""
    state: dict = {"hwnd": None, "tool": False}

    def hwnd():
        if not state["hwnd"]:
            state["hwnd"] = find_hwnd(win.get_title() or "QuickAsk")
        return state["hwnd"]

    def as_tool_window():
        """Убрать кнопку с панели задач — лучше до первого показа, но и после сработает."""
        h = hwnd()
        if h and not state["tool"]:
            hide_from_taskbar(h)
            state["tool"] = True

    def on_map(*_):
        def later():
            as_tool_window()
            h = hwnd()
            if h:
                place(h, win.cfg["ui"]["margin_top"])
            return False
        GLib.idle_add(later)                 # после первой раскладки — чтобы знать ширину окна

    def on_realize(*_):
        as_tool_window()

    def on_unrealize(*_):
        state.update(hwnd=None, tool=False)

    win.connect("realize", on_realize)
    win.connect("map", on_map)
    win.connect("unrealize", on_unrealize)
