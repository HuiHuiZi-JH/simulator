import logging
import random
import time
import math
import csv
import os
logger = logging.getLogger('state')

class PVModel:
    def __init__(self, config, start_hour):
        self.config = config
        self.rated_power = config.get('ratedPower')
        if self.rated_power is None:
            raise ValueError(f"ratedPower must be specified for {config.get('DeviceKey', 'PV')} in device.json")
        self.mode = config.get('mode', 1)
        self.csv_file = os.path.join('config', config.get('csv_file', ''))
        self.power_curve = {}
        self.limit_power = float(self.rated_power)
        self.gen_active_pw = 0.0
        self.total_production_kwh = 0.0
        self.oem_state = 1
        self.ctrl_state = 1
        self.state = 0
        self.start = 0
        self.stop = 0
        self.ap_production = 0.0
        self.start_time = time.time()
        self.last_active_time = None
        self.start_hour = start_hour
        self.fixed_start = 6.0
        self.fixed_peak = 12.0
        self.fixed_end = 18.0
        try:
            if self.mode == 0 and self.csv_file:
                self.load_power_curve()
        except Exception as e:
            logger.error(f"Error loading power curve for {config.get('DeviceKey')}: {str(e)}")
            self.mode = 1
        logger.info(f"PVModel initialized for {config.get('DeviceKey')}: ratedPower = {self.rated_power} kW, LimitPower = {self.limit_power} kW, Mode = {self.mode}, Start hour = {self.start_hour}, Fixed curve 6:00-18:00 peak 12:00")

    def load_power_curve(self):
        if not os.path.exists(self.csv_file):
            raise FileNotFoundError(f"CSV file {self.csv_file} not found")
        self.power_curve = {}
        with open(self.csv_file, 'r') as f:
            content = f.read().strip()
            if '\n' not in content:
                data = content.split()
                for i in range(0, len(data), 2):
                    try:
                        hour = float(data[i])
                        power = float(data[i + 1])
                        self.power_curve[hour] = power
                    except (IndexError, ValueError):
                        logger.warning(f"Invalid data in {self.csv_file} at position {i}: {data[i:i+2]}")
            else:
                reader = csv.reader(content.splitlines())
                next(reader, None)
                for row in reader:
                    if len(row) >= 2:
                        try:
                            hour = float(row[0])
                            power = float(row[1])
                            self.power_curve[hour] = power
                        except ValueError:
                            logger.warning(f"Invalid data in {self.csv_file} at row {row}")
        if not self.power_curve:
            raise ValueError(f"No valid data loaded from {self.csv_file}")
        logger.info(f"Loaded power curve from {self.csv_file}: {len(self.power_curve)} points, max power = {max(self.power_curve.values()):.1f} kW at hour {max(self.power_curve, key=self.power_curve.get)}")

    def interpolate_power(self, current_hour):
        if not self.power_curve:
            return 0.0
        current_hour = current_hour % 24
        times = sorted(self.power_curve.keys())
        logger.debug(f"PVModel interpolate: current_hour={current_hour:.4f}, times={times[:5]}...{times[-5:]}")
        if current_hour <= times[0]:
            return self.power_curve[times[0]]
        if current_hour >= times[-1]:
            return self.power_curve[times[-1]]
        for i in range(len(times) - 1):
            t1, t2 = times[i], times[i + 1]
            if abs(t1 - current_hour) < 0.0001:
                return self.power_curve[t1]
            if t1 < current_hour < t2:
                p1, p2 = self.power_curve[t1], self.power_curve[t2]
                interpolated = p1 + (p2 - p1) * (current_hour - t1) / (t2 - t1)
                logger.debug(f"PVModel interpolate: t1={t1:.4f}, t2={t2:.4f}, p1={p1}, p2={p2}, interpolated={interpolated:.4f}")
                return interpolated
        return 0.0

    def update(self, p_limit=None):
        try:
            if p_limit is not None and p_limit > 0:
                self.limit_power = max(0.0, min(float(self.rated_power), p_limit))
            logger.info(f"PVModel update: Received p_limit = {p_limit}, Current limit_power = {self.limit_power} kW")
            elapsed_seconds = time.time() - self.start_time
            current_hour = self.start_hour + (elapsed_seconds / 3600)
            current_hour_display = current_hour % 24
            if current_hour >= 24:
                logger.info(f"PVModel: Time crossed midnight, resetting to {current_hour_display:.4f}h")
            logger.info(f"PVModel update: Elapsed seconds = {elapsed_seconds:.1f}, Current hour = {current_hour_display:.4f}h")
            if self.mode == 0:
                base_power = self.interpolate_power(current_hour_display)
            else:
                if self.fixed_start <= current_hour_display <= self.fixed_end:
                    phase = math.pi * (current_hour_display - self.fixed_start) / (self.fixed_end - self.fixed_start)
                    base_power = self.rated_power * (math.sin(phase) ** 2)
                    fluctuation = 1.0 + random.uniform(-0.05, 0.05)
                    base_power *= fluctuation
                else:
                    base_power = 0.0
            self.gen_active_pw = max(0.0, min(self.limit_power, base_power))
            self.last_active_time = time.time() if self.gen_active_pw > 0 else self.last_active_time
            time_step = 1.0 / 3600.0
            energy_increment = self.gen_active_pw * time_step
            self.total_production_kwh += energy_increment
            self.ap_production = self.gen_active_pw
            self.state = 1 if self.gen_active_pw > 0 else 0
            if self.gen_active_pw > 0 and self.start == 0:
                self.start = 1
            if self.gen_active_pw == 0 and self.stop == 0:
                self.stop = 1
            if self.last_active_time and (time.time() - self.last_active_time) > 60:
                self.start = 0
        except Exception as e:
            logger.error(f"Error in PVModel update for {self.config.get('DeviceKey')}: {str(e)}")
            self.gen_active_pw = 0.0
        logger.info(f"PVModel update: GenActivePW = {self.gen_active_pw} kW, LimitPower = {self.limit_power} kW, "
                    f"TotalProductionKWH = {self.total_production_kwh:.2f} kWh, APProduction = {self.ap_production} kW, "
                    f"Start = {self.start}, Stop = {self.stop}")
        return {
            'INV.GenActivePW': self.gen_active_pw,
            'INV.APProductionKWH': self.total_production_kwh,
            'INV.LimitPower': self.limit_power,
            'INV.OemState': self.oem_state,
            'INV.CtrlState': self.ctrl_state,
            'INV.Start': self.start,
            'INV.Stop': self.stop,
            'INV.State': self.state,
            'INV.APProduction': self.ap_production
        }