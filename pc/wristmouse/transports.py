"""Wi-Fi (TCP + UDP discovery beacon) and Bluetooth LE links to the watch."""
import asyncio
import logging
import socket
import time

log = logging.getLogger("wristmouse")

# BLE GATT layout (the watch is the peripheral / GATT server)
SERVICE_UUID = "6e7a0001-5a3c-4d1b-9b8e-77a1c0de1e55"
TX_UUID = "6e7a0002-5a3c-4d1b-9b8e-77a1c0de1e55"   # watch -> pc (notify)
RX_UUID = "6e7a0003-5a3c-4d1b-9b8e-77a1c0de1e55"   # pc -> watch (write)
BEACON_PREFIX = "WRISTMOUSE"


def local_ipv4s():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # the address used for the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))


def broadcast_targets():
    targets = {"255.255.255.255"}
    for ip in local_ipv4s():
        a, b, c, _ = ip.split(".")
        targets.add("%s.%s.%s.255" % (a, b, c))
    return sorted(targets)


class LineBuffer:
    def __init__(self, on_line):
        self.buf = bytearray()
        self.on_line = on_line

    def feed(self, data: bytes):
        self.buf.extend(data)
        while True:
            i = self.buf.find(b"\n")
            if i < 0:
                break
            line = bytes(self.buf[:i]).decode("utf-8", "replace").rstrip("\r")
            del self.buf[: i + 1]
            self.on_line(line)
        if len(self.buf) > 1 << 20:  # garbage protection
            self.buf.clear()


# ---------------------------------------------------------------------- Wi-Fi
class TcpLink:
    kind = "Wi-Fi"

    def __init__(self, writer, peer):
        self.writer = writer
        self.peer = peer

    def send(self, line):
        self.writer.write((line + "\n").encode("utf-8"))

    def __repr__(self):
        return "TcpLink(%s)" % (self.peer,)


class WifiTransport:
    def __init__(self, hub, cfg):
        self.hub = hub
        self.cfg = cfg
        self.server = None
        self.tasks = []
        self.status = "starting"

    async def start(self):
        port = int(self.cfg["tcp_port"])
        try:
            self.server = await asyncio.start_server(self._client, "0.0.0.0", port, limit=1 << 20)
        except OSError as e:
            self.status = "can't listen on port %d (%s)" % (port, e)
            log.error(self.status)
            return
        self.status = "listening on %s:%d" % (", ".join(local_ipv4s()) or "?", port)
        self.tasks.append(asyncio.create_task(self._beacon()))

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        self.tasks.clear()
        if self.server:
            self.server.close()
            for l in [l for l in self.hub.links if isinstance(l, TcpLink)]:
                try:
                    l.writer.close()
                except Exception:
                    pass
            self.server = None
        self.status = "off"

    async def _client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        sock = writer.get_extra_info("socket")
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        link = TcpLink(writer, peer)
        self.hub.link_up(link)
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=8)
                if not line:
                    break
                self.hub.on_line(link, line.decode("utf-8", "replace").rstrip("\r\n"))
        except (asyncio.TimeoutError, ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self.hub.link_down(link)
            try:
                writer.close()
            except Exception:
                pass

    async def _beacon(self):
        port = int(self.cfg["tcp_port"])
        udp = int(self.cfg["udp_port"])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        msg = ("%s %d %s" % (BEACON_PREFIX, port, socket.gethostname()[:40])).encode()
        try:
            while True:
                for target in broadcast_targets():
                    try:
                        sock.sendto(msg, (target, udp))
                    except OSError:
                        pass
                await asyncio.sleep(1.0)
        finally:
            sock.close()


# ------------------------------------------------------------------ Bluetooth
class BleLink:
    kind = "Bluetooth"

    def __init__(self, client):
        self.client = client
        self.last_rx = time.monotonic()
        self.queue = asyncio.Queue(maxsize=200)
        self.writer_task = asyncio.create_task(self._writer())

    def send(self, line):
        try:
            self.queue.put_nowait((line + "\n").encode("utf-8"))
        except asyncio.QueueFull:
            pass

    async def _writer(self):
        while True:
            data = await self.queue.get()
            size = max(20, min(180, (getattr(self.client, "mtu_size", 23) or 23) - 3))
            for i in range(0, len(data), size):
                try:
                    await self.client.write_gatt_char(RX_UUID, data[i:i + size], response=False)
                except Exception as e:
                    log.debug("ble write failed: %s", e)
                    break

    def close(self):
        self.writer_task.cancel()

    def __repr__(self):
        return "BleLink(%s)" % getattr(self.client, "address", "?")


class BleTransport:
    def __init__(self, hub, cfg):
        self.hub = hub
        self.cfg = cfg
        self.task = None
        self.status = "starting"
        self.client = None

    async def start(self):
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except BaseException:
                pass
        self.task = None
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.status = "off"

    async def _watch_stream(self, client, link, gone, on_data):
        """The watch streams constantly while subscribed. If data stops while still 'connected'
        (e.g. the watch missed the subscribe during a quick reconnect), subscribe again; if that
        doesn't help, drop the connection so the outer loop reconnects from scratch."""
        tries = 0
        resub_at = 0.0
        while not gone.is_set():
            try:
                await asyncio.wait_for(gone.wait(), 2.0)
            except asyncio.TimeoutError:
                pass
            if gone.is_set():
                return
            now = time.monotonic()
            if link.last_rx > resub_at:
                tries = 0  # real data has arrived since the last re-subscribe
            if now - max(link.last_rx, resub_at) <= 3.0:
                continue
            tries += 1
            if tries > 2:
                log.info("BLE: still no data from the watch, reconnecting")
                self.status = "no data, reconnecting…"
                return
            log.info("BLE: no data from the watch, re-subscribing (try %d)", tries)
            self.status = "no data, re-subscribing…"
            try:
                try:
                    await client.stop_notify(TX_UUID)
                except Exception:
                    pass
                await asyncio.sleep(0.3)
                await client.start_notify(TX_UUID, on_data)
                link.send("HELLO-PC")
                resub_at = time.monotonic()
            except Exception as e:
                log.info("BLE: re-subscribe failed: %s", e)
                return

    async def _run(self):
        try:
            from bleak import BleakClient, BleakScanner
        except Exception as e:
            self.status = "Bluetooth unavailable (%s)" % e
            return
        loop = asyncio.get_running_loop()
        while True:
            link = None
            try:
                self.status = "searching for the watch…"
                dev = await BleakScanner.find_device_by_filter(
                    lambda d, ad: SERVICE_UUID in [u.lower() for u in (ad.service_uuids or [])],
                    timeout=8.0,
                )
                if dev is None:
                    await asyncio.sleep(0.5)
                    continue
                self.status = "connecting…"
                gone = asyncio.Event()
                client = BleakClient(dev, disconnected_callback=lambda c: loop.call_soon_threadsafe(gone.set))
                self.client = client
                await client.connect(timeout=15.0)
                link = BleLink(client)
                buf = LineBuffer(lambda line: self.hub.on_line(link, line))

                def on_data(_c, data, link=link, buf=buf):
                    link.last_rx = time.monotonic()
                    buf.feed(bytes(data))

                await client.start_notify(TX_UUID, on_data)
                self.hub.link_up(link)
                self.status = "connected to %s" % (dev.name or dev.address)
                link.send("HELLO-PC")
                await self._watch_stream(client, link, gone, on_data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = str(e) or e.__class__.__name__
                self.status = "error: %s" % msg[:80]
                log.info("BLE: %s", msg)
                await asyncio.sleep(2.0)
            finally:
                if link is not None:
                    link.close()
                    self.hub.link_down(link)
                if self.client is not None:
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                    self.client = None
            await asyncio.sleep(1.0)
