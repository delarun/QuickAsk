# -*- mode: python -*-
# Единый бинарник QuickAsk: pyinstaller --noconfirm --clean packaging/quickask.spec
#
# GTK4 и PyGObject берутся из системы сборщика (apt / MSYS2 / Homebrew) и вшиваются в бинарник
# хуками pyinstaller-hooks-contrib. MCP-серверы из mcps/ едут внутри и запускаются как
# `quickask --mcp <имя>`, так что у пользователя не нужен ни Python, ни исходники.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
LINUX = sys.platform.startswith("linux")
WINDOWS = sys.platform == "win32"

hiddenimports = ["mcps.desk", "mcps.agent", "quickask.sdk"]    # mcps.run() импортирует по имени
if WINDOWS:
    hiddenimports.append("quickask.ui.win32")                    # трей и хоткей, импорт внутри функции
if not LINUX:
    hiddenimports.append("quickask.ui.instance")                 # «одна копия» без D-Bus
if LINUX:
    hiddenimports.append("gi.repository.Gtk4LayerShell")   # опционален, есть только под Wayland

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src"), str(ROOT)],
    # пример конфига — из него создаётся config.toml на первом запуске; иконка — для трея
    datas=[(str(ROOT / "src" / "quickask" / "config.example.toml"), "quickask"),
           (str(ROOT / "src" / "quickask" / "ui" / "quickask.ico"), "quickask/ui")],
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    hooksconfig={
        "gi": {
            # без этого хуки соберут GTK 3 — версия по умолчанию для gi
            "module-versions": {"Gtk": "4.0", "Gdk": "4.0"},
            "icons": ["Adwaita"],
            "themes": ["Adwaita"],
            "languages": ["ru", "en"],
        },
    },
    excludes=["tkinter", "unittest", "pydoc_data"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="quickask",
    # На Windows — без консольного окна. stdio для `--mcp` при этом работает: родитель передаёт пайпы.
    console=not WINDOWS,
    strip=LINUX,
    upx=False,
    icon=str(ROOT / "src" / "quickask" / "ui" / "quickask.ico") if WINDOWS else None,
)
