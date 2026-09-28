"""Built-in geography family — geolocate + timezone + distance.

A GENERALIZED geographic toolkit (the Operator's 08-12 spec):
  geolocate   — resolve an IP (or the host machine's IP) to a location
                (city, region, country, lat/lon, timezone)
  timezone    — the current time in a named timezone (or the
                geolocated timezone)
  geo_distance — the distance between two lat/lon points

Network-aware but keyless: ip-api.com for geolocation, the system tz
database for timezone. All results degrade gracefully offline.
"""

import json
import math
import subprocess
from datetime import datetime


def _fetch(url: str, timeout: float = 12.0) -> dict | None:
    try:
        r = subprocess.run(["curl", "-s", "--max-time", str(int(timeout)), url],
                           capture_output=True, text=True, timeout=timeout + 5)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def _geo_impl(args: dict, timeout: float = 10.0) -> str:
    ip = str(args.get("ip", "")).strip() or ""
    url = "http://ip-api.com/json/" + (ip if ip else "")
    data = _fetch(url, timeout)
    if not data or data.get("status") != "success":
        return json.dumps({"ok": False,
                           "detail": "geolocation unavailable (offline?)"},
                          ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "ip": data.get("query", ip),
        "city": data.get("city", ""),
        "region": data.get("regionName", ""),
        "country": data.get("country", ""),
        "lat": data.get("lat"),
        "lon": data.get("lon"),
        "timezone": data.get("timezone", ""),
        "isp": data.get("isp", ""),
    }, ensure_ascii=False)


def _tz_impl(args: dict, timeout: float = 10.0) -> str:
    import zoneinfo
    tz_name = str(args.get("timezone", "")).strip()
    if not tz_name:
        # Fall back to geolocation's timezone.
        geo = _fetch("http://ip-api.com/json/", timeout)
        tz_name = (geo or {}).get("timezone", "") if geo else ""
    if not tz_name:
        return json.dumps({"ok": False, "detail": "timezone unavailable"},
                          ensure_ascii=False)
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
        now = datetime.now(tz)
        return json.dumps({
            "ok": True,
            "timezone": tz_name,
            "local_time": now.isoformat(timespec="seconds"),
            "utc_offset_h": now.utcoffset().total_seconds() / 3600 if now.utcoffset() else 0,
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False)


def _distance_impl(args: dict, timeout: float = 10.0) -> str:
    try:
        lat1, lon1 = float(args.get("lat1")), float(args.get("lon1"))
        lat2, lon2 = float(args.get("lat2")), float(args.get("lon2"))
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "detail": "lat1/lon1/lat2/lon2 required"},
                          ensure_ascii=False)
    # Haversine distance in km.
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    km = R * c
    return json.dumps({"ok": True, "distance_km": round(km, 2),
                       "distance_mi": round(km * 0.621371, 2)},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    tools = [
        ("geolocate", "Resolve an IP (or the host machine's IP) to a "
                      "geographic location: city, region, country, lat/lon, "
                      "timezone.", _geo_impl,
         {"ip": {"type": "string", "description": "Optional IP to locate"}},
         []),
        ("timezone", "Current time in a named timezone (or the geolocated "
                     "timezone).", _tz_impl,
         {"timezone": {"type": "string", "description": "IANA timezone name"}},
         []),
        ("geo_distance", "Distance between two lat/lon points.", _distance_impl,
         {"lat1": {"type": "number"}, "lon1": {"type": "number"},
          "lat2": {"type": "number"}, "lon2": {"type": "number"}},
         ["lat1", "lon1", "lat2", "lon2"]),
    ]
    for name, desc, fn, props, req in tools:
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object", "properties": props,
                        "required": req},
            fn=fn,
        ))
    return [t[0] for t in tools]
