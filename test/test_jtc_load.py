# -*- coding: utf-8 -*-
"""The JTC common load is whatever curve the operator supplies -- and only that.

Run from the repository root:  python3 -m unittest discover -s test -t .
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.jtc_load_model import JTCLoadModel


def curve_file(text):
    f = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False)
    f.write(text)
    f.close()
    return f.name


class CurveLoading(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            os.unlink(path)

    def model(self, text, start_hour=0.0):
        path = curve_file(text)
        self.paths.append(path)
        return JTCLoadModel({'DeviceKey': 'JTC_TEST', 'csv_file': path}, start_hour)

    def test_header_row_is_detected_not_assumed(self):
        with_header = self.model("hour,kW\n0.0,100\n12.0,400\n")
        without_header = self.model("0.0,100\n12.0,400\n")
        # The older loaders drop the first data row unconditionally; this one
        # keeps midnight when there is no header to skip.
        self.assertEqual(len(with_header.curve), 2)
        self.assertEqual(len(without_header.curve), 2)
        self.assertEqual(with_header.curve, without_header.curve)

    def test_blank_and_commented_lines_are_skipped(self):
        m = self.model("hour,kW\n\n# overnight\n0.0,100\n\n12.0,400\n")
        self.assertEqual(m.curve, [(0.0, 100.0), (12.0, 400.0)])

    def test_missing_file_fails_loudly(self):
        with self.assertRaises(ValueError):
            JTCLoadModel({'DeviceKey': 'JTC_TEST',
                          'csv_file': '/nonexistent/curve.csv'}, 0.0)

    def test_no_csv_configured_fails_loudly(self):
        # There is no synthetic fallback to quietly stand in for the curve.
        with self.assertRaises(ValueError):
            JTCLoadModel({'DeviceKey': 'JTC_TEST'}, 0.0)

    def test_empty_curve_fails_loudly(self):
        with self.assertRaises(ValueError):
            self.model("hour,kW\n")


class Interpolation(unittest.TestCase):
    def setUp(self):
        self.path = curve_file("hour,kW\n0.0,100\n6.0,400\n18.0,1000\n")
        self.m = JTCLoadModel({'DeviceKey': 'JTC_TEST', 'csv_file': self.path}, 0.0)

    def tearDown(self):
        os.unlink(self.path)

    def test_exact_points(self):
        self.assertAlmostEqual(self.m.interpolate(0.0), 100.0)
        self.assertAlmostEqual(self.m.interpolate(6.0), 400.0)
        self.assertAlmostEqual(self.m.interpolate(18.0), 1000.0)

    def test_between_points_is_linear_at_any_resolution(self):
        self.assertAlmostEqual(self.m.interpolate(3.0), 250.0)
        self.assertAlmostEqual(self.m.interpolate(12.0), 700.0)
        # Not quantised to quarter hours, unlike the older load curve reader.
        self.assertAlmostEqual(self.m.interpolate(0.1), 105.0)

    def test_midnight_wrap_joins_last_point_to_first(self):
        # 18:00 = 1000 kW wrapping to 00:00 = 100 kW over six hours.
        self.assertAlmostEqual(self.m.interpolate(21.0), 550.0)
        self.assertAlmostEqual(self.m.interpolate(23.0), 250.0)

    def test_update_publishes_its_own_point_name(self):
        self.assertEqual(list(self.m.update()), ['JTC.Power'])


class ShippedCurve(unittest.TestCase):
    def test_repository_curve_loads_all_96_points(self):
        m = JTCLoadModel({'DeviceKey': 'JTC_COMMON_01',
                          'csv_file': 'jtc_common_curve.csv'}, 0.0)
        self.assertEqual(len(m.curve), 96)
        self.assertAlmostEqual(m.interpolate(0.0), 560.0)
        self.assertAlmostEqual(m.interpolate(14.0), 1450.0)


if __name__ == '__main__':
    unittest.main()
