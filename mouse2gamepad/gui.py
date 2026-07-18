"""Interfaz gráfica: dibujo del control (estilo Wii U Pro) y ventana principal."""

import json
import os
import queue
import sys
import threading

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    sys.exit("Falta tkinter. Instala con: sudo pacman -S tk")

try:
    from evdev import InputDevice, ecodes as e, list_devices
except ImportError:
    sys.exit("Falta python-evdev. Instala con: sudo pacman -S python-evdev")

from mouse2gamepad.bindings import (
    ACTIONS, AXIS_MAX, CEMU_NAME, DEFAULT_BINDINGS, DSU_PORT, assign_binding,
    keyname,
)
from mouse2gamepad.config import CONFIG_FILE
from mouse2gamepad.config_validation import validate_params
from mouse2gamepad.engine import Engine, capture_key_offline


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
        self.texts[action] = self.create_text(cx, cy - 46, text=name,
                                              fill="#9a9aa0", font=("", 7))
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

        validated, warnings = validate_params(data, self.params)
        self.params.update(validated)
        for w in warnings:
            print(f"[Mouse2Gamepad] {w}", file=sys.stderr)

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
