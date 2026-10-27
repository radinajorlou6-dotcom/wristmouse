import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pytest

from synth import Synth
from wristmouse import calibrate, commands
from wristmouse.config import Config
from wristmouse.engine import Engine
from wristmouse.output import DryOutput
from wristmouse.voice import mulaw_decode, mulaw_encode


def make():
    cfg = Config(persist=False)
    out = DryOutput()
    events = []
    eng = Engine(cfg, out, lambda ev, **kw: events.append((ev, kw)))
    return cfg, out, eng, events


def test_rest_does_nothing():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(3000)
    s.feed(eng)
    assert out.of("click") == [] and out.of("scroll") == []
    assert abs(out.x) < 2 and abs(out.y) < 2


def test_single_pinch_left_click():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500); s.pinch(); s.rest(800)
    s.feed(eng)
    assert out.of("click") == [("click", "left", 1)]


def test_double_pinch_right_click():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500); s.pinch(); s.rest(220); s.pinch(); s.rest(800)
    s.feed(eng)
    assert out.of("click") == [("click", "right", 1)]


def test_two_separate_pinches_two_left_clicks():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500); s.pinch(); s.rest(900); s.pinch(); s.rest(800)
    s.feed(eng)
    assert out.of("click") == [("click", "left", 1), ("click", "left", 1)]


def test_triple_pinch_dictate():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500)
    for _ in range(3):
        s.pinch(); s.rest(200)
    s.rest(800)
    s.feed(eng)
    assert ("dictate", {}) in ev
    assert out.of("click") == []


def test_weak_tap_ignored():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500); s.pinch(strength=0.8); s.rest(800)
    s.feed(eng)
    assert out.of("click") == []


def test_flick_scrolls_once_despite_return_swing():
    cfg, out, eng, ev = make()
    ax, sign = cfg["scroll_axis"]
    s = Synth()
    s.rest(500); s.flick(ax, sign); s.rest(600)
    s.feed(eng)
    assert out.of("scroll") == [("scroll", -3)]
    s.flick(ax, -sign); s.rest(600)   # flick up
    s.feed(eng)
    assert out.of("scroll")[-1] == ("scroll", 3)
    assert out.of("click") == []


def test_repeated_flicks_scroll_repeatedly():
    cfg, out, eng, ev = make()
    ax, sign = cfg["scroll_axis"]
    s = Synth()
    s.rest(300)
    for _ in range(3):
        s.flick(ax, sign); s.rest(250)
    s.rest(500)
    s.feed(eng)
    assert out.of("scroll") == [("scroll", -3)] * 3


def test_cursor_moves_right_and_down():
    cfg, out, eng, ev = make()
    axx, sx = cfg["cursor_axis_x"]
    axy, sy = cfg["cursor_axis_y"]
    s = Synth()
    s.rest(300); s.rotate(axx, 1.0 * sx, 500); s.rest(300)
    s.feed(eng)
    assert out.x > 200 and abs(out.y) < 20
    x0 = out.x
    s.rotate(axy, 1.0 * sy, 500); s.rest(300)
    s.feed(eng)
    assert out.y > 200 and abs(out.x - x0) < 20


def test_palm_up_toggles():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(300); s.palm_up(1000); s.rest(500)
    s.feed(eng)
    assert eng.active is False
    s.pinch(); s.rest(600); s.rotate(2, 2.0, 300)
    s.feed(eng)
    assert out.of("click") == [] and out.x == 0
    s.palm_up(1000); s.rest(500); s.pinch(); s.rest(600)
    s.feed(eng)
    assert eng.active is True
    assert out.of("click") == [("click", "left", 1)]


def test_short_palm_up_no_toggle():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(300); s.palm_up(400); s.rest(300)
    s.feed(eng)
    assert eng.active


def test_mulaw_roundtrip():
    x = (np.sin(np.linspace(0, 50, 1600)) * 20000).astype(np.int16)
    y = mulaw_decode(mulaw_encode(x))
    err = np.abs(y.astype(int) - x.astype(int))
    assert err.max() < 20000 * 0.04


def test_commands():
    assert commands.parse("Enter.") == ("key", "enter")
    assert commands.parse(" Select all! ") == ("key", "ctrl", "a")
    assert commands.parse("type copy") == ("type", "copy")
    assert commands.parse("Hello there, Bob.") == ("type", "Hello there, Bob.")
    assert commands.parse("") is None


def test_calibrate_pinch():
    cfg, out, eng, ev = make()
    got = []
    eng.start_capture(6000, got.append)
    s = Synth()
    s.rest(200)
    for st in (2.0, 2.5, 3.0, 2.2, 2.8):
        s.rest(900); s.pinch(st)
    s.rest(1600)
    s.feed(eng)
    assert got, "capture finished"
    thr, n = calibrate.pinch_threshold(got[0], cfg["pinch_max_gyro"])
    assert n == 5
    assert 0.3 < thr < 2.0


def test_calibrate_axes():
    cfg, out, eng, ev = make()
    got = []
    eng.start_capture(2000, got.append)
    s = Synth()
    s.rest(200); s.rotate(0, -2.0, 300); s.rotate(0, 2.0, 300); s.rest(1600)
    s.feed(eng)
    assert calibrate.axis_from_motion(got[0]) == [0, -1]
    got.clear()
    eng.start_capture(2000, got.append)
    s.rest(100); s.flick(2, 1); s.rest(1800)
    s.feed(eng)
    assert calibrate.axis_from_motion(got[0]) == [2, 1]


def test_calibrate_no_motion_fails():
    cfg, out, eng, ev = make()
    got = []
    eng.start_capture(2000, got.append)
    s = Synth(); s.rest(2300); s.feed(eng)
    with pytest.raises(calibrate.CalibrationError):
        calibrate.axis_from_motion(got[0])


def test_vibration_does_not_trigger_pinch():
    cfg, out, eng, ev = make()
    eng.pinch_blocked = lambda: True
    s = Synth()
    s.rest(500); s.pinch(); s.rest(800)
    s.feed(eng)
    assert out.of("click") == []


def test_hub_blocks_pinches_while_vibrating():
    from wristmouse.hub import Hub

    cfg = Config(persist=False)
    out = DryOutput()
    hub = Hub(cfg, out)

    class L:
        kind = "test"
        sent = []
        def send(self, line):
            self.sent.append(line)

    link = L()
    hub.link_up(link)
    s = Synth()
    s.rest(500); s.pinch(); s.rest(500)
    for line in s.lines():
        hub.on_line(link, line)
    assert out.of("click") == [("click", "left", 1)]
    assert any(x.startswith("vib") for x in link.sent)
    # the buzz arrives right after the click: it must not count as another pinch
    s.pinch(4.0); s.rest(500)
    for line in s.lines():
        hub.on_line(link, line)
    assert out.of("click") == [("click", "left", 1)]


def test_axis_wizard_excludes_taken_axis():
    cfg, out, eng, ev = make()
    got = []
    eng.start_capture(2000, got.append)
    s = Synth()
    # mostly axis 2 with some axis 1: without exclusion it would pick 2 again
    s.rest(200)
    for _ in range(30):
        s._sample(w=(0.0, 1.2, 2.0))
    s.rest(1700)
    s.feed(eng)
    assert calibrate.axis_from_motion(got[0], exclude=2) == [1, 1]


def test_config_fixes_same_axes(tmp_path):
    import json
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"cursor_axis_x": [1, 1], "cursor_axis_y": [1, -1]}))
    cfg = Config(str(p))
    assert cfg["cursor_axis_x"][0] != cfg["cursor_axis_y"][0]


def test_up_down_boost():
    cfg, out, eng, ev = make()
    axx, sx = cfg["cursor_axis_x"]
    axy, sy = cfg["cursor_axis_y"]
    s = Synth()
    s.rest(300); s.rotate(axx, 1.0 * sx, 500); s.rest(300)
    s.feed(eng)
    x = out.x
    s.rotate(axy, 1.0 * sy, 500); s.rest(300)
    s.feed(eng)
    assert out.y > 1.8 * x  # same wrist rotation moves further vertically
