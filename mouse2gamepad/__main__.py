"""Punto de entrada de Mouse2Gamepad: crea la ventana principal y arranca el mainloop."""

import tkinter as tk
from tkinter import ttk

from mouse2gamepad.gui import App


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
