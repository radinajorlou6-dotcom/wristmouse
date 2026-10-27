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


def axis_from_motion(samples, exclude=None):
    """Find which gyro axis a deliberate motion used, and its direction.

    `exclude` is an axis that's already taken (the up/down axis must differ from left/right,
    otherwise the cursor can only move diagonally).
    Returns [axis_index, sign] where sign makes the *first* big movement positive.
    """
    if len(samples) < 30:
        raise CalibrationError("Not enough data from the watch. Is it connected?")
    # total rotation energy per axis is steadier than a single peak sample
    energy = [sum(s[2][i] * s[2][i] for s in samples) for i in range(3)]
    peaks = [max(abs(s[2][i]) for s in samples) for i in range(3)]
    axes = [i for i in range(3) if i != exclude]
    axis = max(axes, key=lambda i: energy[i])
    if peaks[axis] < 0.8:
        raise CalibrationError("Didn't detect a clear movement. Try a bigger motion.")
    others = sorted(energy[i] for i in axes if i != axis)
    if others and others[-1] > 0.8 * energy[axis]:
        raise CalibrationError("The movement mixed two directions. Try a cleaner, straighter motion.")
    for s in samples:
        v = s[2][axis]
        if abs(v) >= 0.5 * peaks[axis]:
            return [axis, 1 if v > 0 else -1]
    raise CalibrationError("Couldn't read the motion.")
