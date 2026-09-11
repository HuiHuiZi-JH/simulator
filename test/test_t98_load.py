# -*- coding: utf-8 -*-
"""The Tower 10 (T98) load curve, and the header both older loaders need.

`LoadModel` and `PVModel` discard their first row as a header. Their curves used
to have none, so midnight was silently thrown away; the generated files carry
`hour,kW` now, and these tests pin that -- a regenerated file that lost its
header would drop a point without failing anything else.

The shipped `load_curve.csv` is a simulated tenant day in the 1,000-1,800 kW
band the operator gave for T98. The interpolation tests run against a fixture of
their own rather than the shipped file, so the model stays covered whichever way
the generator's T98_LOAD_ZERO switch is set.
"""
import os
import tempfile
import unittest

from src.models.load_model import LoadModel
from src.models.pv_model import PVModel

T98_LOOP_LIMIT = 1750.0
PCS = 800.0


class ShippedT98Load(unittest.TestCase):

    def setUp(self):
        self.m = LoadModel({'DeviceKey': 'LOAD_001', 'base_power': 50,
                            'mode': 0, 'csv_file': 'load_curve.csv'}, 0.0)

    def test_header_keeps_every_point_including_midnight(self):
        self.assertEqual(len(self.m.power_curve), 97)
        self.assertIn(0.0, self.m.power_curve)
        self.assertIn(24.0, self.m.power_curve)

    def test_shipped_curve_runs_in_the_operator_band(self):
        # The operator gave T98 as roughly 1,000-1,800 kW, and the curve is
        # generated to sit inside that: a tenant that never goes quiet, with an
        # overnight base and a mid-afternoon peak. If this fails, someone either
        # reshaped T98_ANCHORS or set T98_LOAD_ZERO = True -- both decisions,
        # not accidents, so update this test with them.
        values = list(self.m.power_curve.values())
        self.assertGreaterEqual(min(values), 1000.0)
        self.assertLessEqual(max(values), 1800.0)
        self.assertLess(min(values), 1100.0)        # the base really is the base
        self.assertGreater(max(values), 1750.0)     # and the peak really is a peak

    def test_afternoon_peak_sits_above_the_overnight_base(self):
        self.assertGreater(self.m.interpolate_power(14.5),
                           self.m.interpolate_power(3.5) + 600.0)

    def test_load_alone_crosses_the_multi_loop_limit(self):
        # The consequence that makes use-case 3 worth having: around the peak
        # the T98 load is over the 1,750 kW limit on its own, so the EGC's
        # charge cap of `limit - load` is negative and only discharge holds the
        # loop.
        peak = max(self.m.power_curve.values())
        self.assertGreater(peak, T98_LOOP_LIMIT)

    def test_charge_cap_never_admits_the_whole_pcs(self):
        # Even at the overnight minimum the cap is under 800 kW, so a full-power
        # charge request is cut back at every hour of the day.
        headroom = T98_LOOP_LIMIT - max(self.m.power_curve.values())
        self.assertLess(T98_LOOP_LIMIT - min(self.m.power_curve.values()), PCS)
        self.assertLess(headroom, 0.0)


class LoadInterpolation(unittest.TestCase):
    """Pins the fix for the quarter-hour staircase, on a curve of its own.

    LoadModel used to round the clock to the nearest quarter hour before
    interpolating. Its curves are sampled at exactly that resolution, so the
    rounded hour always landed on a curve point: the load held one value for 15
    minutes and then jumped, and the interpolation branch never ran.
    """

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.csv', dir='config')
        with os.fdopen(fd, 'w') as f:
            f.write('hour,kW\n')
            for i in range(97):
                f.write('%.2f,%.1f\n' % (i * 0.25, 100.0 + i * 10.0))
        self.m = LoadModel({'DeviceKey': 'L', 'base_power': 50, 'mode': 0,
                            'csv_file': os.path.basename(self.path)}, 0.0)

    def tearDown(self):
        os.unlink(self.path)

    def test_moves_between_curve_points(self):
        inside = [self.m.interpolate_power(14.5 + i / 60.0) for i in range(0, 15, 3)]
        self.assertEqual(len(set(inside)), len(inside))     # no staircase
        self.assertEqual(inside, sorted(inside))            # and monotone with the ramp

    def test_sampled_points_still_read_exactly(self):
        for h in (0.0, 6.25, 14.5, 23.75, 24.0):
            self.assertAlmostEqual(self.m.interpolate_power(h), self.m.power_curve[h])

    def test_midpoint_is_the_average_of_its_neighbours(self):
        self.assertAlmostEqual(self.m.interpolate_power(14.625),
                               (self.m.power_curve[14.5] + self.m.power_curve[14.75]) / 2)

    def test_clamps_outside_the_curve(self):
        self.assertEqual(self.m.interpolate_power(-1.0), self.m.power_curve[0.0])
        self.assertEqual(self.m.interpolate_power(30.0), self.m.power_curve[24.0])


class ShippedT10PV(unittest.TestCase):

    def setUp(self):
        self.m = PVModel({'DeviceKey': 'PV_01', 'ratedPower': 52, 'mode': 0,
                          'csv_file': 'pv_curve.csv'}, 0.0)

    def test_header_keeps_every_point_including_midnight(self):
        self.assertEqual(len(self.m.power_curve), 97)
        self.assertIn(0.0, self.m.power_curve)

    def test_stays_under_the_tower_10_rating(self):
        self.assertLessEqual(max(self.m.power_curve.values()), 52.0)
        self.assertGreater(max(self.m.power_curve.values()), 30.0)

    def test_dark_overnight_and_generating_at_noon(self):
        self.assertEqual(self.m.interpolate_power(2.0), 0.0)
        self.assertEqual(self.m.interpolate_power(22.0), 0.0)
        self.assertGreater(self.m.interpolate_power(13.0), 30.0)


if __name__ == '__main__':
    unittest.main()
