"""Turns raw watch motion (accelerometer + gyroscope) into mouse actions.

Gestures
    palm up, hold ~1s ........ pause / resume the mouse (the "clutch")
    tilt / sweep the hand .... move the cursor (gyro air mouse)
    single finger pinch ...... left click
    double pinch ............. right click
    triple pinch ............. start voice typing
    quick wrist flick ........ scroll (down flick = scroll down)

Units: acceleration in m/s^2, angular velocity in rad/s, time in milliseconds
(watch clock).
"""
import collections
import math

CURSOR_DELAY_MS = 60      # cursor moves are held this long so pinch/flick jitter can be dropped
PINCH_REFRACTORY_MS = 150
FLICK_RETURN_MS = 450     # ignore the opposite "return swing" after a flick for this long
FLICK_REPEAT_MS = 150
PALM_UP_RATIO = -0.7      # gravity z / |g| below this = screen facing the floor
GAP_RESET_MS = 250


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
        self.live_peak = 0.0        # recent pinch strength, for the settings meter
        self.pinch_blocked = lambda: False   # hub sets this: True while the watch is vibrating
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
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
        self.queue = collections.deque()
        self.recent_m = collections.deque()
        self.still_since = None
        self._peak_decay_t = None

    def set_active(self, active: bool, t=None):
        if active == self.active:
            return
        self.active = active
        self.taps.clear()
        self.queue.clear()
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
            self.queue.clear()
            return
        dt = (t - self.last_t) / 1000.0
        self.last_t = t

        g = self.g
        k = dt / (0.3 + dt)
        for i in range(3):
            g[i] += k * (a[i] - g[i])
        m = math.dist(a, self.prev_a)
        self.prev_a = list(a)

        w = [w_raw[i] - self.bias[i] for i in range(3)]
        wmag = math.sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2])
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

        # ---- clutch: palm up and hold
        gn = math.sqrt(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]) or 1.0
        if g[2] / gn < PALM_UP_RATIO:
            if self.palm_since is None:
                self.palm_since = t
            elif not self.palm_fired and t - self.palm_since >= c["palm_hold_ms"]:
                self.palm_fired = True
                self.set_active(not self.active)
            self.taps.clear()
            self.queue.clear()
            return
        if self.palm_since is not None:
            self.freeze_until = max(self.freeze_until, t + 300)  # turning the hand back over
        self.palm_since = None
        self.palm_fired = False

        if not self.active:
            return

        self._flick(t, w)
        self._pinch(t, m, wmag)
        self._resolve_taps(t)
        self._cursor(t, dt, w)

    # ------------------------------------------------------------- internals
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

    def _flick(self, t, w):
        c = self.cfg
        ax, sign = c["scroll_axis"]
        v = w[ax] * sign
        thr = c["flick_threshold"]
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
                    self.out.scroll(notches)
                    self.notify("scroll", notches=notches)
                    self.flick_until = t + FLICK_RETURN_MS
                    self.flick_block_same = t + FLICK_REPEAT_MS
                    self.taps.clear()
                    self.queue.clear()
                    self.freeze_until = max(self.freeze_until, t + FLICK_RETURN_MS)
            self.freeze_until = max(self.freeze_until, t + 150)
            self.queue.clear()
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
        if self.pinch_blocked():
            return  # the watch's own vibration shakes the accelerometer
        if t - self.last_pinch_t < PINCH_REFRACTORY_MS or t < self.flick_until:
            return
        prior = max((mm for tt, mm in rm if t - 140 <= tt <= t - 40), default=0.0)
        if prior >= 0.6 * thr:
            return  # part of a longer shake, not a crisp tap
        self.last_pinch_t = t
        self.taps.append(t)
        # throw away the cursor jitter the tap caused
        while self.queue and self.queue[-1][0] >= t - 80:
            self.queue.pop()
        self.freeze_until = max(self.freeze_until, t + PINCH_REFRACTORY_MS)

    def _resolve_taps(self, t):
        if not self.taps or t - self.taps[-1] <= self.cfg["multi_tap_window_ms"]:
            return
        n = len(self.taps)
        self.taps.clear()
        if n == 1:
            self.out.click("left")
            self.notify("click", button="left")
        elif n == 2:
            self.out.click("right")
            self.notify("click", button="right")
        else:
            self.notify("dictate")

    def _shape(self, v):
        c = self.cfg
        lim = 0.7 * c["flick_threshold"]
        s = min(abs(v), lim) - c["cursor_deadzone"]
        if s <= 0:
            return 0.0
        return math.copysign(c["cursor_gain"] * s * (1.0 + c["cursor_accel"] * s), v)

    def _cursor(self, t, dt, w):
        c = self.cfg
        axx, sx = c["cursor_axis_x"]
        axy, sy = c["cursor_axis_y"]
        if t >= self.freeze_until:
            dx = self._shape(w[axx] * sx) * dt
            dy = self._shape(w[axy] * sy) * dt
            if dx or dy:
                self.queue.append((t, dx, dy))
        mx = my = 0.0
        q = self.queue
        while q and q[0][0] <= t - CURSOR_DELAY_MS:
            _, qx, qy = q.popleft()
            mx += qx
            my += qy
        if mx or my:
            self.out.move(mx, my)

    def flush(self):
        """Resolve any waiting taps immediately (e.g. when the stream stops)."""
        if self.taps:
            self._resolve_taps(self.taps[-1] + self.cfg["multi_tap_window_ms"] + 1)
        self.queue.clear()
