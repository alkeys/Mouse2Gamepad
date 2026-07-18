"""Resolución de la ruta del archivo de configuración persistente.

Se guarda junto al script/ejecutable que el usuario invoca (no junto a este
módulo), para que `mouse2gamepad_config.json` quede al lado de
`mouse2gamepad_gui.py` en desarrollo, o del binario compilado bajo PyInstaller.
"""

import os
import sys

if getattr(sys, "frozen", False):
    # Onefile de PyInstaller: __file__ caería en el _MEIPASS temporal, que se
    # borra al cerrar el programa, así que hay que usar el ejecutable real.
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

CONFIG_FILE = os.path.join(_BASE_DIR, "mouse2gamepad_config.json")
