---
name: clock
description: "Current date/time — plus the clock family: timer and stopwatch."
---

# Clock

The **clock** family reports time and tracks it. HANDS-OFF — the code
in `scripts/` handles the calls. Do NOT use terminal to chase the time;
the tools ARE the implementation.

## Tools

- `clock` — current date/time (`iso`, `unix`, `date`, `time`)
- `timer` / `timer_check` / `timer_clear` — countdown timer
- `stopwatch` / `stopwatch_check` / `stopwatch_lap` /
  `stopwatch_stop` / `stopwatch_reset` — elapsed time

## Usage

```
clock {"format": "date"}
timer {"seconds": 300, "label": "pomodoro"}
timer_check {}
stopwatch {}            # start
stopwatch_lap {}        # record a lap
stopwatch_stop {}       # stop + report
```

## When to use

- The operator asks what time/date it is.
- A task needs a timestamp.
- A countdown or elapsed-time measurement is needed.

## References

- `references/` — (empty; the tools are self-contained)

## Scripts

- `scripts/clock.py` — the time tool
- `scripts/timer.py` — the timer family
- `scripts/stopwatch.py` — the stopwatch family

---
---

## The clock family

- `clock` — current date/time (iso, unix, date, time)
- `timer` / `timer_check` / `timer_clear` — countdown
- `stopwatch` / `stopwatch_check` / `stopwatch_lap` /
  `stopwatch_stop` / `stopwatch_reset` — elapsed time

```json
{"format": "time"}
{"seconds": 300, "label": "pomodoro"}
```

Use for time-of-day, timestamps, countdowns, and elapsed-time
measurement.

---
---
