# Mouse2Gamepad

[![Captura-de-pantalla-20260718-094648.png](https://i.postimg.cc/q7SnKZMw/Captura-de-pantalla-20260718-094648.png)](https://postimg.cc/rdGKXj4R)


[![Captura-de-pantalla-20260718-094706.png](https://i.postimg.cc/L8yj9sdn/Captura-de-pantalla-20260718-094706.png)](https://postimg.cc/18q8Ms3Q)

[![Captura-de-pantalla-20260718-094725.png](https://i.postimg.cc/c4SRTjMC/Captura-de-pantalla-20260718-094725.png)](https://postimg.cc/qg16v1FH)

Mouse + Teclado como gamepad virtual con giroscopio simulado (mediante protocolo DSU/cemuhook) para Cemu en Linux. Incluye una interfaz gráfica intuitiva para la asignación de teclas personalizable y configuración en tiempo real.

## Características
* **Giroscopio Simulado:** Usa el movimiento del mouse para simular el giroscopio mediante DSU (Cemuhook).
* **Gamepad Virtual:** Emula un control estándar en Linux con ayuda de `uinput`.
* **Interfaz Gráfica:** Ajusta la sensibilidad, invierte los ejes y asigna las teclas de tu teclado y mouse fácilmente.
* **Ajuste al vuelo:** Usa teclas rápidas para ajustar la sensibilidad sin salir de tus juegos.

## Requisitos e Instalación

Necesitarás instalar `python-evdev` para simular los eventos de entrada y `tk` para la interfaz gráfica.

**Arch Linux o derivadas (CachyOS, Manjaro, etc.):**

```bash
sudo pacman -S python-evdev tk
```

**Debian / Ubuntu y derivadas:**

```bash
sudo apt install python3-evdev python3-tk
```

**Fedora:**

```bash
sudo dnf install python3-evdev python3-tkinter
```

Alternativamente, `python-evdev` también puede instalarse con pip
(`tk`/`tkinter` siempre debe instalarse con el gestor de paquetes del sistema,
no con pip):

```bash
pip install -r requirements.txt
```

## Uso

Para que el programa pueda crear un dispositivo virtual e interceptar el teclado, es necesario ejecutarlo con permisos de superusuario:

```bash
sudo python3 mouse2gamepad_gui.py
```

> **Nota para usuarios de Wayland:** Si la ventana no se abre al usar `sudo`, debes darle permisos al usuario root para usar tu servidor gráfico ejecutando este comando en tu terminal antes:
> `xhost +SI:localuser:root`

## Teclas Rápidas (Atajos de teclado)
Las siguientes teclas están reservadas y funcionan globalmente mientras el control está capturado. No puedes asignarlas a otras funciones:

* **F5**: Reducir sensibilidad general
* **F6**: Aumentar sensibilidad general
* **F7**: Cambiar modo de mouse (Giroscopio / Stick / Ambos)
* **F8**: Capturar / Liberar el mouse y el teclado
* **F10**: Detener el motor de entrada de inmediato

## Configuración en Cemu
1. Abre Cemu y dirígete a las opciones de controles.
2. Selecciona **SDLController** como API y escoge el dispositivo **"Mouse2Gamepad (virtual)"**.
3. Asegúrate de habilitar la opción de **"Use Motion"** y configurar el servidor DSU en `127.0.0.1:26760`.
4. El archivo de configuración `mouse2gamepad_config.json` se guardará automáticamente en el mismo directorio que el script (o junto al ejecutable, si usas el binario compilado).

## Compilar un ejecutable

El repo incluye `mouse2gamepad_gui.spec` para empaquetar la app con [PyInstaller](https://pyinstaller.org/) en un único binario:

```bash
pip install pyinstaller
pyinstaller mouse2gamepad_gui.spec
```

El ejecutable queda en `dist/mouse2gamepad_gui`. La configuración se guarda junto al binario real (no en una carpeta temporal), así que persiste entre ejecuciones.

## Desarrollo

El código vive en el paquete `mouse2gamepad/` (`dsu.py`, `motion.py`, `config_validation.py`, `bindings.py`, `engine.py`, `gui.py`); `mouse2gamepad_gui.py` es solo el lanzador. Hay pruebas con `pytest` para la lógica que no depende de hardware ni de Tkinter (protocolo DSU, cálculo de gyro/stick, validación de config, bindings):

```bash
pip install pytest
python -m pytest tests/
```
