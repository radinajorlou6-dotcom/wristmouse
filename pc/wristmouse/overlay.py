"""A small always-on-top label that shows the action you just did (click, scroll, voice...).

It never takes focus and clicks pass straight through it. It's kept on screen at all times
and hidden by making it fully transparent, because showing and hiding a window can steal focus
on Windows.
"""
import queue
import sys
import time
import tkinter as tk

STYLE = {
    # kind: (symbol, background)
    "left": ("●", "#2563eb"),
    "right": ("●●", "#7c3aed"),
    "scroll": ("↕", "#374151"),
    "on": ("▶", "#15803d"),
    "off": ("⏸", "#b91c1c"),
    "voice": ("🎤", "#047857"),
    "typed": ("⌨", "#047857"),
    "command": ("⌘", "#047857"),
    "cancel": ("✕", "#6b7280"),
    "drag": ("✊", "#c2410c"),
    "mode": ("⇄", "#0e7490"),
    "drop": ("✋", "#c2410c"),
}
SHOW_MS = 900
FADE_MS = 250
ALPHA = 0.92
OFFSET = (22, 26)   # below-right of the cursor


class Overlay:
    def __init__(self, root, cfg, events: queue.Queue):
        self.root = root
        self.cfg = cfg
        self.events = events
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.0)
        self.label = tk.Label(self.win, text="", fg="white", bg="#374151",
                              font=("Segoe UI", 11, "bold"), padx=10, pady=4)
        self.label.pack()
        self.win.geometry("+0+0")
        self.win.update_idletasks()
        self.hwnd = None
        self._make_click_through()
        self.text = ""
        self.count = 0
        self.sticky = False
        self.shown_at = 0.0
        self.visible = False
        self.root.after(30, self._poll)

    def _make_click_through(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            GWL_EXSTYLE = -20
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # layered | transparent (click-through) | toolwindow (no taskbar) | noactivate | topmost
            ex |= 0x80000 | 0x20 | 0x80 | 0x08000000 | 0x8
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            self.hwnd = hwnd
            self.win.attributes("-alpha", 0.0)  # re-apply now that the style changed
        except Exception:
            self.hwnd = None

    def _raise_without_focus(self):
        if self.hwnd:
            import ctypes

            # HWND_TOPMOST, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            ctypes.windll.user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)

    def _screen_box(self):
        if sys.platform == "win32":
            try:
                import ctypes

                gm = ctypes.windll.user32.GetSystemMetrics
                x, y, w, h = gm(76), gm(77), gm(78), gm(79)  # whole virtual desktop
                if w and h:
                    return x, y, x + w, y + h
            except Exception:
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _place(self):
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        x0, y0, x1, y1 = self._screen_box()
        if self.cfg["overlay"] == "corner":
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            x, y = sw - w - 24, sh - h - 70   # above the taskbar
        else:
            px, py = self.root.winfo_pointerxy()
            x, y = px + OFFSET[0], py + OFFSET[1]
            if x + w > x1:
                x = px - w - OFFSET[0]
            if y + h > y1:
                y = py - h - OFFSET[1]
        x = max(x0, min(x, x1 - w))
        y = max(y0, min(y, y1 - h))
        self.win.geometry("+%d+%d" % (x, y))

    def show(self, kind, text, sticky=False):
        if self.cfg["overlay"] == "off":
            return
        now = time.monotonic()
        # repeated identical actions (scrolling) count up instead of flickering
        if self.visible and not self.sticky and text == self.text and now - self.shown_at < 1.5:
            self.count += 1
        else:
            self.count = 1
            self.text = text
            self._place()
        sym, bg = STYLE.get(kind, ("•", "#374151"))
        label = "%s  %s" % (sym, text) + ("  ×%d" % self.count if self.count > 1 else "")
        self.label.config(text=label, bg=bg)
        self.sticky = sticky
        self.shown_at = now
        self.visible = True
        self.win.attributes("-alpha", ALPHA)
        self._raise_without_focus()

    def _poll(self):
        try:
            while True:
                kind, text, sticky = self.events.get_nowait()
                self.show(kind, text, sticky)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if self.visible and not self.sticky:
            age = (time.monotonic() - self.shown_at) * 1000
            if age > SHOW_MS + FADE_MS:
                self.win.attributes("-alpha", 0.0)
                self.visible = False
            elif age > SHOW_MS:
                self.win.attributes("-alpha", ALPHA * (1 - (age - SHOW_MS) / FADE_MS))
        if self.visible and self.cfg["overlay"] == "off":
            self.win.attributes("-alpha", 0.0)
            self.visible = False
        self.root.after(30, self._poll)

    def test(self):
        self.show("left", "Left click (overlay test)")
