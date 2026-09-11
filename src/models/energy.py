# -*- coding: utf-8 -*-
"""Turning a sampled power signal into directional energy counters.

Two devices integrate a *signed* power into separate import and export totals:
`MeterModel` at the Tower 10 point of common coupling, and `VirtualGridModel` at
the calculated point above the boundary. They did it the same naive way, and
this module is that arithmetic done once, properly, for both.

What was wrong with adding `P_now · Δt`:

* **The rectangle is charged at the wrong power.** A tick is about a second, and
  the whole second was billed at the power read at its *end*. On a load that
  ramps ~800 kW across a day every interval is biased the same direction, and
  the bias accumulates over 86,400 of them. The trapezoid — the mean of the two
  readings that bound the interval — is the standard reading of a sampled
  signal, and it is exact for any power that moves linearly between samples.

* **A sign change inside an interval landed entirely in one bucket.** Command
  the battery from -200 kW to +300 kW and that second was booked wholly as
  import, when the site actually exported for the first 40% of it. The crossing
  is solved for and the interval split at it, so each side is counted where it
  belongs and neither counter absorbs the other's energy.

* **An absurd Δt went straight in.** A stalled cycle or a suspended host would
  hand the counter hours of elapsed time and one tick would add energy that
  never flowed. Anything longer than `MAX_STEP_HOURS` is treated as a gap in
  the record rather than as a very long interval: the counters do not move.

The counters stay monotonic and are still only reset by a restart.
"""
import logging

logger = logging.getLogger('state')

# Longest interval that can still be one integration step. The tick is ~1 s, so
# a minute is far beyond any normal cycle and far short of the hours a suspended
# host would report.
MAX_STEP_HOURS = 60.0 / 3600.0


def split_energy(p_prev, p_now, dt_hours):
    """Energy through an interval, as (imported_kWh, exported_kWh).

    `p_prev` and `p_now` bound the interval in kW, positive for import. Power is
    taken to move linearly between them, which is what the trapezoid integrates
    and what makes the zero crossing solvable.
    """
    if dt_hours <= 0.0:
        return 0.0, 0.0
    if dt_hours > MAX_STEP_HOURS:
        logger.warning("Energy integration skipped a %.1f s gap -- longer than "
                       "the %.0f s step limit, so no energy is booked for it",
                       dt_hours * 3600.0, MAX_STEP_HOURS * 3600.0)
        return 0.0, 0.0

    # Same side of zero (or touching it): one trapezoid, one bucket.
    if p_prev * p_now >= 0.0:
        area = (p_prev + p_now) / 2.0 * dt_hours
        return (area, 0.0) if area > 0.0 else (0.0, -area)

    # Crossed zero: two triangles, meeting where the line reaches it.
    t_cross = dt_hours * abs(p_prev) / (abs(p_prev) + abs(p_now))
    first = abs(p_prev) / 2.0 * t_cross
    second = abs(p_now) / 2.0 * (dt_hours - t_cross)
    return (first, second) if p_prev > 0.0 else (second, first)
