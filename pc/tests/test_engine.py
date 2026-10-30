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
    cfg["voice_trigger"] = "triple"
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




# face up: yaw = gyro z, -pitch ("down") = gyro y, roll = gyro x
def test_flick_scrolls_once_despite_return_swing():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500); s.flick(1, 1); s.rest(600)
    s.feed(eng)
    assert out.of("scroll") == [("scroll", -3)]
    s.flick(1, -1); s.rest(600)   # flick up
    s.feed(eng)
    assert out.of("scroll")[-1] == ("scroll", 3)
    assert out.of("click") == []


def test_flick_windup_is_undone():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500); s.flick(1, 1); s.rest(600)
    s.feed(eng)
    assert abs(out.y) < 15, out.y


def test_repeated_flicks_scroll_repeatedly():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(300)
    for _ in range(3):
        s.flick(1, 1); s.rest(250)
    s.rest(500)
    s.feed(eng)
    assert out.of("scroll") == [("scroll", -3)] * 3


def test_cursor_right_and_down_palm_down():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(300); s.rotate(2, -1.0, 500); s.rest(300)     # turn right around vertical
    s.feed(eng)
    assert out.x > 200 and abs(out.y) < 20
    x0 = out.x
    s.rotate(1, 1.0, 300); s.rest(300)                     # point forearm down
    s.feed(eng)
    assert out.y > 150 and abs(out.x - x0) < 20


def test_cursor_same_with_palm_sideways():
    """Watch on its side (gravity along watch y): the world-aligned model still maps
    turning right -> right and pointing down -> down, with no setup."""
    cfg, out, eng, ev = make()
    s = Synth()
    s.base = (0.0, 9.81, 0.0)
    s.rest(400); s.rotate(1, -1.0, 500); s.rest(300)     # turn right: around watch y (= up)
    s.feed(eng)
    assert out.x > 200 and abs(out.y) < 20
    x0 = out.x
    s.rotate(2, -1.0, 300); s.rest(300)                    # point down: around watch z now
    s.feed(eng)
    assert out.y > 150 and abs(out.x - x0) < 20


def test_twist_does_not_move_cursor():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(300); s.rotate(0, 2.0, 300); s.rest(300)
    s.feed(eng)
    assert abs(out.x) < 5 and abs(out.y) < 5


def test_twist_scroll_mode():
    cfg, out, eng, ev = make()
    cfg["scroll_gesture"] = "twist"
    s = Synth()
    s.rest(300); s.flick(0, 1); s.rest(600)
    s.feed(eng)
    assert out.of("scroll") == [("scroll", -3)]


def test_cursor_has_no_delay():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(300)
    s.feed(eng)
    s.rotate(2, -1.0, 30)  # 3 samples
    s.feed(eng)
    assert out.x > 0


def test_up_down_gain_and_invert():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(300); s.rotate(2, -1.0, 300); s.rest(300)
    s.feed(eng)
    x = out.x
    s.rotate(1, 1.0, 300); s.rest(300)
    s.feed(eng)
    assert out.y > 1.2 * x
    cfg["invert_y"] = True
    y = out.y
    s.rotate(1, 1.0, 300); s.rest(300)
    s.feed(eng)
    assert out.y < y


RAISED = (8.6, 0.8, 3.0)       # forearm up, screen facing you (watch at your mouth)
POINTING = (7.0, -3.3, 5.8)    # real reading while using the air mouse
HANGING = (-9.4, 0.5, 2.0)     # arm hanging down by your side


def test_raise_to_mouth_starts_and_lowering_stops_voice():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500)
    s.move_to(RAISED)
    s.rest(1000)
    s.feed(eng)
    assert [e for e, _ in ev].count("dictate") == 1
    s.move_to((0.0, 0.0, 9.81))
    s.rest(600)
    s.feed(eng)
    assert ("lowered", {}) in ev
    assert out.of("click") == []


def test_pointing_or_hanging_arm_does_not_start_voice():
    for pose in (POINTING, HANGING):
        cfg, out, eng, ev = make()
        s = Synth()
        s.rest(300)
        s.move_to(pose)
        s.rest(3000)
        s.feed(eng)
        assert "dictate" not in [e for e, _ in ev], pose


def test_raise_undoes_cursor_and_scroll_from_lifting_arm():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500)
    s.rotate(2, -1.5, 300)        # cursor sweeps right while lifting
    s.flick(1, -1)                 # lifting fast looks like a flick up
    s.move_to(RAISED)
    s.rest(1000)
    s.feed(eng)
    assert "dictate" in [e for e, _ in ev]
    assert abs(out.x) < 10, out.x
    assert sum(a[1] for a in out.of("scroll")) == 0


def test_short_raise_does_nothing():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(300)
    s.move_to(RAISED)
    s.rest(250)
    s.move_to((0.0, 0.0, 9.81))
    s.rest(800)
    s.feed(eng)
    assert "dictate" not in [e for e, _ in ev]
    assert "lowered" not in [e for e, _ in ev]


def test_triple_pinch_is_right_click_in_raise_mode():
    cfg, out, eng, ev = make()
    s = Synth()
    s.rest(500)
    for _ in range(3):
        s.pinch(); s.rest(200)
    s.rest(800)
    s.feed(eng)
    assert out.of("click") == [("click", "right", 1)]
    assert "dictate" not in [e for e, _ in ev]


def test_raise_disabled_in_triple_mode():
    cfg, out, eng, ev = make()
    cfg["voice_trigger"] = "triple"
    s = Synth()
    s.rest(300); s.move_to(RAISED); s.rest(1500)
    s.feed(eng)
    assert "dictate" not in [e for e, _ in ev]


def test_overlay_events_from_hub():
    from wristmouse.hub import Hub

    cfg = Config(persist=False)
    cfg["haptics"] = False
    hub = Hub(cfg, DryOutput())
    s = Synth()
    s.rest(500); s.pinch(); s.rest(600)

    class L:
        kind = "t"
        def send(self, line):
            pass

    link = L()
    hub.link_up(link)
    for line in s.lines():
        hub.on_line(link, line)
    kind, text, sticky = hub.overlay.get_nowait()
    assert (kind, text, sticky) == ("left", "Left click", False)


class PosOut(DryOutput):
    """Records where the cursor was for every button event."""
    def press(self):
        self.actions.append(("press", round(self.x), round(self.y)))

    def release(self):
        self.actions.append(("release", round(self.x), round(self.y)))


def make_pos():
    cfg = Config(persist=False)
    out = PosOut()
    ev = []
    eng = Engine(cfg, out, lambda e, **kw: ev.append(e))
    return cfg, out, eng, ev


def test_double_pinch_and_move_drags_then_pinch_drops():
    cfg, out, eng, ev = make_pos()
    s = Synth()
    s.rest(500); s.pinch(); s.rest(200); s.pinch()
    s.feed(eng)
    x_pinch = out.x
    s.rotate(2, -1.5, 400)          # move right while "holding"
    s.rest(300)
    s.feed(eng)
    presses = out.of("press")
    assert len(presses) == 1 and abs(presses[0][1] - x_pinch) < 3   # pressed where you pinched
    assert out.x > x_pinch + 200                                     # and the tab moved
    assert eng.dragging and "drag" in ev
    s.pinch(); s.rest(600)
    s.feed(eng)
    assert out.of("release") and not eng.dragging
    assert out.of("click") == []                                     # no stray clicks
    # press happened before the drag movement, release after it
    kinds = [a[0] for a in out.actions]
    assert kinds.index("press") < max(i for i, k in enumerate(kinds) if k == "move")


def test_double_pinch_still_is_right_click_not_drag():
    cfg, out, eng, ev = make_pos()
    s = Synth()
    s.rest(500); s.pinch(); s.rest(200); s.pinch(); s.rest(700)
    s.feed(eng)
    assert out.of("click") == [("click", "right", 1)]
    assert out.of("press") == []


def test_single_pinch_and_move_is_click_not_drag():
    cfg, out, eng, ev = make_pos()
    s = Synth()
    s.rest(500); s.pinch(); s.rest(150); s.rotate(2, -1.5, 300); s.rest(400)
    s.feed(eng)
    assert out.of("click") == [("click", "left", 1)]
    assert out.of("press") == []


def test_pause_and_reset_release_the_button():
    for how in ("pause", "reset"):
        cfg, out, eng, ev = make_pos()
        s = Synth()
        s.rest(500); s.pinch(); s.rest(200); s.pinch(); s.rotate(2, -1.5, 400)
        s.feed(eng)
        assert eng.dragging
        if how == "pause":
            s.palm_up(1000); s.feed(eng)
        else:
            eng.reset()
        assert not eng.dragging and len(out.of("release")) == 1, how


def test_no_scroll_while_dragging():
    cfg, out, eng, ev = make_pos()
    s = Synth()
    s.rest(500); s.pinch(); s.rest(200); s.pinch(); s.rotate(2, -1.5, 400)
    s.flick(1, 1); s.rest(300)
    s.feed(eng)
    assert eng.dragging and out.of("scroll") == []


def test_voice_grab_and_drop():
    cfg, out, eng, ev = make_pos()
    s = Synth(); s.rest(300); s.feed(eng)
    eng.set_drag(True)
    assert eng.dragging and len(out.of("press")) == 1
    eng.set_drag(False)
    assert not eng.dragging and len(out.of("release")) == 1
    assert commands.parse("Let go.") == ("drag", False)
    assert commands.parse("grab") == ("drag", True)


def test_smoothmover_keeps_actions_in_order():
    import time
    from wristmouse.output import SmoothMover

    rec = PosOut()
    m = SmoothMover(rec)
    t = 1_000_000.0
    for _ in range(5):
        m.move_at(t, 10, 0); t += 10
    m.act_at(t, "press")
    for _ in range(5):
        m.move_at(t, 10, 0); t += 10
    m.act_at(t, "release")
    time.sleep(0.4)
    assert out_seq(rec) == ["move", "press", "move", "release"]
    assert rec.of("press")[0][1] == 50 and rec.of("release")[0][1] == 100


def out_seq(rec):
    seq = []
    for a in rec.actions:
        if not seq or seq[-1] != a[0]:
            seq.append(a[0])
    return seq
