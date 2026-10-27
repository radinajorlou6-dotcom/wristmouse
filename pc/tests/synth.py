"""Synthetic watch motion for tests (100 Hz, face-up resting pose)."""
import math
import random

G = 9.81


class Synth:
    def __init__(self, seed=1, rate=100):
        self.t = 1000.0
        self.dt = 1000.0 / rate
        self.rng = random.Random(seed)
        self.out = []

    def _sample(self, a=(0.0, 0.0, G), w=(0.0, 0.0, 0.0)):
        n = lambda s: self.rng.gauss(0, s)
        self.out.append((self.t, tuple(x + n(0.03) for x in a), tuple(x + n(0.005) for x in w)))
        self.t += self.dt

    def rest(self, ms):
        for _ in range(int(ms / self.dt)):
            self._sample()

    def pinch(self, strength=3.0):
        self._sample(a=(0.4, 0.2, G + strength))
        self._sample(a=(0.0, 0.0, G - strength * 0.3))

    def rotate(self, axis, rad_s, ms):
        for _ in range(int(ms / self.dt)):
            w = [0.0, 0.0, 0.0]
            w[axis] = rad_s
            self._sample(w=tuple(w))

    def flick(self, axis, sign=1, peak=8.0, ms=120):
        n = int(ms / self.dt)
        for i in range(n):  # out and back: half sine each way
            w = [0.0, 0.0, 0.0]
            w[axis] = sign * peak * math.sin(math.pi * i / n) * (1 if i < n else 1)
            self._sample(w=tuple(w))
        for i in range(n):
            w = [0.0, 0.0, 0.0]
            w[axis] = -sign * peak * 0.8 * math.sin(math.pi * i / n)
            self._sample(w=tuple(w))

    def palm_up(self, ms):
        for _ in range(int(ms / self.dt)):
            self._sample(a=(0.0, 0.0, -G))

    def feed(self, engine):
        for t, a, w in self.out:
            engine.feed(t, a, w)
        self.out = []

    def lines(self):
        res = []
        for t, a, w in self.out:
            res.append("s %d %d %d %d %d %d %d" % ((int(t),) + tuple(int(x * 1000) for x in a)
                                                   + tuple(int(x * 1000) for x in w)))
        self.out = []
        return res
