"""Weather skill — provides weather query functions.

Each function here corresponds to a `scripts` entry in SKILL.md frontmatter.
The harness wraps them as LangChain Tools at resolve time.
"""

import json
from urllib import request
from urllib.error import URLError


def get_current_weather(city: str) -> str:
    """Query current weather for a city (temperature, humidity, wind, condition).

    Args:
        city: City name — Chinese (北京), English (Beijing), or pinyin.
    """
    try:
        data = _fetch_weather(city)
        current = data["current_condition"][0]
        return (
            f"City: {city}\n"
            f"Temperature: {current['temp_C']} C / "
            f"Feels like: {current['FeelsLikeC']} C\n"
            f"Humidity: {current['humidity']}%\n"
            f"Condition: {current['weatherDesc'][0]['value']}\n"
            f"Wind: {current['winddir16Point']} {current['windspeedKmph']} km/h"
        )
    except Exception as e:
        return f"Weather query failed: {e}"


def get_forecast(city: str) -> str:
    """Query 3-day weather forecast for a city.

    Args:
        city: City name — Chinese (北京), English (Beijing), or pinyin.
    """
    try:
        data = _fetch_weather(city)
        days = data["weather"]
        lines = [f"Forecast for {city}:"]
        for day in days:
            lines.append(
                f"  {day['date']}: "
                f"{day['mintempC']}~{day['maxtempC']} C, "
                f"{day['hourly'][4]['weatherDesc'][0]['value']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Forecast query failed: {e}"


def _fetch_weather(city: str) -> dict:
    """Internal helper: fetch raw weather data from wttr.in."""
    url = f"https://wttr.in/{city}?format=j1"
    with request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())
