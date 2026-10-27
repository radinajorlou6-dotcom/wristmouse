"""Calibration helpers. Samples are (t_ms, accel(3), gyro(3), m) tuples from Engine.start_capture."""
import math


class CalibrationError(Exception):
    pass


def pinch_threshold(samples, max_gyro: float):
    """User pinches ~5 times while recording. Returns (threshold, n_pinches_found)."""
    if len(samples) < 50:
        raise CalibrationError("Not enough data from the watch. Is it connected?")
    ms = [s[3] for s in samples]
    ts = [s[0] for s in samples]
    wm = [math.sqrt(sum(x * x for x in s[2])) for s in samples]

    peaks = []
    for i in range(1, len(ms) - 1):
        if ms[i] >= ms[i - 1] and ms[i] >= ms[i + 1] and wm[i] <= max_gyro * 1.5:
            peaks.append((ms[i], ts[i]))
    peaks.sort(reverse=True)
    chosen = []
    for val, t in peaks:
        if all(abs(t - ct) >= 150 for _, ct in chosen):
            chosen.append((val, t))
        if len(chosen) >= 8:
            break
    if not chosen:
        raise CalibrationError("No pinches detected.")
    top = chosen[0][0]
    real = [(v, t) for v, t in chosen if v >= 0.4 * top]
    if len(real) < 3:
        raise CalibrationError("Only found %d clear pinch(es). Try again and pinch firmly 5 times." % len(real))

    quiet = sorted(m for m, t in zip(ms, ts) if all(abs(t - rt) > 120 for _, rt in real))
    noise = quiet[int(0.98 * (len(quiet) - 1))] if quiet else 0.0
    weakest = min(v for v, _ in real)
    if weakest < 1.5 * noise:
        raise CalibrationError("Pinches were too close to normal hand movement. Keep your arm still and pinch firmly.")
    thr = max(1.3 * noise, 0.6 * weakest, 0.3)
    return round(thr, 2), len(real)

