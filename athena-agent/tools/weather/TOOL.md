---
name: weather
description: "Current weather for a city or your IP-derived location."
---

# Weather

The **weather** tool reports current weather — for a named city or the
host machine's IP-derived location. Uses the free keyless Open-Meteo
API. HANDS-OFF — the code in `scripts/weather.py` handles the calls.

## Usage

```
weather {"city": "Atlanta"}
weather {}
```

With no city, the location comes from the host IP geolocation.

## What it returns

- temperature (°C)
- windspeed (km/h) + direction
- condition (clear, rain, snow, thunderstorm, ...)
- whether it is day

## When to use

- The operator asks about the weather.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/weather.py` — registers `weather`.

---
---
