"""Settings, stored as JSON in %APPDATA%\\WristMouse\\config.json."""
import copy
import json
import logging
import os
import sys
import threading

APP_NAME = "WristMouse"
VERSION = "1.2.0"

log = logging.getLogger("wristmouse")


def app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def resource_path(rel: str) -> str:
    """Path to a file bundled with the app (works for PyInstaller and from source)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


# Axis settings are [axis_index, sign]: 0 = watch x, 1 = watch y, 2 = watch z.
DEFAULTS = {
    "connection": "auto",          # auto | wifi | bluetooth
    "tcp_port": 47320,
    "udp_port": 47321,
    # cursor (gyro air mouse)
    "cursor_axis_x": [2, -1],      # rotation that moves the cursor right
    "cursor_axis_y": [1, 1],       # rotation that moves the cursor down
    "cursor_gain": 900.0,          # pixels per radian
    "cursor_accel": 0.5,
    "cursor_deadzone": 0.04,       # rad/s
    "cursor_y_scale": 2.0,         # up/down is boosted: wrists bend less up/down than they sweep sideways
    # pinch taps
    "pinch_threshold": 1.6,        # m/s^2 change between two samples
    "pinch_max_gyro": 1.2,         # rad/s; hand must be fairly still for a pinch
    "multi_tap_window_ms": 350,
    # wrist flick scrolling
    "scroll_axis": [1, 1],         # rotation that means "flick down"
    "flick_threshold": 5.0,        # rad/s
    "scroll_notches": 3,
    "invert_scroll": False,
    # clutch
    "palm_hold_ms": 800,
    "start_active": True,
    # voice
    "voice_model": "base.en",
    "voice_silence_ms": 1100,
    "voice_max_s": 25,
    "voice_min_level": 500,
    # misc
    "haptics": True,
    "watch_adb_address": "",
}


class Config:
    def __init__(self, path: str | None = None, persist: bool = True):
        self.path = path or os.path.join(app_dir(), "config.json")
        self.persist = persist
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULTS)
        if persist:
            self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k in DEFAULTS:
                    self._data[k] = v
        except FileNotFoundError:
            pass
        except Exception as e:  # corrupt file: keep defaults
            log.warning("Could not read config: %s", e)
        self.fix_axes()

    def fix_axes(self):
        """Left/right and up/down must use different rotation axes, or the cursor only moves
        diagonally (older versions of the setup wizard could save that)."""
        x, y = self._data["cursor_axis_x"], self._data["cursor_axis_y"]
        if x[0] == y[0]:
            dy = DEFAULTS["cursor_axis_y"]
            self._data["cursor_axis_y"] = list(dy) if dy[0] != x[0] else [next(i for i in range(3) if i != x[0]), 1]

    def save(self):
        if not self.persist:
            return
        with self._lock:
            data = copy.deepcopy(self._data)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def __getitem__(self, k):
        return self._data[k]

    def __setitem__(self, k, v):
        with self._lock:
            self._data[k] = v

    def get(self, k, default=None):
        return self._data.get(k, default)

    def reset(self, keys=None):
        with self._lock:
            for k in keys or DEFAULTS.keys():
                self._data[k] = copy.deepcopy(DEFAULTS[k])
