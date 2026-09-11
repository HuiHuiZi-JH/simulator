REGISTERS = {
    "Meter": {
        "base_addr": 0,
        "points": {
            "ActivePower": 0,
            "APProductionKWH": 2,
            "APConsumedKWH": 4
        },
        "descriptions": {
            "ActivePower": "Active Power (calculated), + = import from grid, - = export to grid",
            "APProductionKWH": "Accumulated energy exported to grid (kWh)",
            "APConsumedKWH": "Accumulated energy imported from grid (kWh)"
        }
    },
    "BESS": {
        "base_addr": 0,
        "points": {
            "BS.ActivePW": 0,
            "BS.Soc": 2,
            "BS.MaxChargePower": 4,
            "BS.MaxDischargePower": 6,
            "BS.EndChargeSOC": 8,
            "BS.EndDischargeSOC": 10,
            "BS.Soh": 12,
            "BS.SysAPSetPoint": 14,
            "BS.OemState": 16,
            "BS.CtrlState": 18,
            "BS.TotalChargingEng": 20,
            "BS.TotalDischargingEng": 22
        },
        "descriptions": {
            "BS.ActivePW": "Battery Active Power",
            "BS.Soc": "State of Charge",
            "BS.MaxChargePower": "Max Charge Power",
            "BS.MaxDischargePower": "Max Discharge Power",
            "BS.EndChargeSOC": "End Charge SOC",
            "BS.EndDischargeSOC": "End Discharge SOC",
            "BS.Soh": "State of Health",
            "BS.SysAPSetPoint": "System Active Power Setpoint",
            "BS.OemState": "OEM State",
            "BS.CtrlState": "Control State",
            "BS.TotalChargingEng": "Total Charging Energy",
            "BS.TotalDischargingEng": "Total Discharging Energy"
        }
    },
    "PV": {
        "base_addr": 0,
        "points": {
            "INV.GenActivePW": 0,
            "INV.APProductionKWH": 2,
            "INV.LimitPower": 4,
            "INV.OemState": 6,
            "INV.CtrlState": 8,
            "INV.Start": 10,
            "INV.Stop": 12,
            "INV.State": 14,
            "INV.APProduction": 16
        },
        "descriptions": {
            "INV.GenActivePW": "Generated Active Power",
            "INV.APProductionKWH": "Active Power Production KWH",
            "INV.LimitPower": "Limit Power",
            "INV.OemState": "OEM State",
            "INV.CtrlState": "Control State",
            "INV.Start": "Start",
            "INV.Stop": "Stop",
            "INV.State": "State",
            "INV.APProduction": "Active Power Production"
        }
    },
    "EV": {
        "base_addr": 0,
        "points": {
            "PUB_CONN.ChargePW": 0,
            "PUB_CONN.CurrentL1": 2,
            "PUB_CONN.CurrentL2": 4,
            "PUB_CONN.CurrentL3": 6,
            "PUB_CONN.ChargePWSet": 8,
            "PUB_CONN.ChargeCurSetL1": 10,
            "PUB_CONN.OemState": 12,
            "PUB_CONN.ChargeEnergyKWH": 14,
            "PUB_CONN.CtrlState": 16,
            "PUB_CONN.State": 18
        },
        "descriptions": {
            "PUB_CONN.ChargePW": "Charge Power",
            "PUB_CONN.CurrentL1": "Current L1",
            "PUB_CONN.CurrentL2": "Current L2",
            "PUB_CONN.CurrentL3": "Current L3",
            "PUB_CONN.ChargePWSet": "Charge Power Setpoint",
            "PUB_CONN.ChargeCurSetL1": "Charge Current Set L1",
            "PUB_CONN.OemState": "OEM State",
            "PUB_CONN.ChargeEnergyKWH": "Charge Energy KWH",
            "PUB_CONN.CtrlState": "Control State",
            "PUB_CONN.State": "State"
        }
    },
    "Load": {
        "base_addr": 0,
        "points": {
            "Load.Power": 0,
            "Load.ModeSet": 2,
            "Load.PowerSet": 4
        },
        "descriptions": {
            "Load.Power": "Load Power",
            "Load.ModeSet": "Source: 0 = CSV curve, 1 = simulated, 2 = manual (writable)",
            "Load.PowerSet": "Manual load power (kW), used when the source is 2 (writable)"
        }
    },
    "JTCLoad": {
        "base_addr": 0,
        "points": {
            "JTC.Power": 0
        },
        "descriptions": {
            "JTC.Power": "JTC common load power (landlord / common services)"
        }
    },
    "T7PV": {
        "base_addr": 0,
        "points": {
            "T7PV.GenActivePW": 0,
            "T7PV.APProductionKWH": 2
        },
        "descriptions": {
            "T7PV.GenActivePW": "Tower 7 PV generated power (outside the Tower 10 network)",
            "T7PV.APProductionKWH": "Tower 7 PV accumulated production (kWh)"
        }
    },
    "VirtualGrid": {
        "base_addr": 0,
        "points": {
            "VG.ActivePower": 0,
            "VG.APConsumedKWH": 2,
            "VG.APProductionKWH": 4,
            "VG.MaxImport": 6,
            "VG.BessZeroExport": 8,
            "VG.T98LoopLimit": 10
        },
        "descriptions": {
            "VG.ActivePower": "Virtual grid incoming (calculated), + = import, - = export",
            "VG.APConsumedKWH": "Accumulated energy imported through the virtual point (kWh)",
            "VG.APProductionKWH": "Accumulated energy exported through the virtual point (kWh)",
            "VG.MaxImport": "EGC static setting: maximum import (kW)",
            "VG.BessZeroExport": "EGC static setting: BESS zero-export threshold (kW)",
            "VG.T98LoopLimit": "EGC static setting: Tower 10 loop limit (kW)"
        }
    }
}