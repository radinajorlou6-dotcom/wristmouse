import argparse
import logging
import logging.handlers
import os
import sys
import time

from .config import APP_NAME, VERSION, Config, app_dir
from .core import Core
from .output import make_output


def setup_logging(verbose=False):
    log = logging.getLogger("wristmouse")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(os.path.join(app_dir(), "log.txt"), maxBytes=1_000_000,
                                              backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr is not None:  # windowed exe has no console
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    return log


def install_crash_logging(log):
    """The windowed exe has no console, so send every uncaught error to the log file."""
    import threading

    sys.excepthook = lambda t, v, tb: log.error("uncaught error", exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.error("thread %s crashed", a.thread and a.thread.name,
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--headless", action="store_true", help="no window (for testing)")
    ap.add_argument("--dry", action="store_true", help="don't really move the mouse")
    ap.add_argument("--no-voice", action="store_true", help="don't load the speech model")
    ap.add_argument("--config", help="config file path")
    ap.add_argument("--selftest", metavar="MODEL", nargs="?", const="",
                    help="check the packaged app works (optionally load a speech model) and exit")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args(argv)
    log = setup_logging(args.v)
    install_crash_logging(log)
    log.info("%s %s starting", APP_NAME, VERSION)

    if args.selftest is not None:
        return selftest(log, args.selftest)

    cfg = Config(args.config) if args.config else Config()
    out = make_output(dry=args.dry)
    core = Core(cfg, out)
    if not args.no_voice:
        core.hub.voice.transcriber.load_async(cfg["voice_model"])
    core.start()

    if args.headless:
        try:
            while True:
                time.sleep(2)
                s = core.status()
                log.info("connected=%s via=%s active=%s rate=%.0f event=%s",
                         s["connected"], s["via"], s["active"], s["rate"], s["event"])
        except KeyboardInterrupt:
            return 0
    from .app import App

    App(core, cfg).run()
    return 0


def selftest(log, model):
    """Used by the build to make sure nothing is missing from the packaged exe."""
    ok = True
    for name in ("numpy", "bleak", "tkinter", "faster_whisper", "ctranslate2"):
        try:
            __import__(name)
            log.info("selftest import %s ok", name)
        except Exception as e:
            log.error("selftest import %s FAILED: %r", name, e)
            ok = False
    try:
        out = make_output()
        out.move(0, 0)
        log.info("selftest output %s ok", type(out).__name__)
    except Exception as e:
        log.error("selftest output FAILED: %r", e)
        ok = False
    from . import watchsetup
    log.info("selftest bundled apk: %s", watchsetup.bundled_apk())
    if getattr(sys, "frozen", False) and not watchsetup.bundled_apk():
        log.error("selftest: watch apk missing from bundle")
        ok = False
    if model:
        try:
            import numpy as np
            from .voice import Transcriber

            t = Transcriber()
            text = t.transcribe(np.zeros(16000, dtype=np.int16), model)
            log.info("selftest whisper %s ok (%r)", model, text)
        except Exception as e:
            log.error("selftest whisper FAILED: %r", e)
            ok = False
    log.info("selftest %s", "PASSED" if ok else "FAILED")
    return 0 if ok else 1
