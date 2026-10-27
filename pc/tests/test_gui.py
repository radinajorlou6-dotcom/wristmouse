"""Builds the real window (only where tkinter + a display exist, e.g. the Windows CI runner)."""
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
    app.root.destroy()
