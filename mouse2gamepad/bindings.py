"""Constantes del protocolo de gamepad, asignación de teclas por defecto y
utilidades para mostrar bindings (acción -> tecla de teclado o mouse)."""

import sys

try:
    from evdev import ecodes as e
except ImportError:
    sys.exit("Falta python-evdev. Instala con: sudo pacman -S python-evdev")

AXIS_MAX = 32767
TRIG_MAX = 255
DSU_PORT = 26760

# ---- Teclas reservadas del programa (no reasignables) -----------------------
HK_SENS_DOWN, HK_SENS_UP, HK_MODE, HK_GRAB, HK_QUIT = (
    e.KEY_F5, e.KEY_F6, e.KEY_F7, e.KEY_F8, e.KEY_F10)
HOTKEYS = {HK_SENS_DOWN, HK_SENS_UP, HK_MODE, HK_GRAB, HK_QUIT}

# ---- Acciones del gamepad ----------------------------------------------------
ACTIONS = [
    ("A", "A"), ("B", "B"), ("X", "X"), ("Y", "Y"),
    ("L", "L"), ("R", "R"), ("ZL", "ZL"), ("ZR", "ZR"),
    ("PLUS", "+ (Start)"), ("MINUS", "− (Select)"), ("HOME", "HOME"),
    ("L3", "L3 (pres. stick izq)"), ("R3", "R3 (pres. stick der)"),
    ("LS_UP", "Stick izq ↑"), ("LS_DOWN", "Stick izq ↓"),
    ("LS_LEFT", "Stick izq ←"), ("LS_RIGHT", "Stick izq →"),
    ("DPAD_UP", "D-Pad ↑"), ("DPAD_DOWN", "D-Pad ↓"),
    ("DPAD_LEFT", "D-Pad ←"), ("DPAD_RIGHT", "D-Pad →"),
]

# Acción -> botón del gamepad virtual (fijo; lo que cambia es qué tecla lo dispara)
ACTION_BTN = {
    "A": e.BTN_EAST, "B": e.BTN_SOUTH, "X": e.BTN_NORTH, "Y": e.BTN_WEST,
    "L": e.BTN_TL, "R": e.BTN_TR,
    "PLUS": e.BTN_START, "MINUS": e.BTN_SELECT, "HOME": e.BTN_MODE,
    "L3": e.BTN_THUMBL, "R3": e.BTN_THUMBR,
}
# ZL/ZR son SOLO gatillos analógicos (ABS_Z / ABS_RZ), igual que en un control
# Xbox real, que no tiene botones digitales de gatillo. Si además enviáramos
# BTN_TL2/BTN_TR2, SDL los vería como un botón extra suelto y al presionar
# ZL/ZR Cemu registraría "otra tecla" fantasma al mismo tiempo.
ACTION_TRIG = {"ZL": e.ABS_Z, "ZR": e.ABS_RZ}

STICK_ACTIONS = {"LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT"}
DPAD_ACTIONS = {"DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"}

# Nombre con el que Cemu (API SDLController) muestra cada acción al mapearla.
# Ojo: SDL nombra por POSICIÓN estilo Xbox, por eso el A de Wii U aparece
# como "b" (el botón A de Nintendo está donde Xbox pone su B), etc.
CEMU_NAME = {
    "A": "b", "B": "a", "X": "y", "Y": "x",
    "L": "leftshoulder", "R": "rightshoulder",
    "ZL": "X-Trigger+", "ZR": "Y-Trigger+",
    "PLUS": "start", "MINUS": "back", "HOME": "guide",
    "L3": "leftstick", "R3": "rightstick",
    "LS_UP": "Y-Axis-", "LS_DOWN": "Y-Axis+",
    "LS_LEFT": "X-Axis-", "LS_RIGHT": "X-Axis+",
    "DPAD_UP": "dpup", "DPAD_DOWN": "dpdown",
    "DPAD_LEFT": "dpleft", "DPAD_RIGHT": "dpright",
}

# Asignación predeterminada: accion -> (origen, código evdev)  |  origen: "kbd"/"mouse"
DEFAULT_BINDINGS = {
    "A": ("kbd", e.KEY_SPACE), "B": ("kbd", e.KEY_LEFTCTRL),
    "X": ("kbd", e.KEY_R), "Y": ("kbd", e.KEY_F),
    "L": ("kbd", e.KEY_Q), "R": ("kbd", e.KEY_E),
    "ZL": ("mouse", e.BTN_RIGHT), "ZR": ("mouse", e.BTN_LEFT),
    "PLUS": ("kbd", e.KEY_ENTER), "MINUS": ("kbd", e.KEY_BACKSPACE),
    "HOME": ("kbd", e.KEY_TAB),
    "L3": ("kbd", e.KEY_C), "R3": ("kbd", e.KEY_V),
    "LS_UP": ("kbd", e.KEY_W), "LS_DOWN": ("kbd", e.KEY_S),
    "LS_LEFT": ("kbd", e.KEY_A), "LS_RIGHT": ("kbd", e.KEY_D),
    "DPAD_UP": ("kbd", e.KEY_UP), "DPAD_DOWN": ("kbd", e.KEY_DOWN),
    "DPAD_LEFT": ("kbd", e.KEY_LEFT), "DPAD_RIGHT": ("kbd", e.KEY_RIGHT),
}


def keyname(binding):
    """Nombre legible de una asignación (src, code) o None."""
    if not binding:
        return "—"
    src, code = binding
    name = e.keys.get(code, str(code))
    if isinstance(name, (list, tuple)):
        name = name[0]
    name = str(name)
    if name.startswith("BTN_"):
        return "Mouse " + name[4:].capitalize()
    if name.startswith("KEY_"):
        name = name[4:]
    nice = {"LEFTCTRL": "Ctrl izq", "RIGHTCTRL": "Ctrl der",
            "LEFTSHIFT": "Shift izq", "RIGHTSHIFT": "Shift der",
            "LEFTALT": "Alt izq", "RIGHTALT": "Alt der",
            "SPACE": "Espacio", "ENTER": "Enter", "BACKSPACE": "Retroceso",
            "UP": "Flecha ↑", "DOWN": "Flecha ↓",
            "LEFT": "Flecha ←", "RIGHT": "Flecha →"}
    return nice.get(name, name.capitalize())


def assign_binding(bindings, action, src, code):
    """Asigna (src, code) a la acción; quita la tecla de cualquier otra acción
    para evitar duplicados. Devuelve la lista de acciones modificadas."""
    changed = [action]
    for a, b in list(bindings.items()):
        if a != action and b == (src, code):
            bindings[a] = None
            changed.append(a)
    bindings[action] = (src, code)
    return changed
