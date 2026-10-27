"""Mouse and keyboard output. WinOutput drives Windows; DryOutput just records (tests / other OSes)."""
import logging
import sys
import time

from .engine import Output

log = logging.getLogger("wristmouse")

VK = {
    "enter": 0x0D, "backspace": 0x08, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "win": 0x5B,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "delete": 0x2E,
    "f4": 0x73, "f5": 0x74,
}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E, 0x5B}


def vk_for(name: str) -> int:
    name = name.lower()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ValueError("unknown key " + name)


class DryOutput(Output):
    def __init__(self):
        self.actions = []
        self.x = 0.0
        self.y = 0.0

    def move(self, dx, dy):
        self.x += dx
        self.y += dy
        self.actions.append(("move", dx, dy))

    def click(self, button="left", count=1):
        self.actions.append(("click", button, count))

    def scroll(self, notches):
        self.actions.append(("scroll", notches))

    def key(self, *keys):
        self.actions.append(("key",) + tuple(keys))

    def type_text(self, text):
        self.actions.append(("type", text))

    def of(self, kind):
        return [a for a in self.actions if a[0] == kind]


class WinOutput(Output):
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ct = ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32 = user32
        try:  # move the cursor in real screen pixels on scaled displays
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.INPUT, self.MOUSEINPUT, self.KEYBDINPUT = INPUT, MOUSEINPUT, KEYBDINPUT
        user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        user32.SendInput.restype = wintypes.UINT
        user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self.POINT = wintypes.POINT
        self.rx = 0.0
        self.ry = 0.0

    # -------------------------------------------------------------- helpers
    def _send(self, inputs):
        n = len(inputs)
        arr = (self.INPUT * n)(*inputs)
        sent = self.user32.SendInput(n, arr, self.ct.sizeof(self.INPUT))
        if sent != n:
            log.debug("SendInput sent %d/%d", sent, n)

    def _mouse(self, flags, data=0):
        i = self.INPUT(type=0)
        i.mi = self.MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
        return i

    def _key(self, vk=0, scan=0, flags=0):
        i = self.INPUT(type=1)
        i.ki = self.KEYBDINPUT(vk, scan, flags, 0, 0)
        return i

    # --------------------------------------------------------------- output
    def move(self, dx, dy):
        # SetCursorPos bypasses Windows pointer acceleration, so motion is predictable
        self.rx += dx
        self.ry += dy
        ix, iy = int(self.rx), int(self.ry)
        if not ix and not iy:
            return
        self.rx -= ix
        self.ry -= iy
        p = self.POINT()
        if self.user32.GetCursorPos(self.ct.byref(p)):
            self.user32.SetCursorPos(p.x + ix, p.y + iy)

    def click(self, button="left", count=1):
        down, up = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}[button]
        for _ in range(count):
            self._send([self._mouse(down), self._mouse(up)])
            if count > 1:
                time.sleep(0.05)

    def scroll(self, notches):
        self._send([self._mouse(0x0800, int(notches) * 120)])

    def key(self, *keys):
        vks = [vk_for(k) for k in keys]
        downs = [self._key(v, 0, 0x1 if v in EXTENDED else 0) for v in vks]
        ups = [self._key(v, 0, (0x1 if v in EXTENDED else 0) | 0x2) for v in reversed(vks)]
        self._send(downs + ups)

    def type_text(self, text):
        # KEYEVENTF_UNICODE types any character regardless of keyboard layout
        batch = []
        data = text.replace("\r\n", "\n").encode("utf-16-le")
        units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        for u in units:
            if u == 0x0A:
                if batch:
                    self._send(batch)
                    batch = []
                self.key("enter")
                continue
            batch.append(self._key(0, u, 0x4))
            batch.append(self._key(0, u, 0x4 | 0x2))
            if len(batch) >= 64:
                self._send(batch)
                batch = []
        if batch:
            self._send(batch)


def make_output(dry: bool = False) -> Output:
    if dry or sys.platform != "win32":
        return DryOutput()
    return WinOutput()
