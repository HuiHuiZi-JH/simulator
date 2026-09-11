# -*- coding: utf-8 -*-
"""The directional energy integration behind both sets of kWh counters.

`split_energy` is what the Tower 10 meter and the virtual grid point both use to
turn a sampled power into imported and exported kWh. The properties pinned here
are the ones the old `P_now · Δt` accumulation got wrong: a ramp is charged at
its mean rather than its end, an interval that crosses zero is divided between
the two counters instead of landing wholly in one, and a gap in the record adds
nothing at all.
"""
import unittest

from src.models.energy import MAX_STEP_HOURS, split_energy

H = 1.0 / 3600.0           # one second, the tick the simulator actually runs at


class Trapezoid(unittest.TestCase):

    def test_constant_power_is_power_times_time(self):
        imported, exported = split_energy(500.0, 500.0, 2 * H)
        self.assertAlmostEqual(imported, 500.0 * 2 * H)
        self.assertEqual(exported, 0.0)

    def test_a_ramp_is_charged_at_its_mean_not_its_end(self):
        # The old rule booked 1,800 kW for the whole interval; the mean of the
        # two readings is what actually flowed if the load moved linearly.
        imported, _ = split_energy(1000.0, 1800.0, H)
        self.assertAlmostEqual(imported, 1400.0 * H)
        self.assertLess(imported, 1800.0 * H)

    def test_export_is_the_same_arithmetic_on_the_other_side(self):
        imported, exported = split_energy(-200.0, -400.0, H)
        self.assertEqual(imported, 0.0)
        self.assertAlmostEqual(exported, 300.0 * H)

    def test_touching_zero_stays_one_trapezoid(self):
        imported, exported = split_energy(0.0, 600.0, H)
        self.assertAlmostEqual(imported, 300.0 * H)
        self.assertEqual(exported, 0.0)

    def test_a_flat_zero_books_nothing(self):
        self.assertEqual(split_energy(0.0, 0.0, H), (0.0, 0.0))


class ZeroCrossing(unittest.TestCase):
    """-200 kW to +300 kW: the site exported for the first 40% of the interval."""

    def setUp(self):
        self.imported, self.exported = split_energy(-200.0, 300.0, H)

    def test_both_counters_take_their_share(self):
        self.assertGreater(self.imported, 0.0)
        self.assertGreater(self.exported, 0.0)

    def test_each_share_is_its_own_triangle(self):
        t_cross = H * 200.0 / 500.0
        self.assertAlmostEqual(self.exported, 200.0 / 2 * t_cross)
        self.assertAlmostEqual(self.imported, 300.0 / 2 * (H - t_cross))

    def test_the_crossing_goes_the_other_way_too(self):
        # +300 kW falling to -200 kW: importing for the first 60% of it.
        imported, exported = split_energy(300.0, -200.0, H)
        self.assertAlmostEqual(imported, 150.0 * 0.6 * H)
        self.assertAlmostEqual(exported, 100.0 * 0.4 * H)

    def test_the_old_rule_would_have_put_it_all_in_one_bucket(self):
        # Recorded as the defect this replaces: P_now · Δt is the whole interval
        # at +300 kW, more import than actually flowed and no export at all.
        self.assertLess(self.imported, 300.0 * H)


class Guards(unittest.TestCase):

    def test_a_zero_or_negative_interval_books_nothing(self):
        self.assertEqual(split_energy(500.0, 500.0, 0.0), (0.0, 0.0))
        self.assertEqual(split_energy(500.0, 500.0, -H), (0.0, 0.0))

    def test_a_long_gap_is_a_gap_not_an_interval(self):
        # A suspended host would otherwise hand the counter hours of elapsed
        # time and book energy that never flowed.
        self.assertEqual(split_energy(500.0, 500.0, 2.0), (0.0, 0.0))

    def test_the_limit_itself_still_counts(self):
        imported, _ = split_energy(500.0, 500.0, MAX_STEP_HOURS)
        self.assertAlmostEqual(imported, 500.0 * MAX_STEP_HOURS)


class Conservation(unittest.TestCase):

    def test_a_day_of_intervals_matches_the_closed_form(self):
        # A triangular day, sampled every second: the sum of the trapezoids is
        # the area under the line, exactly, because the power moves linearly.
        total = 0.0
        steps = 600
        for i in range(steps):
            total += split_energy(i * 2.0, (i + 1) * 2.0, H)[0]
        self.assertAlmostEqual(total, (steps * 2.0) / 2 * (steps * H), places=9)

    def test_a_symmetric_crossing_splits_evenly(self):
        # -400 kW to +400 kW crosses at the midpoint, so each side is the same
        # triangle: half the peak over half the interval.
        imported, exported = split_energy(-400.0, 400.0, H)
        self.assertAlmostEqual(imported, 200.0 * 0.5 * H)
        self.assertAlmostEqual(exported, 200.0 * 0.5 * H)
        self.assertAlmostEqual(imported + exported, 200.0 * H)


if __name__ == '__main__':
    unittest.main()
