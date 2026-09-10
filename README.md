# MicroGrid Simulator

A pure-Python Modbus TCP slave that impersonates a whole microgrid — solar inverter, battery,
EV charger, building load, and revenue meter — so an energy management system can be exercised
against realistic, controllable devices instead of hardware.

No third-party dependencies. Python 3, standard library only.

---

## Rules

**These rules govern all work in this repository. Read them before changing code.**

1. **Every change is pushed to GitHub, for recording purposes.** GitHub is the record of
   this project — every change must land there so the history is complete and nothing exists
   only on one machine. Do not stop at a local commit. Run `git push` after committing.

2. **README.md is the source of truth.** Every rule and convention for this project is written
   here. Do not keep rules in your head, in commit messages, or in comments only.

3. **Every code change goes through README.md.** Before changing code, read this file for the
   rules that apply. After changing code, update the affected section of this file **in the same
   commit** so documentation never drifts from behaviour.

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
- **Chart colours are validated, not chosen by eye.** The eight `--s1`…`--s8` series slots in
  `web/index.html` were run through a colourblind-safety validator against the dashboard's own
  light (`#FFFFFF`) and dark (`#161F26`) surfaces: every adjacent pair clears ΔE 8 under simulated
  CVD and ΔE 15 under normal vision. Three light-mode hues fall below 3:1 contrast, which is why
  the chart ships direct labels and a table view rather than leaving colour to carry meaning alone.
  Changing a slot means re-running that check, and a ninth series is never a new hue.

---

## Running

Start from the repository root so the relative config paths resolve:

```
python3 main.py
```

Two servers come up:

| Interface | Address | Purpose |
|---|---|---|
| Modbus TCP | `0.0.0.0:5021` | Machine interface for an EMS or controller |
| Web dashboard | `http://localhost:8080` | Human interface — live state and setpoints |

Point any Modbus TCP client at 5021, or open the dashboard in a browser.

To read the meter: unit 1, address 0, quantity 2 returns net active power as a signed 32-bit
integer — divide by 100 for kW. Positive means the site is importing from the grid, negative
means exporting.

To run as a service, edit the paths in `MicroGridSimulator.service` to match your checkout, then
install it with systemd. It restarts on failure and routes output to syslog.

---

## Architecture

`main.py` starts three threads over a single `devices_data` dictionary guarded by one lock:

- **Simulation thread** (`update_device_data`) — ticks every device model once a second and writes
  the results into each device's register table.
- **Modbus thread** (`ModbusServer.run`) — answers Modbus requests out of that same table, one
  daemon thread per client connection.
- **Web thread** (`WebServer.run`) — serves the dashboard and its JSON API from the same table.

Every reader and writer takes the lock, so a client always sees a coherent snapshot, and anything
written becomes an input to the next tick. The Modbus and web interfaces are equivalent: a
setpoint written through either one lands in the same register.

### Rolling history

Models publish instantaneous values only — this tick's numbers and nothing older — so the
dashboard's trend chart would open with nothing behind it. `src/history.py` keeps the past: the
simulation thread drops one sample of every device's headline power into a fixed-size ring every
**10 seconds**, holding **24 hours** (8,640 samples), and the web server serves it at
`/api/history`.

The ring is bounded, so memory is flat however long the process runs — the oldest sample falls off
as the newest arrives. Nothing is persisted: a restart begins with an empty ring, exactly like SOC
and the energy counters.

`PowerHistory.record` takes `data_lock` to read `devices_data`, then appends under the module's own
lock. The two are taken one after the other and never nested, which is what keeps the simulation
thread and a browser request from deadlocking — and it is why `main.py` calls `record()` from
outside every `with data_lock` block in the tick loop, since the lock is not reentrant.

One point per device type is tracked, listed in `TRACKED_POINT`: the same headline power each
device's card shows. Energy counters and SOC are deliberately absent — a chart mixing kW with kWh
or with a percentage would need a second y-scale.

### Simulated clock

Time advances with real elapsed seconds, wrapping at midnight. One real second is one simulated
second — a full day takes a full day.

Where it *starts* depends on `start_time` in `config/device.json`:

| `start_time` | Behaviour |
|---|---|
| `"now"` *(default)* | The clock is set from the host's local time — hours, minutes **and seconds** — every time the simulator starts. Simulated time therefore always matches the wall clock. |
| `"HH:MM"` | The clock always starts at that fixed hour, whatever the local time is. Use this to replay a specific part of the day. |

Because configuration changes require a restart, `"now"` is the default: it means the simulated
clock re-aligns with local time on every restart rather than jumping back to a stale fixed hour.
The dashboard's **Match local time on start** checkbox toggles between the two.

---

## Device models

Eight model classes in `src/models/`. Six simulate device behaviour from config and a clock; the
meter and the virtual grid point have no physics of their own and derive everything from the
others.

The classes are what the simulator *can* run; `config/device.json` decides what it *does* run.
The shipped file instantiates six of the eight — see **Shipped device set** below. `EVModel` and
`T7PVModel` stay in the tree, fully supported and tested, and come back the moment a device of
their type is added to the file.

| Model | Type key | Behaviour |
|---|---|---|
| `PVModel` | `PV` | Follows a 24-hour irradiance curve from CSV, or a synthetic sin² arc 06:00–18:00 with ±5% noise. Output clamped to the written power limit. |
| `BatteryModel` | `BESS` | Integrates a signed power command into SOC. On hitting the SOC ceiling or floor it back-calculates the energy actually absorbed, so the kWh counters stay honest. |
| `EVModel` | `EV` | Draws rated power inside configured charging windows (midnight-wrapping supported), capped by the written setpoint, converted to balanced three-phase currents. |
| `LoadModel` | `Load` | Replays a demand profile interpolated from CSV at quarter-hour resolution, or jitters 80–120% of base power. |
| `JTCLoadModel` | `JTCLoad` | The JTC common load — landlord and common services. Its own algorithm, sharing no code with `LoadModel`: an operator-supplied CSV read through `DayCurve`, linearly interpolated at any resolution and wrapped across midnight. No synthetic mode and no base power — the curve or nothing. |
| `T7PVModel` | `T7PV` | Tower 7's PV plant, outside the Tower 10 network. The same direct `DayCurve` readout as the JTC common load, plus an integrated kWh total. No irradiance model, no synthetic arc, no curtailment input. |
| `MeterModel` | `Meter` | **Derived.** Sums the other devices into net power at the point of common coupling, then integrates it on a monotonic clock into separate import and export counters. |
| `VirtualGridModel` | `VirtualGrid` | **Derived.** Sums the JTC common load and the battery into the virtual grid incoming figure the EGC regulates against, with its own import and export counters and the three static settings. |

Every model exposes the same contract: a constructor taking its slice of `device.json`, and an
`update()` returning a dict of named points. `MeterModel` and `VirtualGridModel` additionally
receive `devices_data` and `data_lock` because they read the other devices. Both are listed in
`DERIVED_TYPES` in `main.py`, which is what makes the tick loop run them only after every physical
device has produced this cycle's values.

---

## Site model — the EGC control boundary

`JTC_Archi_Diagram.png` is the architecture this simulator stands in for. The site is larger than
the part the controller owns, so the EGC is shown a *synthetic* measurement rather than the real
incoming meter:

```
                 "Virtual" grid incoming
        = Incoming - (T5 + T6 + T7 + Podium + Basement)
                          |
--------------------------+--------------------------  EGC control boundary
   towers 5/6/7, podium and basement are pass-through and hidden from the loop
   T7 PV can be simulated on its own, outside both measurements — no T7PV device is configured
          |                                   |
   JTC Common Load                      Tower 10 (T98)
   landlord / common services           whole T98 load
                                              |  T98 LV bus
                                       +------+------+
                                     BESS           Solar PV
```

Two measurement points therefore exist, and they are deliberately independent:

| Point | Device | Sums |
|---|---|---|
| Tower 10 grid meter | `Meter_01`, unit 1 | `Load` - `PV` + `BESS` — plus `EV`, when a charger is configured |
| Virtual grid incoming | `VGRID_01`, unit 6 | `JTCLoad` + `BESS` |

`Meter_01` is the grid meter for the Tower 10 network: the T10 battery, inverter, charger and load
are its terms and nothing else. `T7PV` and `JTCLoad` are in neither sum. The shipped config has no
charger, so unit 1 currently reads `Load - PV + BESS`; `MeterModel` still sums the `EV` type and
picks one up unchanged if it is added back.

The virtual point is `JTC common load + BESS`: the JTC common load figure already contains Tower
10's own demand, so T98 is not added a second time, and the battery is the one element under
Tower 10 that moves the virtual import on its own. Battery charging (+) pushes the virtual import
up, discharging (-) pulls it down.

### Shipped device set

`config/device.json` defines exactly six devices, grouped by which side of the boundary they sit
on. The file is ordered that way too — the grid meter and the three Tower 10 devices it sums, then
the two off-loop points — so the boundary is legible in the config itself and not only here:

| Side of the boundary | Device | Type | Unit |
|---|---|---|---|
| Tower 10 | `Meter_01` | `Meter` | 1 |
| Tower 10 | `PV_01` | `PV` | 2 |
| Tower 10 | `BESS_01` | `BESS` | 5 |
| Tower 10 | `LOAD_001` | `Load` | 8 |
| Outside Tower 10 | `JTC_COMMON_01` | `JTCLoad` | 9 |
| Outside Tower 10 | `VGRID_01` | `VirtualGrid` | 6 |

One PV, one battery, one load and the grid meter that sums them make up the controlled network;
the JTC common load and the virtual grid point are the two figures above the boundary. Nothing
else is configured — in particular **no EV charger and no Tower 7 PV** — so a controller reading
this simulator sees the six units above and no others.

Both absent devices are a config decision, not a code one. Their models, their register maps, their
curve files and their tests are all still here; adding the object back to `device.json` and
restarting is the whole of what it takes to have them again.

### Tower 7 PV

**Not in the shipped `device.json`** — the section below describes the `T7PV` type as it behaves
when one is configured, which is what makes adding it back a one-object edit.

`T7_PV_01` simulates the PV plant on Tower 7, one of the towers the EGC does not control. It is
**excluded from the Tower 10 network and from the virtual grid figure alike** — nothing derives
from it. `MeterModel` matches the type `PV`, so the T7 plant carries its own type `T7PV` and
cannot land in the Tower 10 meter; `VirtualGridModel` sums the JTC common load and the battery, so
it is not in that figure either. It is a straight readout of an operator-supplied curve, published
positive like the Tower 10 inverter, with its generated energy integrated alongside:

```json
{ "T7PV": [ {
    "DeviceKey": "T7_PV_01",
    "csv_file":  "t7_pv_curve.csv",
    "slave_id":  10
} ] }
```

Should it ever need to feed the virtual point, name it in that device's `sources` list rather than
changing any model.

### The JTC common load

`JTC_COMMON_01` is a device in `device.json` like any other, but it is **not** the building load
model under a different name. `JTCLoadModel` imports nothing from `LoadModel` and implements its
own curve algorithm, because the two answer different questions: the building load may fall back
to a synthetic 80–120% jitter when it has no file, whereas the JTC common load is *only* ever the
profile the operator supplies.

That difference is deliberate. A fabricated common load would put the virtual grid point at a
wrong value without looking wrong, so there is no fallback at all: `csv_file` is required, and a
missing, empty or unreadable file raises at startup instead of inventing a curve.

The reader is also more forgiving than the older ones, since this file comes from the operator
rather than the repository:

- a header row is **detected**, not assumed — `hour,kW` is skipped, `0.0,560` is kept
- blank lines and `#` comments are ignored
- points may be at any resolution — hourly, quarter-hourly, five-minute, irregular
- interpolation is linear between neighbouring points and **wraps across midnight**, so a curve
  ending at 23:45 joins back up to its 00:00 value instead of flattening

Configure it with nothing but its curve:

```json
{ "JTCLoad": [ {
    "DeviceKey": "JTC_COMMON_01",
    "csv_file":  "jtc_common_curve.csv",
    "slave_id":  9
} ] }
```

To use your own profile, drop the file in `config/` and point `csv_file` at it — or give an
absolute path — then restart. `config/jtc_common_curve.csv` is a worked example, not a fixture the
code depends on; the shipped one runs in the **100–200 kW** band.

Tower 7's PV reads its curve exactly the same way — both go through `DayCurve` in
`src/models/curve.py`, which is shared only between these two operator-fed devices. The older
models keep their own loaders; nothing about them changed.

**The `Load` device under Tower 10 ships with an all-zero curve.** T98's demand is already carried
by the JTC common load figure above the boundary, so the site is fully represented without it, and
`LOAD_001` is left flat rather than restating demand that is already accounted for.

This only changes what `Meter_01` reports — with the curve at zero the Tower 10 point reads
`EV + BESS - PV`, so it shows PV export whenever nothing local is drawing. It does not affect the
virtual grid point at all: `VirtualGridModel` never reads `Load`, so the curve could not have
double-counted into it whatever its values. The device stays in place — it is the T98 load point,
and it is where a real profile belongs once T98's own metering is available.

`MeterModel` only sums the types it knows — `PV`, `BESS`, `EV`, `Load` — so the JTC common load
having its own type is what keeps it out of the Tower 10 figure. Adding the virtual point changed
nothing about what unit 1 reports.

**Nothing above the boundary may feed back below it.** `VirtualGridModel` reads `devices_data` and
never writes to it, and no other device type contributes to it. The failure mode is silent — the
Tower 10 meter would just start reporting a different number — so the invariant is pinned by
tests rather than left to inspection:

```
python3 -m unittest discover -s test -t .
```

`test/test_isolation.py` asserts that `MeterModel` returns the identical value with and without
the off-loop devices present, that an arbitrarily large JTC common load does not move it, and that
a virtual grid update leaves every other device's registers untouched.
`test/test_jtc_load.py` covers the common-load curve reader: header detection, comments and blank
lines, midnight wrapping, that a missing or empty file raises rather than falling back, and that
the shipped curve stays inside its 100–200 kW band at every interpolated instant, not just at the
sampled points. `test/test_t7_pv.py` does the same for Tower 7, and the isolation suite asserts
that 150 kW of T7 generation moves neither the Tower 10 meter nor the virtual grid point.
`test/test_history.py` covers the rolling ring behind the trend chart: that it stays bounded, that
an incremental read joins up with what a browser already holds, and that a late tick takes its
sample without shifting the cadence of the ones after it.

### The three static settings

The EGC regulates the virtual site with three fixed limits. The simulator publishes them as
registers on the virtual grid device so a controller reads the limits it is meant to respect
instead of carrying its own copy:

| Setting | Register | Default |
|---|---|---|
| Maximum import | `VG.MaxImport` | 1,700 kW |
| BESS zero-export threshold | `VG.BessZeroExport` | 150 kW |
| Tower 10 loop limit | `VG.T98LoopLimit` | `0` — **not yet supplied by JTC**, set it in `device.json` when the figure is known |

They are configuration, not physics: nothing in the simulator enforces them. Exceeding the import
cap is exactly the condition a controller under test is supposed to detect and correct — with the
shipped 100–200 kW common-load curve, the battery is what gets there: commanding about 1,500 kW of
charge at the afternoon peak puts the virtual point over 1,700 kW.

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

With the shipped device set the first two are the only ones reachable: there is no EV device, so
no unit answers for the charger. Curtailing the inverter and commanding the battery are the two
levers a controller has against Tower 10.

---

## Web dashboard

A browser UI served by the simulator itself on port 8080, built on `http.server` — no
dependencies, in keeping with Rule 1's conventions.

The dashboard has three tabs.

**Live** shows the Tower 10 point of common coupling as the headline figure with a rolling
sparkline, then a card per device with live power, state, accumulated energy, and battery SOC. The
JTC common load and the virtual grid incoming point get cards of their own; both sit outside the
Tower 10 coupling point, so neither is counted into the headline generation and consumption
totals. The three control
registers get a slider and a numeric field, so curtailing the inverter or commanding the battery
takes a drag rather than a hand-built Modbus frame.

**Trends** charts every device's active power over time, drawn from the rolling history above so
it is populated the moment the tab opens rather than filling in from empty. One line per device on
**one** kW axis — a second scale would invent a relationship the data does not have, which is why
SOC and the energy counters are not on it.

- **Range** — 15 minutes, 1 hour, 6 hours or 24 hours, in one control row above the chart.
- **Legend** — click a series to hide it. Colour follows the device type, never its position, so
  hiding one never repaints the others.
- **Hover or focus** — a crosshair snaps to the nearest sample and one tooltip lists *every*
  visible series at that instant. The chart is keyboard-reachable: arrow keys move the crosshair,
  Shift jumps ten samples, Home/End go to the ends, Escape clears it.
- **Direct labels** — each line ends in a dot carrying its value; a label that would collide with
  its neighbour is dropped rather than nudged off its line, and the legend and tooltip still carry
  it.
- **Max import** — the virtual grid's 1,700 kW cap is drawn as a dashed threshold whenever the
  data comes within reach of it. When everything on screen is far below, drawing it would flatten
  every series into one band, so the subtitle says it is above the range instead.
- **Table view** — the same window as a table, so no value is reachable only by hovering.

At the 24-hour range a line is 8,640 samples and the plot is about a thousand pixels wide, so
where a column of pixels holds many samples only that column's **minimum and maximum** are drawn,
in the order they occurred. Every point on screen is still a real sample and no peak is lost —
extremes are exactly what survives — but the path drops from ~52,000 points to ~9,700 and a redraw
from 17 ms to 6 ms, which matters because it runs on every pointer move. Below that density every
sample is drawn. Hover, the tooltip and the table always address real samples at full resolution;
decimation is a drawing concern only.

**The chart shows what the ring holds, and the ring starts empty.** A 24-hour view is a full day
only once the process has been running a full day; before that it shows everything recorded since
start-up, which is what the subtitle's sample count and time span state. Nothing is backfilled: the
simulator will not invent a past it did not simulate, for the same reason the JTC common load has
no synthetic fallback.

The chart polls `/api/history` only while its tab is open, and asks for `?after=<seq>` so it
fetches the handful of samples it is missing rather than the whole day. A restart resets the
sequence counter; the client notices the discontinuity and refetches in full.

The open tab is in the URL — `#live`, `#trends`, `#config` — so a reload comes back where you were
and a link can point at one.

**Configuration** edits `config/device.json` in a form — start time, dashboard port, and every
device's fields, including EV charging windows. Devices can be added and removed. Saving is
validated before anything is written, and rejected saves leave the file untouched.

**Match local time on start** keeps the simulated clock aligned to this machine's local time. Leave
it on so that every restart after a configuration change resumes at the current time; uncheck it
only to pin the simulation to a fixed hour.

Configuration changes do **not** affect the running simulation — `device.json` is read once at
startup. Saving raises a restart banner with a **Restart now** button, so the round trip stays in
the browser; there is also a **Restart simulator** button in the configuration actions.

Restarting re-reads `device.json` and resets SOC, energy counters, and the simulated clock. With
`start_time: "now"` the clock comes back aligned to local time.

### API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The dashboard page (`web/index.html`) |
| `GET` | `/api/state` | JSON snapshot: every device, its named points, config limits |
| `GET` | `/api/history` | Rolling power history, columnar; `?after=<seq>` returns only newer samples |
| `POST` | `/api/control` | Write one control register |
| `GET` | `/api/config` | Current `device.json`, the editable field schema, and the restart flag |
| `POST` | `/api/config` | Validate and write `device.json` |
| `POST` | `/api/restart` | Restart the simulator in place |

`/api/history` answers with the sample interval and span, a sequence counter, the simulated hour of
each sample, and one array per device:

```json
{ "interval": 10.0, "span_hours": 24.0, "seq": 431, "first_seq": 1, "count": 431,
  "sim_hour": [13.80, 13.81, "..."],
  "series":   { "Meter_01": [-100.0, -98.4, "..."], "VGRID_01": [200.0, 203.1, "..."] },
  "devices":  [ { "key": "Meter_01", "type": "Meter", "point": "ActivePower" } ] }
```

```
curl -X POST http://localhost:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"device":"PV_01","point":"INV.LimitPower","value":30}'
```

Writes are refused unless the point is one of the three control registers listed above —
everything else is an output the next tick would overwrite, so accepting it would be misleading.

### How restart works

`POST /api/restart` answers first, then re-executes the process with `os.execv`, replacing the
running image with a fresh `python3 main.py`. The listening sockets are closed beforehand and both
set `SO_REUSEADDR`, so ports 5021 and 8080 rebind immediately.

Because `execv` keeps the working directory, argv, and PID, this behaves the same whether the
simulator was started by hand or by systemd — systemd sees a continuously running process rather
than a failure, so `Restart=always` is not involved.

The browser polls `/api/state` until it answers again, then reloads. If the replacement process
cannot start, nothing is listening and the UI reports the failure after about 20 seconds — check
`log/simulation.log`. This is why `POST /api/config` validates before writing: a config that fails
validation can never reach the file, so a restart cannot be wedged by a bad save.

### Editing configuration safely

`POST /api/config` validates the whole document before touching disk. It checks the start-time
format, port range, device types, required fields per type, charging-window times, and that
`DeviceKey` and `slave_id` are unique and in range. If anything fails, nothing is written and the
problems come back as a list.

Writes are atomic — the new file goes to `device.json.tmp` and is swapped in with `os.replace`, so
an interrupted save can never leave a truncated `device.json`. The previous version is kept as
`config/device.json.bak` (gitignored).

The dashboard polls `/api/state` once a second. It is not authenticated and binds `0.0.0.0`; keep
it on a trusted network or change the bind address before exposing it. Anyone who can reach the
dashboard can rewrite `device.json`.

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

*Not in the shipped device set — this map applies when an `EV` device is configured.*

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

### Tower 7 PV — unit 10

*Not in the shipped device set — this map applies when a `T7PV` device is configured.*

| Addr | Point | Meaning |
|---|---|---|
| 0 | `T7PV.GenActivePW` | Generated power, outside the Tower 10 network |
| 2 | `T7PV.APProductionKWH` | Accumulated production |

### Building load — unit 8

| Addr | Point | Meaning |
|---|---|---|
| 0 | `Load.Power` | Demand drawn |

### JTC common load — unit 9

| Addr | Point | Meaning |
|---|---|---|
| 0 | `JTC.Power` | Landlord / common-services demand |

### Virtual grid incoming — unit 6

| Addr | Point | Meaning |
|---|---|---|
| 0 | `VG.ActivePower` | Virtual incoming, + import / − export |
| 2 | `VG.APConsumedKWH` | Imported energy through the virtual point |
| 4 | `VG.APProductionKWH` | Exported energy through the virtual point |
| 6 | `VG.MaxImport` | Static setting: maximum import |
| 8 | `VG.BessZeroExport` | Static setting: BESS zero-export threshold |
| 10 | `VG.T98LoopLimit` | Static setting: Tower 10 loop limit |

---

## Configuration

### `config/device.json`

Sets the simulated start time and lists the devices. Adding a second inverter or a third charger
means adding an object, not touching code.

The shipped file lists six devices in boundary order — `Meter`, `PV`, `BESS` and `Load` for Tower
10, then `JTCLoad` and `VirtualGrid` above it. See **Shipped device set** under Site model for what
each one is and why nothing else is there.

```json
{
  "start_time": "now",
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

A `JTCLoad` device takes the same fields as a `Load`. A `VirtualGrid` device takes the three
static settings and, optionally, a `sources` list naming exactly which devices feed it:

```json
{ "VirtualGrid": [ {
    "DeviceKey":        "VGRID_01",
    "max_import":       1700,
    "bess_zero_export": 150,
    "t98_loop_limit":   0,
    "slave_id":         6,
    "sources": [ { "device": "JTC_COMMON_01", "sign": 1 },
                 { "device": "BESS_01",       "sign": 1 } ]
} ] }
```

Leave `sources` out — as the shipped config does — and every `JTCLoad` and `BESS` device
contributes with a positive sign, which is the formula above. Set it only to depart from that,
for instance to subtract a PV plant that the common-load figure does not already net off. A
`sources` entry naming a device that does not exist is logged as a warning and ignored, so a
renamed `DeviceKey` shows up in `log/simulation.log` rather than silently dropping a term.

`start_time` is either `"now"` — align to local time at every start, the default — or a fixed
`"HH:MM"`. An optional top-level `"web_port"` key moves the dashboard off its default of 8080.

### Power curves

`config/pv_curve.csv`, `config/load_curve.csv` and `config/jtc_common_curve.csv` hold quarter-hour
points covering 24 hours as `hour,kW` pairs. Values between points are linearly interpolated.

The loader discards the first row as a header. `jtc_common_curve.csv` therefore starts with a
literal `hour,kW` line and keeps all 96 points; the two older files have no header and lose their
midnight point — see Known issues.

`jtc_common_curve.csv` and `t7_pv_curve.csv` are read through `DayCurve`, not by the loaders
above, so they follow the looser rules described under Site model — header optional, any
resolution, midnight wrap. Both are examples to replace with real data:

| File | Shape | Range |
|---|---|---|
| `jtc_common_curve.csv` | landlord common-services day — flat overnight, ramping from 06:00 to an early-afternoon plateau, falling away through the evening | 102–200 kW |
| `t7_pv_curve.csv` | solar day — dark until about 06:45, peaking early afternoon, dark again by 19:30 | 0–150 kW |

`t7_pv_curve.csv` is kept even though no `T7PV` device is configured — it is the curve that device
reads, and removing it would make adding one back a two-file job instead of a one-object edit.

`load_curve.csv` is all zeros, deliberately — see Site model above for why. Put a T98 profile in
it and the Tower 10 meter picks it up on the next restart; nothing else needs changing.

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
  `LoadModel.load_power_curve` call `next(reader, None)` to skip a header, but `pv_curve.csv` and
  `load_curve.csv` have none — they start directly at `0.0,0.0`. Both files run `0.0` to `24.0`
  inclusive, so 96 of their 97 points survive, but the one discarded is midnight. Impact is small
  because both interpolators clamp below `times[0]`. `jtc_common_curve.csv` ships with a header
  row and is unaffected.
- **`config/deviceLogic.conf` is empty** and currently unread by any code.

---

## Layout

```
main.py                          entry point, threads, tick loop
test/
  test_history.py                the rolling power ring behind the trend chart
  test_isolation.py              the Tower 10 loop is unaffected by the virtual point
  test_jtc_load.py               the JTC common load curve reader
  test_t7_pv.py                  the Tower 7 PV curve reader
config/
  device.json                    site definition
  modbus_registers.py            point name → register offset
  logging_config.json            log levels and files
  pv_curve.csv, load_curve.csv   24h power profiles
  jtc_common_curve.csv           24h JTC common load profile
  t7_pv_curve.csv                24h Tower 7 PV profile
src/
  history.py                     rolling power ring the trend chart reads
  communication/modbus_server.py Modbus TCP server
  communication/web_server.py    dashboard HTTP server and JSON API
  models/                        eight device models
  models/curve.py                shared reader for operator-supplied curves
web/
  index.html                     the dashboard UI
utils/
  config_loader.py               JSON loader
  locks.py                       the shared data_lock
introduction.html                illustrated overview of the system
JTC_Archi_Diagram.png            the site architecture this simulator stands in for
MicroGridSimulator.service       systemd unit
```
