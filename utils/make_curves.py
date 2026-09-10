# -*- coding: utf-8 -*-
"""Generate the four 24-hour curves in config/ from the CC2&3 design basis.

The numbers this works from come from the JTC BESS control-logic submission
(v1.2, 25/08/2026), which is not in this repository -- see README, "Site data".
The point of the script is that the CSVs are *derived*, not hand-typed: the
anchors below are the design basis, and re-running reproduces the files exactly.

Run from the repository root:

    python3 utils/make_curves.py

Curves are written at quarter-hour resolution. `jtc_common_curve.csv` and
`t7_pv_curve.csv` go to DayCurve, which wraps midnight, so they end at 23:45.
`pv_curve.csv` and `load_curve.csv` go to the older loaders, which clamp
outside their range instead of wrapping, so those carry a closing 24:00 point
equal to their 00:00 one. All four now carry a `hour,kW` header -- both older
loaders discard their first row, so a headerless file silently lost midnight.

Every value is kW at the metering point, positive for consumption.
"""
import math
import os

STEP = 0.25                       # quarter-hour resolution
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')

# --- design basis, from the submission -------------------------------------
#
# Maximum Import Power        1,700 kW   regulated against the JTC common load
# Power Margin                5%         so control acts from 1,615 kW
# BESS Zero Export offset       150 kW   discharge floor under the common load
# T98 multi-loop limit        1,750 kW   70% of the 2,500 kW T98 transformer
# BESS PCS                      800 kW   at Tower 10 (T98)
# Rooftop PV                    240 kW   Tower 7
#                                52 kW   Tower 10

MAX_IMPORT = 1700.0
MARGIN = 0.05
FLOOR = 150.0
T98_LOOP = 1750.0
PCS = 800.0

# JTC common load -- landlord and common services, the calculated grid
# reference the EGC regulates. Shaped so all three use-cases are reachable:
# it crosses 1,700 kW in the early afternoon (use-case 1 discharges), passes
# through the 1,615 kW margin line on the way down, and its overnight minimum
# sits just above the 150 kW floor so a night discharge runs into it
# (use-case 2) instead of never approaching it.
JTC_ANCHORS = [
    (0.0, 240), (1.0, 220), (2.0, 205), (3.0, 195), (4.0, 185), (4.5, 178),
    (5.0, 190), (6.0, 260), (7.0, 430), (8.0, 720), (9.0, 1050), (10.0, 1320),
    (11.0, 1480), (12.0, 1560), (12.5, 1520), (13.0, 1640), (13.5, 1720),
    (14.0, 1810), (14.25, 1860), (15.0, 1780), (15.5, 1700), (16.0, 1610),
    (17.0, 1420), (18.0, 1150), (19.0, 900), (20.0, 720), (21.0, 560),
    (22.0, 420), (23.0, 320), (24.0, 240),
]

# Tower 10 (T98) tenant load, behind the 2,500 kW transformer. Its working-hours
# peak leaves about 190 kW of headroom under the 1,750 kW multi-loop limit, so a
# BESS asking for its full 800 kW at that hour must be cut back (use-case 3),
# while overnight there is room for the whole PCS.
T98_ANCHORS = [
    (0.0, 280), (1.0, 265), (2.0, 255), (3.0, 250), (4.0, 248), (5.0, 255),
    (6.0, 300), (7.0, 460), (8.0, 780), (9.0, 1080), (10.0, 1270),
    (11.0, 1380), (12.0, 1330), (13.0, 1420), (14.0, 1510), (15.0, 1560),
    (16.0, 1480), (17.0, 1300), (18.0, 1000), (19.0, 760), (20.0, 600),
    (21.0, 470), (22.0, 380), (23.0, 320), (24.0, 280),
]

# Rooftop PV: a clear-ish tropical day, generating 07:00-19:00, peaking a little
# after solar noon. Peak is held below the plate rating -- a rooftop array does
# not reach its rating at the meter.
PV_SUNRISE, PV_SUNSET, PV_PEAK_HOUR = 7.0, 19.0, 13.0
T7_RATED, T7_PEAK = 240.0, 204.0
T10_RATED, T10_PEAK = 52.0, 44.0


def lerp(anchors, h):
    """Linear read of an anchor list at hour h."""
    if h <= anchors[0][0]:
        return float(anchors[0][1])
    if h >= anchors[-1][0]:
        return float(anchors[-1][1])
    for i in range(len(anchors) - 1):
        h1, v1 = anchors[i]
        h2, v2 = anchors[i + 1]
        if h1 <= h <= h2:
            return float(v1) + (float(v2) - float(v1)) * (h - h1) / (h2 - h1)
    return float(anchors[-1][1])


def smooth(values, passes=2):
    """Round the corners a piecewise-linear anchor set leaves behind.

    A three-point moving average over a circular series -- the day joins back
    up at midnight, so the ends wrap rather than flatten.
    """
    out = list(values)
    for _ in range(passes):
        n = len(out)
        out = [(out[(i - 1) % n] + 2 * out[i] + out[(i + 1) % n]) / 4.0
               for i in range(n)]
    return out


def texture(h, amplitude, seed):
    """Small deterministic wobble, so a curve is not glassy-smooth.

    Fixed sines rather than random numbers: re-running the script reproduces
    the same file, which is the point of generating them at all.
    """
    return amplitude * (math.sin(h * 2.7 + seed) * 0.6
                        + math.sin(h * 6.1 + seed * 2) * 0.3
                        + math.sin(h * 11.3 + seed * 3) * 0.1)


def solar(h, peak):
    """One PV day: zero outside daylight, skewed slightly past solar noon."""
    if h <= PV_SUNRISE or h >= PV_SUNSET:
        return 0.0
    # Map sunrise..sunset onto 0..pi, nudged so the maximum lands on PV_PEAK_HOUR.
    span = PV_SUNSET - PV_SUNRISE
    x = (h - PV_SUNRISE) / span
    skew = (PV_PEAK_HOUR - PV_SUNRISE) / span
    x = x ** (math.log(0.5) / math.log(skew))
    return peak * math.sin(math.pi * x) ** 1.15


def build(anchors, hours, wobble, seed, lo=0.0, hi=None):
    vals = smooth([lerp(anchors, h) for h in hours])
    out = []
    for h, v in zip(hours, vals):
        v += texture(h, wobble, seed)
        v = max(lo, v if hi is None else min(hi, v))
        out.append(round(v, 1))
    return out


def build_solar(peak, rated, hours, wobble, seed):
    out = []
    for h in hours:
        v = solar(h, peak)
        if v > 0:
            # Cloud texture only while the sun is up, and never below zero.
            v = max(0.0, v + texture(h, wobble, seed) * (v / peak))
        out.append(round(min(v, rated), 1))
    return out


def write(name, hours, values, note):
    path = os.path.join(OUT, name)
    with open(path, 'w') as f:
        f.write('hour,kW\n')
        for h, v in zip(hours, values):
            f.write('%.2f,%.1f\n' % (h, v))
    lo, hi = min(values), max(values)
    print('%-24s %3d points  %8.1f - %8.1f kW   %s' % (name, len(values), lo, hi, note))
    return lo, hi


def main():
    wrapping = [i * STEP for i in range(int(24 / STEP))]          # 00:00-23:45
    clamping = wrapping + [24.0]                                  # + closing point

    jtc = build(JTC_ANCHORS, wrapping, 9.0, 1.7)
    t98 = build(T98_ANCHORS, clamping, 11.0, 0.9)
    t98[-1] = t98[0]
    t7 = build_solar(T7_PEAK, T7_RATED, wrapping, 7.0, 2.3)
    t10 = build_solar(T10_PEAK, T10_RATED, clamping, 1.6, 3.1)
    t10[-1] = t10[0]

    write('jtc_common_curve.csv', wrapping, jtc, 'JTC common load (EGC grid reference)')
    write('load_curve.csv', clamping, t98, 'Tower 10 / T98 tenant load')
    write('t7_pv_curve.csv', wrapping, t7, 'Tower 7 rooftop PV, 240 kW rated')
    write('pv_curve.csv', clamping, t10, 'Tower 10 rooftop PV, 52 kW rated')

    # The curves only earn their keep if each use-case is actually reachable.
    print()
    over = [h for h, v in zip(wrapping, jtc) if v > MAX_IMPORT]
    print('use-case 1  common load over %.0f kW for %.2f h, peak %.1f kW (excursion %.1f kW)'
          % (MAX_IMPORT, len(over) * STEP, max(jtc), max(jtc) - MAX_IMPORT))
    print('            margin line %.0f kW crossed on the way up and down'
          % (MAX_IMPORT * (1 - MARGIN)))
    print('use-case 2  common load minimum %.1f kW, %.1f kW of discharge headroom over the %.0f kW floor'
          % (min(jtc), min(jtc) - FLOOR, FLOOR))
    print('use-case 3  T98 peak %.1f kW leaves %.1f kW under the %.0f kW multi-loop limit,'
          % (max(t98), T98_LOOP - max(t98), T98_LOOP))
    print('            so a %.0f kW charge request must be cut back for %.2f h of the day'
          % (PCS, sum(STEP for v in t98 if T98_LOOP - v < PCS)))
    night = min(t98)
    print('            overnight T98 %.1f kW leaves %.1f kW, the whole PCS fits'
          % (night, T98_LOOP - night))


if __name__ == '__main__':
    main()
