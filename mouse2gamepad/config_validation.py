"""Validación de los valores numéricos/booleanos cargados desde el JSON de
configuración. No lanza excepciones: ante un valor con tipo o rango
inválido, conserva el valor por defecto y lo reporta como advertencia."""

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
