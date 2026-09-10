# -*- coding: utf-8 -*-
"""Rolling power history, the past the dashboard's trend chart draws.

Every model publishes instantaneous values only -- this tick's numbers and
nothing older -- so a chart opened in the browser would have nothing behind it
until something keeps a record.  The simulation thread drops one sample of
every device's headline power into the fixed-size ring here, and the web server
serves it through /api/history.

The ring is bounded, so memory is flat however long the process runs: the
oldest sample falls off as the newest arrives.  Nothing is persisted -- a
restart begins with an empty ring, exactly like SOC and the energy counters.

Two locks are involved and never held at the same time: devices_data is read
under the shared data_lock, then the resulting sample is appended under this
module's own lock.  Taking them one after the other rather than nested is what
keeps the simulation thread and a browser request from deadlocking.
"""
import threading
from collections import deque

from config.modbus_registers import REGISTERS

INTERVAL = 10.0                                  # seconds between samples
SPAN_HOURS = 24.0                                # how much past the ring holds
MAXLEN = int(SPAN_HOURS * 3600 / INTERVAL)       # 8,640 samples

# The one point per device type the chart plots: the same headline power each
# device's card shows on the Live tab.  A type absent from this table is not
# charted -- there is nothing here for Meter's energy counters or the battery's
# SOC, because a chart mixing kW and kWh or kW and % would need two y-scales.
TRACKED_POINT = {
    'Meter':       'ActivePower',
    'PV':          'INV.GenActivePW',
    'BESS':        'BS.ActivePW',
    'EV':          'PUB_CONN.ChargePW',
    'Load':        'Load.Power',
    'JTCLoad':     'JTC.Power',
    'T7PV':        'T7PV.GenActivePW',
    'VirtualGrid': 'VG.ActivePower',
}


class PowerHistory:
    """A fixed-size ring of power samples, one every `interval` seconds."""

    def __init__(self, interval=INTERVAL, maxlen=MAXLEN):
        self.interval = float(interval)
        self.maxlen = int(maxlen)
        self._lock = threading.Lock()
        self._samples = deque(maxlen=self.maxlen)
        self._types = {}
        self._seq = 0
        self._next_due = None

    # ---------- writing (simulation thread) ----------

    def record(self, now, sim_hour, devices_data, data_lock):
        """Sample every tracked device if the interval has elapsed.

        `now` is a monotonic-enough wall clock in seconds; `sim_hour` is the
        simulated clock the sample belongs to.  Returns True when a sample was
        actually taken, so the caller can log it.
        """
        if self._next_due is None:
            self._next_due = now + self.interval
        elif now >= self._next_due:
            # Step the schedule by whole intervals instead of from `now`, so a
            # slow tick delays one sample rather than skewing every later one.
            while self._next_due <= now:
                self._next_due += self.interval
        else:
            return False

        values = {}
        types = {}
        with data_lock:
            for key, dev in devices_data.items():
                point = TRACKED_POINT.get(dev['type'])
                if point is None:
                    continue
                offset = REGISTERS.get(dev['type'], {}).get('points', {}).get(point)
                if offset is None:
                    continue
                values[key] = round(float(dev['data'].get(offset, 0.0)), 2)
                types[key] = dev['type']

        with self._lock:
            self._seq += 1
            self._types.update(types)
            self._samples.append((self._seq, round(float(sim_hour) % 24, 4), values))
        return True

    # ---------- reading (web thread) ----------

    def dump(self, after=None):
        """Columnar history for the chart, oldest sample first.

        With `after` set, only samples newer than that sequence number come
        back, so a browser already holding the buffer asks for the handful it
        is missing instead of the whole day.  `first_seq` lets the client spot
        a gap -- it fell behind, or the process restarted and the counter went
        back to zero -- and refetch in full.
        """
        with self._lock:
            samples = list(self._samples)
            seq = self._seq
            types = dict(self._types)

        if after is not None:
            samples = [s for s in samples if s[0] > after]

        hours = [s[1] for s in samples]
        series = {}
        for key in types:
            if any(key in s[2] for s in samples):
                series[key] = [s[2].get(key) for s in samples]

        return {
            'interval': self.interval,
            'span_hours': SPAN_HOURS,
            'seq': seq,
            'first_seq': samples[0][0] if samples else None,
            'count': len(samples),
            'sim_hour': hours,
            'series': series,
            'devices': [{'key': k, 'type': t, 'point': TRACKED_POINT.get(t)}
                        for k, t in types.items()],
        }
