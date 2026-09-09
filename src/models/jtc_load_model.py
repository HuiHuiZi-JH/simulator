# -*- coding: utf-8 -*-
"""JTC common load -- the landlord / common-services demand on the diagram.

This is not the building load model wearing a different name. It shares no
code with LoadModel and deliberately has no synthetic mode: the JTC common
load is whatever profile the operator supplies as a CSV, and nothing else. If
the file is missing or unreadable the device fails loudly at startup rather
than inventing a plausible-looking curve, because a quietly fabricated common
load would make the virtual grid point wrong without looking wrong.

The reader is more forgiving than the older curve loaders -- a header row is
detected rather than assumed, blank and commented lines are skipped, and the
points may be at any resolution, not just quarter-hourly. Interpolation is
linear between neighbouring points and wraps across midnight, so a curve that
stops at 23:45 still joins back up to its 00:00 value.

It also carries its own device type. MeterModel sums PV, BESS, EV and Load, so
a distinct type is what keeps this load out of the Tower 10 control loop while
the virtual grid point above the EGC boundary can still see it.
"""
import csv
import logging
import os
import time

logger = logging.getLogger('state')

CONFIG_DIR = 'config'


class JTCLoadModel:
    def __init__(self, config, start_hour):
        self.config = config
        self.start_hour = start_hour
        self.start_time = time.time()
        self.power = 0.0

        csv_file = config.get('csv_file', '')
        if not csv_file:
            raise ValueError("csv_file is required for %s: the JTC common load "
                             "is defined by its curve, there is no fallback"
                             % config.get('DeviceKey'))
        self.csv_file = (csv_file if os.path.isabs(csv_file)
                         else os.path.join(CONFIG_DIR, csv_file))
        self.curve = self.load_curve()
        logger.info("JTCLoadModel initialized for %s: %d points from %s, "
                    "%.1f-%.1f kW, start hour = %.4f",
                    config.get('DeviceKey'), len(self.curve), self.csv_file,
                    min(p for _, p in self.curve), max(p for _, p in self.curve),
                    self.start_hour)

    def load_curve(self):
        """Read hour,kW pairs into a sorted list. Raises if nothing usable."""
        if not os.path.exists(self.csv_file):
            raise ValueError("CSV file %s not found for %s"
                             % (self.csv_file, self.config.get('DeviceKey')))
        points = {}
        with open(self.csv_file, 'r', encoding='utf-8-sig') as f:
            for line_no, row in enumerate(csv.reader(f), start=1):
                if len(row) < 2 or not row[0].strip() or row[0].lstrip().startswith('#'):
                    continue
                try:
                    hour, power = float(row[0]), float(row[1])
                except ValueError:
                    # A header line, not bad data -- but only on the first row.
                    if line_no > 1:
                        logger.warning("Ignoring unreadable row %d in %s: %r",
                                       line_no, self.csv_file, row)
                    continue
                if not 0.0 <= hour <= 24.0:
                    logger.warning("Ignoring out-of-range hour %s in %s",
                                   hour, self.csv_file)
                    continue
                points[hour % 24.0] = power   # 24.0 and 0.0 are the same instant
        if not points:
            raise ValueError("No usable hour,kW rows in %s" % self.csv_file)
        return sorted(points.items())

    def interpolate(self, hour):
        """Linear interpolation between neighbouring points, wrapping midnight."""
        curve = self.curve
        if len(curve) == 1:
            return curve[0][1]

        # Last point of the day joined to the first point of the next, so the
        # curve is continuous rather than flat across the midnight gap.
        first_h, first_p = curve[0]
        last_h, last_p = curve[-1]
        if hour < first_h or hour >= last_h:
            t1, p1 = last_h, last_p
            t2, p2 = first_h + 24.0, first_p
            if hour < first_h:
                hour += 24.0
        else:
            lo, hi = 0, len(curve) - 1
            while hi - lo > 1:                 # binary search the bracketing pair
                mid = (lo + hi) // 2
                if curve[mid][0] <= hour:
                    lo = mid
                else:
                    hi = mid
            (t1, p1), (t2, p2) = curve[lo], curve[hi]

        span = t2 - t1
        if span <= 0:
            return p1
        return p1 + (p2 - p1) * (hour - t1) / span

    def update(self):
        try:
            elapsed_hours = (time.time() - self.start_time) / 3600.0
            self.power = self.interpolate((self.start_hour + elapsed_hours) % 24.0)
        except Exception as e:
            logger.error("Error in JTCLoadModel update for %s: %s",
                         self.config.get('DeviceKey'), e)
            self.power = 0.0
        logger.debug("JTCLoadModel update: Power = %.3f kW", self.power)
        return {'JTC.Power': self.power}
