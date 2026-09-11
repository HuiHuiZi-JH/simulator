# -*- coding: utf-8 -*-
"""JTC common load -- the landlord / common-services demand on the diagram.

This is not the building load model wearing a different name. It shares no code
with LoadModel and has no synthetic mode: the JTC common load is the profile the
operator supplies as a CSV, or a figure the operator types in, and never a
number the simulator invented for itself. If the file is missing or unreadable
the device fails loudly at startup rather than fabricating a plausible-looking
curve, because a quietly fabricated common load would make the virtual grid
point wrong without looking wrong.

The manual source is not a fallback and does not weaken that rule -- it is an
operator deliberately holding the common load at a stated figure to exercise a
controller against it, the same way `LoadModel` is driven by hand. What the rule
forbids is the *simulator* choosing a value; both sources here are the
operator's own.

It also carries its own device type. MeterModel sums PV, BESS, EV and Load, so
a distinct type is what keeps this load out of the Tower 10 control loop while
the virtual grid point above the EGC boundary can still see it.
"""
import logging
import time

from src.models.curve import DayCurve

logger = logging.getLogger('state')

# The two sources this load can run from, numbered as `LoadModel` numbers its
# own: an operator or an EMS writing 2 means "manual" at every device that has
# one. 1 -- the synthetic day -- deliberately has no counterpart here, and a
# write of 1 is refused rather than quietly treated as something else.
MODE_CURVE, MODE_MANUAL = 0, 2
MODES = (MODE_CURVE, MODE_MANUAL)


class JTCLoadModel:
    def __init__(self, config, start_hour):
        self.config = config
        self.start_hour = start_hour
        self.start_time = time.time()
        self.power = 0.0
        self.curve = DayCurve(config.get('csv_file'), config.get('DeviceKey'))
        self.mode = self.coerce_mode(config.get('mode', MODE_CURVE), MODE_CURVE)
        # Manual opens where the curve opens. `manual_power` in device.json wins
        # if it is there; otherwise the figure comes from the operator's own
        # file at the simulated start hour, so switching to manual holds
        # something this site actually draws rather than a constant from the
        # repository or a zero nobody asked for.
        self.manual_power = self.coerce_power(config.get('manual_power'),
                                              self.curve.at(start_hour % 24.0))
        low, high = self.curve.span
        logger.info("JTCLoadModel initialized for %s: %d points from %s, "
                    "%.1f-%.1f kW, start hour = %.4f, mode = %d, "
                    "manual power = %.1f kW",
                    config.get('DeviceKey'), len(self.curve), self.curve.path,
                    low, high, self.start_hour, self.mode, self.manual_power)

    @staticmethod
    def coerce_mode(value, current):
        """A mode register holds a float and may hold anything at all.

        Anything that is not one of this device's two sources leaves the mode
        where it was: a Modbus client writing 1 -- the building load's synthetic
        day -- must not land on a source this device does not have.
        """
        try:
            requested = int(round(float(value)))
        except (TypeError, ValueError):
            return current
        return requested if requested in MODES else current

    @staticmethod
    def coerce_power(value, current):
        """A demand, so never negative. Unreadable writes leave it alone."""
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return current

    def interpolate(self, hour):
        return self.curve.at(hour)

    def update(self, mode=None, p_set=None):
        """One tick. `mode` and `p_set` are the two control registers, so a
        write from the dashboard or from Modbus takes effect on the next cycle
        without a restart; with neither given the model runs on its configured
        source."""
        rejected = False
        try:
            if mode is not None:
                self.mode = self.coerce_mode(mode, self.mode)
                rejected = not self.reads_as(mode, self.mode)
            if p_set is not None:
                self.manual_power = self.coerce_power(p_set, self.manual_power)

            if self.mode == MODE_MANUAL:
                self.power = self.manual_power
            else:
                elapsed_hours = (time.time() - self.start_time) / 3600.0
                self.power = self.interpolate((self.start_hour + elapsed_hours) % 24.0)
        except Exception as e:
            logger.error("Error in JTCLoadModel update for %s: %s",
                         self.config.get('DeviceKey'), e)
            self.power = 0.0
        logger.debug("JTCLoadModel update: Mode = %d, Power = %.3f kW",
                     self.mode, self.power)
        out = {'JTC.Power': self.power}
        if rejected:
            # An input is normally never republished -- a write stands until the
            # next one. A *refused* write is the exception: 1 is a real source on
            # the building load, so an EMS that writes it here would otherwise
            # read the register back as 1 and believe this load was running on a
            # synthetic day it does not have. Correcting only the refused value
            # leaves every accepted write untouched.
            logger.warning("JTCLoadModel %s refused source %s; register reset to %d",
                           self.config.get('DeviceKey'), mode, self.mode)
            out['JTC.ModeSet'] = float(self.mode)
        return out

    @staticmethod
    def reads_as(written, mode):
        """Whether the register's value already means this mode."""
        try:
            return int(round(float(written))) == mode
        except (TypeError, ValueError):
            return False
