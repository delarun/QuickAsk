# QuickAsk — https://github.com/delarun/quickask — MIT
"""Точка входа: `quickask`, `python -m quickask` и собранный бинарник.

  quickask                 окно (повторный запуск — показать/спрятать)
  quickask --daemon        резидентно, без окна
  quickask --open LINK     открыть страницу MCP-сервера: `agent:agent?id=0922-…`
  quickask --mcp NAME      встроенный MCP-сервер на stdio (desk, agent) — бинарнику не нужен отдельный Python
  quickask --version       версия, GTK и наличие gtk4-layer-shell
"""
from __future__ import annotations

import os
import sys


def _ensure_mcps() -> None:
    """Из исходников пакет mcps лежит рядом с src/, а не внутри — добавим корень репозитория в путь."""
    try:
        import mcps  # noqa: F401
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _version() -> int:
    from quickask import __version__
    from quickask.ui import HAVE_LAYER_SHELL, LayerShell   # инициализирует GTK так же, как окно
    from gi.repository import Gtk

    layer = "no"
    if HAVE_LAYER_SHELL:   # active — окно будет поверх всего и с ui.margin_top; inactive — обычное окно
        ver = f"{LayerShell.get_major_version()}.{LayerShell.get_minor_version()}.{LayerShell.get_micro_version()}"
        layer = f"{ver} ({'active' if LayerShell.is_supported() else 'inactive'})"
    print(f"quickask {__version__}  GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}."
          f"{Gtk.get_micro_version()}  layer-shell: {layer}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--mcp"]:
        _ensure_mcps()
        from mcps import run

        return run(args[1] if len(args) > 1 else "")
    if args[:1] == ["--version"]:
        return _version()
    from quickask.ui.app import main as gui

    return gui()


if __name__ == "__main__":
    sys.exit(main())
