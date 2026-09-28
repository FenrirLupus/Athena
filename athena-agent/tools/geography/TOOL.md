---
name: geography
description: "Geographic toolkit — geolocate an IP, timezone-aware time, and distance between points."
---

# Geography

The **geography** tools are a generalized geographic toolkit. They use
the host machine's IP (or a given IP) to determine geographic location,
report timezone-aware time, and compute distances. Network-aware but
keyless. HANDS-OFF — the code in `scripts/geography.py` handles the
calls.

## Tools

- `geolocate` — resolve an IP to a location (city, region, country,
  lat/lon, timezone, ISP)
- `timezone` — current time in a named timezone (or the geolocated one)
- `geo_distance` — distance between two lat/lon points (km + mi)

## Usage

```
geolocate {}
geolocate {"ip": "8.8.8.8"}
timezone {"timezone": "America/New_York"}
geo_distance {"lat1": 33.83, "lon1": -84.38, "lat2": 40.71, "lon2": -74.01}
```

## When to use

- The operator wants to know where the machine is geographically.
- Timezone-aware time is needed.
- A distance between two points is needed.

## References

- `references/` — (empty; the tools are self-contained)

## Scripts

- `scripts/geography.py` — registers `geolocate`, `timezone`,
  `geo_distance`.

---
---
