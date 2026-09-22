# QuickAsk — https://github.com/delarun/quickask — MIT
"""Интерфейс QuickAsk на GTK4.

Здесь только инициализация GTK: gi.require_version и опциональный gtk4-layer-shell
обязаны отработать раньше, чем кто-нибудь импортирует gi.repository, поэтому они
живут в __init__ — его выполнит любой `from quickask.ui.… import …`.
"""
from __future__ import annotations

import ctypes
import os
import sys

import gi


def _preload_layer_shell() -> None:
    """gtk4-layer-shell ≥ 1.0 работает, только если загружен РАНЬШЕ libwayland-client:
    он подменяет часть её функций. Иначе is_supported() тихо возвращает False, окно
    становится обычным (компоузитор ставит его по центру) и ui.margin_top не действует.
    Из C это решает порядок линковки, из Python — загрузить библиотеку до GTK, в общее
    пространство символов. В бинарнике PyInstaller она лежит во временной папке сборки."""
    if not sys.platform.startswith("linux"):
        return
    names = ["libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so"]
    bundle = getattr(sys, "_MEIPASS", "")
    for path in [os.path.join(bundle, n) for n in names if bundle] + names:
        try:
            ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
            return
        except OSError:
            continue


_preload_layer_shell()

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")

try:  # должен импортироваться ДО открытия дисплея
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # type: ignore

    HAVE_LAYER_SHELL = True
except (ValueError, ImportError):
    LayerShell = None  # type: ignore[assignment]
    HAVE_LAYER_SHELL = False
