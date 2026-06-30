#!/usr/bin/env python3
"""Weather lookup script — fetches data from wttr.in (free, no API key).

Can be used standalone:
    python weather.py current Beijing      # current conditions
    python weather.py forecast Shanghai    # 3-day forecast

Or invoked as a script tool by the weather skill.
"""

import json
import sys

import httpx

WTTR_BASE = "https://wttr.in"
TIMEOUT_SECONDS = 10


def fetch_weather(city: str) -> dict:
    """Fetch raw weather data from wttr.in for *city* (j1 JSON format)."""
    url = f"{WTTR_BASE}/{city}"
    params = {"format": "j1"}
    response = httpx.get(url, params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def extract_current(data: dict, city: str = "") -> dict:
    """Extract current weather from a wttr.in j1 response."""
    cc = (data.get("current_condition") or [{}])[0]
    return {
        "city": city,
        "temperature_c": _as_int(cc.get("temp_C")),
        "condition": (cc.get("weatherDesc") or [{}])[0].get("value", ""),
        "humidity": _as_int(cc.get("humidity")),
        "wind_speed_kmph": _as_int(cc.get("windspeedKmph")),
        "wind_direction": cc.get("winddir16Point", ""),
        "feels_like_c": _as_int(cc.get("FeelsLikeC")),
    }


def extract_forecast(data: dict) -> list[dict]:
    """Extract 3-day forecast from a wttr.in j1 response."""
    days = []
    for entry in data.get("weather", [])[:3]:
        days.append({
            "date": entry.get("date", ""),
            "temp_min_c": _as_int(entry.get("mintempC")),
            "temp_max_c": _as_int(entry.get("maxtempC")),
            "condition": (
                (entry.get("hourly") or [{}])[0]
                .get("weatherDesc", [{}])[0]
                .get("value", "")
            ),
        })
    return days


# ── Helpers ────────────────────────────────────────────────────────


def _as_int(value) -> int:
    """Coerce a string-or-int value to int, defaulting to 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ── CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: weather.py [current|forecast] <city>",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = sys.argv[1]
    city = sys.argv[2]

    try:
        raw = fetch_weather(city)
    except Exception as exc:
        print(
            json.dumps(
                {"error": f"Failed to fetch weather for {city!r}: {exc}"},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    if cmd == "current":
        result = extract_current(raw, city=city)
    elif cmd == "forecast":
        result = extract_forecast(raw)
    else:
        print(f"Unknown command: {cmd}", file=stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))
