"""Voice typing: audio streams from the watch mic (16 kHz mu-law), is cut by a simple
energy voice-activity detector, transcribed locally with faster-whisper, then typed."""
import base64
import logging
import os
import threading
import time

import numpy as np

from . import commands
from .config import data_dir

log = logging.getLogger("wristmouse")
RATE = 16000
FRAME = 320  # 20 ms


def _mulaw_table():
    t = np.zeros(256, dtype=np.int16)
    for i in range(256):
        u = ~i & 0xFF
        sign = u & 0x80
        exp = (u >> 4) & 0x07
        mant = u & 0x0F
        s = (((mant << 3) + 0x84) << exp) - 0x84
        t[i] = -s if sign else s
    return t


MULAW = _mulaw_table()


def mulaw_decode(data: bytes) -> np.ndarray:
    return MULAW[np.frombuffer(data, dtype=np.uint8)]


def mulaw_encode(samples) -> bytes:
    """Reference encoder (same algorithm as the watch). Used by tests and the fake watch."""
    out = bytearray()
    for s in samples:
        s = int(s)
        sign = 0
        if s < 0:
            s = -s
            sign = 0x80
        s = min(s, 32635) + 0x84
        exp = 7
        mask = 0x4000
        while exp > 0 and not (s & mask):
            exp -= 1
            mask >>= 1
        mant = (s >> (exp + 3)) & 0x0F
        out.append(~(sign | (exp << 4) | mant) & 0xFF)
    return bytes(out)


class Transcriber:
    """Loads the Whisper model in the background so the first dictation isn't slow."""

    def __init__(self):
        self.model = None
        self.model_name = None
        self.state = "not loaded"
        self._lock = threading.Lock()

    def load_async(self, name: str):
        threading.Thread(target=self._load, args=(name,), daemon=True).start()

    def _load(self, name):
        with self._lock:
            if self.model is not None and self.model_name == name:
                return
            self.state = "downloading/loading %s…" % name
            try:
                from faster_whisper import WhisperModel

                root = os.path.join(data_dir(), "models")
                self.model = WhisperModel(name, device="cpu", compute_type="int8", download_root=root)
                self.model_name = name
                self.state = "ready (%s)" % name
            except Exception as e:  # pragma: no cover - depends on the machine
                log.exception("Whisper load failed")
                self.model = None
                self.state = "error: %s" % e

    def transcribe(self, audio: np.ndarray, name: str) -> str:
        if self.model is None or self.model_name != name:
            self._load(name)
        if self.model is None:
            raise RuntimeError("speech model not available (%s)" % self.state)
        f = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            f, language="en", beam_size=5, condition_on_previous_text=False, vad_filter=False,
            without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()


class Voice:
    """State machine: idle -> listening -> transcribing -> idle. All methods run on the core loop."""

    def __init__(self, cfg, hub, transcriber=None):
        self.cfg = cfg
        self.hub = hub
        self.transcriber = transcriber or Transcriber()
        self.state = "idle"
        self.last_typed = ""
        self.last_text = ""
        self._reset()

    def _reset(self):
        self.chunks = []
        self.pending = np.zeros(0, dtype=np.int16)
        self.levels = []
        self.started = 0.0
        self.last_audio = 0.0
        self.speech = False
        self.speech_frames = 0
        self.last_voice = 0.0
        self.noise = None

    @property
    def status(self):
        if self.state == "idle":
            return "Ready. Triple pinch to talk" if self.transcriber.model else "Speech model: " + self.transcriber.state
        return {"listening": "Listening…" + (" (hearing you)" if self.speech else ""),
                "transcribing": "Turning speech into text…"}.get(self.state, self.state)

    def start(self):
        if self.state != "idle":
            return
        self._reset()
        self.state = "listening"
        self.started = self.last_audio = time.monotonic()
        self.hub.engine.flush()
        self.hub.send("mic 1")
        self.hub.haptic(120)
        self.hub.event("Voice typing: listening")
        hint = "lower your arm when done" if self.cfg["voice_trigger"] != "triple" else "stops when you stop talking"
        self.hub.flash("voice", "Listening… (%s)" % hint, sticky=True)

    def cancel(self, why=""):
        if self.state == "listening":
            self.hub.send("mic 0")
            self.hub.haptic(40, 60, 40)
        self.state = "idle"
        self._reset()
        if why:
            self.hub.event("Voice typing cancelled: " + why)
            self.hub.flash("cancel", "Voice cancelled: " + why)

    def on_audio(self, b64: str):
        if self.state != "listening":
            return
        try:
            pcm = mulaw_decode(base64.b64decode(b64))
        except Exception:
            return
        now = time.monotonic()
        self.last_audio = now
        self.chunks.append(pcm)
        self.pending = np.concatenate([self.pending, pcm])
        while len(self.pending) >= FRAME:
            frame = self.pending[:FRAME].astype(np.float32)
            self.pending = self.pending[FRAME:]
            self._frame(float(np.sqrt(np.mean(frame * frame))), now)

    def _frame(self, rms, now):
        self.levels.append(rms)
        if self.noise is None:
            if len(self.levels) >= 10:
                self.noise = float(np.median(self.levels))
            return
        thr = max(self.noise * 2.5, float(self.cfg["voice_min_level"]))
        if rms > thr:
            self.speech_frames += 1
            if self.speech_frames >= 3:
                self.speech = True
            self.last_voice = now
        else:
            self.speech_frames = 0

    def on_audio_end(self):
        if self.state == "listening":
            if self.speech:
                self.finish()
            else:
                self.cancel("watch stopped the mic")

    def on_lowered(self):
        """Arm came down from the mouth: done talking."""
        if self.state != "listening":
            return
        if self.speech:
            self.finish()
        else:
            self.cancel("didn't hear anything")

    def tick(self):
        if self.state != "listening":
            return
        now = time.monotonic()
        if now - self.last_audio > 3.0:
            self.cancel("no audio from watch")
        elif self.speech and now - self.last_voice > self.cfg["voice_silence_ms"] / 1000.0:
            self.finish()
        elif not self.speech and now - self.started > 6.0:
            self.cancel("didn't hear anything")
        elif now - self.started > self.cfg["voice_max_s"]:
            self.finish()

    def finish(self):
        if self.state != "listening":
            return
        self.hub.send("mic 0")
        self.hub.haptic(40)
        audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.int16)
        self.state = "transcribing"
        self.hub.flash("voice", "Turning speech into text…", sticky=True)
        self.hub.run_blocking(self._transcribe, audio, self._done)

    def _transcribe(self, audio):
        if len(audio) < RATE * 0.3:
            return ""
        return self.transcriber.transcribe(audio, self.cfg["voice_model"])

    def _done(self, text, error=None):
        self.state = "idle"
        self._reset()
        if error is not None:
            self.hub.event("Voice typing failed: %s" % error)
            self.hub.flash("cancel", "Voice typing failed")
            self.hub.watch_msg("Voice failed")
            return
        text = (text or "").strip()
        self.last_text = text
        if not text:
            self.hub.event("Voice typing: heard nothing")
            self.hub.flash("cancel", "Didn't catch that")
            return
        self.run_text(text)

    def run_text(self, text):
        cmd = commands.parse(text)
        if cmd is None:
            return
        out = self.hub.out
        kind = cmd[0]
        if kind == "type":
            typed = cmd[1].strip()
            if typed:
                typed += " "
                out.type_text(typed)
                self.last_typed = typed
            self.hub.event('Typed: "%s"' % typed.strip())
            shown = typed.strip()
            self.hub.flash("typed", "Typed: " + (shown if len(shown) <= 40 else shown[:39] + "…"))
            self.hub.watch_msg(typed.strip()[:60])
            return
        if kind == "key":
            out.key(*cmd[1:])
        elif kind == "delete_last":
            for _ in range(len(self.last_typed)):
                out.key("backspace")
            self.last_typed = ""
        elif kind == "click":
            out.click(cmd[1], cmd[2] if len(cmd) > 2 else 1)
        elif kind == "scroll":
            out.scroll(cmd[1])
        elif kind == "mouse":
            self.hub.engine.set_active(cmd[1])
        self.hub.event('Voice command: "%s"' % commands.normalize(text))
        self.hub.flash("command", "Command: " + commands.normalize(text))
        self.hub.watch_msg(commands.normalize(text))
