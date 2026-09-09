# MicroGrid Simulator

A pure-Python Modbus TCP slave that impersonates a whole microgrid — solar inverter, battery,
EV charger, building load, and revenue meter — so an energy management system can be exercised
against realistic, controllable devices instead of hardware.

No third-party dependencies. Python 3, standard library only.

---

## Rules

**These rules govern all work in this repository. Read them before changing code.**

1. **README.md is the source of truth.** Every rule and convention for this project is written
   here. Do not keep rules in your head, in commit messages, or in comments only.

2. **Every code change goes through README.md.** Before changing code, read this file for the
   rules that apply. After changing code, update the affected section of this file **in the same
   commit** so documentation never drifts from behaviour.

3. **Every change is pushed to GitHub.** Do not stop at a local commit. Run `git push` so
   `origin` always reflects the working state.

### Conventions these rules protect

- **Register offsets are even.** Every point is a 32-bit value spanning two registers. Adding a
  point means taking the next *even* offset and recording it in both `config/modbus_registers.py`
  and the register map below.
- **Scaling is ×100 everywhere.** Values go on the wire as `int(value * 100)`. Do not introduce a
  per-point scale factor without documenting it here.
- **Shared state is lock-guarded.** Anything touching `devices_data` takes `data_lock` from
  `utils/locks.py`. The simulation thread and the server thread both write to it.
- **Models never talk to sockets.** A model takes config and returns a dict of named points. The
  mapping from names to register offsets lives in `config/modbus_registers.py`, nowhere else.
- **Files are UTF-8.** See Known issues — `src/models/ev_model.py` currently violates this.

---

## Running

Start from the repository root so the relative config paths resolve:

```
python3 main.py
```

The server listens on `0.0.0.0:5021`. Point any Modbus TCP client at it.

To read the meter: unit 1, address 0, quantity 2 returns net active power as a signed 32-bit
integer — divide by 100 for kW. Positive means the site is importing from the grid, negative
means exporting.

To run as a service, edit the paths in `MicroGridSimulator.service` to match your checkout, then
install it with systemd. It restarts on failure and routes output to syslog.

---

## Architecture

`main.py` starts two threads over a single `devices_data` dictionary guarded by one lock:

- **Simulation thread** (`update_device_data`) — ticks every device model once a second and writes
  the results into each device's register table.
- **Server thread** (`ModbusServer.run`) — answers Modbus requests out of that same table, one
  daemon thread per client connection.

Because both sides take the lock, a client always reads a coherent snapshot, and anything it
writes becomes an input to the next tick.

### Simulated clock

Time starts at `start_time` in `config/device.json` and advances with real elapsed seconds,
wrapping at midnight. One real second is one simulated second — a full day takes a full day.

---

## Device models

Five model classes in `src/models/`. Four simulate device behaviour from config and a clock; the
meter has no physics of its own and derives everything from the other four.

| Model | Type key | Behaviour |
|---|---|---|
| `PVModel` | `PV` | Follows a 24-hour irradiance curve from CSV, or a synthetic sin² arc 06:00–18:00 with ±5% noise. Output clamped to the written power limit. |
| `BatteryModel` | `BESS` | Integrates a signed power command into SOC. On hitting the SOC ceiling or floor it back-calculates the energy actually absorbed, so the kWh counters stay honest. |
| `EVModel` | `EV` | Draws rated power inside configured charging windows (midnight-wrapping supported), capped by the written setpoint, converted to balanced three-phase currents. |
| `LoadModel` | `Load` | Replays a demand profile interpolated from CSV at quarter-hour resolution, or jitters 80–120% of base power. |
| `MeterModel` | `Meter` | **Derived.** Sums the other devices into net power at the point of common coupling, then integrates it on a monotonic clock into separate import and export counters. |

Every model exposes the same contract: a constructor taking its slice of `device.json`, and an
`update()` returning a dict of named points. `MeterModel` additionally receives `devices_data`
and `data_lock` because it reads the other devices.

---

## Protocol

Modbus TCP, hand-written on raw sockets in `src/communication/modbus_server.py`.

- **Function code 3** — read holding registers.
- **Function code 16** — write multiple registers.
- **Function code 6** — rejected with exception 1. A single-register write cannot carry a 32-bit
  value.
- Bad address ranges return exception 2; malformed frames return exception 3.

Every point occupies two consecutive registers as a **big-endian signed 32-bit integer scaled by
100**. All offsets are even, and request quantities must be even — an odd count would split a
value in half. Maximum quantity is 125.

Devices are routed by unit ID, 1–247. On startup the loader validates configured IDs; anything out
of range or duplicated is replaced with the next free ID rather than shadowing another device.

### Control registers

Three registers are **inputs**, read back by the simulation each tick. Writing one changes what
the next tick produces — this is what makes the simulator useful for testing a controller rather
than replaying data.

| Device | Addr | Point | Effect |
|---|---|---|---|
| PV | 4 | `INV.LimitPower` | Curtails inverter output |
| BESS | 14 | `BS.SysAPSetPoint` | Commands charge (+) or discharge (−) |
| EV | 8 | `PUB_CONN.ChargePWSet` | Caps charging power |

Function code 16 will accept a write to any even offset, but only these three are consumed by the
models. The rest are overwritten on the next tick.

---

## Register map

Offsets are relative to base address 0 for each unit. **W** marks a control input.

### Meter — unit 1

| Addr | Point | Meaning |
|---|---|---|
| 0 | `ActivePower` | Net at PCC, + import / − export |
| 2 | `APProductionKWH` | Exported energy |
| 4 | `APConsumedKWH` | Imported energy |

### PV inverter — unit 2

| Addr | Point | Meaning |
|---|---|---|
| 0 | `INV.GenActivePW` | Generated power |
| 2 | `INV.APProductionKWH` | Lifetime production |
| 4 | `INV.LimitPower` **W** | Curtailment limit |
| 6 | `INV.OemState` | OEM state |
| 8 | `INV.CtrlState` | Control state |
| 10 | `INV.Start` | Start flag |
| 12 | `INV.Stop` | Stop flag |
| 14 | `INV.State` | Generating or idle |
| 16 | `INV.APProduction` | Instantaneous output |

### EV charger — unit 4

| Addr | Point | Meaning |
|---|---|---|
| 0 | `PUB_CONN.ChargePW` | Charging power |
| 2 | `PUB_CONN.CurrentL1` | Phase 1 current |
| 4 | `PUB_CONN.CurrentL2` | Phase 2 current |
| 6 | `PUB_CONN.CurrentL3` | Phase 3 current |
| 8 | `PUB_CONN.ChargePWSet` **W** | Power setpoint |
| 10 | `PUB_CONN.ChargeCurSetL1` | Current setpoint |
| 12 | `PUB_CONN.OemState` | OEM state |
| 14 | `PUB_CONN.ChargeEnergyKWH` | Session energy |
| 16 | `PUB_CONN.CtrlState` | Control state |
| 18 | `PUB_CONN.State` | Charging or idle |

### Battery — unit 5

| Addr | Point | Meaning |
|---|---|---|
| 0 | `BS.ActivePW` | + charge, − discharge |
| 2 | `BS.Soc` | State of charge % |
| 4 | `BS.MaxChargePower` | Charge rate ceiling |
| 6 | `BS.MaxDischargePower` | Discharge rate ceiling |
| 8 | `BS.EndChargeSOC` | Upper SOC bound |
| 10 | `BS.EndDischargeSOC` | Lower SOC bound |
| 12 | `BS.Soh` | State of health |
| 14 | `BS.SysAPSetPoint` **W** | Power command |
| 16 | `BS.OemState` | OEM state |
| 18 | `BS.CtrlState` | Control state |
| 20 | `BS.TotalChargingEng` | Lifetime charged |
| 22 | `BS.TotalDischargingEng` | Lifetime discharged |

### Building load — unit 8

| Addr | Point | Meaning |
|---|---|---|
| 0 | `Load.Power` | Demand drawn |

---

## Configuration

### `config/device.json`

Sets the simulated start time and lists the devices. Adding a second inverter or a third charger
means adding an object, not touching code.

```json
{
  "start_time": "14:19",
  "Devices": [
    { "PV": [ {
        "DeviceKey":  "PV_01",
        "ratedPower": 100,
        "mode":       0,
        "csv_file":   "pv_curve.csv",
        "slave_id":   2
    } ] }
  ]
}
```

`mode` is `0` for a CSV curve and `1` for synthetic generation. `DeviceKey` must be unique — it
is the key into `devices_data`.

### Power curves

`config/pv_curve.csv` and `config/load_curve.csv` hold quarter-hour points covering 24 hours as
`hour,kW` pairs. Values between points are linearly interpolated.

### `config/logging_config.json`

Three rotating log streams under `log/`, with independent levels:

| Stream | File | Contents |
|---|---|---|
| `state` | `simulation.log` | Device state each tick |
| `message` | `message.log` | Protocol-level request handling |
| `traffic` | `modbus_traffic.log` | Raw frame bytes sent and received |

When a client sees a value it did not expect, the traffic log has the bytes.

---

## Known issues

- **`src/models/ev_model.py` is ISO-8859 encoded, not UTF-8.** The Chinese comments on lines 62
  and 70 render as `���`, and tools such as `grep` treat the file as binary and skip it. Needs
  re-encoding to UTF-8.
- **Both CSV loaders discard the first data row.** `PVModel.load_power_curve` and
  `LoadModel.load_power_curve` call `next(reader, None)` to skip a header, but neither CSV has
  one — they start directly at `0.0,0.0`. Each curve therefore loads 95 points instead of 96, and
  the midnight point is lost. Impact is small because both interpolators clamp below `times[0]`.
- **`config/deviceLogic.conf` is empty** and currently unread by any code.

---

## Layout

```
main.py                          entry point, threads, tick loop
config/
  device.json                    site definition
  modbus_registers.py            point name → register offset
  logging_config.json            log levels and files
  pv_curve.csv, load_curve.csv   24h power profiles
src/
  communication/modbus_server.py Modbus TCP server
  models/                        five device models
utils/
  config_loader.py               JSON loader
  locks.py                       the shared data_lock
introduction.html                illustrated overview of the system
MicroGridSimulator.service       systemd unit
```
