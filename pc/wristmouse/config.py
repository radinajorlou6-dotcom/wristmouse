"""Settings, stored as JSON in %APPDATA%\\WristMouse\\config.json."""
import copy
import json
import logging
import os
import sys
import threading

APP_NAME = "WristMouse"
VERSION = "1.0.0"

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
