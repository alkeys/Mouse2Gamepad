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
    "L": e.BTN_TL, "R": e.BTN_TR, "ZL": e.BTN_TL2, "ZR": e.BTN_TR2,
    "PLUS": e.BTN_START, "MINUS": e.BTN_SELECT, "HOME": e.BTN_MODE,
    "L3": e.BTN_THUMBL, "R3": e.BTN_THUMBR,
}
# ZL/ZR también mueven los gatillos ANALÓGICOS (así los espera SDL en un pad
# estilo Xbox: ABS_Z = gatillo izq, ABS_RZ = gatillo der). Nunca se debe
# escribir el stick derecho en Z/RZ: SDL lo interpretaría como gatillos
# presionados constantemente (input fantasma en Cemu).
ACTION_TRIG = {"ZL": e.ABS_Z, "ZR": e.ABS_RZ}

STICK_ACTIONS = {"LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT"}
DPAD_ACTIONS = {"DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"}

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
                        if action in STICK_ACTIONS or action in DPAD_ACTIONS:
                            (pressed.add if val else pressed.discard)(action)
                            if action in DPAD_ACTIONS:
                                write_axis(e.ABS_HAT0X,
                                           ("DPAD_RIGHT" in pressed) - ("DPAD_LEFT" in pressed))
                                write_axis(e.ABS_HAT0Y,
                                           ("DPAD_DOWN" in pressed) - ("DPAD_UP" in pressed))
                                ui.syn()
                        else:
                            ui.write(e.EV_KEY, ACTION_BTN[action], val)
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
# Interfaz gráfica
# ============================================================================

class App:
    def __init__(self, root):
        self.root = root
        root.title("Mouse2Gamepad — gyro para Cemu")
        root.resizable(False, False)

        self.engine = None
        self.q = queue.Queue()
        self.binding_action = None    # acción en espera de tecla
        self.prio_txt = ""
        self.params = {
            "gyro_sens": 0.06, "stick_sens": 55.0, "decay": 0.86,
            "mode": "both", "gy_inv_x": False, "gy_inv_y": False,
            "rs_inv_x": False, "rs_inv_y": False,
            "grabbed": False, "clients": 0, "hz": 500,
            "bindings": dict(DEFAULT_BINDINGS), "bind_version": 0,
        }
        self.load_config()

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(root, padding=10)
        frm.grid(sticky="nsew")

        # ---------- Dispositivos ----------
        devf = ttk.LabelFrame(frm, text="Dispositivos", padding=8)
        devf.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Label(devf, text="Teclado:").grid(row=0, column=0, sticky="w")
        ttk.Label(devf, text="Mouse:").grid(row=1, column=0, sticky="w")
        self.kbd_var, self.mouse_var = tk.StringVar(), tk.StringVar()
        self.kbd_cb = ttk.Combobox(devf, textvariable=self.kbd_var, width=48, state="readonly")
        self.mouse_cb = ttk.Combobox(devf, textvariable=self.mouse_var, width=48, state="readonly")
        self.kbd_cb.grid(row=0, column=1, **pad)
        self.mouse_cb.grid(row=1, column=1, **pad)
        ttk.Button(devf, text="Actualizar", command=self.refresh_devices)\
            .grid(row=0, column=2, rowspan=2, **pad)

        # ---------- Asignación de teclas ----------
        bindf = ttk.LabelFrame(
            frm, text="Asignación de teclas — haz clic en un botón y presiona la tecla "
                      "o botón del mouse que quieras", padding=8)
        bindf.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)
        self.bind_btns = {}
        COLS = 3
        for i, (action, label) in enumerate(ACTIONS):
            r, c = divmod(i, COLS)
            cell = ttk.Frame(bindf)
            cell.grid(row=r, column=c, sticky="ew", padx=6, pady=2)
            ttk.Label(cell, text=label, width=17, anchor="w").pack(side="left")
            b = ttk.Button(cell, width=13,
                           command=lambda a=action: self.start_binding(a))
            b.pack(side="left")
            self.bind_btns[action] = b
        btnrow = ttk.Frame(bindf)
        btnrow.grid(row=(len(ACTIONS) + COLS - 1) // COLS, column=0,
                    columnspan=COLS, sticky="e", pady=(6, 0))
        ttk.Button(btnrow, text="Borrar todas",
                   command=self.clear_bindings).pack(side="right", padx=4)
        ttk.Button(btnrow, text="Restaurar predeterminadas",
                   command=self.reset_bindings).pack(side="right", padx=4)
        ttk.Button(btnrow, text="Guardar configuración",
                   command=self.save_config).pack(side="right", padx=4)
        self.refresh_bind_labels()

        # ---------- Modo del mouse ----------
        modef = ttk.LabelFrame(frm, text="Modo del mouse (F7)", padding=8)
        modef.grid(row=2, column=0, sticky="nsew", **pad)
        self.mode_var = tk.StringVar(value=self.params["mode"])
        for i, (val, txt) in enumerate([("gyro", "Solo giroscopio"),
                                        ("stick", "Solo stick derecho"),
                                        ("both", "Ambos")]):
            ttk.Radiobutton(modef, text=txt, value=val, variable=self.mode_var,
                            command=self.on_mode).grid(row=i, column=0, sticky="w")

        # ---------- Inversión de ejes ----------
        invf = ttk.LabelFrame(frm, text="Invertir ejes", padding=8)
        invf.grid(row=2, column=1, sticky="nsew", **pad)
        self.inv_vars = {}
        for i, (key, txt) in enumerate([("gy_inv_x", "Gyro horizontal"),
                                        ("gy_inv_y", "Gyro vertical"),
                                        ("rs_inv_x", "Stick horizontal"),
                                        ("rs_inv_y", "Stick vertical")]):
            v = tk.BooleanVar(value=self.params[key])
            self.inv_vars[key] = v
            ttk.Checkbutton(invf, text=txt, variable=v, command=self.on_invert)\
                .grid(row=i // 2, column=i % 2, sticky="w", padx=4)

        # ---------- Sensibilidad ----------
        sensf = ttk.LabelFrame(frm, text="Velocidad / sensibilidad del mouse (F5 / F6)", padding=8)
        sensf.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)
        sensf.columnconfigure(1, weight=1)
        self.gyro_var = tk.DoubleVar(value=self.params["gyro_sens"])
        self.stick_var = tk.DoubleVar(value=self.params["stick_sens"])
        self.decay_var = tk.DoubleVar(value=self.params["decay"])
        self.gyro_lbl = self._slider(sensf, 0, "Giroscopio", self.gyro_var, 0.005, 2.0)
        self.stick_lbl = self._slider(sensf, 1, "Stick derecho", self.stick_var, 5.0, 2000.0)
        self.decay_lbl = self._slider(sensf, 2, "Suavizado stick", self.decay_var, 0.50, 0.98)

        # ---------- Control ----------
        ctlf = ttk.Frame(frm)
        ctlf.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)
        self.start_btn = ttk.Button(ctlf, text="▶ Iniciar", command=self.toggle_start)
        self.start_btn.pack(side="left", padx=4)
        self.grab_btn = ttk.Button(ctlf, text="Capturar mouse+teclado (F8)",
                                   command=self.toggle_grab, state="disabled")
        self.grab_btn.pack(side="left", padx=4)
        ttk.Label(ctlf, text="Frecuencia:").pack(side="left", padx=(16, 2))
        self.hz_var = tk.StringVar(value=str(self.params["hz"]))
        hz_cb = ttk.Combobox(ctlf, textvariable=self.hz_var, width=6,
                             state="readonly", values=("250", "500", "1000"))
        hz_cb.pack(side="left")
        hz_cb.bind("<<ComboboxSelected>>", lambda _ev: self.on_hz())
        ttk.Label(ctlf, text="Hz").pack(side="left", padx=(2, 0))

        self.status = tk.StringVar(value="Detenido. Selecciona dispositivos e inicia.")
        ttk.Label(frm, textvariable=self.status, relief="sunken", anchor="w", padding=4)\
            .grid(row=5, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(frm, foreground="#666", justify="left", text=(
            "Cemu: controller SDLController → \"Mouse2Gamepad (virtual)\"  +  "
            "DSUController 127.0.0.1:26760 con \"use motion\".\n"
            "F5-F8 y F10 están reservadas para el programa y no se pueden asignar. "
            "El cambio de frecuencia aplica al reiniciar el motor."))\
            .grid(row=6, column=0, columnspan=2, sticky="w", **pad)

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
                    self.grab_btn.config(text=("Liberar mouse+teclado (F8)" if value
                                               else "Capturar mouse+teclado (F8)"))
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
        self._update_sens_labels()
        self.root.after(150, self.poll)

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
