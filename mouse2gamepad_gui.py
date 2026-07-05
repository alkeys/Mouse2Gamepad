#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mouse2gamepad_gui.py — Mouse + teclado como gamepad virtual con giroscopio
simulado (protocolo DSU/cemuhook) para Cemu en Linux. Interfaz gráfica con
asignación de teclas personalizable.

Requisitos:
    sudo pacman -S python-evdev tk      (Arch / CachyOS)

Uso:
    sudo python3 mouse2gamepad_gui.py
    (si usas Wayland y la ventana no abre con sudo:  xhost +SI:localuser:root )

Teclas rápidas RESERVADAS (no se pueden reasignar, funcionan dentro del juego):
    F5/F6 = sensibilidad -/+   F7 = modo mouse   F8 = capturar/liberar   F10 = detener

La configuración (teclas, sensibilidad, modo, inversiones, frecuencia) se guarda
en mouse2gamepad_config.json junto al script y se carga automáticamente.

NOTAS DE FIDELIDAD:
- Los botones se envían en el instante en que llega el evento del kernel (sin
  esperar al tick), así que su latencia ya es mínima.
- El hilo del motor intenta subir a prioridad de tiempo real (SCHED_FIFO); como
  corres con sudo, normalmente lo consigue y elimina el jitter del scheduler.
- La frecuencia de actualización de ejes/gyro es configurable (250/500/1000 Hz).
"""

import json
import os
import queue
import random
import select
import socket
import struct
import sys
import threading
import time
import zlib

try:
    from evdev import InputDevice, UInput, AbsInfo, ecodes as e, list_devices
except ImportError:
    sys.exit("Falta python-evdev. Instala con: sudo pacman -S python-evdev")

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    sys.exit("Falta tkinter. Instala con: sudo pacman -S tk")

AXIS_MAX = 32767
TRIG_MAX = 255
DSU_PORT = 26760
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "mouse2gamepad_config.json")

# ---- Teclas reservadas del programa (no reasignables) -----------------------
HK_SENS_DOWN, HK_SENS_UP, HK_MODE, HK_GRAB, HK_QUIT = (
    e.KEY_F5, e.KEY_F6, e.KEY_F7, e.KEY_F8, e.KEY_F10)
HOTKEYS = {HK_SENS_DOWN, HK_SENS_UP, HK_MODE, HK_GRAB, HK_QUIT}
MODES = ["gyro", "stick", "both"]

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


def boost_priority():
    """Sube la prioridad del hilo actual para minimizar jitter.
    Con sudo, SCHED_FIFO casi siempre funciona. Devuelve descripción o None."""
    try:
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(10))
        return "SCHED_FIFO 10"
    except (AttributeError, PermissionError, OSError):
        pass
    try:
        os.nice(-10)
        return "nice -10"
    except OSError:
        return None


# ============================================================================
# Servidor DSU (cemuhook)
# ============================================================================

class DSUServer:
    MSG_VERSION, MSG_PORTS, MSG_DATA = 0x100000, 0x100001, 0x100002

    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.setblocking(False)
        self.server_id = random.randint(0, 0xFFFFFFFF)
        self.clients = {}
        self.counter = 0
        self.ctrl_header = struct.pack("<BBBB6sB", 0, 2, 2, 1,
                                       b"\x0a\x1b\x2c\x3d\x4e\x5f", 5)

    def _packet(self, msg_type, payload):
        data = struct.pack("<I", msg_type) + payload
        header = struct.pack("<4sHHII", b"DSUS", 1001, len(data), 0, self.server_id)
        pkt = header + data
        crc = zlib.crc32(pkt) & 0xFFFFFFFF
        return pkt[:8] + struct.pack("<I", crc) + pkt[12:]

    def handle_incoming(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
            except (BlockingIOError, InterruptedError, OSError):
                return
            if len(data) < 20 or data[:4] != b"DSUC":
                continue
            msg = struct.unpack_from("<I", data, 16)[0]
            if msg == self.MSG_VERSION:
                self.sock.sendto(self._packet(self.MSG_VERSION,
                                              struct.pack("<H", 1001)), addr)
            elif msg == self.MSG_PORTS and len(data) >= 24:
                count = struct.unpack_from("<i", data, 20)[0]
                for i in range(min(count, 4)):
                    if 24 + i >= len(data):
                        break
                    port = data[24 + i]
                    if port == 0:
                        payload = self.ctrl_header + b"\x00"
                    else:
                        payload = struct.pack("<BBBB6sB", port, 0, 0, 0,
                                              b"\x00" * 6, 0) + b"\x00"
                    self.sock.sendto(self._packet(self.MSG_PORTS, payload), addr)
            elif msg == self.MSG_DATA:
                self.clients[addr] = time.time()

    def send_motion(self, pitch, yaw, roll=0.0, acc=(0.0, -1.0, 0.0)):
        now = time.time()
        self.clients = {a: t for a, t in self.clients.items() if now - t < 5.0}
        if not self.clients:
            return
        self.counter = (self.counter + 1) & 0xFFFFFFFF
        payload = (self.ctrl_header + b"\x01"
                   + struct.pack("<I", self.counter)
                   + b"\x00\x00\x00\x00"
                   + bytes([128, 128, 128, 128])
                   + b"\x00" * 12 + b"\x00" * 12
                   + struct.pack("<Q", int(now * 1_000_000))
                   + struct.pack("<fff", *acc)
                   + struct.pack("<fff", pitch, yaw, roll))
        pkt = self._packet(self.MSG_DATA, payload)
        for addr in self.clients:
            try:
                self.sock.sendto(pkt, addr)
            except OSError:
                pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ============================================================================
# Motor: lee mouse/teclado, alimenta el gamepad virtual y el DSU
# ============================================================================

class Engine(threading.Thread):
    """Un solo hilo con select() sobre teclado + mouse + socket DSU.
    No hace falta un hilo por dispositivo: select() despierta en cuanto
    CUALQUIERA de los dos tiene datos (event-driven, latencia < 1 ms), y con
    un solo hilo no hay locks ni condiciones de carrera entre dispositivos."""

    def __init__(self, kbd_path, mouse_path, dsu_port, params, event_q):
        super().__init__(daemon=True)
        self.kbd_path, self.mouse_path = kbd_path, mouse_path
        self.dsu_port = dsu_port
        self.p = params
        self.q = event_q
        self.stop_flag = threading.Event()
        self.grab_request = None
        self.bind_request = None      # id de acción esperando tecla, o None

    def _emit(self, kind, value=None):
        self.q.put((kind, value))

    def request_grab(self, state):
        self.grab_request = state

    def stop(self):
        self.stop_flag.set()

    def run(self):
        try:
            kbd = InputDevice(self.kbd_path)
            mouse = InputDevice(self.mouse_path)
        except OSError as ex:
            self._emit("fatal", f"No pude abrir los dispositivos: {ex}\n¿Ejecutaste con sudo?")
            return

        absinfo = AbsInfo(0, -AXIS_MAX, AXIS_MAX, 16, 128, 0)
        triginfo = AbsInfo(0, 0, TRIG_MAX, 0, 0, 0)
        hatinfo = AbsInfo(0, -1, 1, 0, 0, 0)
        try:
            ui = UInput(events={
                e.EV_KEY: sorted(set(ACTION_BTN.values())),
                e.EV_ABS: [(e.ABS_X, absinfo), (e.ABS_Y, absinfo),
                           (e.ABS_RX, absinfo), (e.ABS_RY, absinfo),
                           (e.ABS_Z, triginfo), (e.ABS_RZ, triginfo),
                           (e.ABS_HAT0X, hatinfo), (e.ABS_HAT0Y, hatinfo)],
            }, name="Mouse2Gamepad (virtual)", vendor=0x045E, product=0x028E)
        except PermissionError:
            self._emit("fatal", "Sin permiso para /dev/uinput. Ejecuta con sudo.")
            return

        try:
            dsu = DSUServer(self.dsu_port)
        except OSError as ex:
            ui.close()
            self._emit("fatal", f"No pude abrir el puerto DSU {self.dsu_port}: {ex}")
            return

        prio = boost_priority()
        self._emit("started", prio)

        grabbed = False
        pressed = set()               # acciones (no teclas) actualmente presionadas
        rs_x = rs_y = 0.0
        acc_dx = acc_dy = 0
        hz = max(60, min(1000, int(self.p.get("hz", 500))))
        tick = 1.0 / hz
        last = time.monotonic()
        next_tick = last + tick
        rev = {}                      # (src, code) -> acción
        bind_ver = -1
        last_axes = {}                # cache: solo escribir ejes si cambian

        def write_axis(axis, value):
            if last_axes.get(axis) != value:
                last_axes[axis] = value
                ui.write(e.EV_ABS, axis, value)
                return True
            return False

        def release_all():
            """Suelta todo (botones, hat, gatillos) para evitar inputs pegados
            al reasignar teclas o al salir."""
            pressed.clear()
            for btn in set(ACTION_BTN.values()):
                ui.write(e.EV_KEY, btn, 0)
            write_axis(e.ABS_HAT0X, 0)
            write_axis(e.ABS_HAT0Y, 0)
            write_axis(e.ABS_Z, 0)
            write_axis(e.ABS_RZ, 0)
            ui.syn()

        def rebuild_rev():
            nonlocal rev, bind_ver
            rev = {b: a for a, b in self.p["bindings"].items() if b}
            bind_ver = self.p["bind_version"]

        def set_grab(state):
            nonlocal grabbed
            if state == grabbed:
                return
            for d in (kbd, mouse):
                try:
                    d.grab() if state else d.ungrab()
                except OSError:
                    pass
            grabbed = state
            self.p["grabbed"] = state
            self._emit("grab", state)

        rebuild_rev()

        try:
            while not self.stop_flag.is_set():
                if self.grab_request is not None:
                    set_grab(self.grab_request)
                    self.grab_request = None
                if bind_ver != self.p["bind_version"]:
                    rebuild_rev()
                    release_all()     # evita teclas pegadas tras reasignar

                timeout = max(0.0, next_tick - time.monotonic())
                try:
                    ready, _, _ = select.select(
                        [kbd.fd, mouse.fd, dsu.sock.fileno()], [], [], timeout)
                except OSError:
                    break

                for fd in ready:
                    if fd == dsu.sock.fileno():
                        dsu.handle_incoming()
                        continue
                    dev = kbd if fd == kbd.fd else mouse
                    src = "kbd" if dev is kbd else "mouse"
                    try:
                        events = list(dev.read())
                    except OSError:
                        continue
                    for ev in events:
                        if ev.type == e.EV_REL:
                            if ev.code == e.REL_X:
                                acc_dx += ev.value
                            elif ev.code == e.REL_Y:
                                acc_dy += ev.value
                            continue
                        if ev.type != e.EV_KEY or ev.value == 2:
                            continue
                        code, val = ev.code, ev.value

                        # ---- teclas reservadas del programa ----
                        if src == "kbd" and code in HOTKEYS:
                            if val == 1:
                                if code == HK_QUIT:
                                    self.stop_flag.set()
                                elif code == HK_GRAB:
                                    set_grab(not grabbed)
                                elif code == HK_MODE:
                                    m = MODES[(MODES.index(self.p["mode"]) + 1) % len(MODES)]
                                    self.p["mode"] = m
                                    rs_x = rs_y = 0.0
                                    self._emit("mode", m)
                                elif code == HK_SENS_UP:
                                    self.p["gyro_sens"] *= 1.15
                                    self.p["stick_sens"] *= 1.15
                                    self._emit("sens")
                                elif code == HK_SENS_DOWN:
                                    self.p["gyro_sens"] /= 1.15
                                    self.p["stick_sens"] /= 1.15
                                    self._emit("sens")
                            continue

                        # ---- modo de asignación: capturar la siguiente PULSACIÓN ----
                        # (las liberaciones siguen su curso normal para no dejar
                        #  botones pegados si empezaste a asignar con algo presionado)
                        if self.bind_request is not None and val == 1:
                            action = self.bind_request
                            self.bind_request = None
                            changed = assign_binding(self.p["bindings"],
                                                     action, src, code)
                            self.p["bind_version"] += 1
                            rebuild_rev()
                            release_all()
                            self._emit("bound", changed)
                            continue

                        # ---- acción normal (los botones salen AL INSTANTE) ----
                        action = rev.get((src, code))
                        if action is None:
                            continue
                        (pressed.add if val else pressed.discard)(action)
                        if action in STICK_ACTIONS or action in DPAD_ACTIONS:
                            if action in DPAD_ACTIONS:
                                write_axis(e.ABS_HAT0X,
                                           ("DPAD_RIGHT" in pressed) - ("DPAD_LEFT" in pressed))
                                write_axis(e.ABS_HAT0Y,
                                           ("DPAD_DOWN" in pressed) - ("DPAD_UP" in pressed))
                                ui.syn()
                        else:
                            btn = ACTION_BTN.get(action)
                            if btn is not None:
                                ui.write(e.EV_KEY, btn, val)
                            trig = ACTION_TRIG.get(action)
                            if trig is not None:
                                write_axis(trig, TRIG_MAX if val else 0)
                            ui.syn()

                # -------- tick de ejes / gyro --------
                now = time.monotonic()
                if now >= next_tick:
                    dt = max(now - last, 1e-4)
                    last = now
                    next_tick += tick
                    if now > next_tick:
                        next_tick = now + tick

                    p = self.p
                    mode = p["mode"]

                    ls_x = (("LS_RIGHT" in pressed) - ("LS_LEFT" in pressed)) * AXIS_MAX
                    ls_y = (("LS_DOWN" in pressed) - ("LS_UP" in pressed)) * AXIS_MAX

                    if mode in ("stick", "both"):
                        sx = -1 if p["rs_inv_x"] else 1
                        sy = -1 if p["rs_inv_y"] else 1
                        rs_x = rs_x * p["decay"] + acc_dx * p["stick_sens"] * sx
                        rs_y = rs_y * p["decay"] + acc_dy * p["stick_sens"] * sy
                        if abs(rs_x) < 1.0:
                            rs_x = 0.0
                        if abs(rs_y) < 1.0:
                            rs_y = 0.0
                    else:
                        rs_x = rs_y = 0.0

                    if mode in ("gyro", "both"):
                        gx = -1 if p["gy_inv_x"] else 1
                        gy = -1 if p["gy_inv_y"] else 1
                        yaw = -(acc_dx / dt) * p["gyro_sens"] * gx
                        pitch = -(acc_dy / dt) * p["gyro_sens"] * gy
                        # roll = yaw: truco para que el giro horizontal funcione
                        # sin importar la orientación que asuma el juego
                        roll = yaw
                    else:
                        yaw = pitch = roll = 0.0

                    acc_dx = acc_dy = 0

                    clamp = lambda v: max(-AXIS_MAX, min(AXIS_MAX, int(v)))
                    changed = False
                    changed |= write_axis(e.ABS_X, clamp(ls_x))
                    changed |= write_axis(e.ABS_Y, clamp(ls_y))
                    changed |= write_axis(e.ABS_RX, clamp(rs_x))
                    changed |= write_axis(e.ABS_RY, clamp(rs_y))
                    if changed:
                        ui.syn()

                    dsu.send_motion(pitch, yaw, roll)
                    p["clients"] = len(dsu.clients)
                    p["monitor"] = (tuple(pressed), int(ls_x), int(ls_y),
                                    int(rs_x), int(rs_y),
                                    round(yaw, 1), round(pitch, 1))
        finally:
            try:
                release_all()
            except Exception:
                pass
            set_grab(False)
            try:
                ui.close()
            except Exception:
                pass
            dsu.close()
            self._emit("stopped")


# ============================================================================
# Captura de tecla cuando el motor NO está corriendo
# ============================================================================

def capture_key_offline(kbd_path, mouse_path, action, event_q, timeout=6.0):
    """Abre los dispositivos temporalmente y espera la siguiente tecla."""
    devs = []
    try:
        for path, src in ((kbd_path, "kbd"), (mouse_path, "mouse")):
            try:
                devs.append((InputDevice(path), src))
            except OSError:
                pass
        if not devs:
            event_q.put(("capture_fail", "No pude abrir los dispositivos (¿sudo?)."))
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([d.fd for d, _ in devs], [], [], 0.2)
            for fd in ready:
                for d, src in devs:
                    if d.fd != fd:
                        continue
                    try:
                        for ev in d.read():
                            if ev.type == e.EV_KEY and ev.value == 1:
                                if src == "kbd" and ev.code in HOTKEYS:
                                    continue
                                event_q.put(("captured", (action, src, ev.code)))
                                return
                    except OSError:
                        pass
        event_q.put(("capture_timeout", action))
    finally:
        for d, _ in devs:
            try:
                d.close()
            except OSError:
                pass


# ============================================================================
# Dibujo del control (estilo Wii U Pro) con resaltado de botones presionados
# ============================================================================

class GamepadCanvas(tk.Canvas):
    """Dibuja un control estilo Wii U Pro Controller y resalta en verde los
    botones presionados. Los sticks se desplazan según los ejes (LS por WASD,
    RS por el mouse) y el propio stick se ilumina al presionar L3/R3."""

    W, H = 660, 330
    ON = "#00d26a"           # relleno de "presionado"
    BODY = "#1e1e24"         # cuerpo del control
    WELL = "#141418"         # pozo de los sticks
    BTN = "#32323a"          # botón en reposo
    EDGE = "#0f0f12"         # bordes
    TXT = "#f0f0f0"          # texto en reposo
    TXT_ON = "#002b12"       # texto sobre botón presionado
    KNOB_R = 20              # radio de la perilla del stick
    STICK_TRAVEL = 14        # desplazamiento máximo de la perilla (px)

    def __init__(self, parent, **kw):
        bg = ttk.Style().lookup("TFrame", "background") or "#dcdad5"
        super().__init__(parent, width=self.W, height=self.H,
                         bg=bg, highlightthickness=0, **kw)
        self.parts = {}      # acción -> [ids de figuras a colorear]
        self.texts = {}      # acción -> id del texto del botón
        self._build()

    # ---------- primitivas ----------
    def _btn_oval(self, action, cx, cy, r, label="", font=("", 10, "bold")):
        s = self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             fill=self.BTN, outline=self.EDGE, width=2)
        self.parts[action] = [s]
        if label:
            self.texts[action] = self.create_text(cx, cy, text=label,
                                                  fill=self.TXT, font=font)

    def _btn_rect(self, action, x1, y1, x2, y2, label="", font=("", 9, "bold")):
        s = self.create_rectangle(x1, y1, x2, y2, fill=self.BTN,
                                  outline=self.EDGE, width=2)
        self.parts[action] = [s]
        if label:
            self.texts[action] = self.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                                  text=label, fill=self.TXT,
                                                  font=font)

    def _stick(self, action, cx, cy, name):
        self.create_oval(cx - 36, cy - 36, cx + 36, cy + 36,
                         fill=self.WELL, outline=self.EDGE, width=2)
        self.create_text(cx, cy - 46, text=name, fill="#9a9aa0", font=("", 7))
        r = self.KNOB_R
        knob = self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                fill=self.BTN, outline=self.EDGE, width=2)
        self.parts[action] = [knob]
        return (cx, cy, knob)

    def _dpad(self, cx, cy):
        h, l = 13, 39
        self._btn_rect("DPAD_UP", cx - h, cy - l, cx + h, cy - h, "▲", ("", 7))
        self._btn_rect("DPAD_DOWN", cx - h, cy + h, cx + h, cy + l, "▼", ("", 7))
        self._btn_rect("DPAD_LEFT", cx - l, cy - h, cx - h, cy + h, "◀", ("", 7))
        self._btn_rect("DPAD_RIGHT", cx + h, cy - h, cx + l, cy + h, "▶", ("", 7))
        self.create_rectangle(cx - h, cy - h, cx + h, cy + h,
                              fill=self.BTN, outline=self.EDGE, width=2)

    # ---------- construcción ----------
    def _build(self):
        # gatillos y hombros (por fuera del cuerpo, arriba)
        self._btn_rect("ZL", 118, 36, 212, 60, "ZL")
        self._btn_rect("L", 108, 64, 222, 88, "L")
        self._btn_rect("ZR", 448, 36, 542, 60, "ZR")
        self._btn_rect("R", 438, 64, 552, 88, "R")

        # cuerpo
        self.create_oval(95, 90, 265, 305, fill=self.BODY, outline="")
        self.create_oval(395, 90, 565, 305, fill=self.BODY, outline="")
        self.create_rectangle(178, 90, 482, 250, fill=self.BODY, outline="")

        # sticks (izq arriba, der abajo — disposición Wii U Pro)
        self.ls = self._stick("L3", 185, 155, "L3")
        self.rs = self._stick("R3", 420, 240, "R3")

        # ABXY (disposición Nintendo: X arriba, A derecha, B abajo, Y izq)
        self._btn_oval("X", 475, 123, 16, "X")
        self._btn_oval("A", 507, 155, 16, "A")
        self._btn_oval("B", 475, 187, 16, "B")
        self._btn_oval("Y", 443, 155, 16, "Y")

        # cruceta
        self._dpad(245, 240)

        # −  HOME  +
        self._btn_oval("MINUS", 300, 128, 10, "−", ("", 9, "bold"))
        self._btn_oval("PLUS", 360, 128, 10, "+", ("", 9, "bold"))
        self._btn_oval("HOME", 330, 178, 13, "HOME", ("", 6))

    # ---------- actualización ----------
    def _move_knob(self, stick, vx, vy):
        cx, cy, knob = stick
        dx = max(-1.0, min(1.0, vx / AXIS_MAX)) * self.STICK_TRAVEL
        dy = max(-1.0, min(1.0, vy / AXIS_MAX)) * self.STICK_TRAVEL
        r = self.KNOB_R
        self.coords(knob, cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r)

    def update_state(self, act, lsx=0, lsy=0, rsx=0, rsy=0):
        """act = conjunto de acciones presionadas; ejes en [-AXIS_MAX, AXIS_MAX].
        Las direcciones LS_*/mouse ya vienen reflejadas en los valores de eje,
        por eso el stick se mueve en vez de iluminar chips separados."""
        for action, ids in self.parts.items():
            on = action in act
            fill = self.ON if on else self.BTN
            for i in ids:
                self.itemconfig(i, fill=fill)
            t = self.texts.get(action)
            if t is not None:
                self.itemconfig(t, fill=self.TXT_ON if on else self.TXT)
        self._move_knob(self.ls, lsx, lsy)
        self._move_knob(self.rs, rsx, rsy)

    def reset(self):
        self.update_state(frozenset())

    def update_key_labels(self, bindings):
        if not hasattr(self, 'key_labels'):
            self.key_labels = {}
        for action, text_id in self.texts.items():
            bind = bindings.get(action)
            kname = keyname(bind)
            if action not in self.key_labels:
                coords = self.coords(text_id)
                if coords:
                    x, y = coords
                    # Draw the mapped key name just below the button label
                    self.key_labels[action] = self.create_text(x, y + 16, text=kname, fill="#00ffcc", font=("", 8, "bold"))
            else:
                self.itemconfig(self.key_labels[action], text=kname)


# ============================================================================
# Interfaz gráfica
# ============================================================================

class App:
    def __init__(self, root):
        self.root = root
        root.title("Mouse2Gamepad — gyro para Cemu | Creado por aviles alkeys")
        root.minsize(720, 540)
        root.resizable(True, True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        
        # Aplicar un estilo más moderno
        style = ttk.Style()
        if 'clam' in style.theme_names():
            style.theme_use('clam')
            
        bg_color = "#f4f5f7"
        fg_color = "#2c2c2c"
        accent = "#0078D7"
        
        style.configure(".", background=bg_color, foreground=fg_color, font=("Segoe UI", 10))
        style.configure("TFrame", background=bg_color)
        style.configure("TLabel", background=bg_color, foreground=fg_color)
        style.configure("TLabelframe", background=bg_color, font=("Segoe UI", 10, "bold"))
        style.configure("TLabelframe.Label", background=bg_color, foreground=accent)
        style.configure("TButton", font=("Segoe UI", 10), padding=4, background="#e2e2e2", borderwidth=0)
        style.map("TButton", background=[("active", "#d0d0d0"), ("disabled", "#f4f5f7")])
        style.configure("TCombobox", padding=4)
        style.configure("TNotebook", background=bg_color, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 6), font=("Segoe UI", 10, "bold"), background="#e2e2e2")
        style.map("TNotebook.Tab", background=[("selected", bg_color)], foreground=[("selected", accent)])


        self.engine = None
        self.q = queue.Queue()
        self.binding_action = None
        self.prio_txt = ""
        self.params = {
            "gyro_sens": 0.06, "stick_sens": 55.0, "decay": 0.86,
            "mode": "both", "gy_inv_x": False, "gy_inv_y": False,
            "rs_inv_x": False, "rs_inv_y": False,
            "grabbed": False, "clients": 0, "hz": 500,
            "bindings": dict(DEFAULT_BINDINGS), "bind_version": 0,
        }
        self.load_config()

        px, py = 6, 3

        # ---- marco principal scrollable ----
        outer = ttk.Frame(root, padding=8)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        # fila del notebook se expande para llenar espacio extra
        outer.rowconfigure(1, weight=1)

        # ============ Barra superior: control + estado ============
        topbar = ttk.Frame(outer)
        topbar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        topbar.columnconfigure(5, weight=1)   # espacio entre botones y Hz

        self.start_btn = ttk.Button(topbar, text="▶ Iniciar",
                                    command=self.toggle_start)
        self.start_btn.grid(row=0, column=0, padx=2)
        self.grab_btn = ttk.Button(topbar, text="🔓 Capturar (F8)",
                                   command=self.toggle_grab, state="disabled")
        self.grab_btn.grid(row=0, column=1, padx=2)

        self.monitor_btn = ttk.Button(topbar, text="🎮 Monitor",
                                       command=self.toggle_monitor_win)
        self.monitor_btn.grid(row=0, column=2, padx=2)

        ttk.Separator(topbar, orient="vertical").grid(row=0, column=3,
                                                       sticky="ns", padx=6)

        ttk.Label(topbar, text="Hz:").grid(row=0, column=4)
        self.hz_var = tk.StringVar(value=str(self.params["hz"]))
        hz_cb = ttk.Combobox(topbar, textvariable=self.hz_var, width=5,
                             state="readonly", values=("250", "500", "1000"))
        hz_cb.grid(row=0, column=5, sticky="w", padx=(2, 8))
        hz_cb.bind("<<ComboboxSelected>>", lambda _ev: self.on_hz())

        self.status = tk.StringVar(value="Detenido. Selecciona dispositivos e inicia.")
        ttk.Label(topbar, textvariable=self.status, relief="sunken",
                  anchor="w", padding=(6, 2)).grid(row=0, column=6, sticky="ew")
        topbar.columnconfigure(6, weight=1)

        # ============ Notebook con pestañas ============
        nb = ttk.Notebook(outer)
        nb.grid(row=1, column=0, sticky="nsew")

        # ---- Tab 1: Dispositivos + Teclas ----
        tab1 = ttk.Frame(nb, padding=8)
        nb.add(tab1, text="  Dispositivos y teclas  ")
        tab1.columnconfigure(0, weight=1)
        tab1.rowconfigure(1, weight=1)

        # -- Dispositivos --
        devf = ttk.LabelFrame(tab1, text="Dispositivos", padding=6)
        devf.grid(row=0, column=0, sticky="ew", pady=py)
        devf.columnconfigure(1, weight=1)
        self.kbd_var, self.mouse_var = tk.StringVar(), tk.StringVar()

        ttk.Label(devf, text="Teclado:").grid(row=0, column=0, sticky="w", padx=px)
        self.kbd_cb = ttk.Combobox(devf, textvariable=self.kbd_var, state="readonly")
        self.kbd_cb.grid(row=0, column=1, sticky="ew", padx=px, pady=2)

        ttk.Label(devf, text="Mouse:").grid(row=1, column=0, sticky="w", padx=px)
        self.mouse_cb = ttk.Combobox(devf, textvariable=self.mouse_var, state="readonly")
        self.mouse_cb.grid(row=1, column=1, sticky="ew", padx=px, pady=2)

        ttk.Button(devf, text="↻ Detectar",
                   command=self.refresh_devices).grid(row=0, column=2, rowspan=2, padx=px)

        # -- Asignación de teclas --
        bindf = ttk.LabelFrame(tab1, text="Asignación de teclas — clic → presiona "
                                          "tecla/mouse  (gris = nombre en Cemu)", padding=6)
        bindf.grid(row=1, column=0, sticky="nsew", pady=py)
        bindf.columnconfigure(0, weight=1)
        bindf.columnconfigure(1, weight=1)
        
        groups = [
            ("Botones", ["A", "B", "X", "Y"]),
            ("Cruceta", ["DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"]),
            ("Gatillos y Hombros", ["L", "R", "ZL", "ZR"]),
            ("Sticks", ["LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT", "L3", "R3"]),
            ("Sistema", ["PLUS", "MINUS", "HOME"]),
        ]
        
        self.bind_btns = {}
        act_dict = dict(ACTIONS)
        
        for idx, (gname, act_keys) in enumerate(groups):
            r, c = divmod(idx, 2)
            gf = ttk.LabelFrame(bindf, text=gname, padding=4)
            gf.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
            gf.columnconfigure(1, weight=1)
            for i, action in enumerate(act_keys):
                label = act_dict[action]
                ttk.Label(gf, text=label, anchor="w", width=14).grid(row=i, column=0, sticky="w")
                b = ttk.Button(gf, command=lambda a=action: self.start_binding(a), width=16)
                b.grid(row=i, column=1, sticky="ew", padx=2, pady=1)
                ttk.Label(gf, text=CEMU_NAME.get(action, ""), anchor="w", foreground="#888").grid(row=i, column=2, sticky="w")
                self.bind_btns[action] = b

        btnrow = ttk.Frame(bindf)
        btnrow.grid(row=2, column=1, sticky="se", padx=4, pady=4)
        ttk.Button(btnrow, text="Borrar todas",
                   command=self.clear_bindings).pack(side="right", padx=3)
        ttk.Button(btnrow, text="Rest. predet.",
                   command=self.reset_bindings).pack(side="right", padx=3)
        ttk.Button(btnrow, text="Guardar config",
                   command=self.save_config).pack(side="right", padx=3)
        self.refresh_bind_labels()

        # ---- Tab 2: Sensibilidad + Opciones ----
        tab2 = ttk.Frame(nb, padding=8)
        nb.add(tab2, text="  Sensibilidad y opciones  ")
        tab2.columnconfigure(0, weight=1)
        tab2.columnconfigure(1, weight=1)

        # -- Modo del mouse --
        modef = ttk.LabelFrame(tab2, text="Modo del mouse (F7)", padding=6)
        modef.grid(row=0, column=0, sticky="nsew", padx=px, pady=py)
        self.mode_var = tk.StringVar(value=self.params["mode"])
        for i, (val, txt) in enumerate([("gyro", "Solo giroscopio"),
                                        ("stick", "Solo stick derecho"),
                                        ("both", "Ambos (recomendado)")]):
            ttk.Radiobutton(modef, text=txt, value=val, variable=self.mode_var,
                            command=self.on_mode).grid(row=i, column=0,
                                                       sticky="w", pady=1)

        # -- Inversión --
        invf = ttk.LabelFrame(tab2, text="Invertir ejes", padding=6)
        invf.grid(row=0, column=1, sticky="nsew", padx=px, pady=py)
        self.inv_vars = {}
        for i, (key, txt) in enumerate([("gy_inv_x", "Gyro horizontal"),
                                        ("gy_inv_y", "Gyro vertical"),
                                        ("rs_inv_x", "Stick horizontal"),
                                        ("rs_inv_y", "Stick vertical")]):
            v = tk.BooleanVar(value=self.params[key])
            self.inv_vars[key] = v
            ttk.Checkbutton(invf, text=txt, variable=v,
                            command=self.on_invert).grid(row=i // 2, column=i % 2,
                                                          sticky="w", padx=4, pady=1)

        # -- Sliders --
        sensf = ttk.LabelFrame(tab2, text="Velocidad / sensibilidad del mouse (F5 ↓ / F6 ↑)",
                                padding=6)
        sensf.grid(row=1, column=0, columnspan=2, sticky="ew", padx=px, pady=py)
        sensf.columnconfigure(1, weight=1)
        self.gyro_var = tk.DoubleVar(value=self.params["gyro_sens"])
        self.stick_var = tk.DoubleVar(value=self.params["stick_sens"])
        self.decay_var = tk.DoubleVar(value=self.params["decay"])
        self.gyro_lbl = self._slider(sensf, 0, "Giroscopio", self.gyro_var, 0.005, 2.0)
        self.stick_lbl = self._slider(sensf, 1, "Stick derecho", self.stick_var, 5.0, 2000.0)
        self.decay_lbl = self._slider(sensf, 2, "Suavizado (retorno al centro)",
                                      self.decay_var, 0.50, 0.98)

        # -- Ayuda --
        helpf = ttk.LabelFrame(tab2, text="Configuración en Cemu", padding=6)
        helpf.grid(row=2, column=0, columnspan=2, sticky="ew", padx=px, pady=py)
        helpf.columnconfigure(0, weight=1)
        ttk.Label(helpf, foreground="#555", justify="left", wraplength=700, text=(
            "1. Mando emulado → Wii U GamePad (el Pro Controller NO tiene giroscopio).\n"
            "2. Mando → SDLController → \"Mouse2Gamepad (virtual)\". Mapear botones "
            "con F8 (capturar) activado para que las teclas no interfieran.\n"
            "3. Añadir otro mando (+) → DSUController, IP 127.0.0.1, puerto 26760, "
            "marcar \"use motion\". Al conectar verás ✔ en la barra de estado.\n"
            "4. F5-F8 y F10 están reservadas para este programa y no se pueden asignar."))\
            .grid(row=0, column=0, sticky="ew")

        # ---- Tab 3: Monitor (instrucciones mínimas) ----
        tab3 = ttk.Frame(nb, padding=8)
        nb.add(tab3, text="  Monitor  ")
        tab3.columnconfigure(0, weight=1)

        ttk.Label(tab3, text="Usa el botón «🎮 Monitor» de la barra superior "
                  "para abrir la ventana flotante del control.\n"
                  "Queda siempre encima y puedes ver las teclas mientras las asignas.",
                  justify="center", foreground="#555").grid(row=0, column=0, pady=20)

        # ---- Ventana flotante del control ----
        self.pad_win = None
        self.pad = None
        self.mon_lbl = None

        self.refresh_devices()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll)

    # ---------- helpers ----------
    def _slider(self, parent, row, text, var, lo, hi):
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w")
        s = ttk.Scale(parent, from_=lo, to=hi, variable=var,
                      command=lambda _=None: self.on_sens())
        s.grid(row=row, column=1, sticky="ew", padx=8)
        lbl = ttk.Label(parent, width=8, anchor="e")
        lbl.grid(row=row, column=2)
        return lbl

    def toggle_monitor_win(self):
        """Abre o cierra la ventana flotante del control."""
        if self.pad_win is not None and self.pad_win.winfo_exists():
            self.pad_win.destroy()
            self.pad_win = None
            self.pad = None
            self.mon_lbl = None
            self.monitor_btn.config(text="🎮 Monitor")
            return

        win = tk.Toplevel(self.root)
        win.title("Monitor del control")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.toggle_monitor_win)

        self.pad = GamepadCanvas(win)
        self.pad.pack(padx=8, pady=(8, 0))

        self.mon_lbl = ttk.Label(win, anchor="center", font=("monospace", 9),
                                  text="LS (    0,     0)  ·  RS (    0,     0)  ·  "
                                       "Gyro yaw 0.0 / pitch 0.0 °/s")
        self.mon_lbl.pack(fill="x", padx=8, pady=(4, 8))

        self.pad_win = win
        self.monitor_btn.config(text="🎮 Cerrar monitor")
        self._update_pad_labels()

    def _update_pad_labels(self):
        """Pinta debajo de cada botón del canvas la tecla asignada."""
        if self.pad is None:
            return
        self.pad.update_key_labels(self.params["bindings"])

    def refresh_devices(self):
        kbds, mice = [], []
        for path in list_devices():
            try:
                d = InputDevice(path)
            except OSError:
                continue
            if "mouse2gamepad" in d.name.lower():
                continue
            caps = d.capabilities()
            keys = set(caps.get(e.EV_KEY, []))
            rels = set(caps.get(e.EV_REL, []))
            label = f"{path}  |  {d.name}"
            if e.KEY_A in keys and e.KEY_SPACE in keys and e.KEY_F8 in keys:
                kbds.append(label)
            if e.REL_X in rels and e.BTN_LEFT in keys:
                mice.append(label)
        self.kbd_cb["values"] = kbds
        self.mouse_cb["values"] = mice
        if kbds and not self.kbd_var.get():
            self.kbd_var.set(kbds[0])
        if mice and not self.mouse_var.get():
            self.mouse_var.set(mice[0])
        if not kbds and not mice:
            self.status.set("No se detectaron dispositivos. ¿Ejecutaste con sudo?")

    def _path(self, var):
        return var.get().split("  |  ")[0].strip()

    # ---------- asignación de teclas ----------
    def refresh_bind_labels(self):
        for action, btn in self.bind_btns.items():
            if action == self.binding_action:
                btn.config(text="Presiona…")
            else:
                btn.config(text=keyname(self.params["bindings"].get(action)))

    def start_binding(self, action):
        self.root.focus_set()
        if self.binding_action == action:      # clic de nuevo = cancelar
            self.cancel_binding()
            return
        if self.binding_action is not None:
            self.cancel_binding()
        kbd, mouse = self._path(self.kbd_var), self._path(self.mouse_var)
        if not kbd and not mouse:
            messagebox.showwarning("Dispositivos", "Selecciona primero teclado y mouse.")
            return
        self.binding_action = action
        self.refresh_bind_labels()
        self.status.set(f"Presiona la tecla o botón del mouse para «{dict(ACTIONS)[action]}» "
                        "(clic de nuevo para cancelar)…")
        if self.engine and self.engine.is_alive():
            self.engine.bind_request = action
        else:
            threading.Thread(target=capture_key_offline,
                             args=(kbd, mouse, action, self.q),
                             daemon=True).start()

    def cancel_binding(self):
        if self.engine and self.engine.is_alive():
            self.engine.bind_request = None
        self.binding_action = None
        self.refresh_bind_labels()
        self.status.set("Asignación cancelada.")

    def apply_capture(self, action, src, code):
        assign_binding(self.params["bindings"], action, src, code)
        self.params["bind_version"] += 1
        self.binding_action = None
        self.refresh_bind_labels()
        self.status.set(f"«{dict(ACTIONS)[action]}» asignado a "
                        f"{keyname((src, code))}.")

    def reset_bindings(self):
        self.params["bindings"] = dict(DEFAULT_BINDINGS)
        self.params["bind_version"] += 1
        self.binding_action = None
        self.refresh_bind_labels()
        self.status.set("Asignaciones restauradas a las predeterminadas.")

    def clear_bindings(self):
        for a in self.params["bindings"]:
            self.params["bindings"][a] = None
        self.params["bind_version"] += 1
        self.binding_action = None
        self.refresh_bind_labels()
        self.status.set("Todas las asignaciones han sido borradas.")

    # ---------- configuración persistente ----------
    def save_config(self):
        data = {
            "bindings": {a: list(b) if b else None
                         for a, b in self.params["bindings"].items()},
            "gyro_sens": self.params["gyro_sens"],
            "stick_sens": self.params["stick_sens"],
            "decay": self.params["decay"],
            "mode": self.params["mode"],
            "hz": self.params["hz"],
            "gy_inv_x": self.params["gy_inv_x"], "gy_inv_y": self.params["gy_inv_y"],
            "rs_inv_x": self.params["rs_inv_x"], "rs_inv_y": self.params["rs_inv_y"],
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
            self.status.set(f"Configuración guardada en {CONFIG_FILE}")
        except OSError as ex:
            messagebox.showerror("Guardar", f"No pude guardar: {ex}")

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        raw = data.get("bindings", {})
        bindings = dict(DEFAULT_BINDINGS)
        for a, b in raw.items():
            if a in bindings:
                bindings[a] = (b[0], int(b[1])) if b else None
        self.params["bindings"] = bindings
        for k in ("gyro_sens", "stick_sens", "decay", "mode", "hz",
                  "gy_inv_x", "gy_inv_y", "rs_inv_x", "rs_inv_y"):
            if k in data:
                self.params[k] = data[k]

    # ---------- callbacks ----------
    def on_mode(self):
        self.params["mode"] = self.mode_var.get()

    def on_invert(self):
        for k, v in self.inv_vars.items():
            self.params[k] = v.get()

    def on_hz(self):
        try:
            self.params["hz"] = int(self.hz_var.get())
        except ValueError:
            pass
        if self.engine and self.engine.is_alive():
            self.status.set("La nueva frecuencia se aplicará al reiniciar el motor "
                            "(Detener → Iniciar).")

    def on_sens(self):
        self.params["gyro_sens"] = self.gyro_var.get()
        self.params["stick_sens"] = self.stick_var.get()
        self.params["decay"] = self.decay_var.get()
        self._update_sens_labels()

    def _update_sens_labels(self):
        self.gyro_lbl.config(text=f"{self.params['gyro_sens']:.3f}")
        self.stick_lbl.config(text=f"{self.params['stick_sens']:.1f}")
        self.decay_lbl.config(text=f"{self.params['decay']:.2f}")

    def toggle_start(self):
        if self.engine and self.engine.is_alive():
            self.engine.stop()
            self.start_btn.config(state="disabled")
            return
        kbd, mouse = self._path(self.kbd_var), self._path(self.mouse_var)
        if not kbd or not mouse:
            messagebox.showwarning("Dispositivos", "Selecciona un teclado y un mouse.")
            return
        self.on_mode(); self.on_invert(); self.on_sens()
        self.engine = Engine(kbd, mouse, DSU_PORT, self.params, self.q)
        self.engine.start()
        self.start_btn.config(text="■ Detener")
        self.grab_btn.config(state="normal")
        self.status.set("Iniciando…")

    def toggle_grab(self):
        if self.engine and self.engine.is_alive():
            self.engine.request_grab(not self.params["grabbed"])

    # ---------- bucle de la GUI ----------
    def poll(self):
        try:
            while True:
                kind, value = self.q.get_nowait()
                if kind == "started":
                    self.prio_txt = f" · prioridad {value}" if value else ""
                    self.status.set("Corriendo. Gamepad virtual creado · DSU en 127.0.0.1:26760")
                elif kind == "stopped":
                    self.start_btn.config(text="▶ Iniciar", state="normal")
                    self.grab_btn.config(state="disabled")
                    self.status.set("Detenido.")
                elif kind == "fatal":
                    self.start_btn.config(text="▶ Iniciar", state="normal")
                    self.grab_btn.config(state="disabled")
                    self.status.set("Error.")
                    messagebox.showerror("Error", value)
                elif kind == "grab":
                    self.grab_btn.config(text=("🔒 Liberar (F8)" if value
                                               else "🔓 Capturar (F8)"))
                elif kind == "mode":
                    self.mode_var.set(value)
                elif kind == "sens":
                    self.gyro_var.set(self.params["gyro_sens"])
                    self.stick_var.set(self.params["stick_sens"])
                elif kind == "bound":          # el motor asignó la tecla
                    self.binding_action = None
                    self.refresh_bind_labels()
                    action = value[0]
                    self.status.set(f"«{dict(ACTIONS)[action]}» asignado a "
                                    f"{keyname(self.params['bindings'][action])}.")
                elif kind == "captured":       # captura sin motor corriendo
                    self.apply_capture(*value)
                elif kind == "capture_timeout":
                    self.binding_action = None
                    self.refresh_bind_labels()
                    self.status.set("Tiempo agotado, no se presionó ninguna tecla.")
                elif kind == "capture_fail":
                    self.binding_action = None
                    self.refresh_bind_labels()
                    messagebox.showerror("Asignación", value)
        except queue.Empty:
            pass

        if self.engine and self.engine.is_alive() and self.binding_action is None:
            g = "capturado" if self.params["grabbed"] else "libre"
            c = self.params["clients"]
            self.status.set(f"Corriendo · mouse {g} · modo {self.params['mode']} · "
                            f"{self.params['hz']} Hz{self.prio_txt} · clientes DSU: {c}"
                            + ("  ✔ Cemu conectado" if c else ""))

        # ---- monitor visual (dibujo del control) ----
        running = self.engine is not None and self.engine.is_alive()
        mon = self.params.get("monitor") if running else None
        if mon:
            act, lsx, lsy, rsx, rsy, yaw, pitch = mon
            if self.pad is not None:
                self.pad.update_state(set(act), lsx, lsy, rsx, rsy)
            if self.mon_lbl is not None:
                self.mon_lbl.config(text=f"LS ({lsx}, {lsy}) · RS ({rsx}, {rsy}) · "
                                         f"Gyro yaw {yaw} / pitch {pitch} °/s")
        elif not running:
            if self.pad is not None:
                self.pad.reset()

        self._update_sens_labels()
        # 60 ms: suficiente fluidez para el dibujo sin cargar la CPU
        self.root.after(60, self.poll)

    def on_close(self):
        self.save_config()
        if self.engine and self.engine.is_alive():
            self.engine.stop()
            self.engine.join(timeout=1.0)
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()