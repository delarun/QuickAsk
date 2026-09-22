# PyInstaller-хук для gtk4-layer-shell: в pyinstaller-hooks-contrib его нет,
# а без него бинарник не найдёт typelib и .so и молча откатится на обычное окно.
from PyInstaller.utils.hooks.gi import GiModuleInfo

module_info = GiModuleInfo("Gtk4LayerShell", "1.0")
if module_info.available:
    binaries, datas, hiddenimports = module_info.collect_typelib_data()
