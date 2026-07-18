from evdev import ecodes as e

from mouse2gamepad.bindings import DEFAULT_BINDINGS, assign_binding, keyname


def test_keyname_none_binding():
    assert keyname(None) == "—"


def test_keyname_keyboard_key():
    assert keyname(("kbd", e.KEY_SPACE)) == "Espacio"


def test_keyname_mouse_button():
    name = keyname(("mouse", e.BTN_LEFT))
    assert name.startswith("Mouse ")


def test_assign_binding_replaces_existing_action():
    bindings = dict(DEFAULT_BINDINGS)
    changed = assign_binding(bindings, "A", "kbd", 999)
    assert bindings["A"] == ("kbd", 999)
    assert changed == ["A"]


def test_assign_binding_clears_duplicate_from_other_action():
    bindings = dict(DEFAULT_BINDINGS)
    # "B" ya está asignado a KEY_LEFTCTRL; asignar esa misma tecla a "A"
    # debe liberar la asignación previa de "B" para evitar duplicados.
    src, code = bindings["B"]
    changed = assign_binding(bindings, "A", src, code)
    assert bindings["A"] == (src, code)
    assert bindings["B"] is None
    assert set(changed) == {"A", "B"}
