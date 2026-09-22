# Модули gi.repository.* создаются на лету, и PyInstaller их не видит, пока не объявить
# модуль «рантаймовым» — так же сделано в pyinstaller-hooks-contrib для Gtk, Gdk и прочих.
def pre_safe_import_module(api):
    api.add_runtime_module(api.module_name)
