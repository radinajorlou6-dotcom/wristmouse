"""The WristMouse window (tkinter)."""
import logging
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import calibrate, watchsetup
from .config import APP_NAME, DEFAULTS, VERSION
from .transports import local_ipv4s

CHEATSHEET = (
    "Palm up, hold 1 s  →  pause / resume mouse\n"
    "Point / swing your forearm  →  move cursor\n"
    "Pinch (thumb + index)  →  left click\n"
    "Double pinch  →  right click\n"
    "Raise watch to your mouth  →  voice typing (lower your arm when done)\n"
    "Quick flick down / up (or twist)  →  scroll"
)

VOICE_HELP = (
    "Anything you say is typed. If the whole phrase is one of these, it runs instead:\n"
    "enter / new line · backspace · delete · tab · escape · space\n"
    "select all · copy · paste · cut · undo · redo · save · find\n"
    "delete that (removes what was just typed) · delete word\n"
    "new tab · close tab · next tab · previous tab · go back · go forward · refresh\n"
    "switch window · minimize · maximize · show desktop · start menu\n"
    "click · right click · double click · scroll up · scroll down\n"
    "pause mouse · start mouse · cancel\n"
    'Say "type …" to type words that are commands, e.g. "type copy".'
)

WATCH_STEPS = (
    "One-time setup (about 5 minutes):\n"
    "1. Watch: Settings → About watch → Software information → tap “Software version” 5–7 times\n"
    "   until it says Developer mode is on.\n"
    "2. Watch: Settings → Developer options → turn on “ADB debugging” and “Wireless debugging”.\n"
    "   (Watch and laptop must be on the same Wi-Fi.)\n"
    "3. Watch: Wireless debugging → “Pair new device”. Type the pairing IP:port and code below.\n"
    "4. Type the IP:port shown on the main Wireless debugging screen into “Connect address”.\n"
    "5. Click Install. Permissions are granted automatically and the app opens on the watch."
)


class App:
    def __init__(self, core, cfg):
        self.core = core
        self.cfg = cfg
        self.results = queue.Queue()
        self.root = tk.Tk()
        self.root.title("%s %s" % (APP_NAME, VERSION))
        self.root.geometry("600x720")
        self.root.minsize(520, 600)
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
        self.vars = {}
        log = logging.getLogger("wristmouse")
        self.root.report_callback_exception = lambda t, v, tb: log.error("window error", exc_info=(t, v, tb))
        self._build()
        self.overlay = None
        try:
            from .overlay import Overlay

            self.overlay = Overlay(self.root, cfg, core.hub.overlay)
        except Exception:
            log.exception("overlay failed to start")
        self.root.after(150, self._refresh)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------- layout
    def _build(self):
        r = self.root
        top = ttk.Frame(r, padding=12)
        top.pack(fill="x")
        self.conn_lbl = ttk.Label(top, text="Waiting for the watch…", font=("Segoe UI", 13, "bold"))
        self.conn_lbl.pack(anchor="w")
        row = ttk.Frame(top)
        row.pack(fill="x", pady=(6, 0))
        self.mouse_lbl = ttk.Label(row, text="Mouse: –", font=("Segoe UI", 11))
        self.mouse_lbl.pack(side="left")
        self.toggle_btn = ttk.Button(row, text="Pause", command=self._toggle_mouse)
        self.toggle_btn.pack(side="right")
        self.voice_lbl = ttk.Label(top, text="Voice: –")
        self.voice_lbl.pack(anchor="w", pady=(4, 0))
        self.event_lbl = ttk.Label(top, text="", foreground="#555", wraplength=560)
        self.event_lbl.pack(anchor="w", pady=(4, 0))

        meter = ttk.Frame(top)
        meter.pack(fill="x", pady=(8, 0))
        ttk.Label(meter, text="Pinch strength").pack(side="left")
        self.meter = tk.Canvas(meter, height=16, highlightthickness=0, bg="#e8e8e8")
        self.meter.pack(side="left", fill="x", expand=True, padx=8)

        ttk.Label(top, text=CHEATSHEET, justify="left", foreground="#333").pack(anchor="w", pady=(10, 0))

        nb = ttk.Notebook(r)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        nb.add(self._tab_gestures(nb), text="Gestures")
        nb.add(self._tab_connection(nb), text="Connection")
        nb.add(self._tab_voice(nb), text="Voice")
        nb.add(self._tab_watch(nb), text="Install on watch")

    def _slider(self, parent, label, key, lo, hi, fmt="{:.1f}", integer=False, help_text=""):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=4)
        ttk.Label(f, text=label, width=22).pack(side="left")
        var = tk.DoubleVar(value=float(self.cfg[key]))
        val = ttk.Label(f, width=7)

        def changed(_=None):
            v = var.get()
            v = int(round(v)) if integer else round(v, 2)
            self.cfg[key] = v
            val.config(text=fmt.format(v))

        s = ttk.Scale(f, from_=lo, to=hi, variable=var, command=changed)
        s.pack(side="left", fill="x", expand=True, padx=6)
        s.bind("<ButtonRelease-1>", lambda e: self.cfg.save())
        val.pack(side="left")
        changed()
        if help_text:
            ttk.Label(parent, text=help_text, foreground="#777").pack(anchor="w", padx=4)
        self.vars[key] = (var, changed)
        return var

    def _tab_gestures(self, nb):
        t = ttk.Frame(nb, padding=10)
        self._slider(t, "Cursor speed", "cursor_gain", 200, 3000, "{:.0f}")
        self._slider(t, "Up/down speed", "cursor_y_gain", 0.5, 4, "{:.1f}×",
                     help_text="Raise this if reaching the top/bottom of the screen takes too much arm movement.")
        self._slider(t, "Cursor acceleration", "cursor_accel", 0, 2)
        self._slider(t, "Pinch threshold", "pinch_threshold", 0.3, 6,
                     help_text="Lower = more sensitive. Use “Calibrate pinch” to set it automatically.")
        self._slider(t, "Flick strength needed", "flick_threshold", 2, 12,
                     help_text="Higher = harder flick needed to scroll (and faster cursor moves allowed).")
        self._slider(t, "Scroll amount", "scroll_notches", 1, 10, "{:.0f}", integer=True)
        self._slider(t, "Double-pinch speed (ms)", "multi_tap_window_ms", 200, 700, "{:.0f}", integer=True)

        f = ttk.Frame(t)
        f.pack(fill="x", pady=4)
        ttk.Label(f, text="Scroll by", width=22).pack(side="left")
        self.sg = tk.StringVar(value={"flick": "Flicking up/down", "twist": "Twisting the wrist"}[self.cfg["scroll_gesture"]])
        cb = ttk.Combobox(f, textvariable=self.sg, state="readonly", width=20,
                          values=["Flicking up/down", "Twisting the wrist"])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._set(
            "scroll_gesture", "twist" if self.sg.get().startswith("Twist") else "flick"))
        self.invy = tk.BooleanVar(value=self.cfg["invert_y"])
        ttk.Checkbutton(t, text="Reverse up/down (if pointing down moves the cursor up)", variable=self.invy,
                        command=lambda: self._set("invert_y", self.invy.get())).pack(anchor="w", pady=2)
        self.invx = tk.BooleanVar(value=self.cfg["invert_x"])
        ttk.Checkbutton(t, text="Reverse left/right", variable=self.invx,
                        command=lambda: self._set("invert_x", self.invx.get())).pack(anchor="w", pady=2)
        self.inv = tk.BooleanVar(value=self.cfg["invert_scroll"])
        ttk.Checkbutton(t, text="Invert scroll direction", variable=self.inv,
                        command=lambda: self._set("invert_scroll", self.inv.get())).pack(anchor="w", pady=2)
        self.hap = tk.BooleanVar(value=self.cfg["haptics"])
        ttk.Checkbutton(t, text="Vibrate the watch on clicks and mode changes", variable=self.hap,
                        command=lambda: self._set("haptics", self.hap.get())).pack(anchor="w", pady=2)
        self.smooth = tk.BooleanVar(value=self.cfg["smooth_cursor"])
        ttk.Checkbutton(t, text="Smooth cursor (evens out choppy Wi-Fi; adds a tiny delay)", variable=self.smooth,
                        command=self._smooth_changed).pack(anchor="w", pady=2)

        f = ttk.Frame(t)
        f.pack(fill="x", pady=4)
        ttk.Label(f, text="Show action on screen", width=22).pack(side="left")
        names = {"cursor": "Next to the cursor", "corner": "Bottom-right corner", "off": "Off"}
        self.ov = tk.StringVar(value=names[self.cfg["overlay"]])
        cb = ttk.Combobox(f, textvariable=self.ov, state="readonly", width=20, values=list(names.values()))
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._overlay_changed(
            {v: k for k, v in names.items()}[self.ov.get()]))

        b = ttk.Frame(t)
        b.pack(fill="x", pady=10)
        ttk.Button(b, text="Calibrate pinch", command=self._calibrate_pinch).pack(side="left")
        ttk.Button(b, text="Save motion recording", command=self._save_recording).pack(side="left", padx=6)
        ttk.Button(b, text="Reset to defaults", command=self._reset).pack(side="right")
        ttk.Label(t, foreground="#777", wraplength=540,
                  text="Movement follows your forearm relative to the room, so it works with your palm "
                       "down, sideways, or anything in between. Twisting your wrist doesn't move the cursor.").pack(anchor="w")
        return t

    def _tab_connection(self, nb):
        t = ttk.Frame(nb, padding=10)
        ttk.Label(t, text="Connect using:").pack(anchor="w")
        self.mode = tk.StringVar(value=self.cfg["connection"])
        for val, label in (("auto", "Automatic: Wi-Fi when available, otherwise Bluetooth"),
                           ("wifi", "Wi-Fi only"), ("bluetooth", "Bluetooth only")):
            ttk.Radiobutton(t, text=label, value=val, variable=self.mode, command=self._mode_changed).pack(anchor="w")
        self.wifi_lbl = ttk.Label(t, text="")
        self.wifi_lbl.pack(anchor="w", pady=(10, 0))
        self.ble_lbl = ttk.Label(t, text="")
        self.ble_lbl.pack(anchor="w")
        self.rate_lbl = ttk.Label(t, text="", foreground="#777")
        self.rate_lbl.pack(anchor="w")
        ttk.Label(t, wraplength=540, foreground="#555", justify="left", text=(
            "\nWi-Fi: the watch finds the laptop automatically when both are on the same network. "
            "Campus Wi-Fi (like eduroam) usually blocks devices from talking to each other. There, "
            "Automatic mode falls back to Bluetooth, or you can use your phone's hotspot for both.\n\n"
            "Bluetooth: no pairing needed. The watch shows up by itself. Your laptop needs Bluetooth turned on.\n\n"
            "If Windows asked about the firewall the first time, choose Allow. If you missed it, click below."
        )).pack(anchor="w")
        b = ttk.Frame(t)
        b.pack(fill="x", pady=8)
        ttk.Button(b, text="Allow through Windows Firewall", command=self._firewall).pack(side="left")
        self.auto = tk.BooleanVar(value=watchsetup.autostart_enabled())
        ttk.Checkbutton(t, text="Start WristMouse when Windows starts", variable=self.auto,
                        command=self._autostart).pack(anchor="w", pady=4)
        return t

    def _tab_voice(self, nb):
        t = ttk.Frame(nb, padding=10)
        f = ttk.Frame(t)
        f.pack(fill="x", pady=(0, 6))
        ttk.Label(f, text="Start voice typing by:").pack(side="left")
        trig = {"raise": "Raising the watch to my mouth", "triple": "Triple pinch", "both": "Either one"}
        self.trig = tk.StringVar(value=trig[self.cfg["voice_trigger"]])
        cb = ttk.Combobox(f, textvariable=self.trig, state="readonly", width=28, values=list(trig.values()))
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._set(
            "voice_trigger", {v: k for k, v in trig.items()}[self.trig.get()]))
        ttk.Label(t, foreground="#777", wraplength=540, text=(
            "Raise: bring the watch up to your mouth like you're talking into it and hold for half a "
            "second. Talk, then lower your arm and it types. Whatever the cursor did while you lifted "
            "your arm is undone. With raise on, three pinches count as a right click.")).pack(anchor="w")
        self._slider(t, "Hold at mouth for (ms)", "raise_hold_ms", 200, 1500, "{:.0f}", integer=True)
        f = ttk.Frame(t)
        f.pack(fill="x", pady=(8, 0))
        ttk.Label(f, text="Speech model:").pack(side="left")
        self.model = tk.StringVar(value=self.cfg["voice_model"])
        cb = ttk.Combobox(f, textvariable=self.model, state="readonly", width=12,
                          values=["tiny.en", "base.en", "small.en"])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._model_changed())
        ttk.Label(t, foreground="#777", wraplength=540, text=(
            "tiny = fastest, small = most accurate but slower. It downloads once (75–480 MB) "
            "and then runs offline on your laptop.")).pack(anchor="w", pady=(4, 8))
        self._slider(t, "Stop after silence (ms)", "voice_silence_ms", 500, 3000, "{:.0f}", integer=True)
        self._slider(t, "Mic sensitivity level", "voice_min_level", 100, 3000, "{:.0f}", integer=True,
                     help_text="Lower if it stops listening while you talk quietly; raise it in noisy rooms.")
        ttk.Button(t, text="Test: start listening now", command=lambda: self.core.call(self.core.hub.voice.start)
                   ).pack(anchor="w", pady=8)
        ttk.Label(t, text=VOICE_HELP, justify="left", foreground="#333", wraplength=560).pack(anchor="w")
        return t

    def _tab_watch(self, nb):
        t = ttk.Frame(nb, padding=10)
        ttk.Label(t, text=WATCH_STEPS, justify="left", wraplength=560).pack(anchor="w")
        g = ttk.Frame(t)
        g.pack(fill="x", pady=8)
        self.pair_addr = tk.StringVar()
        self.pair_code = tk.StringVar()
        self.conn_addr = tk.StringVar(value=self.cfg["watch_adb_address"])
        for i, (lbl, var) in enumerate((("Pairing IP:port", self.pair_addr), ("Pairing code", self.pair_code),
                                        ("Connect address IP:port", self.conn_addr))):
            ttk.Label(g, text=lbl).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(g, textvariable=var, width=28).grid(row=i, column=1, sticky="w", padx=6, pady=2)
        b = ttk.Frame(t)
        b.pack(fill="x")
        self.install_btn = ttk.Button(b, text="Install on watch", command=self._install)
        self.install_btn.pack(side="left")
        ttk.Button(b, text="Open app on watch", command=self._start_remote).pack(side="left", padx=6)
        if not watchsetup.bundled_apk():
            ttk.Label(t, text="(Watch app not bundled in this copy.)", foreground="#b00").pack(anchor="w")
        self.log = tk.Text(t, height=9, wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(8, 0))
        return t

    # ------------------------------------------------------------ actions
    def _set(self, key, value):
        self.cfg[key] = value
        self.cfg.save()

    def _toggle_mouse(self):
        e = self.core.hub.engine
        self.core.call(e.set_active, not e.active)

    def _mode_changed(self):
        self._set("connection", self.mode.get())
        self.core.restart_transports()

    def _model_changed(self):
        self._set("voice_model", self.model.get())
        self.core.hub.voice.transcriber.load_async(self.model.get())

    def _smooth_changed(self):
        self._set("smooth_cursor", self.smooth.get())
        out = self.core.hub.out
        if hasattr(out, "enabled"):
            out.enabled = self.smooth.get()

    def _overlay_changed(self, value):
        self._set("overlay", value)
        if self.overlay and value != "off":
            self.overlay.test()

    def _reset(self):
        if not messagebox.askyesno(APP_NAME, "Reset all gesture settings to defaults?"):
            return
        keys = [k for k in DEFAULTS if k.startswith(("cursor", "pinch", "scroll", "flick", "multi"))]
        keys += ["invert_scroll", "invert_x", "invert_y"]
        self.cfg.reset(keys)
        self.cfg.save()
        for key, (var, changed) in self.vars.items():
            var.set(float(self.cfg[key]))
            changed()
        self.inv.set(self.cfg["invert_scroll"])
        self.invx.set(self.cfg["invert_x"])
        self.invy.set(self.cfg["invert_y"])
        self.sg.set({"flick": "Flicking up/down", "twist": "Twisting the wrist"}[self.cfg["scroll_gesture"]])

    def _save_recording(self):
        import datetime
        import os

        from .config import app_dir

        d = os.path.join(app_dir(), "recordings")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "motion-%s.txt" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        n = self.core.hub.save_recording(path)
        messagebox.showinfo(APP_NAME, "Saved the last %d motion samples (~%d s) to:\n%s" % (n, n // 100, path))

    def _firewall(self):
        try:
            watchsetup.allow_firewall()
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    def _autostart(self):
        try:
            watchsetup.set_autostart(self.auto.get())
        except Exception as e:
            self.auto.set(False)
            messagebox.showerror(APP_NAME, str(e))

    # -------------------------------------------------------- calibration
    def _capture(self, title, prompt, ms, done):
        """Countdown dialog, then record `ms` of motion and call done(samples) on the UI thread."""
        if not self.core.hub.connected:
            messagebox.showwarning(APP_NAME, "Connect the watch first.")
            return
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("420x200")
        win.transient(self.root)
        win.grab_set()
        lbl = ttk.Label(win, text=prompt, wraplength=380, font=("Segoe UI", 11), justify="center")
        lbl.pack(expand=True, pady=10)
        cnt = ttk.Label(win, text="", font=("Segoe UI", 22, "bold"))
        cnt.pack(pady=10)
        token = object()
        self._capture_token = token

        def tick(n):
            if n > 0:
                cnt.config(text=str(n))
                win.after(1000, tick, n - 1)
            else:
                cnt.config(text="GO!", foreground="#0a0")
                self.core.call(self.core.hub.engine.start_capture, ms,
                               lambda samples: self.results.put((token, samples)))
                win.after(ms + 4000, timeout)

        def timeout():
            if self._capture_token is token and win.winfo_exists():
                self.core.call(setattr, self.core.hub.engine, "capture", None)
                win.destroy()
                messagebox.showerror(APP_NAME, "No data came from the watch. Is it connected?")

        def finish(samples):
            if win.winfo_exists():
                win.destroy()
            done(samples)

        self._capture_finish = (token, finish)
        tick(3)

    def _calibrate_pinch(self):
        def done(samples):
            try:
                thr, n = calibrate.pinch_threshold(samples, self.cfg["pinch_max_gyro"])
            except calibrate.CalibrationError as e:
                messagebox.showerror(APP_NAME, str(e))
                return
            self.cfg["pinch_threshold"] = thr
            self.cfg.save()
            var, changed = self.vars["pinch_threshold"]
            var.set(thr)
            changed()
            messagebox.showinfo(APP_NAME, "Found %d pinches. Threshold set to %.2f." % (n, thr))

        self._capture("Calibrate pinch",
                      "Rest your arm, then pinch your thumb and index finger together firmly 5 times "
                      "(about one per second).", 6000, done)

    # ------------------------------------------------------------- watch
    def _log(self, text):
        def put():
            self.log.insert("end", text + "\n")
            self.log.see("end")
        self.root.after(0, put)

    def _install(self):
        addr = self.conn_addr.get().strip()
        if not watchsetup.valid_addr(addr):
            messagebox.showerror(APP_NAME, "Connect address should look like 192.168.1.23:37011")
            return
        self._set("watch_adb_address", addr)
        self.install_btn.config(state="disabled")
        pa, pc = self.pair_addr.get(), self.pair_code.get()

        def work():
            try:
                watchsetup.install(pa, pc, addr, self._log)
            except Exception as e:
                self._log("❌ " + str(e))
            finally:
                self.root.after(0, lambda: self.install_btn.config(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def _start_remote(self):
        addr = self.conn_addr.get().strip()
        if not watchsetup.valid_addr(addr):
            messagebox.showerror(APP_NAME, "Fill in the connect address first.")
            return

        def work():
            try:
                watchsetup.start_remote(addr, self._log)
            except Exception as e:
                self._log("❌ " + str(e))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ refresh
    def _refresh(self):
        try:
            while True:
                token, samples = self.results.get_nowait()
                fin = getattr(self, "_capture_finish", None)
                if fin and fin[0] is token:
                    self._capture_token = None
                    self._capture_finish = None
                    fin[1](samples)
        except queue.Empty:
            pass

        s = self.core.status()
        if s["connected"] and s["rate"] < 5:
            self.conn_lbl.config(text="● Connected via %s, but no motion data yet… (fixing itself)" % s["via"],
                                 foreground="#a15c00")
        elif s["connected"]:
            self.conn_lbl.config(text="● Connected via %s%s" % (s["via"], "  (%s)" % s["watch"] if s["watch"] else ""),
                                 foreground="#0a7d2c")
        else:
            self.conn_lbl.config(text="○ Waiting for the watch… (open WristMouse on it)", foreground="#a15c00")
        self.mouse_lbl.config(text="Mouse: " + ("ON" if s["active"] else "PAUSED"),
                              foreground="#0a7d2c" if s["active"] else "#a00")
        self.toggle_btn.config(text="Pause" if s["active"] else "Resume")
        self.voice_lbl.config(text="Voice: " + s["voice"])
        self.event_lbl.config(text=s["event"])
        self.wifi_lbl.config(text="Wi-Fi: " + s["wifi"])
        self.ble_lbl.config(text="Bluetooth: " + s["ble"])
        ips = ", ".join(local_ipv4s())
        self.rate_lbl.config(text="Laptop IP: %s · sensor data: %.0f samples/s" % (ips or "none", s["rate"]))
        self._draw_meter(s["peak"])
        self.root.after(150, self._refresh)

    def _draw_meter(self, peak):
        c = self.meter
        c.delete("all")
        w = max(c.winfo_width(), 50)
        h = 16
        full = max(self.cfg["pinch_threshold"] * 2.5, 1.0)
        fill = min(peak / full, 1.0) * w
        c.create_rectangle(0, 0, fill, h, fill="#4a8df0" if peak < self.cfg["pinch_threshold"] else "#1db954",
                           width=0)
        x = self.cfg["pinch_threshold"] / full * w
        c.create_line(x, 0, x, h, fill="#d33", width=2)

    def _close(self):
        self.cfg.save()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
