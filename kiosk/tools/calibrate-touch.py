#!/usr/bin/env python3
"""Record raw touches while calibrate.html is on screen, fit a libinput
calibration matrix, and write it to kiosk/touch.conf.

Usage: python3 -I kiosk/tools/calibrate-touch.py [timeout_seconds]
Needs read access to the touch device (user in the 'input' group). No sudo.
Apply the result afterwards with: sudo ./kiosk/install-kiosk.sh
"""
import array, fcntl, glob, os, select, struct, sys, time

NAME = "ADS7846 Touchscreen"
TARGETS = [(.1, .1), (.9, .1), (.9, .9), (.1, .9), (.5, .5)]  # == calibrate.html
MIN_HOLD = 0.7      # seconds, == calibrate.html HOLD
MERGE_GAP = 0.06    # join contact drop-outs shorter than this (resistive panels bounce)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "touch.conf")


def find_device():
    for p in sorted(glob.glob("/sys/class/input/event*/device/name")):
        if open(p).read().strip() == NAME:
            return "/dev/input/" + p.split("/")[4]
    sys.exit(f"{NAME} not found in /sys/class/input")


def absinfo(f, code):
    buf = array.array("i", [0] * 6)
    fcntl.ioctl(f, 0x80184540 + code, buf)
    return buf[1], buf[2]


def capture(dev, timeout):
    f = open(dev, "rb", buffering=0)
    rng = absinfo(f, 0), absinfo(f, 1)
    fmt = "llHHi"; sz = struct.calcsize(fmt)
    x = y = None; down = False; pts = []; t_down = t_up = 0.0; presses = []
    end = time.time() + timeout
    while len(presses) < len(TARGETS) and time.time() < end:
        if not down and pts and time.time() - t_up > MERGE_GAP:   # press finished
            dur = t_up - t_down
            if dur >= MIN_HOLD and len(pts) >= 5:
                pts.sort(); xs = sorted(p[0] for p in pts); ys = sorted(p[1] for p in pts)
                core = lambda v: v[len(v) // 4: 3 * len(v) // 4 + 1]     # drop outliers
                px = sum(core(xs)) / len(core(xs)); py = sum(core(ys)) / len(core(ys))
                presses.append((px, py))
                print(f"target {len(presses)}: raw x={px:.0f} y={py:.0f} "
                      f"({len(pts)} samples, {dur:.2f}s)", flush=True)
            else:
                print(f"  (ignored short press {dur:.2f}s, {len(pts)} samples)", flush=True)
            pts = []
        r, _, _ = select.select([f], [], [], 0.02)
        if not r:
            continue
        _, _, t, c, v = struct.unpack(fmt, f.read(sz))
        now = time.time()
        if t == 3 and c == 0: x = v
        elif t == 3 and c == 1: y = v
        elif t == 1 and c == 330:
            if v:
                if not pts: t_down = now
                down = True
            else:
                down = False; t_up = now
        elif t == 0 and down and x is not None:
            pts.append((x, y))
    return rng, presses


def solve3(A, b):
    """Gaussian elimination for a 3x3 system."""
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        for r in range(3):
            if r != c:
                k = M[r][c] / M[c][c]
                M[r] = [a - k * bb for a, bb in zip(M[r], M[c])]
    return [M[i][3] / M[i][i] for i in range(3)]


def fit(rng, presses):
    (x0, x1), (y0, y1) = rng
    n = [((px - x0) / (x1 - x0), (py - y0) / (y1 - y0), 1.0) for px, py in presses]
    ata = [[sum(a[i] * a[j] for a in n) for j in range(3)] for i in range(3)]
    row = []
    for axis in (0, 1):
        atb = [sum(a[i] * t[axis] for a, t in zip(n, TARGETS)) for i in range(3)]
        row.append(solve3(ata, atb))
    return row, n


def main():
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 180
    dev = find_device()
    print(f"Reading {dev}. Press and hold each target shown on the screen.", flush=True)
    rng, presses = capture(dev, timeout)
    if len(presses) < len(TARGETS):
        sys.exit(f"Only got {len(presses)} of {len(TARGETS)} targets; nothing written.")
    ((a, b, c), (d, e, f)), n = fit(rng, presses)
    print("\nresidual error per target (fraction of screen -> px at 480x320):")
    worst = 0
    for (nx, ny, _), (tx, ty) in zip(n, TARGETS):
        ex = (a * nx + b * ny + c - tx) * 480; ey = (d * nx + e * ny + f - ty) * 320
        worst = max(worst, (ex * ex + ey * ey) ** .5)
        print(f"  target ({tx:.1f},{ty:.1f}): dx={ex:+.1f}px dy={ey:+.1f}px")
    swapped = abs(b) > abs(a)
    print(f"\naxes {'SWAPPED' if swapped else 'not swapped'}; "
          f"x {'inverted' if (b if swapped else a) < 0 else 'normal'}, "
          f"y {'inverted' if (d if swapped else e) < 0 else 'normal'}; worst error {worst:.1f}px")
    m = " ".join(f"{v:.6f}" for v in (a, b, c, d, e, f))
    with open(OUT, "w") as fh:
        fh.write(f'# Written by calibrate-touch.py on {time.strftime("%Y-%m-%d %H:%M")}\n'
                 f'# libinput calibration matrix (a b c d e f) for "{NAME}"\n'
                 f'TOUCH_CALIBRATION="{m}"\n')
    print(f"\nLIBINPUT_CALIBRATION_MATRIX=\"{m}\"\nwritten to {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
