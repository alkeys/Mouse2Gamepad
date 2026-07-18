from evdev import ecodes as e

from mouse2gamepad.config_validation import parse_binding, validate_params

DEFAULTS = {
    "gyro_sens": 0.06, "stick_sens": 55.0, "decay": 0.86,
    "mode": "both", "gy_inv_x": False, "gy_inv_y": False,
    "rs_inv_x": False, "rs_inv_y": False, "hz": 500,
}


def test_valid_values_are_applied():
    data = {"gyro_sens": 0.1, "mode": "gyro", "hz": 250, "gy_inv_x": True}
    params, warnings = validate_params(data, DEFAULTS)
    assert params["gyro_sens"] == 0.1
    assert params["mode"] == "gyro"
    assert params["hz"] == 250
    assert params["gy_inv_x"] is True
    assert warnings == []


def test_invalid_types_fall_back_to_default_with_warning():
    data = {"gyro_sens": "rapido", "decay": None, "hz": "muy rapido"}
    params, warnings = validate_params(data, DEFAULTS)
    assert params["gyro_sens"] == DEFAULTS["gyro_sens"]
    assert params["decay"] == DEFAULTS["decay"]
    assert params["hz"] == DEFAULTS["hz"]
    assert len(warnings) == 3


def test_decay_out_of_range_is_rejected():
    params, warnings = validate_params({"decay": 1.5}, DEFAULTS)
    assert params["decay"] == DEFAULTS["decay"]
    assert warnings


def test_negative_or_zero_sensitivity_is_rejected():
    params, warnings = validate_params({"gyro_sens": -1.0, "stick_sens": 0}, DEFAULTS)
    assert params["gyro_sens"] == DEFAULTS["gyro_sens"]
    assert params["stick_sens"] == DEFAULTS["stick_sens"]
    assert len(warnings) == 2


def test_invalid_mode_is_rejected():
    params, warnings = validate_params({"mode": "turbo"}, DEFAULTS)
    assert params["mode"] == DEFAULTS["mode"]
    assert warnings


def test_hz_is_clamped_to_valid_range():
    params, _ = validate_params({"hz": 5000}, DEFAULTS)
    assert params["hz"] == 1000
    params2, _ = validate_params({"hz": 1}, DEFAULTS)
    assert params2["hz"] == 60


def test_bool_flags_reject_non_bool_values():
    params, warnings = validate_params({"gy_inv_x": "true"}, DEFAULTS)
    assert params["gy_inv_x"] == DEFAULTS["gy_inv_x"]
    assert warnings


def test_unknown_keys_are_ignored():
    params, warnings = validate_params({"unknown_field": 123}, DEFAULTS)
    assert params == DEFAULTS
    assert warnings == []


def test_parse_binding_empty_or_none_is_unbound():
    assert parse_binding(None) is None
    assert parse_binding([]) is None
    assert parse_binding(False) is None


def test_parse_binding_accepts_integer_code():
    assert parse_binding(["kbd", 18]) == ("kbd", 18)


def test_parse_binding_recovers_evdev_key_name():
    # Config editada a mano con el nombre en vez del código evdev (el crash
    # original reportado: ValueError al hacer int("KEY_E")).
    assert parse_binding(["kbd", "KEY_E"]) == ("kbd", e.KEY_E)


def test_parse_binding_rejects_unknown_string_code():
    assert parse_binding(["kbd", "no_existe"]) is None


def test_parse_binding_rejects_invalid_src():
    assert parse_binding(["gamepad", 18]) is None


def test_parse_binding_rejects_wrong_shape():
    assert parse_binding(["kbd", 18, "extra"]) is None
    assert parse_binding("kbd") is None
