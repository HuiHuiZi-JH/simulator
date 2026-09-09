# -*- coding: utf-8 -*-
"""Virtual grid incoming -- the measurement the EGC actually regulates against.

The real incoming meter also carries towers 5/6/7, the podium and the basement,
none of which the EGC controls. Those are pass-through: the controller is shown
a synthetic figure with them removed.

    Virtual grid incoming = JTC common load + BESS (Tower 10 subnet)

which is what the sources list in device.json encodes -- one entry per
contributing device with the sign it carries. BESS charging (+) pushes the
virtual import up, discharging (-) pulls it down. Nothing here feeds back into
the Tower 10 meter; this model only reads, exactly like MeterModel.
"""
import logging
import time

logger = logging.getLogger('state')

# Contribute every JTC common load and every battery when device.json does not
# say otherwise -- the formula above, expressed as device types.
DEFAULT_SOURCE_TYPES = ('JTCLoad', 'BESS')


class VirtualGridModel:
    def __init__(self, config, devices_data, data_lock, start_hour):
        self.config = config
        self.devices_data = devices_data
        self.data_lock = data_lock
        self.start_hour = start_hour
        if not devices_data or not data_lock:
            raise ValueError(
                "Invalid initialization for %s: devices_data=%s, data_lock=%s"
                % (config.get('DeviceKey'), devices_data is not None,
                   data_lock is not None))

        # EGC static settings. They are configuration, not physics -- the model
        # publishes them so a controller can read the limits it is supposed to
        # regulate against instead of carrying its own copy.
        self.max_import = float(config.get('max_import', 1700.0))
        self.bess_zero_export = float(config.get('bess_zero_export', 150.0))
        self.t98_loop_limit = float(config.get('t98_loop_limit', 0.0))

        self.sources = self._parse_sources(config.get('sources'))
        self._warned_missing = False
        self.active_power = 0.0
        self.import_kwh = 0.0
        self.export_kwh = 0.0
        self._last_time = None
        logger.info(
            "VirtualGridModel initialized for %s: max import = %.1f kW, "
            "BESS zero export = %.1f kW, T98 loop limit = %.1f kW, sources = %s",
            config.get('DeviceKey'), self.max_import, self.bess_zero_export,
            self.t98_loop_limit, self.sources or 'by type %s' % (DEFAULT_SOURCE_TYPES,))

    @staticmethod
    def _parse_sources(raw):
        """[{"device": "JTC_COMMON_01", "sign": 1}, ...] -> {key: sign}.

        Returns an empty dict when unset, which falls back to the type rule.
        """
        if not raw:
            return {}
        sources = {}
        for entry in raw:
            if not isinstance(entry, dict) or not entry.get('device'):
                logger.warning("Ignoring malformed virtual grid source: %r", entry)
                continue
            sign = entry.get('sign', 1)
            sources[entry['device']] = -1.0 if sign in (-1, '-1', '-') else 1.0
        return sources

    def _contributions(self):
        """(device key, signed kW) for everything feeding the virtual point."""
        out = []
        for device_key, dev_info in self.devices_data.items():
            if not dev_info.get('model') or 'data' not in dev_info:
                continue
            if self.sources:
                sign = self.sources.get(device_key)
                if sign is None:
                    continue
            elif dev_info['type'] in DEFAULT_SOURCE_TYPES:
                sign = 1.0
            else:
                continue
            out.append((device_key, sign * dev_info['data'].get(0, 0.0)))
        if self.sources and not self._warned_missing:
            missing = sorted(set(self.sources) - {key for key, _ in out})
            if missing:
                logger.warning("Virtual grid sources not found in device.json: %s",
                               ", ".join(missing))
            self._warned_missing = True
        return out

    def update(self):
        # Real elapsed time on a monotonic clock, so the kWh counters stay
        # honest across a long cycle or a wall-clock adjustment.
        now = time.monotonic()
        dt_hours = 0.0 if self._last_time is None else (now - self._last_time) / 3600.0
        self._last_time = now

        try:
            with self.data_lock:
                parts = self._contributions()
                self.active_power = sum(power for _, power in parts)
                if self.active_power > 0:
                    self.import_kwh += self.active_power * dt_hours
                elif self.active_power < 0:
                    self.export_kwh += abs(self.active_power) * dt_hours
        except Exception as e:
            logger.error("Error in VirtualGridModel update for %s: %s",
                         self.config.get('DeviceKey'), e)
            self.active_power = 0.0
            parts = []

        logger.debug(
            "VirtualGridModel update: %s -> %.3f kW (headroom %.1f kW to the "
            "%.0f kW import cap), Imported = %.4f kWh, Exported = %.4f kWh",
            ", ".join("%s %+.2f" % p for p in parts) or 'no sources',
            self.active_power, self.max_import - self.active_power,
            self.max_import, self.import_kwh, self.export_kwh)

        return {
            'VG.ActivePower': self.active_power,
            'VG.APConsumedKWH': self.import_kwh,
            'VG.APProductionKWH': self.export_kwh,
            'VG.MaxImport': self.max_import,
            'VG.BessZeroExport': self.bess_zero_export,
            'VG.T98LoopLimit': self.t98_loop_limit,
        }
