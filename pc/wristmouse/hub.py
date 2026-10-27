"""Glue between the watch links, the gesture engine and voice typing. Everything here runs on the
core asyncio loop (one thread), so no locking is needed.

Protocol (UTF-8 lines):
  watch -> pc   HELLO <ver> <model...>        on connect
                s <t_ms> ax ay az gx gy gz     sensors; accel in mm/s^2, gyro in mrad/s
                a <base64 mu-law 16kHz>        mic audio
                ae                             mic stopped
                p                              keepalive
                info <text>                    message to show
  pc -> watch   mic 1 | mic 0                  start / stop the mic
                vib <ms>[,<pause>,<ms>...]     vibrate
                msg <text>                     show text on the watch
                state <on|off>                 mouse active state
                p                              keepalive
"""
import asyncio
import collections
import logging
import time

from .engine import Engine
from .voice import Voice

log = logging.getLogger("wristmouse")


class Hub:
    def __init__(self, cfg, out, transcriber=None):
        self.cfg = cfg
        self.out = out
        self.loop = None
        self.engine = Engine(cfg, out, self._on_engine)
        self.voice = Voice(cfg, self, transcriber)
        self.links = []
        self.active_link = None
        self.watch_model = ""
        self.events = collections.deque(maxlen=50)
        self.last_event = "Waiting for the watch…"
        self.samples = 0
        self.rate = 0.0
        self._rate_t = time.monotonic()
        self._rate_n = 0
        self._last_ping = 0.0
        self.vib_until = 0.0
        self._feed_errors = 0
        self._last_stats = time.monotonic()
        self.engine.pinch_blocked = lambda: time.monotonic() < self.vib_until

    # ---------------------------------------------------------------- links
    def link_up(self, link):
        self.links.append(link)
        self.event("Watch connected over %s" % link.kind)
        log.info("link up: %s", link)

    def link_down(self, link):
        if link in self.links:
            self.links.remove(link)
            self.event("Watch disconnected (%s)" % link.kind)
            log.info("link down: %s", link)
        if self.active_link is link:
            self.active_link = self.links[-1] if self.links else None
            self.engine.reset()
            if self.voice.state == "listening":
                self.voice.cancel("watch disconnected")

    @property
    def connected(self):
        return bool(self.links)

    def connection_label(self):
        if not self.links:
            return None
        l = self.active_link or self.links[-1]
        return l.kind

    def send(self, line: str):
        link = self.active_link or (self.links[-1] if self.links else None)
        if link:
            try:
                link.send(line)
            except Exception as e:
                log.debug("send failed: %s", e)

    def haptic(self, *pattern_ms):
        if self.cfg["haptics"] and self.links:
            # ignore pinches until the buzz is over (plus margin for Bluetooth/Wi-Fi delay),
            # otherwise the vibration itself looks like a pinch and clicks loop forever
            dur = sum(int(x) for x in pattern_ms) / 1000.0
            self.vib_until = max(self.vib_until, time.monotonic() + dur + 0.45)
            self.send("vib " + ",".join(str(int(x)) for x in pattern_ms))

    def watch_msg(self, text: str):
        self.send("msg " + text.replace("\n", " "))

    def event(self, text: str):
        self.last_event = text
        self.events.append((time.time(), text))
        log.info(text)

    # ------------------------------------------------------------- incoming
    def on_line(self, link, line: str):
        if not line:
            return
        if link is not self.active_link:
            if self.active_link is not None:
                self.engine.reset()
            self.active_link = link
        if line.startswith("s "):
            p = line.split()
            if len(p) != 8:
                return
            try:
                t = int(p[1])
                a = (int(p[2]) / 1000.0, int(p[3]) / 1000.0, int(p[4]) / 1000.0)
                w = (int(p[5]) / 1000.0, int(p[6]) / 1000.0, int(p[7]) / 1000.0)
            except ValueError:
                return
            self.samples += 1
            self._rate_n += 1
            try:
                self.engine.feed(t, a, w)
            except Exception:
                self._feed_errors += 1
                if self._feed_errors <= 5 or self._feed_errors % 1000 == 0:
                    log.exception("gesture engine error #%d (line %r)", self._feed_errors, line)
                self.engine.reset()
        elif line.startswith("a "):
            self.voice.on_audio(line[2:])
        elif line == "ae":
            self.voice.on_audio_end()
        elif line.startswith("HELLO"):
            self.watch_model = line[6:].strip()
            self.send("state " + ("on" if self.engine.active else "off"))
        elif line.startswith("info "):
            self.event("Watch: " + line[5:])

    # ---------------------------------------------------------- engine events
    def _on_engine(self, ev, **kw):
        if ev == "active":
            on = kw["active"]
            self.event("Mouse " + ("ON" if on else "PAUSED"))
            self.haptic(80) if on else self.haptic(50, 80, 50)
            self.send("state " + ("on" if on else "off"))
        elif ev == "click":
            self.event(kw["button"].capitalize() + " click")
            self.haptic(15) if kw["button"] == "left" else self.haptic(15, 60, 15)
        elif ev == "scroll":
            self.event("Scroll " + ("up" if kw["notches"] > 0 else "down"))
        elif ev == "dictate":
            self.voice.start()

    # ----------------------------------------------------------------- loop
    def tick(self):
        now = time.monotonic()
        self.voice.tick()
        if now - self._rate_t >= 1.0:
            self.rate = self._rate_n / (now - self._rate_t)
            self._rate_n = 0
            self._rate_t = now
        if now - self._last_stats >= 30.0:
            self._last_stats = now
            if self.links:
                e = self.engine
                log.info("stats: via=%s rate=%.0f/s active=%s gravity=%s errors=%d",
                         self.connection_label(), self.rate, e.active,
                         [round(x, 1) for x in (e.g or [])], self._feed_errors)
        if now - self._last_ping >= 2.0:
            self._last_ping = now
            for l in list(self.links):
                try:
                    l.send("p")
                except Exception:
                    pass

    def run_blocking(self, fn, arg, done):
        """Run fn(arg) on a worker thread, then done(result, error) back on the loop."""
        loop = self.loop or asyncio.get_event_loop()

        async def runner():
            try:
                res = await loop.run_in_executor(None, fn, arg)
            except Exception as e:
                log.exception("worker failed")
                done(None, e)
                return
            done(res, None)

        loop.create_task(runner())
