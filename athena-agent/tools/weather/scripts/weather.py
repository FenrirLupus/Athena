"""Built-in weather family — current weather + forecast.

A GENERALIZED weather toolkit (the Operator's 08-12 spec). Uses the free
keyless Open-Meteo API. The weather can be requested for a named city
or derived from the host machine's IP geolocation.

Network-aware but keyless. Degrades gracefully offline.
"""

import json
import subprocess

# Open-Meteo geocoding: city name → lat/lon (keyless).
_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
# Open-Meteo forecast (keyless). weathercode → human text below.
_FORECAST = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
             "&current_weather=true&hourly=temperature_2m&forecast_days=1")

_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog", 51: "light drizzle",
    53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    80: "slight showers", 81: "moderate showers", 82: "violent showers",
    95: "thunderstorm", 96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def _fetch(url: str, timeout: float = 12.0) -> dict | None:
    try:
        r = subprocess.run(["curl", "-s", "--max-time", str(int(timeout)), url],
                           capture_output=True, text=True, timeout=timeout + 5)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def _coords(city: str, timeout: float = 10.0) -> tuple[float, float] | None:
    if city:
        data = _fetch(_GEOCODE.format(city=city.replace(" ", "%20")), timeout)
        results = (data or {}).get("results") or []
        if results:
            r = results[0]
            return float(r.get("latitude")), float(r.get("longitude"))
        return None
    # No city: derive from the host IP geolocation.
    geo = _fetch("http://ip-api.com/json/", timeout)
    if geo and geo.get("status") == "success":
        return float(geo.get("lat")), float(geo.get("lon"))
    return None


def _weather(args: dict, timeout: float = 10.0) -> str:
    city = str(args.get("city", "")).strip()
    coords = _coords(city, timeout)
    if not coords:
        return json.dumps({"ok": False,
                           "detail": "weather unavailable (offline or city not found)"},
                          ensure_ascii=False)
    lat, lon = coords
    data = _fetch(_FORECAST.format(lat=lat, lon=lon), timeout)
    if not data:
        return json.dumps({"ok": False, "detail": "weather unavailable (offline)"},
                          ensure_ascii=False)
    cw = data.get("current_weather", {})
    code = int(cw.get("weathercode", 0))
    return json.dumps({
        "ok": True,
        "city": city or "your location",
        "latitude": lat, "longitude": lon,
        "temperature_c": cw.get("temperature"),
        "windspeed_kmh": cw.get("windspeed"),
        "wind_direction": cw.get("winddirection"),
        "condition": _WMO.get(code, f"code {code}"),
        "is_day": bool(cw.get("is_day")),
    }, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="weather",
        description="Current weather for a city (or your IP-derived "
                    "location). Keyless Open-Meteo.",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string",
                         "description": "Optional city name"},
            },
            "required": [],
        },
        fn=_weather,
    ))
    return ["weather"]
