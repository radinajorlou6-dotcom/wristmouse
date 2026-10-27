"""Runs the hub and the watch links on a background asyncio thread."""
import asyncio
import logging
import threading

from .hub import Hub
from .transports import BleTransport, WifiTransport

log = logging.getLogger("wristmouse")


class Core:
    def __init__(self, cfg, out, transcriber=None):
        self.cfg = cfg
        self.loop = asyncio.new_event_loop()
        self.hub = Hub(cfg, out, transcriber)
        self.hub.loop = self.loop
        self.wifi = None
        self.ble = None
        self.thread = threading.Thread(target=self._run, name="wristmouse-core", daemon=True)
        self.ready = threading.Event()

    def start(self):
        self.thread.start()
        self.ready.wait(5)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        await self._start_transports()
        self.ready.set()
        while True:
            try:
                self.hub.tick()
            except Exception:
                log.exception("tick failed")
            await asyncio.sleep(0.1)

    async def _start_transports(self):
        mode = self.cfg["connection"]
        if mode in ("auto", "wifi"):
            self.wifi = WifiTransport(self.hub, self.cfg)
            await self.wifi.start()
        if mode in ("auto", "bluetooth"):
            self.ble = BleTransport(self.hub, self.cfg)
            await self.ble.start()

    async def _restart(self):
        for t in (self.wifi, self.ble):
            if t:
                await t.stop()
        self.wifi = self.ble = None
        await self._start_transports()

    # ----------------------------------------------------- thread-safe API
    def call(self, fn, *args):
        self.loop.call_soon_threadsafe(fn, *args)

    def restart_transports(self):
        asyncio.run_coroutine_threadsafe(self._restart(), self.loop)

    def status(self):
        """Snapshot for the UI (plain reads; fine across threads)."""
        h = self.hub
        return {
            "connected": h.connected,
            "via": h.connection_label(),
            "watch": h.watch_model,
            "active": h.engine.active,
            "rate": h.rate,
            "wifi": self.wifi.status if self.wifi else "off",
            "ble": self.ble.status if self.ble else "off",
            "voice": h.voice.status,
            "event": h.last_event,
            "peak": h.engine.live_peak,
        }
