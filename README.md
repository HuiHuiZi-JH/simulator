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
- **The site diagram adds no hues either.** Its boxes and links reuse the `--gen` / `--con` /
  `--accent` roles the device cards already use, so the palette stays the validated one and a node
  means the same thing as the card for the same device.

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

To run a checkout as a service on this machine, edit the paths in
`MicroGridSimulator.service` to match it and install that unit with systemd. To put the simulator
on a machine that should not have the source on it, see **Deployment** below.

---

## Deployment

A demo host is not a development machine. It gets a build, not a checkout: one file that installs
the simulator, registers it with systemd and starts it, leaving no Python source behind to read or
edit.

### Build a release

On this machine, from the repository root:

```
./deploy/build_release.sh            # version taken from git describe
./deploy/build_release.sh v1.2       # or name it yourself
```

That writes `dist/microgridsim-<version>-py<X.Y>.run` — a self-extracting installer of about
110 kB holding:

| In the release | What it is |
|---|---|
| `microgridsim.pyz` | every module compiled to bytecode, zipped, with a shebang — the program |
| `config/` | `device.json`, the four day curves, log settings — the operator's files |
| `web/index.html` | the dashboard, which browsers must read anyway |
| `MicroGridSimulator.service` | the systemd unit, with `@PREFIX@` and `@PYTHON@` still to fill in |
| `install.sh` | the four install steps, run for you |

No `.py` file is in it. `build_release.sh` compiles each module with `compileall -o 2`, deletes the
sources, and rewrites the path recorded in each `.pyc` to `microgridsim/...` so a traceback on the
host names the module without naming the build machine. Docstrings are stripped by `-o 2`.

**This hides the code; it does not encrypt it.** Bytecode can be decompiled by someone with root
on the host who wants to. It stops browsing, not a determined reader — so nothing that must stay
secret belongs on a host you do not control. The `.docx` design documents are not in the release
for that reason, and neither are the tests.

### Install on the host

Copy the one file over and run it as root:

```
scp dist/microgridsim-v1.2-py3.12.run user@host:~/
ssh user@host 'sudo ~/microgridsim-v1.2-py3.12.run'
```

`install.sh` unpacks into `/opt/microgridsim` and carries out the procedure:

1. Write `/lib/systemd/system/MicroGridSimulator.service`, with this install's prefix and
   `python3` path substituted in
2. `systemctl daemon-reload` — pick the unit up
3. `systemctl enable MicroGridSimulator.service` — 开机自动启动, start at boot
4. `systemctl restart` it, then print `systemctl status`

Two environment variables change where things land, and are only needed if `/opt` is wrong for the
site:

```
PREFIX=/srv/microgridsim sudo -E ./microgridsim-v1.2-py3.12.run
UNIT_DIR=/etc/systemd/system sudo -E ./microgridsim-v1.2-py3.12.run
```

The installed tree is root-only — `0700` directories and a `0400` archive. The service runs as
root and nothing else on the host needs to read it.

```
/opt/microgridsim/
  microgridsim.pyz    the program, 0400 root
  config/             device.json and the curves — edited here or from the dashboard
  web/index.html      the dashboard
  log/                simulation.log, message.log, modbus_traffic.log
```

`WorkingDirectory` in the unit is the prefix, and that matters: the code reads `config/` by a
relative path, and the web server takes `web/` and `config/` from beside the package in a checkout
but from the working directory when it is running out of the archive — where no such directory
exists. Started by hand from somewhere else, the simulator would look for its config in the wrong
place.

### Upgrading and removing

Run a newer `.run` file on the same host. The archive and `web/` are replaced; `config/` is not —
every file already there is kept and named on the way past, so a site's `device.json` and its
uploaded curves survive an upgrade. Only files that are missing get seeded from the release. The
service is restarted at the end, so the upgrade lands without a second command.

```
systemctl disable --now MicroGridSimulator.service      # stop it, and stop it starting at boot
rm -rf /opt/microgridsim /lib/systemd/system/MicroGridSimulator.service
```

### The one thing that can go wrong

Bytecode is tied to a Python **minor** version: a release built on 3.12 runs only on 3.12. The
version is recorded in the release and `install.sh` compares it against the host's `python3`
before it touches anything, so a mismatch is one sentence at install time rather than a service
that restarts forever. Build on a machine whose `python3` matches the host's, or install a
matching `python3` there.

The host needs no third-party packages — the release is standard library only, like the source.
Open TCP 5021 and 8080 to whoever must reach the simulator, and nothing else: both servers bind
`0.0.0.0` and neither asks for a password.

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
| `LoadModel` | `Load` | Replays a demand profile from CSV, interpolated linearly *between* its points at whatever instant it is asked for; or jitters 80–120% of base power; or holds a figure the operator typed in. Which of the three is a control register, not a config field, so it changes mid-run. |
| `JTCLoadModel` | `JTCLoad` | The JTC common load — landlord and common services. Its own algorithm, sharing no code with `LoadModel`: an operator-supplied CSV read through `DayCurve`, linearly interpolated at any resolution and wrapped across midnight. No synthetic mode and no base power — the curve or nothing. |
| `T7PVModel` | `T7PV` | Tower 7's PV plant, outside the Tower 10 network. The same direct `DayCurve` readout as the JTC common load, plus an integrated kWh total. No irradiance model, no synthetic arc, no curtailment input. |
| `MeterModel` | `Meter` | **Derived.** Sums the other devices into net power at the point of common coupling, then integrates it on a monotonic clock into separate import and export counters — by trapezoid, split at a zero crossing, through the shared `split_energy`. |
| `VirtualGridModel` | `VirtualGrid` | **Derived.** Sums the JTC common load and the battery into the virtual grid incoming figure the EGC regulates against, with its own import and export counters and the three static settings. |

Every model exposes the same contract: a constructor taking its slice of `device.json`, and an
`update()` returning a dict of named points. `MeterModel` and `VirtualGridModel` additionally
receive `devices_data` and `data_lock` because they read the other devices. Both are listed in
`DERIVED_TYPES` in `main.py`, which is what makes the tick loop run them only after every physical
device has produced this cycle's values.

---

## Formulas

Everything the simulator computes, in one place. `h` is the simulated hour of day, `P` is kW,
`Δt` is hours. Nothing here implements control: these produce the measurements a controller reads.

### The clock

```
current_hour = start_hour + (time.time() - start_time) / 3600
h            = current_hour mod 24
```

`start_hour` is the host's local time when `start_time` is `"now"`, otherwise the configured
`"HH:MM"`. One real second is one simulated second.

### Curve interpolation

Three readers, all linear between neighbouring points, differing only at the edges.

**`DayCurve.at(h)`** — the JTC common load and Tower 7 PV. Wraps midnight, so the last point of the
day joins the first point of the next:

```
                        v₂ - v₁
v(h) = v₁ + (h - t₁) · ─────────
                        t₂ - t₁

for h < t_first or h ≥ t_last:   t₁ = t_last,  t₂ = t_first + 24,  h += 24 if h < t_first
```

**`LoadModel.interpolate_power(h)`** and **`PVModel.interpolate_power(h)`** — the same expression on
the bracketing pair, but they **clamp** instead of wrapping: below the first point they return the
first value, above the last they return the last. Both files therefore carry a closing `24.00` row
equal to their `00:00` one.

### PV inverter — `PVModel`

```
mode 0 (CSV):        base = interpolate(curve, h)
mode 1 (synthetic):  base = rated · sin²( π · (h - 6) / (18 - 6) ) · (1 + U(-0.05, +0.05))   for 6 ≤ h ≤ 18
                     base = 0                                                                otherwise

limit_power     = clamp(p_limit, 0, rated)          when a limit is written and > 0
GenActivePW     = clamp(base, 0, limit_power)
APProductionKWH += GenActivePW · (1/3600)
APProduction     = GenActivePW
```

`INV.LimitPower` is the one input: writing it curtails the inverter. The synthetic arc's ±5% noise
is redrawn every tick, so mode 1 is not reproducible between runs; mode 0 is.

### Battery — `BatteryModel`

```
P         = clamp(setpoint, -maxDischargePower, +maxChargePower)
ΔE        = P · (1/3600)                         kWh this tick
ΔSOC      = (ΔE / ratedCapacity) · 100           percentage points
SOC_new   = SOC + ΔSOC
```

On hitting a bound, the energy is **back-calculated** so the counters record what the battery
actually absorbed, not what was asked for:

```
charging, SOC_new > socMax·100:     excess = SOC_new - socMax·100
                                    ΔE    -= (excess / 100) · ratedCapacity
                                    SOC    = socMax · 100

discharging, SOC_new < socMin·100:  excess = socMin·100 - SOC_new
                                    ΔE    += (excess / 100) · ratedCapacity
                                    SOC    = socMin · 100

TotalChargingEng    += ΔE      (charging, ΔE > 0)
TotalDischargingEng -= ΔE      (discharging, ΔE < 0, so the total grows positive)
BS.Soh               = 100     constant
```

**There is no loss model.** `voltage_nominal` and `resistance` are read from `device.json` and never
used: no I²R heating, no round-trip efficiency, no taper near the SOC bounds. A kWh in is a kWh
out. If the EGC is ever tuned against round-trip losses, they are not here to be tuned against.

### EV charger — `EVModel`

```
in a charging window:   P = max(minChargePW, min(ratedPW · charge_factor, ChargePWSet))
otherwise:              P = 0

window test, start ≤ stop:   start ≤ h < stop
window test, start > stop:   h ≥ start or h < stop        (wraps midnight)

CurrentL1 = L2 = L3 = P · 1000 / (3 · V · pf)
ChargeCurSetL1       = ChargePWSet · 1000 / (V · pf)
ChargeEnergyKWH     += P · (1/3600)

AC charger: V = 220, pf = 0.95      DC charger: V = 400, pf = 1.0
```

Note the **inconsistency between the two current lines**: the measured phase currents divide by
`3·V·pf`, the current *setpoint* divides by `V·pf`, so for the same power the setpoint reads three
times the per-phase current. Unresolved; no EV device is configured, so nothing reads it today.

### Building load — `LoadModel`

```
Load.ModeSet = 0 (CSV):        P = interpolate(curve, h)
Load.ModeSet = 1 (simulated):  P = base_power · U(0.8, 1.2)
Load.ModeSet = 2 (manual):     P = max(0, Load.PowerSet)
```

`Load.ModeSet` starts at the `mode` in `device.json` and `Load.PowerSet` at its `base_power`, so an
unwritten register never means something the operator did not configure. `base_power` therefore
does two jobs: it is the centre of the simulated day, and it is what the manual source holds until
someone types over it. `LOAD_001` ships it at **1,000 kW** — the floor of the 1,000–1,800 kW band
given for T98 — so both start somewhere plausible for this tenant; it was 50 kW while Tower 10 was
not simulated at all, which left the manual source opening at a fiftieth of the real load. A load
with no `base_power` at all falls back to `DEFAULT_BASE_POWER`, 120 kW, in the model *and* in
`main.py`'s register seed — one constant, imported, because the two used to disagree and a load
with no base power switched to manual and held zero.

The default only applies at start-up. `Load.PowerSet` is a register: once written it keeps the last
figure, so switching to the curve and back to manual returns to whatever was typed before, not to
the default. A device that started on
a synthetic or manual source has never read its CSV; switching to the curve loads it at that moment,
and a missing or unusable file falls back to the synthetic day rather than to a silent zero.

### JTC common load — `JTCLoadModel`

```
JTC.Power = DayCurve.at(h)
```

That is the whole model. No synthetic mode, no base power, no fallback — a missing or unreadable
file raises at startup instead of inventing a curve.

### Tower 7 PV — `T7PVModel`

```
T7PV.GenActivePW    = DayCurve.at(h)
T7PV.APProductionKWH += max(P, 0) · Δt
```

### Tower 10 meter — `MeterModel` (derived)

```
load_power = Σ Load + Σ EV + Σ max(BESS, 0)          charging counts as consumption
pv_power   = Σ PV   + Σ |min(BESS, 0)|               discharging counts as generation

ActivePower = load_power - pv_power   ⇒   Load + EV + BESS - PV

APConsumedKWH   += imported      over the interval the last two readings bound
APProductionKWH += exported      — see Directional energy, below
```

Positive is import from the grid. `JTCLoad` and `T7PV` are not in the sum — they carry types
`MeterModel` does not match, which is what keeps them out of the Tower 10 loop.

### Virtual grid incoming — `VirtualGridModel` (derived)

```
VG.ActivePower = Σ sign_i · P_i        over the configured sources

default sources (no `sources` list): every JTCLoad and every BESS, sign +1
                                     ⇒ VG.ActivePower = JTC common load + BESS

VG.APConsumedKWH   += imported   the same integration the Tower 10 meter uses
VG.APProductionKWH += exported
```

`VG.MaxImport`, `VG.BessZeroExport` and `VG.T98LoopLimit` are published unchanged from
`device.json` — configuration passed through, not computed.

### Directional energy — `split_energy`

Both `MeterModel` and `VirtualGridModel` turn a *signed* power into two counters, and both do it
through `src/models/energy.py` rather than each keeping its own arithmetic:

```
given P₀ (the previous reading), P₁ (this one) and Δt in hours:

Δt ≤ 0 or Δt > 1 min          → nothing is booked; the interval is a gap, not an interval
P₀ and P₁ on the same side    → one trapezoid:  ΔE = (P₀ + P₁)/2 · Δt   into that side's counter
P₀ and P₁ across zero         → the crossing, then a triangle each side:
                                t* = Δt · |P₀| / (|P₀| + |P₁|)
                                |P₀|/2 · t*          into P₀'s counter
                                |P₁|/2 · (Δt - t*)   into P₁'s counter
```

Each of the three clauses replaces something the plain `P_now · Δt` accumulation got wrong:

- **The rectangle was charged at the wrong power.** The whole second was billed at the reading that
  *ends* it. With Tower 10 now ramping about 800 kW across a day, every interval was biased the
  same direction, 86,400 times a day. The trapezoid is exact for a power that moves linearly
  between samples, which at a one-second tick it effectively does.
- **A sign change inside an interval landed wholly in one bucket.** Commanding the battery from
  −200 kW to +300 kW booked the entire second as import, when the site exported for the first 40%
  of it. Both counters now take their share, so neither absorbs the other's energy.
- **An absurd Δt went straight in.** A stalled cycle or a suspended host handed the counter hours
  of elapsed time, and one tick added energy that never flowed. Anything longer than a minute is
  logged and skipped instead.

The counters stay monotonic and are still reset only by a restart. `test/test_energy.py` pins all
three clauses, including that a day of intervals sums to the closed-form area under the curve.

### Energy integration — two different Δt

This is worth knowing before trusting a kWh counter:

| Model | Δt used | Rule |
|---|---|---|
| `MeterModel`, `VirtualGridModel` | **measured**: `(time.monotonic() - last) / 3600` | trapezoid, split at a zero crossing |
| `T7PVModel` | **measured**, the same way | rectangle — a PV plant never crosses zero |
| `PVModel`, `BatteryModel`, `EVModel` | **assumed**: a fixed `1/3600` h, i.e. exactly one second per tick | rectangle |

The tick loop sleeps a flat 1.0 s per cycle and does its work on top, so a cycle is always slightly
longer than a second. The measured counters track that; the fixed-step ones quietly run slow by
however long the work takes. Over a day it is a fraction of a percent, and the two sets of counters
will not agree exactly.

### Modbus wire encoding

```
read  (FC3):   int32 = clamp( round_toward_zero(value · 100), -2³¹, 2³¹-1 )
               sent big-endian signed across two consecutive registers

write (FC16):  value = int32 / 100,  clamped to ±21,474,836.48
```

Every point is 32-bit, so offsets and quantities are always even.

### Trend history sampling

```
sample when now ≥ next_due, then:  while next_due ≤ now:  next_due += interval

interval = 10 s,  ring = 24 h / 10 s = 8,640 samples
```

Stepping the schedule by whole intervals rather than from `now` is what stops a slow tick from
skewing every later sample.

### Chart drawing

Envelope decimation, when a pixel column holds more than one sample:

```
buckets = plot width in pixels
per bucket:  draw (i_min, v_min) and (i_max, v_max), in whichever order they occurred
```

Both plotted points are real samples, so peaks survive. Axis steps are rounded to a clean interval:

```
raw  = range / 5
mag  = 10^⌊log₁₀ raw⌋
step = mag · (1 if raw/mag ≤ 1 else 2 if ≤ 2 else 5 if ≤ 5 else 10)
```

---

## Site model — the EGC control boundary

`JTC_Archi_Diagram.png` is the architecture this simulator stands in for — and the layout the
dashboard's site diagram follows. Three details in that drawing are contradicted by the submission
it was drawn from; they are listed under Web dashboard, and the submission wins. The site is larger
than the part the controller owns, so the EGC is shown a *synthetic* measurement rather than the
real incoming meter:

```
              Calculated "JTC common load"  =  the EGC's grid reference
   = Incoming - (T5 + T6 + T7 + Podium + Basement + T10)      recomputed every 2 s
                          |
--------------------------+--------------------------  EGC control boundary
   every tenant feeder — towers 5/6/7, podium, basement AND tower 10 — is
   subtracted out, so what is left is the landlord's own common services
   T7 PV (240 kW) and T10 PV (52 kW) are netted inside those feeders, not controlled
          |                                   |
   JTC Common Load                      Tower 10 (T98)
   landlord / common services           whole T98 tenant load
   max import 1,700 kW                  transformer 2,500 kW, loop limit 1,750 kW
   floor 150 kW                                |  T98 LV bus
          |                            +-------+-------+
          +-------- BESS 0.8 MW -------+            Solar PV 52 kW
```

Two measurement points therefore exist, and they are deliberately independent:

| Point | Device | Sums |
|---|---|---|
| Tower 10 grid meter | `Meter_01`, unit 1 | `Load` - `PV` + `BESS` — plus `EV`, when a charger is configured |
| Virtual grid incoming | `VGRID_01`, unit 6 | `JTCLoad` + `BESS` |

`Meter_01` is the grid meter for the Tower 10 network: the T10 battery, inverter, charger and load
are its terms and nothing else. `T7PV` and `JTCLoad` are in neither sum. The shipped config has no
charger, so unit 1 currently reads `Load - PV + BESS`; `MeterModel` still sums the `EV` type and
picks one up unchanged if it is added back. This is the meter the EGC's **multi-loop** runs its
second control loop on, against the 1,750 kW limit.

The virtual point is `JTC common load + BESS`. Tower 10's own demand is **not** in it: the
submission's formula subtracts the T10 feeder along with every other tenant feeder, so what
survives the subtraction is the landlord's common services and nothing else. The battery is added
because it is what the loop dispatches — Figure 2 of the submission puts the common-services load
and the BESS behind the virtual node. Battery charging (+) pushes the virtual import up,
discharging (-) pulls it down, which is the whole mechanism of use-cases 1 and 2.

### Site data — where these numbers come from

The plant ratings, the three limits and the shape of all four curves come from **"BESS Charging /
Discharging Control Logic — CC2&3", design document v1.2, dated 25/08/2026**, plus the Electrical
SLD v2.0 it cites.

**That document is not in this repository and must not be pushed to it.** `.gitignore` refuses
`*.docx`, `*.doc`, `*.xlsx` and `*.pdf` so it cannot be committed by accident — keep your copy
locally. What the repository carries instead is everything derived from it: the anchors and limits
at the top of `utils/make_curves.py`, the settings in `device.json`, and this section.

What was taken from it:

| | Value |
|---|---|
| BESS PCS | 800 kW, connected at Tower 10 (T98) |
| Rooftop PV | 240 kW at Tower 7, 52 kW at Tower 10 — netted inside the metered feeders, not separately controlled |
| T98 transformer | 2,500 kW rated; multi-loop limit 1,750 kW (70%) |
| Maximum import | 1,700 kW on the calculated JTC common load |
| Power margin | 5% — control acts from 1,615 kW |
| BESS zero-export offset | 150 kW, set directly (not a live percentage) |
| Control cycle | 2 s |
| JTC common load | Incoming − (T5 + T6 + T7 + Podium + Basement + T10) |

The control strategy itself is **not** implemented here and is not meant to be: all three
use-cases are existing EGC functions configured through its HMI (Static Demand Management, BESS
Zero Export, Multi-loop). This simulator's side of that split is to present a site that reaches
every limit, so the strategy has something to act on.

Three things the document leaves open, carried here as they are rather than guessed:

- **BESS energy capacity is not stated** — only the 800 kW PCS. `ratedCapacity` stays at its
  pre-existing 12,000 kWh, which at 800 kW is a 15-hour battery and will barely move SOC across a
  day. If the real figure is nearer 1,600 kWh, say so and it is a one-line change.
- **Where the BESS sits relative to the T10 feeder meter.** If it is behind the meter that the
  common-load calculation subtracts, the calculation would subtract the battery's own action and
  the loop could not see it. The simulator follows Figure 2 — the BESS is behind the virtual node
  and moves it — which is the only reading under which use-cases 1 and 2 function.
- **The 2 s control cycle** is the EGC's, not the simulator's: models tick once a second and the
  trend ring samples every ten. Nothing here needs to match it, but a controller under test will
  see values that are at most one second stale.

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
absolute path, or upload it from the dashboard's configuration tab, which does the same thing
without a shell — then restart. `config/jtc_common_curve.csv` is generated from the design basis by
`utils/make_curves.py`, not a fixture the code depends on; the shipped one runs from **183 kW
overnight to 1,837 kW** at the afternoon peak, crossing the 1,700 kW import limit on the way.

Tower 7's PV reads its curve exactly the same way — both go through `DayCurve` in
`src/models/curve.py`, which is shared only between these two operator-fed devices. The older
models keep their own loaders; nothing about them changed.

**The `Load` device simulates the Tower 10 tenant day, 1,006–1,800 kW.** That band is the
operator's figure for T98: a tenant that never really goes quiet, holding about 1,000 kW overnight
and rising through the working day to about 1,800 kW mid-afternoon. The shape is generated from
`T98_ANCHORS` in `utils/make_curves.py`, beside the switch `T98_LOAD_ZERO`, which is now `False`.

It shipped as an all-zero curve before — nobody had asked for Tower 10 to be simulated — and the
switch is kept so that decision can be taken again in one line. An earlier version of this file
justified the zero differently, that T98's demand was already inside the JTC common load figure.
That reasoning was **wrong** and is worth recording so it is not repeated: the submission subtracts
the T10 feeder along with every other tenant feeder, so the common figure never contained Tower 10
at all — the two are independent, which is the whole point of the two measurement points above.

What the real load buys, and what it costs:

- **Use-case 3 now binds, hard.** The EGC caps charging at `1,750 kW - T98 load`. At the overnight
  minimum that cap is 744 kW, already under the 800 kW PCS, so a full-power charge request is cut
  back at *every* hour of the day; for about 2.5 h around the afternoon peak the load alone is over
  the 1,750 kW limit, the cap goes negative, and only discharging can hold the loop.
- **`Meter_01` now reads a real import all day** — `Load - PV + BESS` on a four-figure load,
  instead of the `BESS - PV` it read at zero, where unit 1 showed PV export whenever the battery
  was idle.
- **The multi-loop and the import loop can now fight.** Use-case 1 discharges against the JTC
  common load's 1,700 kW limit in the early afternoon; the T98 peak wants discharge at the same
  hour. Both want the same direction here, so the shipped day does not deadlock — but the two
  loops are no longer trivially compatible the way they were at zero, and that is now something the
  EGC's strategy is actually exercised on.

What it does *not* touch is the figure the EGC regulates. `VirtualGridModel` never reads `Load`, so
the JTC common load and the virtual grid point are `JTCLoad + BESS` and nothing else — units 9 and
6 are identical whatever Tower 10 does. Use-cases 1 and 2 are unaffected.

`LoadModel` also used to round the clock to the nearest quarter hour before interpolating — the
resolution its curve happens to be sampled at — so it landed exactly on a curve point every time
and the T98 load stepped once every 15 minutes instead of moving. Beside the JTC common load,
which moves every second, it read as a frozen value on unit 8 and drew a flat line on the trend
chart. It now interpolates at the instant it is asked for, like every other curve in the
simulator; `test/test_t98_load.py` pins that values one minute apart differ, while the sampled
points still read exactly.

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
the shipped curve reaches the limits it has to reach (below). `test/test_t7_pv.py` does the same
for Tower 7, and the isolation suite asserts that 150 kW of T7 generation moves neither the Tower
10 meter nor the virtual grid point.
`test/test_history.py` covers the rolling ring behind the trend chart: that it stays bounded, that
an incremental read joins up with what a browser already holds, and that a late tick takes its
sample without shifting the cadence of the ones after it.
`test/test_dashboard_api.py` pins the point catalogue the diagram's detail panel is built on:
that it covers every type in `config/modbus_registers.py`, lists each point once at its own offset
in register order, carries the descriptions from that file rather than a copy, and marks exactly
the five control registers writable — named one by one, so adding a sixth is a decision rather
than a number that quietly moves.
`test/test_energy.py` covers the integration both sets of kWh counters run on: that a constant
power is power × time, that a ramp is charged at its mean rather than at its end, that an interval
crossing zero is divided between the two counters at the crossing, that a long gap books nothing,
and that a day of intervals sums to the closed-form area under the curve.
`test/test_load_modes.py` covers the load's three sources: that each one produces what it should,
that switching to the curve loads a CSV the device never read at start-up, that a missing file
falls back to the synthetic day rather than to a silent zero, that a stray mode is ignored, and
that the register offsets `main.py` passes are the offsets the register map defines — nothing else
would notice if those two drifted apart.
`test/test_curve_upload.py` covers the one path where a file the models depend on arrives from
outside the repository: that an upload lands in `config/` and nowhere else however it is named,
that what the dashboard accepts is exactly what `DayCurve` then reads back, that an unusable or
oversized file is refused without being written, and that an existing curve is replaced only when
replacement is asked for.

The curve tests assert the **design basis**, not arbitrary bands, because a curve that never
approaches a limit would leave the EGC strategy untestable while looking perfectly healthy:
`test_jtc_load.py` pins that the common load crosses 1,700 kW by an excursion the 800 kW PCS can
cover, that its minimum stays above the 150 kW floor but within reach of it, and that the 5% margin
line is crossed in both directions. `test_t98_load.py` pins that the shipped Tower 10 curve stays inside the operator's
1,000–1,800 kW band, that its peak crosses the 1,750 kW multi-loop limit on its own and that the
charge cap therefore never admits the whole PCS — the consequence is asserted rather than left to
be discovered — and covers `LoadModel`'s interpolation against a fixture of its own, so the model
stays tested whichever way `T98_LOAD_ZERO` is set.

### The three static settings

The EGC regulates the virtual site with three fixed limits. The simulator publishes them as
registers on the virtual grid device so a controller reads the limits it is meant to respect
instead of carrying its own copy:

| Setting | Register | Value | Source |
|---|---|---|---|
| Maximum import | `VG.MaxImport` | 1,700 kW | Static Demand Management, requirement 1 |
| BESS zero-export threshold | `VG.BessZeroExport` | 150 kW | BESS Zero Export relative offset, requirement 2 |
| Tower 10 loop limit | `VG.T98LoopLimit` | 1,750 kW | Multi-loop on the T98 meter — 70% of the 2,500 kW transformer, requirement 3 |

They are configuration, not physics: **nothing in the simulator enforces them.** That is the
division of labour — the limits are what the EGC strategy is written against, and this simulator's
job is to present a site that reaches them. With the shipped curves it does so on its own, with the
battery doing nothing at all: the common load crosses 1,700 kW for about two hours in the early
afternoon, and the Tower 10 load crosses the 1,750 kW loop limit for about two and a half hours
around its own peak.

A fourth EGC setting, the **5% power margin** (control acts from 1,615 kW), is not published as a
register. Say the word and it becomes `VG.PowerMargin` at offset 12, on the same reasoning as the
other three — that a controller should read the limits rather than carry its own copy.

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

Five registers are **inputs**, read back by the simulation each tick. Writing one changes what
the next tick produces — this is what makes the simulator useful for testing a controller rather
than replaying data.

| Device | Addr | Point | Effect |
|---|---|---|---|
| PV | 4 | `INV.LimitPower` | Curtails inverter output |
| BESS | 14 | `BS.SysAPSetPoint` | Commands charge (+) or discharge (−) |
| EV | 8 | `PUB_CONN.ChargePWSet` | Caps charging power |
| Load | 2 | `Load.ModeSet` | Source: 0 curve, 1 simulated, 2 manual |
| Load | 4 | `Load.PowerSet` | The manual figure, used when the source is 2 |

Function code 16 will accept a write to any even offset, but only these five are consumed by the
models. The rest are overwritten on the next tick. An input is never republished by its model, so a
write stands until the next one — the tick loop only writes back the points a model returns.

With the shipped device set four of the five are reachable: there is no EV device, so no unit
answers for the charger. Curtailing the inverter, commanding the battery and driving the tenant
load are the levers a controller has against Tower 10.

---

## Web dashboard

A browser UI served by the simulator itself on port 8080, built on `http.server` — no
dependencies, in keeping with Rule 1's conventions.

The dashboard has three tabs.

**Live** headlines the **virtual grid incoming point** — `JTC common load + BESS`, the calculated
figure the EGC actually regulates — as a big number with a rolling sparkline, the day's energy
through that point, the 1,700 kW maximum import and how much headroom is left under it. Headroom
goes negative when the limit is already crossed, which is the moment use-case 1 exists for. The
sparkline carries the same dashed import cap the trend chart does, drawn only when it is on scale.

The headline used to be the Tower 10 point of common coupling — `Meter_01` — and that swap is
deliberate: the PCC is a real meter but it is *not* what the EGC controls against, and a dashboard
leads with the number its operator is steering by. The meter did not lose anything by moving: it
now gets an ordinary device card (power, importing/exporting, both kWh counters) alongside the
others, keeps its box on the site diagram, and keeps the full register list in the detail panel.
The virtual grid point is the one without a card now, because it is in the hero instead.

Below the hero, a card per device with live power, state, accumulated energy, and battery SOC. The
control registers are driven from the cards, so no hand-built Modbus frame is needed: the inverter
limit and the battery command get a slider and a numeric field, and **the load gets a source
switch** — *CSV curve*, *Simulated*, *Manual* — with a **manual power** box that appears only under
*Manual*, because under the other two the number would go nowhere. Both write through
`POST /api/control` exactly as a Modbus write would, and take effect on the next tick; neither is
written back to `device.json`, so a restart returns the load to its configured `mode`. While a
click is in flight the card holds the operator's choice rather than flicking back to the register's
old value.

The **generating / consuming "right now" pair is gone** with the old hero. It summed the devices
inside the Tower 10 loop, which was the boundary the old headline described; against a virtual
point whose only terms are the common load and the battery it would have been answering a question
nobody asked. Per-device power is on the cards and on the diagram.

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

**The site diagram** sits between the virtual-grid headline and the device cards, and draws the
shipped device set as one live single line — laid out the way `JTC_Archi_Diagram.png` lays out the
site, so the dashboard and the architecture drawing read as the same picture:

```
                     ┌ ── Virtual grid incoming ── ┐   dashed: a calculation, not a meter
                     │  = Incoming − Σ(T5+T6+T7+Podium+Basement+T10)
  ══════════════════════════════╪══════════════════════════════  EGC control boundary
        │                                          │
  JTC common load                            Tower 10 (T98)
  landlord / common services                 the whole feeder, at Meter_01
                                        ═══════════╪═══════════  T98 LV bus
                                          │        │        │
                                       BESS 0.8 MW │     Tower 10 load
                                               Solar PV
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ EGC regulates this virtual site with 3 static settings: max import …      │
  └──────────────────────────────────────────────────────────────────────────┘
```

Every box carries its own headline power, tinted the way the cards are: green generating, orange
drawing, grey idle. Links animate in the direction power is actually flowing, from the sign of the
value, and stand still when a device is at zero. The settings strip along the bottom is read from
the registers `VGRID_01` publishes, not retyped, so it cannot disagree with what a controller sees.

The diagram is drawn from the snapshot, not from a fixed picture of the shipped config: boxes are
placed by band and type, so a second battery, a charger added back, or a Tower 7 PV device appears
in its own slot without the file being edited. A band now says which loop a device is in rather
than where it sits on the canvas — `t10` inside the Tower 10 loop, `egc` in the EGC control view
above it, `outside` for a feeder that is pass-through to both, which is where a `T7PV` device is
drawn. The one link that crosses the boundary is the battery's, because the battery is the one
device on both sides of it — in the Tower 10 meter sum, and moving the virtual grid point; it is
routed under the device row and up the margin so it crosses no box on the way.

**Three things the drawing gets wrong are not reproduced**, because the submission itself
contradicts them (`JTC_BESS_Control_Logic_Submission_v1.2.docx`, "Metering & JTC load calculation"
and "Connection point"):

| `JTC_Archi_Diagram.png` says | The submission says | The diagram draws |
|---|---|---|
| `= Incoming − Σ(T5 + T6 + T7 + Podium + Basement)` | `… + Tower 10` in the same sum | Tower 10 included |
| Tower 10 is "part of JTC common load" | its feeder is subtracted out, like every other tenant's | Tower 10 outside the common load |
| "Solar PV 200 kW" | 240 kW at Tower 7, **52 kW** at Tower 10 | the configured `ratedPower`, 52 kW |

The PNG is a drawing of the site; the README and the models are what the simulator runs on. Where
they disagree the submission wins, and the drawing is the thing that should be corrected.

There is no utility-grid box any more. The architecture diagram has none — the virtual point is
the top of its world — and the box only ever repeated `Meter_01`'s reading. The meter now carries
that reading itself, as the Tower 10 (T98) branch.

**The detail panel** beside it follows the selection. Click a box — or tab to it and press Enter —
and the panel names the device, its unit, which side of the boundary it is on, its headline value
and state, what it is, the formula it is computed by, and then **every register it publishes**:
name, description and live value, with `W` against the ones that accept writes. The bus is
selectable too, and lists what is on it. For a device with a control, **Open controls** scrolls to
its card below.

What the panel says about each device is what this README says: the descriptions come from
`config/modbus_registers.py` through `GET /api/points`, and the notes restate the Site model
section. Nothing in the diagram is a second description of the model that could drift from it.

The open tab is in the URL — `#live`, `#trends`, `#config` — so a reload comes back where you were
and a link can point at one.

**Configuration** edits `config/device.json` in a form — start time, dashboard port, and every
device's fields, including EV charging windows. Devices can be added and removed. Saving is
validated before anything is written, and rejected saves leave the file untouched. A **Source**
field offers *CSV curve* and *Synthetic* for a PV, and *Manual* as a third for a load — the
validator holds the same line, so a `mode` of `2` on a PV is refused rather than written and
silently ignored.

**Curve fields take an upload.** Every `csv_file` field is a text box plus an **Upload CSV…**
button. The text box still accepts anything the model does — a name in `config/`, or an absolute
path to a file the operator keeps elsewhere — and offers the curves already in `config/` as
suggestions; under it, a line says how many points the named curve has and the range it covers, or
that no such file is there yet. Uploading sends the file to `POST /api/curve`, which writes it into
`config/` and fills the field in with its name.

The page decides a field is a curve field from either half of what it knows — the `curve` kind in
the schema, or the field being `csv_file` — because the schema comes from the *running* process.
A browser reloaded against a simulator that has not restarted yet gets the new page and the old
schema, and keying only off the schema would render a plain text box with no upload button, which
reads as a missing feature rather than a stale process. An upload does not touch `device.json`: the field
is set in the form and saved with everything else, so an upload the operator changes their mind
about is discarded with **Discard changes** like any other edit.

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
| `GET` | `/api/points` | Every point each device type publishes: description, register offset, writable |
| `GET` | `/api/curves` | The readable day curves in `config/`, with point count and range |
| `POST` | `/api/curve` | Validate an uploaded day curve and write it into `config/` |
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

Writes are refused unless the point is one of the five control registers listed above —
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

### Uploading a curve safely

`POST /api/curve` takes `{"name": "my_load.csv", "content": "hour,kW\n0,183\n...", "overwrite": false}`
and applies four checks before anything reaches disk:

- **The name is a plain file name.** It must match `[A-Za-z0-9][A-Za-z0-9._-]*\.csv`, and only the
  base name is used, so `../device.json` is refused and `/etc/passwd.csv` becomes
  `config/passwd.csv`. An upload cannot write outside `config/` whatever it is called.
- **The file is a day curve.** It is parsed by `parse_points` in `src/models/curve.py` — the same
  reader `DayCurve` uses, not a second laxer one — so a file the dashboard accepts cannot fail when
  the simulator restarts. A file with no usable `hour,value` row is refused, with the reason.
- **The file is curve-sized.** Over 1 MB is refused, and a request whose `Content-Length` is over
  2 MB is refused before the body is read at all.
- **An existing curve is only replaced when asked.** Re-using a name answers `409` with
  `exists: true`; the dashboard asks the operator and retries with `overwrite: true`. This is what
  keeps a stray upload from quietly replacing `jtc_common_curve.csv`.

The write goes through `<name>.csv.tmp` and `os.replace`, like `device.json`, so an interrupted
upload can never leave a truncated curve where a good one was. There is no `.bak` for curves: the
generated ones are rebuilt by `python3 utils/make_curves.py`, and an uploaded one is a file the
operator holds the original of.

Uploading does not change the running simulation any more than saving does. The curve is read when
the simulator restarts — including a curve uploaded over the name a device already points at.

**An upload over a generated name is temporary.** `make_curves.py` rewrites all four shipped CSVs
from the design basis; uploading your own `jtc_common_curve.csv` and later regenerating will
overwrite it. Upload under a name of your own to keep the two apart.

The dashboard polls `/api/state` once a second. It is not authenticated and binds `0.0.0.0`; keep
it on a trusted network or change the bind address before exposing it. Anyone who can reach the
dashboard can rewrite `device.json`, and can write a `.csv` file into `config/`.

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
| 2 | `Load.ModeSet` | **Input.** Source: 0 curve, 1 simulated, 2 manual |
| 4 | `Load.PowerSet` | **Input.** Manual demand (kW), read when the source is 2 |

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
        "ratedPower": 52,
        "mode":       0,
        "csv_file":   "pv_curve.csv",
        "slave_id":   2
    } ] }
  ]
}
```

`mode` is `0` for a CSV curve and `1` for synthetic generation; a `Load` also takes `2`, holding
`base_power` until the operator types a figure into its card. Whatever it is set to is where
`Load.ModeSet` starts, and the Live tab can move it from there without touching this file.
`DeviceKey` must be unique — it is the key into `devices_data`.

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
resolution, midnight wrap.

A curve uploaded from the dashboard lands here as a fifth kind of file: same rules, same directory,
written by `POST /api/curve` instead of by hand or by the generator. See Uploading a curve safely.

All four are **generated**, not hand-typed — `python3 utils/make_curves.py` rewrites them from the
design basis recorded at the top of that script. Edit the anchors there rather than the CSVs, so
the reasoning stays with the numbers:

| File | Shape | Range |
|---|---|---|
| `jtc_common_curve.csv` | the EGC's grid reference — overnight base, ramping from 06:00, crossing 1,700 kW from about 13:20 to 15:30, falling away through the evening | 183–1,837 kW |
| `load_curve.csv` | Tower 10 tenant load — overnight base, working-day ramp, mid-afternoon peak over the 1,750 kW loop limit | 1,006–1,800 kW |
| `t7_pv_curve.csv` | Tower 7 rooftop solar day, 07:00–19:00, 240 kW rated | 0–205 kW |
| `pv_curve.csv` | Tower 10 rooftop solar, same day shape, 52 kW rated | 0–45 kW |

Each one is shaped so a use-case is reachable, and the generator prints the proof when it runs:

```
use-case 1  common load over 1700 kW for 2.00 h, peak 1837.1 kW (excursion 137.1 kW)
use-case 2  common load minimum 183.0 kW, 33.0 kW of discharge headroom over the 150 kW floor
use-case 3  T98 runs 1005.6 - 1799.6 kW against the 1750 kW multi-loop limit
            the charge cap 1750 - T98 never reaches the 800 kW PCS: 744.4 kW of
            headroom at the overnight minimum, 24.25 h of the day capped in all,
            and negative for 2.50 h around the peak, where the loop can only be
            held by discharging
```

The excursion above the import limit is 137 kW against an 800 kW PCS, so the battery can hold the
limit with room to spare; the overnight minimum leaves 33 kW of discharge headroom over the floor,
so a night discharge runs into the floor instead of never approaching it.

`t7_pv_curve.csv` is kept even though no `T7PV` device is configured — it is the curve that device
reads, and removing it would make adding one back a two-file job instead of a one-object edit.

`load_curve.csv` carries the Tower 10 tenant day — see Site model above for the band it holds to,
and for what a load that straddles the multi-loop limit means for the EGC.

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
- ~~**Both CSV loaders discard the first data row.**~~ **Fixed by the data, not the code.**
  `PVModel.load_power_curve` and `LoadModel.load_power_curve` still call `next(reader, None)` to
  skip a header, and `pv_curve.csv` and `load_curve.csv` used to have none — so midnight was
  thrown away. `utils/make_curves.py` now writes a `hour,kW` header on all four files, so all 97
  points survive. The loaders are unchanged and still *require* that header:
  `test/test_t98_load.py` asserts both curves load 97 points including `0.0`, so a regenerated
  file that lost its header fails a test instead of silently dropping a point.
- **`config/deviceLogic.conf` is empty** and currently unread by any code.
- **The battery has no loss model.** `voltage_nominal` and `resistance` are read from
  `device.json` and never used — no I²R heating, no round-trip efficiency, no taper near the SOC
  bounds. A kWh in is a kWh out. See Formulas.
- **Energy counters integrate on two different clocks, and by two different rules.** `MeterModel`,
  `VirtualGridModel` and `T7PVModel` measure elapsed time; `PVModel`, `BatteryModel` and `EVModel`
  assume a fixed one second per tick, so they run slightly slow. The two directional counters also
  integrate by trapezoid now, while every other counter is still a rectangle. Both differences are
  small and neither is hard to close — `split_energy` and a measured Δt would drop straight into
  the other three models — but until they are, no two counters agree exactly. See Formulas.
- **`EVModel` computes its two current figures inconsistently** — the phase currents divide by
  `3·V·pf` while the current setpoint divides by `V·pf`, so the setpoint reads three times the
  per-phase current for the same power. No EV device is configured, so nothing reads it today.

---

## Layout

```
main.py                          entry point, threads, tick loop
test/
  test_history.py                the rolling power ring behind the trend chart
  test_isolation.py              the Tower 10 loop is unaffected by the virtual point
  test_t98_load.py               the Tower 10 curve band, load interpolation, T10 PV
  test_load_modes.py             the load's three sources and the registers that switch them
  test_energy.py                 directional energy: trapezoid, zero crossing, gap guard
  test_jtc_load.py               the JTC common load curve reader
  test_t7_pv.py                  the Tower 7 PV curve reader
  test_curve_upload.py           curve uploads: naming, parsing, replacement
  test_dashboard_api.py          the point catalogue behind the diagram panel
config/
  device.json                    site definition
  modbus_registers.py            point name → register offset
  logging_config.json            log levels and files
  pv_curve.csv                   24h Tower 10 rooftop PV, 52 kW
  load_curve.csv                 24h Tower 10 tenant load
  jtc_common_curve.csv           24h JTC common load — the EGC grid reference
  t7_pv_curve.csv                24h Tower 7 PV, 240 kW
src/
  history.py                     rolling power ring the trend chart reads
  communication/modbus_server.py Modbus TCP server
  communication/web_server.py    dashboard HTTP server and JSON API
  models/                        eight device models
  models/curve.py                shared reader for operator-supplied curves,
                                 and the parser the dashboard upload validates with
web/
  index.html                     the dashboard UI
utils/
  config_loader.py               JSON loader
  locks.py                       the shared data_lock
  make_curves.py                 regenerates all four CSVs from the design basis
deploy/
  build_release.sh               builds the source-free installer into dist/
  install.sh                     runs on the host: unit file, enable, start
  MicroGridSimulator.service     unit template; @PREFIX@ and @PYTHON@ filled in at install
introduction.html                illustrated overview of the system
JTC_Archi_Diagram.png            the site architecture this simulator stands in for
MicroGridSimulator.service       systemd unit for a checkout (see deploy/ for hosts)
```
