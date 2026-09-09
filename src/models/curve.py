# -*- coding: utf-8 -*-
"""Operator-supplied day curves: read a CSV, interpolate it, wrap at midnight.

Shared by the devices that are driven *directly* by a file the operator writes
-- the JTC common load and the T7 PV plant -- as opposed to the older models,
which read a curve as one of several behaviours. Those keep their own loaders;
nothing here changes them.

Because these files come from outside the repository the reader is deliberate
about failure: a missing, empty or unreadable file raises rather than falling
back to a plausible-looking substitute. A fabricated curve would put a derived
measurement at a wrong value without looking wrong.
"""
import csv
import logging
import os

logger = logging.getLogger('state')

CONFIG_DIR = 'config'


def resolve(csv_file, owner):
    """Curve paths are relative to config/ unless given absolute."""
    if not csv_file:
        raise ValueError("csv_file is required for %s: this device is defined "
                         "by its curve, there is no fallback" % owner)
    return csv_file if os.path.isabs(csv_file) else os.path.join(CONFIG_DIR, csv_file)


class DayCurve:
    """A 24-hour `hour,value` profile at any resolution.

    Tolerates what an operator-written file actually looks like: an optional
    header, blank lines, `#` comments, and points spaced however the source
    data happened to be sampled.
    """

    def __init__(self, csv_file, owner):
        self.path = resolve(csv_file, owner)
        self.owner = owner
        self.points = self._read()

    def _read(self):
        if not os.path.exists(self.path):
            raise ValueError("CSV file %s not found for %s" % (self.path, self.owner))
        points = {}
        with open(self.path, 'r', encoding='utf-8-sig') as f:
            for line_no, row in enumerate(csv.reader(f), start=1):
                if len(row) < 2 or not row[0].strip() or row[0].lstrip().startswith('#'):
                    continue
                try:
                    hour, value = float(row[0]), float(row[1])
                except ValueError:
                    # A header line, not bad data -- but only on the first row.
                    if line_no > 1:
                        logger.warning("Ignoring unreadable row %d in %s: %r",
                                       line_no, self.path, row)
                    continue
                if not 0.0 <= hour <= 24.0:
                    logger.warning("Ignoring out-of-range hour %s in %s", hour, self.path)
                    continue
                points[hour % 24.0] = value   # 24.0 and 0.0 are the same instant
        if not points:
            raise ValueError("No usable hour,value rows in %s" % self.path)
        return sorted(points.items())

    def __len__(self):
        return len(self.points)

    @property
    def span(self):
        values = [v for _, v in self.points]
        return min(values), max(values)

    def at(self, hour):
        """Linear interpolation between neighbouring points, wrapping midnight."""
        points = self.points
        if len(points) == 1:
            return points[0][1]

        # Last point of the day joined to the first point of the next, so the
        # curve is continuous rather than flat across the midnight gap.
        first_h, first_v = points[0]
        last_h, last_v = points[-1]
        if hour < first_h or hour >= last_h:
            t1, v1 = last_h, last_v
            t2, v2 = first_h + 24.0, first_v
            if hour < first_h:
                hour += 24.0
        else:
            lo, hi = 0, len(points) - 1
            while hi - lo > 1:                 # binary search the bracketing pair
                mid = (lo + hi) // 2
                if points[mid][0] <= hour:
                    lo = mid
                else:
                    hi = mid
            (t1, v1), (t2, v2) = points[lo], points[hi]

        span = t2 - t1
        if span <= 0:
            return v1
        return v1 + (v2 - v1) * (hour - t1) / span
