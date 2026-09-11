import logging
import random
import time
import csv
import os

logger = logging.getLogger('state')

# The three sources a load can run from. 0 and 1 are what device.json has always
# meant; 2 is the operator driving it by hand, from the dashboard panel or from
# a Modbus write to Load.ModeSet.
MODE_CURVE, MODE_SIM, MODE_MANUAL = 0, 1, 2


class LoadModel:
    def __init__(self, config, start_hour):
        self.config = config
        self.base_power = config.get('base_power', 120.0)  # 仅用于参考，不影响插值
        self.power = 0.0
        self.mode = config.get('mode', 0)
        self.csv_file = os.path.join('config', config.get('csv_file', ''))
        self.power_curve = {}
        self.start_hour = start_hour
        self.start_time = time.time()
        self.manual_power = float(config.get('base_power', 0.0))
        if self.mode == MODE_CURVE and self.csv_file:
            self.load_power_curve()
        logger.info(f"LoadModel initialized for {config.get('DeviceKey')}: base_power = {self.base_power} kW, Mode = {self.mode}, Start hour = {self.start_hour}")

    def load_power_curve(self):
        if not os.path.exists(self.csv_file):
            logger.error(f"CSV file {self.csv_file} not found")
            self.mode = MODE_SIM
            return
        self.power_curve = {}
        with open(self.csv_file, 'r') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    try:
                        hour = float(row[0])  # CSV 中的原始小时
                        power = float(row[1])
                        self.power_curve[hour] = power  # 不偏移，保持绝对时间
                    except ValueError:
                        logger.warning(f"Invalid data in {self.csv_file} at row {row}")
        if not self.power_curve:
            raise ValueError(f"No valid data loaded from {self.csv_file}")
        logger.info(f"Loaded power curve from {self.csv_file}: {len(self.power_curve)} points, keys={sorted(self.power_curve.keys())[:5]}...")

    def interpolate_power(self, current_hour):
        """Read the curve at `current_hour`, interpolated between its points.

        The hour is used as given. An earlier version rounded it to the nearest
        quarter -- the resolution the curve happens to be sampled at -- which
        landed exactly on a curve point every time and turned a demand profile
        into a staircase that only moved every 15 minutes. The value went to a
        controller and onto the trend chart as a flat line beside curves that
        were moving every second.
        """
        if not self.power_curve:
            return 0.0
        times = sorted(self.power_curve.keys())
        logger.debug(f"LoadModel interpolate: current_hour={current_hour:.4f}, times={times[28:32]}...")
        if current_hour <= times[0]:
            return self.power_curve[times[0]]
        if current_hour >= times[-1]:
            return self.power_curve[times[-1]]
        for i in range(len(times) - 1):
            t1, t2 = times[i], times[i + 1]
            if t1 <= current_hour <= t2:
                p1, p2 = self.power_curve[t1], self.power_curve[t2]
                if t2 == t1:
                    return p1
                interpolated = p1 + (p2 - p1) * (current_hour - t1) / (t2 - t1)
                logger.debug(f"LoadModel interpolate: t1={t1:.2f}, t2={t2:.2f}, p1={p1}, p2={p2}, interpolated={interpolated}")
                return interpolated
        return 0.0

    def ensure_curve(self):
        """Load the curve the first time the source is switched to it.

        A device configured as synthetic or manual never read its CSV at start-up,
        so switching to the curve at run time has to fetch it -- otherwise the
        load would silently sit at zero. A missing or unusable file falls back to
        the synthetic day rather than to nothing.
        """
        if self.power_curve or not self.csv_file:
            return bool(self.power_curve)
        try:
            self.load_power_curve()
        except (OSError, ValueError) as e:
            logger.error(f"LoadModel could not load {self.csv_file} on demand: {e}")
        return bool(self.power_curve)

    def update(self, mode=None, p_set=None):
        """One tick. `mode` and `p_set` are the two control registers, so a write
        from the dashboard or from Modbus takes effect on the next cycle without
        a restart; with neither given the model runs on its configured source."""
        try:
            if mode is not None:
                try:
                    requested = int(round(float(mode)))
                except (TypeError, ValueError):
                    requested = self.mode
                if requested in (MODE_CURVE, MODE_SIM, MODE_MANUAL):
                    self.mode = requested
            if p_set is not None:
                try:
                    self.manual_power = max(0.0, float(p_set))
                except (TypeError, ValueError):
                    pass

            elapsed_seconds = time.time() - self.start_time
            current_hour = self.start_hour + (elapsed_seconds / 3600)
            current_hour_display = current_hour % 24

            logger.info(f"LoadModel update: Elapsed seconds = {elapsed_seconds:.1f}, Current hour = {current_hour_display:.2f}h, Mode = {self.mode}")

            if self.mode == MODE_MANUAL:
                self.power = self.manual_power
            elif self.mode == MODE_CURVE and self.ensure_curve():
                self.power = self.interpolate_power(current_hour_display)  # 使用 display hour
                logger.debug(f"LoadModel update: interpolated_power={self.power}")
            else:
                self.power = self.base_power * random.uniform(0.8, 1.2)
        except Exception as e:
            logger.error(f"Error in LoadModel update for {self.config.get('DeviceKey')}: {str(e)}")
            self.power = 0.0

        logger.info(f"LoadModel update: Power = {self.power} kW")
        return {'Load.Power': self.power}
