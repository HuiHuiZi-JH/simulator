# -*- coding: utf-8 -*-
"""Tower 7 PV reads its curve directly and derives nothing.

Run from the repository root:  python3 -m unittest discover -s test -t .
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.t7_pv_model import T7PVModel


class T7PVCurve(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False)
        f.write("hour,kW\n0.0,0\n6.0,0\n13.0,150\n20.0,0\n")
        f.close()
        self.path = f.name
        self.m = T7PVModel({'DeviceKey': 'T7_PV_01', 'csv_file': self.path}, 0.0)

    def tearDown(self):
        os.unlink(self.path)

    def test_reads_the_curve_directly(self):
        self.assertAlmostEqual(self.m.interpolate(6.0), 0.0)
        self.assertAlmostEqual(self.m.interpolate(13.0), 150.0)
        self.assertAlmostEqual(self.m.interpolate(9.5), 75.0)

    def test_publishes_power_and_energy(self):
        self.assertEqual(sorted(self.m.update()),
                         ['T7PV.APProductionKWH', 'T7PV.GenActivePW'])

    def test_no_curve_fails_loudly(self):
        with self.assertRaises(ValueError):
            T7PVModel({'DeviceKey': 'T7_PV_01'}, 0.0)

    def test_missing_file_fails_loudly(self):
        with self.assertRaises(ValueError):
            T7PVModel({'DeviceKey': 'T7_PV_01', 'csv_file': '/nope.csv'}, 0.0)


class ShippedCurve(unittest.TestCase):
    def test_repository_curve_is_a_solar_day(self):
        m = T7PVModel({'DeviceKey': 'T7_PV_01', 'csv_file': 't7_pv_curve.csv'}, 0.0)
        self.assertEqual(len(m.curve), 96)
        low, high = m.curve.span
        self.assertEqual(low, 0.0)                     # dark overnight
        self.assertLessEqual(high, 240.0)              # Tower 7 plate rating
        self.assertGreater(high, 150.0)                # and a real day under it
        self.assertAlmostEqual(m.interpolate(2.0), 0.0)
        self.assertGreater(m.interpolate(13.0), 150.0)


if __name__ == '__main__':
    unittest.main()
