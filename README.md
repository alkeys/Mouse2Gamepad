# Mouse2Gamepad

Mouse + Teclado como gamepad virtual con giroscopio simulado (mediante protocolo DSU/cemuhook) para Cemu en Linux. Incluye una interfaz gráfica intuitiva para la asignación de teclas personalizable y configuración en tiempo real.

## Características
* **Giroscopio Simulado:** Usa el movimiento del mouse para simular el giroscopio mediante DSU (Cemuhook).
* **Gamepad Virtual:** Emula un control estándar en Linux con ayuda de `uinput`.
* **Interfaz Gráfica:** Ajusta la sensibilidad, invierte los ejes y asigna las teclas de tu teclado y mouse fácilmente.
* **Ajuste al vuelo:** Usa teclas rápidas para ajustar la sensibilidad sin salir de tus juegos.

## Requisitos e Instalación

Necesitarás instalar `python-evdev` para simular los eventos de entrada y `tk` para la interfaz gráfica. En Arch Linux o derivadas (CachyOS, Manjaro, etc.), ejecuta:

```bash
sudo pacman -S python-evdev tk
```

## Uso

Para que el programa pueda crear un dispositivo virtual e interceptar el teclado, es necesario ejecutarlo con permisos de superusuario:

```bash
sudo python3 a.py
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
4. El archivo de configuración `mouse2gamepad_config.json` se guardará automáticamente en el mismo directorio que el script.