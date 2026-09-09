# -*- coding: utf-8 -*-
"""Tower 7 PV -- generation outside the Tower 10 network.

T7 is one of the towers the EGC does not control: on the diagram it is part of
the pass-through subtracted out of the virtual incoming figure. Its PV plant is
simulated so its output is visible, but it belongs to neither loop --

  * MeterModel matches the type 'PV', so the T7 plant carries its own type
    'T7PV' and cannot land in the Tower 10 coupling point, and
  * VirtualGridModel sums the JTC common load and the battery, so it is not in
    the virtual grid figure either.

Nothing derives from it. It is a straight readout of an operator-supplied
curve, with the generated energy integrated alongside it -- no irradiance
model, no synthetic arc, no curtailment input. To feed it into the virtual
point after all, name it in that device's `sources` list.

Generation is published positive, matching INV.GenActivePW on the Tower 10
inverter.
"""
import logging
import time

from src.models.curve import DayCurve

logger = logging.getLogger('state')


class T7PVModel:
    def __init__(self, config, start_hour):
        self.config = config
        self.start_hour = start_hour
        self.start_time = time.time()
        self.power = 0.0
        self.production_kwh = 0.0
        self._last_time = None
        self.curve = DayCurve(config.get('csv_file'), config.get('DeviceKey'))
        low, high = self.curve.span
        logger.info("T7PVModel initialized for %s: %d points from %s, "
                    "%.1f-%.1f kW, start hour = %.4f",
                    config.get('DeviceKey'), len(self.curve), self.curve.path,
                    low, high, self.start_hour)

    def interpolate(self, hour):
        return self.curve.at(hour)

    def update(self):
        # Energy integrates on a monotonic clock, so the kWh total stays honest
        # if a cycle runs long or the system clock moves.
        now = time.monotonic()
        dt_hours = 0.0 if self._last_time is None else (now - self._last_time) / 3600.0
        self._last_time = now
        try:
            elapsed_hours = (time.time() - self.start_time) / 3600.0
            self.power = self.interpolate((self.start_hour + elapsed_hours) % 24.0)
            self.production_kwh += max(0.0, self.power) * dt_hours
        except Exception as e:
            logger.error("Error in T7PVModel update for %s: %s",
                         self.config.get('DeviceKey'), e)
            self.power = 0.0
        logger.debug("T7PVModel update: Power = %.3f kW, Produced = %.4f kWh",
                     self.power, self.production_kwh)
        return {
            'T7PV.GenActivePW': self.power,
            'T7PV.APProductionKWH': self.production_kwh,
        }
