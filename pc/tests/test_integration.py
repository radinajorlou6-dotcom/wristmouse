"""End-to-end over real sockets: fake watch <-> Core (Wi-Fi transport), dry mouse output."""
import asyncio
import base64
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np

from synth import Synth
from wristmouse.config import Config
from wristmouse.core import Core
from wristmouse.output import DryOutput
from wristmouse.voice import mulaw_encode


class FakeTranscriber:
    model = object()
    state = "ready"

    def __init__(self, text):
        self.text = text
        self.got = None

    def load_async(self, name):
        pass

    def transcribe(self, audio, name):
        self.got = audio
        return self.text


def free_port(kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_core(text="hello world"):
    cfg = Config(persist=False)
    cfg["connection"] = "wifi"
    cfg["tcp_port"] = free_port()
    cfg["udp_port"] = free_port(socket.SOCK_DGRAM)
    out = DryOutput()
    tr = FakeTranscriber(text)
    core = Core(cfg, out, tr)
    core.start()
    return cfg, out, core, tr


def wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_beacon_discovery():
    cfg = Config(persist=False)
    cfg["connection"] = "wifi"
    cfg["tcp_port"] = free_port()
    cfg["udp_port"] = free_port(socket.SOCK_DGRAM)
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind(("", cfg["udp_port"]))
    rx.settimeout(4)
    core = Core(cfg, DryOutput(), FakeTranscriber(""))
    core.start()
    data, addr = rx.recvfrom(1024)
    parts = data.decode().split()
    assert parts[0] == "WRISTMOUSE" and int(parts[1]) == cfg["tcp_port"]


def test_gestures_and_voice_over_tcp():
    cfg, out, core, tr = start_core("Hello world.")
    sock = socket.create_connection(("127.0.0.1", cfg["tcp_port"]), timeout=5)
    f = sock.makefile("rwb", buffering=0)
    received = []

    import threading

    def reader():
        for line in f:
            received.append(line.decode().strip())

    threading.Thread(target=reader, daemon=True).start()

    def send(lines):
        f.write(("\n".join(lines) + "\n").encode())

    send(["HELLO 1 Galaxy Watch8 test"])
    assert wait(lambda: core.hub.connected)
    assert wait(lambda: "state on" in received)

    s = Synth()
    s.rest(300); s.pinch(); s.rest(600)
    send(s.lines())
    assert wait(lambda: out.of("click") == [("click", "left", 1)])
    assert wait(lambda: any(r.startswith("vib") for r in received))

    # triple pinch -> pc asks the watch to start the mic
    s.rest(200)
    for _ in range(3):
        s.pinch(); s.rest(200)
    s.rest(700)
    send(s.lines())
    assert wait(lambda: "mic 1" in received)

    # stream 0.3 s quiet, 1 s "speech", then silence until the pc stops us
    def chunk(amp):
        n = 960
        x = (np.sin(np.arange(n) * 2 * np.pi * 300 / 16000) * amp).astype(np.int16) if amp else np.zeros(n, np.int16)
        return "a " + base64.b64encode(mulaw_encode(x)).decode()

    for _ in range(5):
        send([chunk(0)]); time.sleep(0.06)
    for _ in range(16):
        send([chunk(12000)]); time.sleep(0.06)
    t0 = time.time()
    while "mic 0" not in received and time.time() - t0 < 4:
        send([chunk(0)]); time.sleep(0.06)
    assert "mic 0" in received
    assert wait(lambda: out.of("type") == [("type", "Hello world. ")])
    assert tr.got is not None and len(tr.got) > 16000

    # "delete that" voice command removes it
    core.call(core.hub.voice.run_text, "Delete that.")
    assert wait(lambda: len(out.of("key")) == len("Hello world. "))
    sock.close()
    assert wait(lambda: not core.hub.connected, 10)
