import logging
import time
logger = logging.getLogger('state')

class EVModel:
    def __init__(self, config, start_hour):
        self.config = config
        self.rated_power = config.get('PUB_CONN.RatedPW')
        if self.rated_power is None:
            raise ValueError(f"PUB_CONN.RatedPW must be specified for {config.get('DeviceKey', 'EV')} in device.json")
        self.min_charge_pw = config.get('PUB_CONN.MinChargePW', 0.0)
        self.charge_factor = config.get('charge_factor', 1.0)
        self.charge_pw = 0.0
        self.charge_energy_kwh = 0.0
        self.state = 0
        self.start = 0
        self.stop = 0
        self.oem_state = 0
        self.ctrl_state = 0
        self.current_l1 = 0.0
        self.current_l2 = 0.0
        self.current_l3 = 0.0
        self.start_hour = start_hour
        self.charge_schedule = []
        for schedule in config.get('charge_schedule', []):
            start_hour = int(schedule['start'].split(':')[0]) + int(schedule['start'].split(':')[1]) / 60.0
            stop_hour = int(schedule['stop'].split(':')[0]) + int(schedule['stop'].split(':')[1]) / 60.0
            self.charge_schedule.append((start_hour, stop_hour))
        if not self.charge_schedule:
            logger.warning(f"No charge_schedule defined for {config.get('DeviceKey')}, using default 8:00-10:00")
            self.charge_schedule = [(8.0, 10.0)]
        self.start_time = time.time()
        self.charger_type = config.get('charger_type', 'AC').upper()
        self.voltage = 220.0 if self.charger_type == 'AC' else 400.0
        self.power_factor = 0.95 if self.charger_type == 'AC' else 1.0
        logger.info(f"EVModel initialized for {config.get('DeviceKey')}: ratedPower = {self.rated_power} kW, minChargePW = {self.min_charge_pw} kW, charge_factor = {self.charge_factor}, Charge schedule = {self.charge_schedule}, Charger type = {self.charger_type}, Voltage = {self.voltage}V, Start hour = {self.start_hour}")

    def update(self, devices_data, device_key):
        try:
            elapsed_seconds = time.time() - self.start_time
            current_hour = self.start_hour + (elapsed_seconds / 3600)
            current_hour_display = current_hour % 24
            if current_hour >= 24:
                logger.info(f"EVModel: Time crossed midnight, resetting to {current_hour_display:.4f}h")
            logger.info(f"EVModel update: Elapsed seconds = {elapsed_seconds:.1f}, Current hour = {current_hour_display:.4f}h")
            p_limit = devices_data[device_key]['data'].get(8, float('inf'))
            self.charge_pw = 0.0
            self.oem_state = 0
            self.ctrl_state = 0
            self.state = 0
            for start_hour, stop_hour in self.charge_schedule:
                current_hour_norm = current_hour_display
                start_hour_norm = start_hour
                stop_hour_norm = stop_hour
                if start_hour > stop_hour:  # 跨天，例如 21:00-04:30
                    if current_hour_norm >= start_hour_norm or current_hour_norm < stop_hour_norm:
                        base_power = self.rated_power * self.charge_factor
                        self.charge_pw = max(self.min_charge_pw, min(base_power, float(p_limit)))
                        self.oem_state = 1
                        self.ctrl_state = 1
                        self.state = 1
                        break
                else:  # 非跨天，例如 00:00-04:30
                    if start_hour_norm <= current_hour_norm < stop_hour_norm:
                        base_power = self.rated_power * self.charge_factor
                        self.charge_pw = max(self.min_charge_pw, min(base_power, float(p_limit)))
                        self.oem_state = 1
                        self.ctrl_state = 1
                        self.state = 1
                        break
            if self.charge_pw > 0:
                self.current_l1 = self.charge_pw * 1000 / (3 * self.voltage * self.power_factor)
                self.current_l2 = self.current_l1
                self.current_l3 = self.current_l1
                charge_cur_set_l1 = p_limit * 1000 / (self.voltage * self.power_factor) if p_limit > 0 else 0.0
            else:
                self.current_l1 = 0.0
                self.current_l2 = 0.0
                self.current_l3 = 0.0
                charge_cur_set_l1 = 0.0
            time_step = 1.0 / 3600.0
            energy_increment = self.charge_pw * time_step
            self.charge_energy_kwh += energy_increment
            if self.charge_pw > 0 and self.start == 0:
                self.start = 1
            if self.charge_pw == 0 and self.stop == 0:
                self.stop = 1
            devices_data[device_key]['data'][0] = self.charge_pw
            devices_data[device_key]['data'][2] = self.current_l1
            devices_data[device_key]['data'][4] = self.current_l2
            devices_data[device_key]['data'][6] = self.current_l3
            devices_data[device_key]['data'][8] = p_limit
            devices_data[device_key]['data'][10] = charge_cur_set_l1
            devices_data[device_key]['data'][12] = self.oem_state
            devices_data[device_key]['data'][14] = self.charge_energy_kwh
            devices_data[device_key]['data'][16] = self.ctrl_state
            devices_data[device_key]['data'][18] = self.state
            logger.info(f"EVModel update: ChargePW = {self.charge_pw} kW, p_limit = {p_limit} kW, Currents = {self.current_l1:.1f}A, Energy = {self.charge_energy_kwh:.2f} kWh, Start = {self.start}, Stop = {self.stop}, OEM = {self.oem_state}, Ctrl = {self.ctrl_state}")
        except Exception as e:
            logger.error(f"Error in EVModel update for {self.config.get('DeviceKey')}: {str(e)}")
            self.charge_pw = 0.0
        return {
            'PUB_CONN.ChargePW': self.charge_pw,
            'PUB_CONN.CurrentL1': self.current_l1,
            'PUB_CONN.CurrentL2': self.current_l2,
            'PUB_CONN.CurrentL3': self.current_l3,
            'PUB_CONN.ChargePWSet': p_limit,
            'PUB_CONN.ChargeCurSetL1': charge_cur_set_l1,
            'PUB_CONN.OemState': self.oem_state,
            'PUB_CONN.ChargeEnergyKWH': self.charge_energy_kwh,
            'PUB_CONN.CtrlState': self.ctrl_state,
            'PUB_CONN.State': self.state
        }