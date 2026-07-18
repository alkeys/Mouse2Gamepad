"""Cálculo puro de giroscopio y stick derecho a partir del delta de mouse.

Sin dependencias de hardware/hilos para que sea testeable de forma aislada.
"""


def compute_gyro(acc_dx, acc_dy, dt, gyro_sens, invert_x, invert_y):
    """Traduce el delta de mouse acumulado en yaw/pitch/roll (°/s aprox.)."""
    gx = -1 if invert_x else 1
    gy = -1 if invert_y else 1
    yaw = -(acc_dx / dt) * gyro_sens * gx
    pitch = -(acc_dy / dt) * gyro_sens * gy
    # roll = yaw: truco para que el giro horizontal funcione sin importar la
    # orientación que asuma el juego.
    roll = yaw
    return yaw, pitch, roll


def compute_stick(rs_x, rs_y, acc_dx, acc_dy, decay, stick_sens,
                   invert_x, invert_y, deadzone=1.0):
    """Emula el stick derecho: decae hacia el centro y acumula el delta de
    mouse escalado por sensibilidad, con una zona muerta cerca de cero."""
    sx = -1 if invert_x else 1
    sy = -1 if invert_y else 1
    rs_x = rs_x * decay + acc_dx * stick_sens * sx
    rs_y = rs_y * decay + acc_dy * stick_sens * sy
    if abs(rs_x) < deadzone:
        rs_x = 0.0
    if abs(rs_y) < deadzone:
        rs_y = 0.0
    return rs_x, rs_y


def clamp_axis(value, axis_max):
    return max(-axis_max, min(axis_max, int(value)))
