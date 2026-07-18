"""Motor: lee mouse/teclado, alimenta el gamepad virtual y el DSU."""

import os
import select
import sys
import threading
import time

try:
    from evdev import InputDevice, UInput, AbsInfo, ecodes as e
except ImportError:
    sys.exit("Falta python-evdev. Instala con: sudo pacman -S python-evdev")

from mouse2gamepad.bindings import (
    ACTION_BTN, ACTION_TRIG, AXIS_MAX, DPAD_ACTIONS, HK_GRAB, HK_MODE,
    HK_QUIT, HK_SENS_DOWN, HK_SENS_UP, HOTKEYS, STICK_ACTIONS, TRIG_MAX,
    assign_binding,
)
from mouse2gamepad.config_validation import MODES
from mouse2gamepad.dsu import DSUServer
from mouse2gamepad.motion import clamp_axis, compute_gyro, compute_stick


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
                    except OSError as ex:
                        nombre = "teclado" if src == "kbd" else "mouse"
                        self._emit("fatal", f"Se perdió el dispositivo de {nombre}: {ex}")
                        self.stop_flag.set()
                        break
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
                        rs_x, rs_y = compute_stick(
                            rs_x, rs_y, acc_dx, acc_dy, p["decay"], p["stick_sens"],
                            p["rs_inv_x"], p["rs_inv_y"])
                    else:
                        rs_x = rs_y = 0.0

                    if mode in ("gyro", "both"):
                        yaw, pitch, roll = compute_gyro(
                            acc_dx, acc_dy, dt, p["gyro_sens"],
                            p["gy_inv_x"], p["gy_inv_y"])
                    else:
                        yaw = pitch = roll = 0.0

                    acc_dx = acc_dy = 0

                    changed = False
                    changed |= write_axis(e.ABS_X, clamp_axis(ls_x, AXIS_MAX))
                    changed |= write_axis(e.ABS_Y, clamp_axis(ls_y, AXIS_MAX))
                    changed |= write_axis(e.ABS_RX, clamp_axis(rs_x, AXIS_MAX))
                    changed |= write_axis(e.ABS_RY, clamp_axis(rs_y, AXIS_MAX))
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
