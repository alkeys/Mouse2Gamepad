from mouse2gamepad.motion import clamp_axis, compute_gyro, compute_stick


def test_compute_gyro_zero_delta_is_zero():
    yaw, pitch, roll = compute_gyro(0, 0, dt=0.01, gyro_sens=0.06,
                                     invert_x=False, invert_y=False)
    assert yaw == pitch == roll == 0.0


def test_compute_gyro_roll_mirrors_yaw():
    yaw, pitch, roll = compute_gyro(10, 5, dt=0.02, gyro_sens=0.1,
                                     invert_x=False, invert_y=False)
    assert roll == yaw


def test_compute_gyro_invert_flips_sign():
    yaw, pitch, _ = compute_gyro(10, 10, dt=0.02, gyro_sens=0.1,
                                  invert_x=False, invert_y=False)
    yaw_inv, pitch_inv, _ = compute_gyro(10, 10, dt=0.02, gyro_sens=0.1,
                                          invert_x=True, invert_y=True)
    assert yaw_inv == -yaw
    assert pitch_inv == -pitch


def test_compute_stick_deadzone_snaps_to_zero_without_input():
    rs_x, rs_y = compute_stick(rs_x=0.0, rs_y=0.0, acc_dx=0, acc_dy=0,
                                decay=0.86, stick_sens=55.0,
                                invert_x=False, invert_y=False)
    assert rs_x == 0.0
    assert rs_y == 0.0


def test_compute_stick_accumulates_then_decays_toward_zero():
    rs_x, rs_y = compute_stick(rs_x=0.0, rs_y=0.0, acc_dx=5, acc_dy=0,
                                decay=0.86, stick_sens=55.0,
                                invert_x=False, invert_y=False)
    assert rs_x > 0

    rs_x2, _ = compute_stick(rs_x=rs_x, rs_y=rs_y, acc_dx=0, acc_dy=0,
                              decay=0.86, stick_sens=55.0,
                              invert_x=False, invert_y=False)
    assert 0 < rs_x2 < rs_x


def test_compute_stick_invert_flips_sign():
    rs_x, _ = compute_stick(0.0, 0.0, 5, 0, 0.86, 55.0, False, False)
    rs_x_inv, _ = compute_stick(0.0, 0.0, 5, 0, 0.86, 55.0, True, False)
    assert rs_x_inv == -rs_x


def test_clamp_axis_bounds_and_truncates():
    assert clamp_axis(999999, 32767) == 32767
    assert clamp_axis(-999999, 32767) == -32767
    assert clamp_axis(100.7, 32767) == 100
