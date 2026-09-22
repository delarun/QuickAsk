# Точка входа бинарника. Не quickask.py из корня: у скрипта и пакета одно имя,
# и PyInstaller разрешил бы `import quickask` в сам скрипт, а не в пакет из src/.
import sys

from quickask.__main__ import main

sys.exit(main())
