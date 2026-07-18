"""Validación de los valores numéricos/booleanos y de las asignaciones de
teclas cargados desde el JSON de configuración. No lanza excepciones: ante
un valor con tipo o rango inválido, conserva el valor por defecto (o descarta
solo esa asignación) y lo reporta como advertencia."""

try:
    from evdev import ecodes as _ecodes
except ImportError:
    _ecodes = None

MODES = ["gyro", "stick", "both"]


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_CHECKS = {
    "mode": lambda v: v in MODES,
    "gyro_sens": lambda v: is_number(v) and v > 0,
    "stick_sens": lambda v: is_number(v) and v > 0,
    "decay": lambda v: is_number(v) and 0 <= v < 1,
    "hz": lambda v: is_number(v),
    "gy_inv_x": lambda v: isinstance(v, bool),
    "gy_inv_y": lambda v: isinstance(v, bool),
    "rs_inv_x": lambda v: isinstance(v, bool),
    "rs_inv_y": lambda v: isinstance(v, bool),
}


def parse_binding(value):
    """Valida/normaliza una asignación (src, code) leída del JSON.

    Acepta `code` como entero o, para configs viejas o editadas a mano, como
    nombre evdev (p. ej. "KEY_E"). Devuelve (src, code:int), o None si el
    valor está vacío/ausente o es irrecuperable (nunca lanza excepción)."""
    if not value:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    src, code = value
    if src not in ("kbd", "mouse"):
        return None
    if isinstance(code, str) and _ecodes is not None:
        resolved = _ecodes.ecodes.get(code)
        if resolved is not None:
            return (src, resolved)
    try:
        return (src, int(code))
    except (TypeError, ValueError):
        return None


def validate_params(data, defaults):
    """Devuelve (params, advertencias): params parte de `defaults` y solo
    sobreescribe los campos de `data` que pasan su validación; el resto
    genera una advertencia en texto y conserva el default."""
    params = dict(defaults)
    warnings = []
    for key, valid in _CHECKS.items():
        if key not in data:
            continue
        value = data[key]
        if not valid(value):
            warnings.append(
                f"Config inválida para «{key}»: {value!r}, uso el valor por defecto.")
            continue
        params[key] = max(60, min(1000, int(value))) if key == "hz" else value
    return params, warnings
