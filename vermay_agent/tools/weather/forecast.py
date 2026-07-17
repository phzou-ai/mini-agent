from __future__ import annotations

import json
import urllib.parse
import urllib.request


def weather_forecast(location: str, days: int = 3) -> dict:
    days = max(1, min(days, 3))
    encoded_location = urllib.parse.quote(location)
    url = f"https://wttr.in/{encoded_location}?format=j1"
    request = urllib.request.Request(url, headers={"User-Agent": "vermay-agent/0.1"})

    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))

    current = payload["current_condition"][0]
    forecast_days = []
    for day in payload["weather"][:days]:
        midday = day["hourly"][4]
        forecast_days.append(
            {
                "date": day["date"],
                "avg_temp_c": day["avgtempC"],
                "min_temp_c": day["mintempC"],
                "max_temp_c": day["maxtempC"],
                "wind_kmph": midday.get("windspeedKmph"),
                "sunrise": day["astronomy"][0]["sunrise"],
                "sunset": day["astronomy"][0]["sunset"],
                "summary": midday["weatherDesc"][0]["value"],
                "chance_of_rain": midday.get("chanceofrain"),
            }
        )

    return {
        "location": location,
        "source": "wttr.in",
        "current": {
            "temp_c": current["temp_C"],
            "feels_like_c": current["FeelsLikeC"],
            "humidity": current["humidity"],
            "wind_kmph": current["windspeedKmph"],
            "description": current["weatherDesc"][0]["value"],
        },
        "forecast": forecast_days,
    }
