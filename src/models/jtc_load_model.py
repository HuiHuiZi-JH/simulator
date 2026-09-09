# -*- coding: utf-8 -*-
"""JTC common load -- the landlord / common-services demand on the diagram.

This is not the building load model wearing a different name. It shares no code
with LoadModel and deliberately has no synthetic mode: the JTC common load is
whatever profile the operator supplies as a CSV, and nothing else. If the file
is missing or unreadable the device fails loudly at startup rather than
inventing a plausible-looking curve, because a quietly fabricated common load
would make the virtual grid point wrong without looking wrong.

It also carries its own device type. MeterModel sums PV, BESS, EV and Load, so
a distinct type is what keeps this load out of the Tower 10 control loop while
the virtual grid point above the EGC boundary can still see it.
"""
import logging
import time

from src.models.curve import DayCurve

logger = logging.getLogger('state')


class JTCLoadModel:
    def __init__(self, config, start_hour):
        self.config = config
        self.start_hour = start_hour
        self.start_time = time.time()
        self.power = 0.0
        self.curve = DayCurve(config.get('csv_file'), config.get('DeviceKey'))
        low, high = self.curve.span
        logger.info("JTCLoadModel initialized for %s: %d points from %s, "
                    "%.1f-%.1f kW, start hour = %.4f",
                    config.get('DeviceKey'), len(self.curve), self.curve.path,
                    low, high, self.start_hour)

    def interpolate(self, hour):
        return self.curve.at(hour)

    def update(self):
        try:
            elapsed_hours = (time.time() - self.start_time) / 3600.0
            self.power = self.interpolate((self.start_hour + elapsed_hours) % 24.0)
        except Exception as e:
            logger.error("Error in JTCLoadModel update for %s: %s",
                         self.config.get('DeviceKey'), e)
            self.power = 0.0
        logger.debug("JTCLoadModel update: Power = %.3f kW", self.power)
        return {'JTC.Power': self.power}
