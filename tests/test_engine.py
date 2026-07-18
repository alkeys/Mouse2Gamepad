import queue

from mouse2gamepad.engine import Engine, boost_priority


def test_engine_construction_does_not_touch_devices():
    q = queue.Queue()
    params = {"bindings": {}, "bind_version": 0, "hz": 500}
    eng = Engine("/dev/null", "/dev/null", 0, params, q)
    assert eng.stop_flag.is_set() is False
    assert eng.kbd_path == "/dev/null"


def test_boost_priority_returns_string_or_none():
    result = boost_priority()
    assert result is None or isinstance(result, str)
