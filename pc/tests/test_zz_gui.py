"""Builds the real window (only where tkinter + a display exist, e.g. the Windows CI runner).
Named zz so it runs last: Tk objects must be freed on the main thread, otherwise their cleanup
can stall other threads."""
import gc
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
tk = pytest.importorskip("tkinter")


def test_window_builds_and_refreshes():
    from test_integration import FakeTranscriber, free_port
    from wristmouse.app import App
    from wristmouse.config import Config
    from wristmouse.core import Core
    from wristmouse.output import DryOutput

    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    cfg = Config(persist=False)
    cfg["connection"] = "wifi"
    cfg["tcp_port"] = free_port()
    core = Core(cfg, DryOutput(), FakeTranscriber(""))
    core.start()
    app = App(core, cfg)
    for _ in range(5):
        app.root.update()
    app._refresh()
    app.root.update()
    assert "Waiting" in app.conn_lbl.cget("text")
    assert app.overlay is not None
    core.hub.flash("left", "Left click")
    core.hub.flash("scroll", "Scroll down")
    core.hub.flash("scroll", "Scroll down")
    for _ in range(10):
        app.root.update()
        import time
        time.sleep(0.04)
    assert "×2" in app.overlay.label.cget("text")
    for mode in ("corner", "off", "cursor"):
        app._overlay_changed(mode)
        app.root.update()
    app.root.destroy()
    app.vars.clear()
    del app
    gc.collect()
