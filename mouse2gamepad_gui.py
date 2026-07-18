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

La implementación vive en el paquete mouse2gamepad/ (dsu.py, motion.py,
config_validation.py, bindings.py, engine.py, gui.py); este archivo es
solo el lanzador, para no romper el comando de arriba ni la Analysis()
de mouse2gamepad_gui.spec.
"""

from mouse2gamepad.__main__ import main

if __name__ == "__main__":
    main()
