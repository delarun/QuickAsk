# QuickAsk — https://github.com/delarun/quickask — MIT
"""Одна копия QuickAsk там, где нет D-Bus, — на Windows и macOS.

На Linux это делает Gtk.Application через D-Bus: повторный запуск бинарника просит уже
запущенную копию показать или спрятать окно. На Windows и macOS сессионной шины обычно нет,
и Gtk.Application там запускается без поиска копий, а их связь идёт здесь: первая копия слушает
именованный канал (Windows) или Unix-сокет (macOS), следующие отправляют ей команду и выходят.

Команды — короткие строки: "toggle", "noop", "open:<сервер:страница?…>". Принимаются только
байты (recv_bytes), без pickle: иначе любой, кто достучался до канала, выполнил бы свой код.
"""
from __future__ import annotations

import getpass
import os
import re
import sys
import tempfile
import threading
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener

AUTHKEY = b"quickask/1"      # не секрет — только чтобы на канал не отвечали случайные клиенты


def _address() -> tuple[str, str]:
    user = re.sub(r"\W", "_", getpass.getuser()) or "user"
    if sys.platform == "win32":
        return rf"\\.\pipe\QuickAsk-{user}", "AF_PIPE"
    return os.path.join(tempfile.gettempdir(), f"quickask-{user}.sock"), "AF_UNIX"


def message_for(args: list[str]) -> str:
    """Что передать уже запущенной копии по аргументам этого запуска."""
    for i, a in enumerate(args):
        if a in ("--open", "-o") and i + 1 < len(args):
            return "open:" + args[i + 1]
        if a.startswith("--open="):
            return "open:" + a.split("=", 1)[1]
    if "--daemon" in args or "-d" in args:
        return "noop"                    # автозапуск, а копия уже есть — ничего не делать
    return "toggle"


def send(message: str) -> bool:
    """Передать команду запущенной копии. False — её нет, этот процесс станет главным."""
    address, family = _address()
    try:
        with Client(address, family=family, authkey=AUTHKEY) as conn:
            conn.send_bytes(message.encode("utf-8"))
        return True
    except (OSError, EOFError, AuthenticationError):
        return False


class Server:
    """Слушать команды от следующих запусков. on_message вызывается из фонового потока."""

    def __init__(self, on_message):
        self.on_message = on_message
        self._listener: Listener | None = None
        self._closed = False

    def start(self) -> bool:
        address, family = _address()
        if family == "AF_UNIX" and os.path.exists(address):
            if send("noop"):             # сокет живой — копия запустилась одновременно с нами, уступаем
                return False
            os.unlink(address)           # никто не отвечает — файл остался после падения
        try:
            self._listener = Listener(address, family=family, authkey=AUTHKEY)
        except OSError:
            return False                 # канал занят — копия запустилась одновременно с нами
        if family == "AF_UNIX":
            os.chmod(address, 0o600)
        threading.Thread(target=self._loop, daemon=True, name="quickask-instance").start()
        return True

    def _loop(self) -> None:
        while not self._closed:
            try:
                with self._listener.accept() as conn:
                    data = conn.recv_bytes(4096)
            except (OSError, EOFError, AuthenticationError):
                continue
            if not self._closed:
                self.on_message(data.decode("utf-8", "replace"))

    def close(self) -> None:
        """Отпустить канал — перед перезапуском, чтобы новая копия смогла его занять."""
        self._closed = True
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
