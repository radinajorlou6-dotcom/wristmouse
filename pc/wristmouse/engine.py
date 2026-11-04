"""Turns raw watch motion (accelerometer + gyroscope) into mouse actions.

Gestures
    palm up, hold ~1s ........ pause / resume the mouse (the "clutch")
    point / swing the forearm  move the cursor (air mode)
    slide hand on the desk ... move the cursor like a mouse (table mode)
    single finger pinch ...... left click
    double pinch ............. right click
    double pinch, then move .. drag (hold the left button); pinch again to drop
    raise watch to mouth ..... start voice typing (lower the arm to finish)
    triple pinch ............. start voice typing (if chosen in settings)
    quick flick / twist ...... scroll

Cursor model: rotation is measured relative to the real world, not the watch. We track which
way is "up" inside the watch (gyro-propagated gravity, corrected by the accelerometer), then
    left/right = turning around the world's vertical axis (yaw)
    up/down    = raising/lowering the forearm (pitch about the horizontal axis across it)
    twist      = rolling around the forearm (ignored for the cursor)
so it works the same whether your palm faces down, sideways or anything in between, on either
wrist, with no setup.

Movement is applied immediately. When a pinch or flick is recognised a few samples late, the
cursor movement it caused is undone instead of delaying every movement.

Units: acceleration in m/s^2, angular velocity in rad/s, time in milliseconds (watch clock).
"""
import collections
import math

PINCH_REFRACTORY_MS = 150
PINCH_UNDO_MS = 60         # cursor motion this long before a pinch is jitter from the tap
FLICK_UNDO_MS = 140        # ... and before a flick is the flick's own wind-up
FLICK_RETURN_MS = 450      # ignore the opposite "return swing" after a flick for this long
FLICK_REPEAT_MS = 150
PALM_UP_RATIO = -0.7       # gravity z / |g| below this = screen facing the floor
# "watch at mouth": forearm pointing well upward and the screen facing sideways (towards you)
RAISE_ON_X, RAISE_ON_Z = 0.75, 0.45
RAISE_OFF_X, RAISE_OFF_Z = 0.55, 0.65
RAISE_UNDO_MS = 1100       # cursor/scroll caused by lifting the arm gets undone
DRAG_MAX_MS = 60_000       # safety: drop automatically after a minute
# table mode: hand slides on the desk, velocity comes from integrating linear acceleration
TABLE_LEAK_S = 0.6         # velocity leaks away with this time constant (limits drift)
TABLE_STILL_MS = 60        # this long without motion = hand stopped -> velocity reset to 0
TABLE_DEADZONE = 0.006     # m/s
TABLE_CLICK_MAX_V = 0.08   # m/s; pinches only count while the hand isn't sliding
GAP_RESET_MS = 250
G = 9.81
FOREARM = (1.0, 0.0, 0.0)  # the watch's x axis runs along the forearm


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


class Output:
    """Interface for whatever actually moves the mouse."""

    def move(self, dx: float, dy: float): ...
    def click(self, button: str = "left", count: int = 1): ...
    def scroll(self, notches: int): ...  # positive = up
    def key(self, *keys: str): ...
    def type_text(self, text: str): ...


class Engine:
    def __init__(self, cfg, out: Output, notify=None):
        self.cfg = cfg
        self.out = out
        self.notify = notify or (lambda ev, **kw: None)
        self.active = bool(cfg["start_active"])
        self.bias = [0.0, 0.0, 0.0]
        self.capture = None
        self.capture_end = 0
        self.capture_cb = None
        self.live_peak = 0.0                  # recent pinch strength, for the settings meter
        self.pinch_blocked = lambda: False    # hub sets this: True while the watch is vibrating
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        if getattr(self, "dragging", False):
            self._drop(None, "reset")   # never leave the mouse button stuck down
        self.dragging = False
        self.drag_since = 0.0
        self.last_t = None
        self.g = None
        self.prev_a = None
        self.palm_since = None
        self.palm_fired = False
        self.taps = []
        self.last_pinch_t = -1e12
        self.flick_dir = 0
        self.flick_active = False
        self.flick_until = -1e12
        self.flick_block_same = -1e12
        self.freeze_until = -1e12
        self.history = collections.deque()   # (t, dx, dy) actually applied, for undo
        self.scrolls = collections.deque()   # (t, notches), for undo when the arm is raised
        self.raised = False
        self.raise_since = None
        self.raise_fired = False
        self.calm_until = -1e12              # no flicks/pinches/cursor right after lowering
        self.recent_m = collections.deque()
        self.still_since = None
        self._peak_decay_t = None
        self.rx = self.ry = 0.0              # lightly smoothed rates
        self.vf = self.vr = 0.0              # table mode: hand velocity forward / right (m/s)
        self.table_quiet_since = None
        self.speeds = collections.deque()    # (t, hand speed) for checking table-mode taps

    def set_active(self, active: bool, t=None):
        if active == self.active:
            return
        if not active and self.dragging:
            self._drop(self.last_t, "paused")
        self.active = active
        self.taps.clear()
        self.history.clear()
        self.notify("active", active=active)

    def start_capture(self, ms: int, callback):
        """Record raw samples for calibration instead of acting on them."""
        self.capture = []
        self.capture_end = None
        self.capture_len = ms
        self.capture_cb = callback

    # ------------------------------------------------------------------- feed
    def feed(self, t: float, a, w_raw):
        c = self.cfg
        if self.last_t is None or t <= self.last_t or t - self.last_t > GAP_RESET_MS:
            self.last_t = t
            self.g = list(a)
            self.prev_a = list(a)
            self.history.clear()
            return
        dt = (t - self.last_t) / 1000.0
        self.last_t = t

        if not all(math.isfinite(x) for x in a) or not all(math.isfinite(x) for x in w_raw):
            return
        w = [w_raw[i] - self.bias[i] for i in range(3)]
        wmag = _norm(w)
        self.table = c["cursor_mode"] == "table"
        self._update_gravity(a, w, dt, wmag)
        m = math.dist(a, self.prev_a)
        self.prev_a = list(a)
        self._update_bias(t, w_raw, wmag)
        self._update_meter(t, m, wmag)

        if self.capture is not None:
            if self.capture_end is None:
                self.capture_end = t + self.capture_len
            self.capture.append((t, tuple(a), tuple(w), m))
            if t >= self.capture_end:
                samples, cb = self.capture, self.capture_cb
                self.capture = None
                self.capture_cb = None
                self.reset()
                if cb:
                    cb(samples)
            return

        g = self.g
        gn = _norm(g) or 1.0
        # ---- clutch: palm up and hold
        if g[2] / gn < PALM_UP_RATIO:
            if self.palm_since is None:
                self.palm_since = t
            elif not self.palm_fired and t - self.palm_since >= c["palm_hold_ms"]:
                self.palm_fired = True
                self.set_active(not self.active)
            self.taps.clear()
            return
        if self.palm_since is not None:
            self.freeze_until = max(self.freeze_until, t + 300)  # turning the hand back over
        self.palm_since = None
        self.palm_fired = False

        if not self.active:
            return

        up = (g[0] / gn, g[1] / gn, g[2] / gn)
        if self.dragging and t - self.drag_since > DRAG_MAX_MS:
            self._drop(t, "timeout")
        if not self.dragging and self._posture(t, up):
            return
        yaw, pitch, roll = self._world_rates(w, up)
        self._flick(t, pitch, roll)
        if self.table:
            self._table_velocity(t, dt, a, up, wmag)
        self._pinch(t, m, wmag)
        self._resolve_taps(t)
        if self.table:
            self._table_cursor(t, dt, yaw)
        else:
            self._cursor(t, dt, yaw, pitch)

    # ------------------------------------------------------------- internals
    def _update_gravity(self, a, w, dt, wmag=0.0):
        """Gyro-propagated gravity with a slow accelerometer correction (complementary filter).
        Stays accurate while the arm is moving, unlike a plain low-pass of the accelerometer."""
        g = self.g
        # a world-fixed vector seen from the rotating watch: dg/dt = g x w
        cx = _cross(g, w)
        for i in range(3):
            g[i] += cx[i] * dt
        an = _norm(a)
        if 0.6 * G < an < 1.4 * G:  # only trust the accelerometer when it's mostly gravity
            gn = _norm(g) or 1.0
            agree = _dot(a, g) / (an * gn)
            tau = 0.6 if agree > 0.5 else 0.08  # way off (e.g. missed rotation): re-sync fast
            if getattr(self, "table", False) and agree > 0.5:
                # sliding on a desk barely rotates the watch, so trust the accelerometer only when
                # it's clearly just gravity; otherwise the slide itself would leak into "down"
                tau = 0.15 if (abs(an - G) < 0.12 and wmag < 0.08) else 3.0
            k = dt / (tau + dt)
            for i in range(3):
                g[i] += k * (a[i] - g[i])
        # keep the estimate sane no matter what the sensors do
        gn = _norm(g)
        if not math.isfinite(gn) or gn < 1e-6:
            self.g = list(a) if _norm(a) > 1e-6 else [0.0, 0.0, G]
        elif gn > 1.5 * G:
            for i in range(3):
                g[i] *= 1.5 * G / gn

    @staticmethod
    def _world_rates(w, up):
        """yaw: + = turning left (counter-clockwise seen from above)
        pitch: + = forearm rising; roll: twist around the forearm."""
        yaw = _dot(w, up)
        h = _cross(FOREARM, up)           # horizontal axis across the forearm
        hn = _norm(h)
        pitch = _dot(w, h) / max(hn, 0.35)
        roll = _dot(w, FOREARM)
        return yaw, pitch, roll

    def _posture(self, t, up):
        """Watch raised to the mouth -> voice typing. Returns True while raised (no other gestures)."""
        c = self.cfg
        if c["voice_trigger"] not in ("raise", "both"):
            self.raised = False
            return False
        sx = -1.0 if c["invert_y"] else 1.0   # flipped watch: the hand is towards -x
        fx, fz = sx * up[0], abs(up[2])
        if not self.raised:
            if fx > RAISE_ON_X and fz < RAISE_ON_Z:
                self.raised = True
                self.raise_since = t
                self.raise_fired = False
                self.taps.clear()
        elif fx < RAISE_OFF_X or fz > RAISE_OFF_Z:
            self.raised = False
            self.calm_until = t + 500
            self.freeze_until = max(self.freeze_until, t + 500)
            self.history.clear()
            if self.raise_fired:
                self.notify("lowered")
            return False
        if not self.raised:
            return False
        if not self.raise_fired and t - self.raise_since >= c["raise_hold_ms"]:
            self.raise_fired = True
            # put the cursor and page back where they were before you lifted your arm
            self._undo(t, RAISE_UNDO_MS + c["raise_hold_ms"])
            for st, n in list(self.scrolls):
                if st >= t - RAISE_UNDO_MS - c["raise_hold_ms"]:
                    self.out.scroll(-n)
            self.scrolls.clear()
            self.notify("dictate")
        return True

    def _update_bias(self, t, w_raw, wmag):
        # slowly learn gyro drift while the hand is very still
        if wmag < 0.06:
            if self.still_since is None:
                self.still_since = t
            elif t - self.still_since > 1500:
                for i in range(3):
                    self.bias[i] += 0.01 * (w_raw[i] - self.bias[i])
        else:
            self.still_since = None

    def _update_meter(self, t, m, wmag):
        if wmag <= self.cfg["pinch_max_gyro"] and m > self.live_peak:
            self.live_peak = m
        elif self._peak_decay_t is None or t - self._peak_decay_t > 50:
            self._peak_decay_t = t
            self.live_peak *= 0.9

    def _move(self, t, dx, dy):
        """Outputs that can smooth playback get the watch timestamp too."""
        mv = getattr(self.out, "move_at", None)
        if mv is not None:
            mv(t, dx, dy)
        else:
            self.out.move(dx, dy)

    def _undo(self, t, ms):
        """Move the cursor back by whatever was applied in the last `ms` milliseconds."""
        ux = uy = 0.0
        while self.history and self.history[-1][0] >= t - ms:
            _, dx, dy = self.history.pop()
            ux += dx
            uy += dy
        if ux or uy:
            self._move(t, -ux, -uy)

    def _flick(self, t, pitch, roll):
        c = self.cfg
        # positive = "scroll down" gesture
        v = roll if c["scroll_gesture"] == "twist" else -pitch
        thr = c["flick_threshold"]
        if t < self.calm_until or self.dragging:
            return
        if abs(v) >= thr:
            d = 1 if v > 0 else -1
            if not self.flick_active:
                is_return = d == -self.flick_dir and t < self.flick_until
                too_soon = d == self.flick_dir and t < self.flick_block_same
                if not is_return and not too_soon:
                    self.flick_active = True
                    self.flick_dir = d
                    notches = -d * int(c["scroll_notches"])   # flick down -> wheel down
                    if c["invert_scroll"]:
                        notches = -notches
                    self._undo(t, FLICK_UNDO_MS)
                    self.out.scroll(notches)
                    self.scrolls.append((t, notches))
                    while self.scrolls and self.scrolls[0][0] < t - 3000:
                        self.scrolls.popleft()
                    self.notify("scroll", notches=notches)
                    self.flick_until = t + FLICK_RETURN_MS
                    self.flick_block_same = t + FLICK_REPEAT_MS
                    self.taps.clear()
                    self.freeze_until = max(self.freeze_until, t + FLICK_RETURN_MS)
            self.freeze_until = max(self.freeze_until, t + 150)
        elif self.flick_active and abs(v) < thr * 0.5:
            self.flick_active = False

    def _pinch(self, t, m, wmag):
        c = self.cfg
        thr = c["pinch_threshold"]
        rm = self.recent_m
        rm.append((t, m))
        while rm and rm[0][0] < t - 300:
            rm.popleft()
        if m < thr or wmag > c["pinch_max_gyro"]:
            return
        if self.table and math.hypot(self.vf, self.vr) > TABLE_CLICK_MAX_V:
            return  # starting/stopping a slide can jolt like a pinch
        if self.pinch_blocked():
            return  # the watch's own vibration shakes the accelerometer
        if t < self.calm_until:
            return
        if t - self.last_pinch_t < PINCH_REFRACTORY_MS or t < self.flick_until:
            return
        prior = max((mm for tt, mm in rm if t - 140 <= tt <= t - 40), default=0.0)
        if prior >= 0.6 * thr:
            return  # part of a longer shake, not a crisp tap
        self.last_pinch_t = t
        self._undo(t, PINCH_UNDO_MS)  # keep the cursor where you aimed
        self.freeze_until = max(self.freeze_until, t + PINCH_REFRACTORY_MS)
        if self.dragging:
            self._drop(t, "pinch")    # pinch while dragging = let go, right away
            return
        self.taps.append(t)

    def _act(self, t, name, *args):
        """Button actions go through the same timeline as cursor movement (see SmoothMover)."""
        at = getattr(self.out, "act_at", None)
        if at is not None and t is not None:
            at(t, name, *args)
        else:
            getattr(self.out, name)(*args)

    def _moved_since(self, t0):
        mx = my = 0.0
        for ht, dx, dy in self.history:
            if ht > t0:
                mx += dx
                my += dy
        return mx, my

    def _start_drag(self, t, from_tap=None):
        """Press the left button. If the drag was recognised from movement after a double pinch,
        the press happens back where the pinch was and the movement is replayed on top."""
        mx = my = 0.0
        if from_tap is not None:
            mx, my = self._moved_since(from_tap)
            if mx or my:
                self._move(t, -mx, -my)
        self._act(t, "press")
        if mx or my:
            self._move(t, mx, my)
        self.dragging = True
        self.drag_since = t if t is not None else 0.0
        self.taps.clear()
        self.notify("drag")

    def _drop(self, t, why):
        self.dragging = False
        self._act(t, "release")
        self.notify("drop", why=why)

    def set_drag(self, on: bool):
        """Voice commands 'grab' / 'drop'."""
        if on and not self.dragging and self.active:
            self._start_drag(self.last_t)
        elif not on and self.dragging:
            self._drop(self.last_t, "voice")

    def _slid_after(self, t0):
        """Table mode: did the hand start sliding right after this 'tap'? Then it was the jolt of
        a slide starting, not a pinch."""
        return any(t0 <= st <= t0 + 150 and v > TABLE_CLICK_MAX_V for st, v in self.speeds)

    def _resolve_taps(self, t):
        if not self.taps:
            return
        c = self.cfg
        if self.table and len(self.taps) == 1:
            # a lone "tap" followed by sliding is the jolt of the slide starting, not a pinch
            # (after a double pinch, sliding is a drag, so that case is left alone)
            if t - self.taps[0] < 150:
                return
            if self._slid_after(self.taps[0]):
                self.taps.clear()
                return
        if len(self.taps) == 2 and c["drag_enabled"]:
            mx, my = self._moved_since(self.taps[-1])
            if math.hypot(mx, my) >= c["drag_start_px"]:
                self._start_drag(t, from_tap=self.taps[-1])
                return
        if t - self.taps[-1] <= c["multi_tap_window_ms"]:
            return
        n = len(self.taps)
        self.taps.clear()
        if n == 1:
            self._act(t, "click", "left")
            self.notify("click", button="left")
        elif n == 2 or c["voice_trigger"] == "raise":
            self._act(t, "click", "right")   # 3+ pinches count as a right click unless triple = voice
            self.notify("click", button="right")
        else:
            self.notify("dictate")

    def _shape(self, v, lim):
        c = self.cfg
        s = min(abs(v), lim) - c["cursor_deadzone"]
        if s <= 0:
            return 0.0
        return math.copysign(c["cursor_gain"] * s * (1.0 + c["cursor_accel"] * s), v)

    def _cursor(self, t, dt, yaw, pitch):
        c = self.cfg
        # very light smoothing (~10 ms) to take the edge off sensor noise
        self.rx = 0.5 * self.rx + 0.5 * (-yaw)
        self.ry = 0.5 * self.ry + 0.5 * (-pitch)
        while self.history and self.history[0][0] < t - 2500:
            self.history.popleft()
        if t < self.freeze_until:
            return
        # in flick mode, vertical speed is capped below the flick threshold so flicks stay flicks
        ylim = 0.7 * c["flick_threshold"] if c["scroll_gesture"] != "twist" else 12.0
        dx = self._shape(self.rx, 12.0) * dt
        dy = self._shape(self.ry, ylim) * dt * c["cursor_y_gain"]
        if c["invert_x"]:
            dx = -dx
        if c["invert_y"]:
            dy = -dy
        if not (math.isfinite(dx) and math.isfinite(dy)):
            self.rx = self.ry = 0.0
            return
        if dx or dy:
            self._move(t, dx, dy)
            self.history.append((t, dx, dy))

    # ------------------------------------------------------------ table mode
    def _table_axes(self, up):
        """Forward (towards your fingers) and right, both flat on the desk, in watch coordinates."""
        sx = -1.0 if self.cfg["invert_y"] else 1.0   # watch worn flipped: fingers are towards -x
        d = sx * up[0]
        f = (sx - d * up[0], -d * up[1], -d * up[2])
        fn = _norm(f)
        if fn < 0.4:
            return None   # forearm pointing up/down: not lying on a desk
        f = (f[0] / fn, f[1] / fn, f[2] / fn)
        return f, _cross(f, up)

    def _table_velocity(self, t, dt, a, up, wmag):
        axes = self._table_axes(up)
        if axes is None:
            self.vf = self.vr = 0.0
            return
        f, r = axes
        g = self.g
        lin = (a[0] - g[0], a[1] - g[1], a[2] - g[2])   # acceleration with gravity removed
        an = _norm(a)
        if abs(an - G) < 0.3 and wmag < 0.25 and _norm(lin) < 0.3:
            if self.table_quiet_since is None:
                self.table_quiet_since = t
            elif t - self.table_quiet_since >= TABLE_STILL_MS:
                self.vf = self.vr = 0.0          # hand has stopped: kill any integration drift
                return
        else:
            self.table_quiet_since = None
        leak = math.exp(-dt / TABLE_LEAK_S)
        self.vf = self.vf * leak + _dot(lin, f) * dt
        self.vr = self.vr * leak + _dot(lin, r) * dt
        lim = 3.0
        self.vf = max(-lim, min(lim, self.vf))
        self.vr = max(-lim, min(lim, self.vr))
        sp = self.speeds
        sp.append((t, math.hypot(self.vf, self.vr)))
        while sp and sp[0][0] < t - 1500:
            sp.popleft()

    def _table_shape(self, v):
        c = self.cfg
        s = abs(v) - TABLE_DEADZONE
        if s <= 0:
            return 0.0
        # mouse-style acceleration: slow = precise, fast = covers the screen
        return math.copysign(c["table_gain"] * s * (1.0 + c["table_accel"] * s / 0.25), v)

    def _table_cursor(self, t, dt, yaw):
        c = self.cfg
        while self.history and self.history[0][0] < t - 2500:
            self.history.popleft()
        if t < self.freeze_until:
            return
        dx = self._table_shape(self.vr) * dt
        dy = -self._table_shape(self.vf) * dt * c["table_y_gain"]   # forward = up the screen
        if c["invert_x"]:
            dx = -dx
        if not (math.isfinite(dx) and math.isfinite(dy)):
            self.vf = self.vr = 0.0
            return
        if dx or dy:
            self._move(t, dx, dy)
            self.history.append((t, dx, dy))

    def set_mode(self, mode: str):
        if mode == self.cfg["cursor_mode"]:
            return
        self.cfg["cursor_mode"] = mode
        self.vf = self.vr = 0.0
        self.notify("mode", mode=mode)

    def flush(self):
        """Resolve any waiting taps immediately (e.g. when the stream stops)."""
        if self.taps:
            self._resolve_taps(self.taps[-1] + self.cfg["multi_tap_window_ms"] + 1)
        self.history.clear()
