import logging
import random
import time
import csv
import os

logger = logging.getLogger('state')

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
        if self.mode == 0 and self.csv_file:
            self.load_power_curve()
        logger.info(f"LoadModel initialized for {config.get('DeviceKey')}: base_power = {self.base_power} kW, Mode = {self.mode}, Start hour = {self.start_hour}")

    def load_power_curve(self):
        if not os.path.exists(self.csv_file):
            logger.error(f"CSV file {self.csv_file} not found")
            self.mode = 1
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
        if not self.power_curve:
            return 0.0
        # 精确对齐 0.25 小时步长，使用 current_hour_display
        rounded_hour = round(current_hour * 4) / 4
        times = sorted(self.power_curve.keys())
        logger.debug(f"LoadModel interpolate: current_hour={current_hour:.4f}, rounded_hour={rounded_hour:.2f}, times={times[28:32]}...")  # 7:00 附近数据
        if rounded_hour <= times[0]:
            return self.power_curve[times[0]]
        if rounded_hour >= times[-1]:
            return self.power_curve[times[-1]]
        for i in range(len(times) - 1):
            t1, t2 = times[i], times[i + 1]
            if abs(t1 - rounded_hour) < 0.01 or abs(t2 - rounded_hour) < 0.01:
                return self.power_curve.get(rounded_hour, (self.power_curve[t1] + self.power_curve[t2]) / 2)
            if t1 <= rounded_hour <= t2:
                p1, p2 = self.power_curve[t1], self.power_curve[t2]
                interpolated = p1 + (p2 - p1) * (rounded_hour - t1) / (t2 - t1)
                logger.debug(f"LoadModel interpolate: t1={t1:.2f}, t2={t2:.2f}, p1={p1}, p2={p2}, interpolated={interpolated}")
                return interpolated
        return 0.0

    def update(self):
        try:
            elapsed_seconds = time.time() - self.start_time
            current_hour = self.start_hour + (elapsed_seconds / 3600)
            current_hour_display = current_hour % 24

            logger.info(f"LoadModel update: Elapsed seconds = {elapsed_seconds:.1f}, Current hour = {current_hour_display:.2f}h")

            if self.mode == 0:
                self.power = self.interpolate_power(current_hour_display)  # 使用 display hour
                logger.debug(f"LoadModel update: interpolated_power={self.power}")
            else:
                self.power = self.base_power * random.uniform(0.8, 1.2)
        except Exception as e:
            logger.error(f"Error in LoadModel update for {self.config.get('DeviceKey')}: {str(e)}")
            self.power = 0.0

        logger.info(f"LoadModel update: Power = {self.power} kW")
        return {'Load.Power': self.power}