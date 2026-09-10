# -*- coding: utf-8 -*-
"""The Tower 10 (T98) load curve, and the header both older loaders need.

`LoadModel` and `PVModel` discard their first row as a header. Their curves used
to have none, so midnight was silently thrown away; the generated files carry
`hour,kW` now, and these tests pin that -- a regenerated file that lost its
header would drop a point without failing anything else.

The shipped `load_curve.csv` is all zeros by operator decision: only the JTC
common load is simulated. The interpolation tests therefore run against their
own fixture, not the shipped file, so the model stays covered whichever way the
generator's T98_LOAD_ZERO switch is set.
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

    def test_shipped_curve_is_zero_all_day(self):
        # Deliberate: the Tower 10 tenant load is not simulated, so unit 8
        # reads a flat zero and Meter_01 carries only PV and the battery. If
        # this ever fails, someone regenerated with T98_LOAD_ZERO = False --
        # which is a decision, not an accident, so update this test with it.
        self.assertEqual(set(self.m.power_curve.values()), {0.0})
        for i in range(0, 2400, 7):
            self.assertEqual(self.m.interpolate_power(i / 100.0), 0.0)

    def test_zero_load_leaves_the_multi_loop_cap_unreachable(self):
        # Recorded as a consequence rather than a defect: the EGC caps charging
        # at 1,750 kW minus the T98 load, so at zero the whole PCS always fits
        # and use-case 3 never binds.
        peak = max(self.m.power_curve.values())
        self.assertGreater(T98_LOOP_LIMIT - peak, PCS)


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
