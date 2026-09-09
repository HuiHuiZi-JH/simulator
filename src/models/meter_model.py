import logging
import time

logger = logging.getLogger('state')

class MeterModel:
    def __init__(self, config, devices_data, data_lock, start_hour):
        self.config = config
        self.devices_data = devices_data
        self.data_lock = data_lock
        self.start_hour = start_hour
        if not devices_data or not data_lock:
            raise ValueError(f"Invalid initialization for {config.get('DeviceKey')}: devices_data={devices_data is not None}, data_lock={data_lock is not None}")
        self.total_power = 0.0
        # Accumulated energy counters (kWh) -- monotonically increasing, reset only on restart
        self.production_kwh = 0.0
        self.consumed_kwh = 0.0
        self._last_time = None
        logger.info(f"MeterModel initialized for {config.get('DeviceKey')}: Start hour = {self.start_hour}, devices_data length: {len(devices_data)}")

    def update(self):
        # Time delta since last update, in hours, for energy integration (kW * h = kWh).
        # monotonic() is immune to wall-clock adjustments, so the energy total stays accurate
        # even if a cycle runs long or the system clock changes.
        now = time.monotonic()
        dt_hours = 0.0 if self._last_time is None else (now - self._last_time) / 3600.0
        self._last_time = now

        pv_power = 0.0
        load_power = 0.0
        bess_discharge = 0.0
        try:
            with self.data_lock:
                for device_key, dev_info in self.devices_data.items():
                    if dev_info.get('model') and 'data' in dev_info and dev_info['data'].get(0) is not None:
                        active_power = dev_info['data'].get(0, 0)
                        if dev_info['type'] == 'PV':
                            pv_power += active_power
                        elif dev_info['type'] in ['Load', 'EV']:
                            load_power += active_power
                        elif dev_info['type'] == 'BESS':
                            if active_power > 0:  # 充电，计入消耗
                                load_power += active_power
                            elif active_power < 0:  # 放电，计入发电
                                pv_power += abs(active_power)
                                bess_discharge += abs(active_power)
                total_power = load_power - pv_power  # 反转符号，正值表示净消耗，负值表示净发电
                self.total_power = total_power

                # Integrate net power into the directional energy counters.
                # total_power > 0 -> importing from grid -> consumed
                # total_power < 0 -> exporting to grid   -> produced
                if total_power > 0:
                    self.consumed_kwh += total_power * dt_hours
                elif total_power < 0:
                    self.production_kwh += abs(total_power) * dt_hours
        except Exception as e:
            logger.error(f"Error in MeterModel update for {self.config.get('DeviceKey')}: {str(e)}")
            self.total_power = 0.0

        logger.debug(
            f"MeterModel update: PV+discharge = {pv_power:.3f} kW, "
            f"BESS discharge = {bess_discharge:.3f} kW, Load = {load_power:.3f} kW, "
            f"Net = {self.total_power:.3f} kW, "
            f"Produced = {self.production_kwh:.4f} kWh, Consumed = {self.consumed_kwh:.4f} kWh"
        )
        return {
            'ActivePower': self.total_power,
            'APProductionKWH': self.production_kwh,
            'APConsumedKWH': self.consumed_kwh,
        }
