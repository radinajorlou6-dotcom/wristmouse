"""Installs the bundled watch app over Wi-Fi debugging (adb), plus small Windows helpers."""
import io
import logging
import os
import re
import subprocess
import sys
import urllib.request
import zipfile

from .config import APP_NAME, data_dir, resource_path

log = logging.getLogger("wristmouse")
PACKAGE = "com.radin.wristmouse"
ADB_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
APK_NAME = "WristMouse-watch.apk"


def bundled_apk():
    for p in (resource_path(os.path.join("bundle", APK_NAME)),
              os.path.join(os.path.dirname(sys.executable), APK_NAME)):
        if os.path.exists(p):
            return p
    return None


def _no_window():
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def ensure_adb(log_fn):
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    path = os.path.join(data_dir(), "platform-tools", exe)
    if os.path.exists(path):
        return path
    log_fn("Downloading Android platform tools (adb, ~7 MB)…")
    with urllib.request.urlopen(ADB_URL, timeout=60) as r:
        data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(data_dir())
    if not os.path.exists(path):
        raise RuntimeError("adb download didn't contain %s" % exe)
    log_fn("adb ready.")
    return path


def _run(args, log_fn, timeout=60):
    log_fn("> " + " ".join(os.path.basename(a) if i == 0 else a for i, a in enumerate(args)))
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **_no_window())
    out = (p.stdout + p.stderr).strip()
    if out:
        log_fn(out)
    return p.returncode, out


def install(pair_addr, pair_code, connect_addr, log_fn):
    """pair_addr/pair_code come from the watch's 'Pair new device' screen; connect_addr from the
    main Wireless debugging screen. Returns True on success."""
    apk = bundled_apk()
    if not apk:
        raise RuntimeError("The watch app isn't bundled with this build.")
    adb = ensure_adb(log_fn)
    connect_addr = connect_addr.strip()
    if pair_addr.strip() and pair_code.strip():
        code, out = _run([adb, "pair", pair_addr.strip(), pair_code.strip()], log_fn)
        if "Successfully paired" not in out:
            raise RuntimeError("Pairing failed. Check the pairing IP:port and the 6-digit code (they change each time).")
    code, out = _run([adb, "connect", connect_addr], log_fn)
    if "connected to" not in out:
        raise RuntimeError("Couldn't connect to %s. Make sure the watch and laptop are on the same Wi-Fi." % connect_addr)
    code, out = _run([adb, "-s", connect_addr, "install", "-r", "-g", apk], log_fn, timeout=240)
    if "Success" not in out:
        raise RuntimeError("Install failed (see log above).")
    post_install(adb, connect_addr, log_fn)
    log_fn("✅ Installed! The app is open on your watch now.")
    return True


def post_install(adb, addr, log_fn):
    for perm in ("android.permission.RECORD_AUDIO", "android.permission.BLUETOOTH_ADVERTISE",
                 "android.permission.BLUETOOTH_CONNECT", "android.permission.POST_NOTIFICATIONS"):
        _run([adb, "-s", addr, "shell", "pm", "grant", PACKAGE, perm], log_fn)
    _run([adb, "-s", addr, "shell", "dumpsys", "deviceidle", "whitelist", "+" + PACKAGE], log_fn)
    _run([adb, "-s", addr, "shell", "cmd", "appops", "set", PACKAGE, "RUN_ANY_IN_BACKGROUND", "allow"], log_fn)
    start_app(adb, addr, log_fn)


def start_app(adb, addr, log_fn):
    _run([adb, "-s", addr, "shell", "am", "start", "-n", PACKAGE + "/.MainActivity"], log_fn)


def start_remote(connect_addr, log_fn):
    adb = ensure_adb(log_fn)
    code, out = _run([adb, "connect", connect_addr.strip()], log_fn)
    if "connected to" not in out:
        raise RuntimeError("Watch not reachable. Wireless debugging turns off when the watch restarts or changes Wi-Fi.")
    start_app(adb, connect_addr.strip(), log_fn)


def valid_addr(s):
    return bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}:\d{2,5}", s.strip()))


# ------------------------------------------------------------- Windows bits
def exe_path():
    return sys.executable if getattr(sys, "frozen", False) else None


def allow_firewall():
    """Adds an inbound allow rule for this exe (asks for admin)."""
    exe = exe_path()
    if sys.platform != "win32" or not exe:
        raise RuntimeError("Only available in the installed Windows app.")
    import ctypes

    args = ('advfirewall firewall add rule name="%s" dir=in action=allow program="%s" enable=yes profile=any'
            % (APP_NAME, exe))
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", args, None, 0)
    if r <= 32:
        raise RuntimeError("Windows didn't allow the change (code %d)." % r)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_enabled():
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    exe = exe_path()
    if sys.platform != "win32" or not exe:
        raise RuntimeError("Only available in the installed Windows app.")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, '"%s"' % exe)
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass
