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
        self.base = (0.0, 0.0, G)   # resting accelerometer reading (face up)

    def _sample(self, a=None, w=(0.0, 0.0, 0.0)):
        if a is None:
            a = self.base
        n = lambda s: self.rng.gauss(0, s)
        self.out.append((self.t, tuple(x + n(0.03) for x in a), tuple(x + n(0.005) for x in w)))
        self.t += self.dt

    def move_to(self, base, ms=400):
        """Turn the watch to a new resting orientation smoothly, with the matching gyro signal."""
        import numpy as np
        a0 = np.array(self.base, float); a1 = np.array(base, float)
        u0, u1 = a0 / np.linalg.norm(a0), a1 / np.linalg.norm(a1)
        axis = np.cross(u1, u0)            # dg/dt = g x w  ->  w along u1 x u0... solve per step
        ang = math.acos(max(-1.0, min(1.0, float(np.dot(u0, u1)))))
        n = max(1, int(ms / self.dt))
        an = np.linalg.norm(axis)
        w = (axis / an) * (ang / (ms / 1000.0)) if an > 1e-9 else np.zeros(3)
        for i in range(1, n + 1):
            f = i / n
            v = np.sin((1 - f) * ang) * u0 + np.sin(f * ang) * u1
            v = v / (np.sin(ang) if ang > 1e-6 else 1.0)
            v = v / np.linalg.norm(v) * G
            self._sample(a=tuple(v), w=tuple(w))
        self.base = tuple(base)

    def rest(self, ms):
        for _ in range(int(ms / self.dt)):
            self._sample()

    def pinch(self, strength=3.0):
        b = self.base
        self._sample(a=(b[0] + 0.4, b[1] + 0.2, b[2] + strength))
        self._sample(a=(b[0], b[1], b[2] - strength * 0.3))

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
