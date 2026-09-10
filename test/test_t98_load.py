# -*- coding: utf-8 -*-
"""The Tower 10 (T98) tenant load curve, and the header both older loaders need.

`LoadModel` and `PVModel` discard their first row as a header. Their curves used
to have none, so midnight was silently thrown away; the generated files carry
`hour,kW` now, and these tests pin that -- a regenerated file that lost its
header would drop a point without failing anything else.

The band assertions are the precondition for the EGC's third use-case: the
charge cap is 1,750 kW minus the T98 load, so a day that never loads the tower
would never exercise it.
"""
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
        # The day joins up: the closing point matches the opening one.
        self.assertAlmostEqual(self.m.power_curve[0.0], self.m.power_curve[24.0])

    def test_peak_leaves_less_headroom_than_the_pcs_can_use(self):
        # Use-case 3 only means something if a full 800 kW charge request has
        # to be cut back at some point in the day.
        peak = max(self.m.power_curve.values())
        self.assertLess(T98_LOOP_LIMIT - peak, PCS)
        self.assertLess(peak, T98_LOOP_LIMIT)      # the tower alone never trips it

    def test_overnight_leaves_room_for_the_whole_pcs(self):
        # ...and charging must still be possible at night, or a time-of-use
        # plan could never fill the battery.
        night = min(self.m.power_curve[h] for h in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
        self.assertGreater(T98_LOOP_LIMIT - night, PCS)

    def test_curve_is_a_plausible_day(self):
        vals = [self.m.interpolate_power(i / 4.0) for i in range(96)]
        self.assertTrue(all(v > 0 for v in vals))
        self.assertGreater(max(vals), 4 * min(vals))   # a working day, not a flat line


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
