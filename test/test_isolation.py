# -*- coding: utf-8 -*-
"""The virtual grid point must not change what the Tower 10 loop reports.

The JTC common load and the virtual grid meter sit above the EGC control
boundary. They read the simulation; nothing about them may feed back into it.
These tests pin that invariant, because the failure mode is silent -- the
Tower 10 meter would simply start reporting a different number.

Run from the repository root:  python3 -m unittest discover -s test -v
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.meter_model import MeterModel
from src.models.virtual_grid_model import VirtualGridModel
from utils.locks import data_lock


def t98_devices():
    """The Tower 10 devices, with fixed register values so the sums are exact."""
    return {
        'PV_01':    {'type': 'PV',   'model': object(), 'data': {0: 40.0}},
        'BESS_01':  {'type': 'BESS', 'model': object(), 'data': {0: 500.0}},
        'EV_01':    {'type': 'EV',   'model': object(), 'data': {0: 7.0}},
        'LOAD_001': {'type': 'Load', 'model': object(), 'data': {0: 60.0}},
    }


def off_loop_devices():
    return {
        'JTC_COMMON_01': {'type': 'JTCLoad',     'model': object(), 'data': {0: 1240.0}},
        'T7_PV_01':      {'type': 'T7PV',        'model': object(), 'data': {0: 150.0}},
        'VGRID_01':      {'type': 'VirtualGrid', 'model': object(), 'data': {0: 0.0}},
    }


class MeterIsolation(unittest.TestCase):
    def test_off_loop_devices_do_not_move_the_t98_meter(self):
        alone = MeterModel({'DeviceKey': 'Meter_01'}, t98_devices(), data_lock, 12.0).update()

        with_jtc = dict(t98_devices())
        with_jtc.update(off_loop_devices())
        alongside = MeterModel({'DeviceKey': 'Meter_01'}, with_jtc, data_lock, 12.0).update()

        self.assertEqual(alone['ActivePower'], alongside['ActivePower'])
        # 60 load + 7 EV + 500 charge - 40 PV
        self.assertAlmostEqual(alone['ActivePower'], 527.0)

    def test_meter_ignores_the_jtc_load_however_large(self):
        huge = dict(t98_devices())
        huge['JTC_COMMON_01'] = {'type': 'JTCLoad', 'model': object(),
                                 'data': {0: 99999.0}}
        meter = MeterModel({'DeviceKey': 'Meter_01'}, huge, data_lock, 12.0).update()
        self.assertAlmostEqual(meter['ActivePower'], 527.0)


class T7PVIsolation(unittest.TestCase):
    """Tower 7 is pass-through: its PV belongs to neither measurement."""

    def setUp(self):
        self.devices = dict(t98_devices())
        self.devices.update(off_loop_devices())

    def test_t7_pv_stays_out_of_the_tower_10_meter(self):
        meter = MeterModel({'DeviceKey': 'Meter_01'}, self.devices, data_lock, 12.0).update()
        self.assertAlmostEqual(meter['ActivePower'], 527.0)   # unchanged by 150 kW of T7 PV

    def test_t7_pv_stays_out_of_the_virtual_grid_point(self):
        out = VirtualGridModel({'DeviceKey': 'VGRID_01'}, self.devices,
                               data_lock, 12.0).update()
        self.assertAlmostEqual(out['VG.ActivePower'], 1740.0)


class VirtualGridIsolation(unittest.TestCase):
    def setUp(self):
        self.devices = dict(t98_devices())
        self.devices.update(off_loop_devices())
        self.cfg = {'DeviceKey': 'VGRID_01', 'max_import': 1700,
                    'bess_zero_export': 150, 't98_loop_limit': 0}

    def test_update_writes_nothing_back(self):
        before = copy.deepcopy({k: v['data'] for k, v in self.devices.items()})
        VirtualGridModel(self.cfg, self.devices, data_lock, 12.0).update()
        after = {k: v['data'] for k, v in self.devices.items()}
        self.assertEqual(before, after)

    def test_sums_jtc_common_load_and_bess_only(self):
        out = VirtualGridModel(self.cfg, self.devices, data_lock, 12.0).update()
        # 1240 common load + 500 charge. The T98 load, EV and PV are already
        # inside the common-load figure, and the T7 plant is outside the
        # boundary altogether -- none of them may be counted here.
        self.assertAlmostEqual(out['VG.ActivePower'], 1740.0)
        self.assertAlmostEqual(out['VG.MaxImport'], 1700.0)

    def test_battery_discharge_lowers_the_virtual_import(self):
        self.devices['BESS_01']['data'][0] = -400.0
        out = VirtualGridModel(self.cfg, self.devices, data_lock, 12.0).update()
        self.assertAlmostEqual(out['VG.ActivePower'], 840.0)

    def test_explicit_sources_override_the_type_rule(self):
        cfg = dict(self.cfg, sources=[{'device': 'JTC_COMMON_01', 'sign': 1},
                                      {'device': 'PV_01', 'sign': -1}])
        out = VirtualGridModel(cfg, self.devices, data_lock, 12.0).update()
        self.assertAlmostEqual(out['VG.ActivePower'], 1200.0)


if __name__ == '__main__':
    unittest.main()
