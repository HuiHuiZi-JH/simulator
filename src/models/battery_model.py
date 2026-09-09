# -*- coding: utf-8 -*-
import logging
logger = logging.getLogger('state')

class BatteryModel:
    def __init__(self, config, start_hour):
        self.config = config
        self.rated_capacity = config.get('ratedCapacity', 200)
        self.max_charge_power = config.get('maxChargePower', 100)
        self.max_discharge_power = config.get('maxDischargePower', 100)
        self.soc_max = config.get('socMax', 0.9)
        self.soc_min = config.get('socMin', 0.1)
        self.initial_soc = config.get('initial_soc', 0.5)
        self.voltage_nominal = config.get('voltage_nominal', 48)
        self.resistance = config.get('resistance', 0.01)
        self.soc = min(self.soc_max * 100.0, max(self.soc_min * 100.0, self.initial_soc * 100.0))
        self.active_power = 0.0
        self.total_charging_eng = 0.0
        self.total_discharging_eng = 0.0
        self.start_hour = start_hour
        logger.info(f"BatteryModel initialized for {config.get('DeviceKey')}: ratedCapacity = {self.rated_capacity} kWh, Start hour = {self.start_hour}, Initial SOC = {self.soc:.1f}%, SOC range = [{self.soc_min * 100.0:.1f}, {self.soc_max * 100.0:.1f}]%")

    def update(self, p_command=0.0):
        try:
            p_command = max(-self.max_discharge_power, min(self.max_charge_power, p_command))
            self.active_power = p_command
            time_step = 1.0 / 3600.0
            energy_increment = self.active_power * time_step
            soc_increment = (energy_increment / self.rated_capacity) * 100.0
            new_soc = self.soc + soc_increment
            if self.active_power > 0:
                if new_soc > self.soc_max * 100.0:
                    excess_soc = new_soc - self.soc_max * 100.0
                    excess_energy = (excess_soc / 100.0) * self.rated_capacity
                    energy_increment -= excess_energy
                    soc_increment = (energy_increment / self.rated_capacity) * 100.0
                    new_soc = self.soc_max * 100.0
                    logger.debug(f"BatteryModel: Charging limited to avoid SOC > {self.soc_max * 100.0:.1f}%, adjusted energy_increment={energy_increment:.6f}")
                self.total_charging_eng += energy_increment
                self.soc = min(self.soc_max * 100.0, self.soc + soc_increment)
            elif self.active_power < 0:
                if new_soc < self.soc_min * 100.0:
                    excess_soc = self.soc_min * 100.0 - new_soc
                    excess_energy = (excess_soc / 100.0) * self.rated_capacity
                    energy_increment += excess_energy
                    soc_increment = (energy_increment / self.rated_capacity) * 100.0
                    new_soc = self.soc_min * 100.0
                    logger.debug(f"BatteryModel: Discharging limited to avoid SOC < {self.soc_min * 100.0:.1f}%, adjusted energy_increment={energy_increment:.6f}")
                self.total_discharging_eng -= energy_increment
                self.soc = max(self.soc_min * 100.0, self.soc + soc_increment)
            else:
                soc_increment = 0.0
            self.soc = min(self.soc_max * 100.0, max(self.soc_min * 100.0, self.soc))
            if self.soc > self.soc_max * 100.0 or self.soc < self.soc_min * 100.0:
                logger.warning(f"BatteryModel: SOC adjusted to {self.soc:.1f}% to stay within [{self.soc_min * 100.0:.1f}, {self.soc_max * 100.0:.1f}]%")
            logger.debug(f"BatteryModel update: ActivePW = {self.active_power} kW, Energy increment = {energy_increment:.6f} kWh, TotalChargingEng = {self.total_charging_eng:.2f} kWh, TotalDischargingEng = {self.total_discharging_eng:.2f} kWh, SOC increment = {soc_increment:.6f}%, new SOC = {self.soc:.1f}%")
            return {
                'BS.ActivePW': self.active_power,
                'BS.Soc': self.soc,
                'BS.MaxChargePower': self.max_charge_power,
                'BS.MaxDischargePower': self.max_discharge_power,
                'BS.EndChargeSOC': self.soc_max * 100.0,
                'BS.EndDischargeSOC': self.soc_min * 100.0,
                'BS.Soh': 100.0,
                'BS.SysAPSetPoint': p_command,
                'BS.OemState': 1,
                'BS.CtrlState': 1,
                'BS.TotalChargingEng': self.total_charging_eng,
                'BS.TotalDischargingEng': self.total_discharging_eng
            }
        except Exception as e:
            logger.error(f"Error in BatteryModel update for {self.config.get('DeviceKey')}: {str(e)}")
            self.active_power = 0.0
            return {
                'BS.ActivePW': self.active_power,
                'BS.Soc': self.soc,
                'BS.MaxChargePower': self.max_charge_power,
                'BS.MaxDischargePower': self.max_discharge_power,
                'BS.EndChargeSOC': self.soc_max * 100.0,
                'BS.EndDischargeSOC': self.soc_min * 100.0,
                'BS.Soh': 100.0,
                'BS.SysAPSetPoint': p_command,
                'BS.OemState': 1,
                'BS.CtrlState': 1,
                'BS.TotalChargingEng': self.total_charging_eng,
                'BS.TotalDischargingEng': self.total_discharging_eng
            }