# -*- coding: utf-8 -*-
"""The rolling power ring the trend chart reads.

What matters here is that the ring stays bounded, that an incremental read
joins up with what the client already holds, and that a slow tick cannot make
the sample clock drift -- a chart drawn on evenly spaced samples would lie
about time otherwise.
"""
import threading
import unittest

from src.history import PowerHistory, TRACKED_POINT


def devices():
    return {
        'Meter_01':      {'type': 'Meter',       'data': {0: -100.0, 2: 1.0, 4: 2.0}},
        'PV_01':         {'type': 'PV',          'data': {0: 100.0}},
        'BESS_01':       {'type': 'BESS',        'data': {0: -25.0, 2: 50.0}},
        'LOAD_001':      {'type': 'Load',        'data': {0: 0.0}},
        'JTC_COMMON_01': {'type': 'JTCLoad',     'data': {0: 185.23}},
        'VGRID_01':      {'type': 'VirtualGrid', 'data': {0: 160.23}},
    }


class TestPowerHistory(unittest.TestCase):

    def setUp(self):
        self.lock = threading.Lock()
        self.dev = devices()

    def fill(self, hist, count, start=1000.0):
        """Take `count` samples on schedule, bumping PV so they differ."""
        for i in range(count):
            hist.record(start + i * hist.interval, 9.0 + i * 0.01, self.dev, self.lock)
            self.dev['PV_01']['data'][0] += 1

    def test_ring_is_bounded(self):
        h = PowerHistory(interval=10.0, maxlen=5)
        self.fill(h, 12)
        d = h.dump()
        self.assertEqual(d['count'], 5)
        self.assertEqual(len(d['series']['PV_01']), 5)
        # The counter keeps running even though older samples have fallen off.
        self.assertEqual(d['seq'], 12)
        self.assertEqual(d['first_seq'], 8)

    def test_incremental_read_joins_up(self):
        h = PowerHistory(interval=10.0)
        self.fill(h, 6)
        full = h.dump()
        part = h.dump(after=full['seq'] - 2)
        self.assertEqual(part['count'], 2)
        self.assertEqual(part['series']['PV_01'], full['series']['PV_01'][-2:])
        self.assertEqual(h.dump(after=full['seq'])['count'], 0)

    def test_interval_is_respected_and_does_not_drift(self):
        h = PowerHistory(interval=10.0)
        self.assertTrue(h.record(0.0, 9.0, self.dev, self.lock))
        # Too soon: no sample.
        self.assertFalse(h.record(4.0, 9.0, self.dev, self.lock))
        # A tick that arrives late takes its sample, but the next one is due on
        # the original cadence rather than 10 s after the late arrival.
        self.assertTrue(h.record(35.0, 9.0, self.dev, self.lock))
        self.assertFalse(h.record(39.0, 9.0, self.dev, self.lock))
        self.assertTrue(h.record(40.0, 9.0, self.dev, self.lock))
        self.assertEqual(h.dump()['count'], 3)

    def test_tracks_one_point_per_device_and_ignores_unknown_types(self):
        self.dev['MYSTERY'] = {'type': 'Nonesuch', 'data': {0: 42.0}}
        h = PowerHistory(interval=10.0)
        self.fill(h, 1)
        d = h.dump()
        self.assertNotIn('MYSTERY', d['series'])
        self.assertEqual(sorted(d['series']), sorted(devices()))
        self.assertEqual(d['series']['Meter_01'], [-100.0])
        self.assertEqual(d['series']['JTC_COMMON_01'], [185.23])
        for entry in d['devices']:
            self.assertEqual(entry['point'], TRACKED_POINT[entry['type']])

    def test_empty_ring_dumps_cleanly(self):
        d = PowerHistory().dump()
        self.assertEqual(d['count'], 0)
        self.assertEqual(d['sim_hour'], [])
        self.assertEqual(d['series'], {})
        self.assertIsNone(d['first_seq'])

    def test_sim_hour_wraps_at_midnight(self):
        h = PowerHistory(interval=10.0)
        h.record(0.0, 25.5, self.dev, self.lock)
        self.assertEqual(h.dump()['sim_hour'], [1.5])


if __name__ == '__main__':
    unittest.main()
