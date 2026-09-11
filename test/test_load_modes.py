# -*- coding: utf-8 -*-
"""The load's three sources, and the two registers that switch between them.

`LoadModel` reads its day from a CSV, invents one around `base_power`, or holds
a number the operator typed in. The choice is a control register rather than a
config field, so an operator -- or a controller over Modbus -- can change it
mid-run without a restart, the same way the inverter limit and the battery
setpoint work.

`main.py` passes register 2 as `mode` and register 4 as `p_set` on every tick.
Those offsets are asserted here against the register map, because nothing else
would notice if the two drifted apart: the load would quietly keep running on
whatever it started with.
"""
import os
import tempfile
import unittest

from src.communication import web_server
from src.models.load_model import LoadModel, MODE_CURVE, MODE_SIM, MODE_MANUAL
from config.modbus_registers import REGISTERS

BASE = 400.0


def curve_file():
    fd, path = tempfile.mkstemp(suffix='.csv', dir='config')
    with os.fdopen(fd, 'w') as f:
        f.write('hour,kW\n')
        for i in range(97):
            f.write('%.2f,%.1f\n' % (i * 0.25, 1000.0))
    return path


class Sources(unittest.TestCase):

    def setUp(self):
        self.path = curve_file()
        self.cfg = {'DeviceKey': 'L', 'base_power': BASE, 'mode': MODE_CURVE,
                    'csv_file': os.path.basename(self.path)}
        self.m = LoadModel(self.cfg, 12.0)

    def tearDown(self):
        os.unlink(self.path)

    def test_curve_is_the_configured_source(self):
        self.assertEqual(self.m.update()['Load.Power'], 1000.0)

    def test_manual_holds_the_number_it_was_given(self):
        self.m.update(mode=MODE_MANUAL, p_set=640.0)
        self.assertEqual(self.m.update(mode=MODE_MANUAL)['Load.Power'], 640.0)

    def test_manual_starts_at_the_base_power(self):
        # Before anyone types anything, the register main.py seeded is the base
        # power, so switching to manual holds a sensible figure rather than zero.
        self.assertEqual(self.m.update(mode=MODE_MANUAL)['Load.Power'], BASE)

    def test_manual_refuses_a_negative(self):
        self.m.update(mode=MODE_MANUAL, p_set=-50.0)
        self.assertEqual(self.m.power, 0.0)

    def test_simulated_wanders_around_the_base_power(self):
        seen = set()
        for _ in range(20):
            v = self.m.update(mode=MODE_SIM)['Load.Power']
            self.assertTrue(BASE * 0.8 <= v <= BASE * 1.2, v)
            seen.add(v)
        self.assertGreater(len(seen), 1)          # a day, not a constant

    def test_switching_back_returns_to_the_curve(self):
        self.m.update(mode=MODE_MANUAL, p_set=640.0)
        self.m.update(mode=MODE_SIM)
        self.assertEqual(self.m.update(mode=MODE_CURVE)['Load.Power'], 1000.0)

    def test_an_unknown_mode_is_ignored(self):
        # A stray Modbus write must not leave the load running on nothing.
        self.m.update(mode=7)
        self.assertEqual(self.m.mode, MODE_CURVE)
        self.assertEqual(self.m.update(mode=7)['Load.Power'], 1000.0)

    def test_no_arguments_keeps_the_configured_source(self):
        self.assertEqual(self.m.update()['Load.Power'], 1000.0)


class CurveLoadedOnDemand(unittest.TestCase):
    """A device that started synthetic never read its CSV; switching must fetch it."""

    def setUp(self):
        self.path = curve_file()

    def tearDown(self):
        os.unlink(self.path)

    def test_switching_to_the_curve_loads_it(self):
        m = LoadModel({'DeviceKey': 'L', 'base_power': BASE, 'mode': MODE_SIM,
                       'csv_file': os.path.basename(self.path)}, 12.0)
        self.assertEqual(m.power_curve, {})
        self.assertEqual(m.update(mode=MODE_CURVE)['Load.Power'], 1000.0)
        self.assertEqual(len(m.power_curve), 97)

    def test_a_missing_file_falls_back_to_simulated_not_to_zero(self):
        m = LoadModel({'DeviceKey': 'L', 'base_power': BASE, 'mode': MODE_SIM,
                       'csv_file': 'no_such_curve.csv'}, 12.0)
        v = m.update(mode=MODE_CURVE)['Load.Power']
        self.assertTrue(BASE * 0.8 <= v <= BASE * 1.2, v)


class ControlRegisters(unittest.TestCase):

    def test_the_offsets_main_passes_are_the_offsets_in_the_map(self):
        points = REGISTERS['Load']['points']
        self.assertEqual(points['Load.ModeSet'], 2)     # main.py: data.get(2)
        self.assertEqual(points['Load.PowerSet'], 4)    # main.py: data.get(4)

    def test_both_are_writable_and_the_reading_is_not(self):
        self.assertEqual(web_server.WRITABLE['Load'],
                         {'Load.ModeSet': 2, 'Load.PowerSet': 4})
        self.assertNotIn('Load.Power', web_server.WRITABLE['Load'])

    def test_writable_offsets_match_the_register_map(self):
        for name, offset in web_server.WRITABLE['Load'].items():
            self.assertEqual(REGISTERS['Load']['points'][name], offset, name)


class ConfiguredSource(unittest.TestCase):
    """device.json may start the load in any of the three; the PV in two."""

    def config(self, dev_type, mode):
        dev = {'DeviceKey': 'X', 'mode': mode}
        if dev_type == 'Load':
            dev['base_power'] = BASE
        else:
            dev['ratedPower'] = 52
        return {'start_time': 'now', 'Devices': [{dev_type: [dev]}]}

    def test_manual_is_a_valid_load_source(self):
        self.assertEqual(web_server.validate_config(self.config('Load', 2)), [])

    def test_manual_is_not_a_valid_pv_source(self):
        errors = web_server.validate_config(self.config('PV', 2))
        self.assertTrue(any('source must be' in e for e in errors), errors)

    def test_the_two_old_sources_still_validate(self):
        for mode in (0, 1):
            self.assertEqual(web_server.validate_config(self.config('Load', mode)), [])
            self.assertEqual(web_server.validate_config(self.config('PV', mode)), [])


if __name__ == '__main__':
    unittest.main()
