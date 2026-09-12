import requests


def get_weather(latitude, longitude):

    url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": [
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "wind_speed_10m",
            "weather_code"
        ],
        "hourly": [
            "precipitation_probability",
            "precipitation",
            "weather_code"
        ],
        "forecast_days": 1,
        "timezone": "auto"
    }

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    response.raise_for_status()

    return response.json()


def weather_risk(weather):

    current = weather["current"]

    temperature = current["temperature_2m"]
    precipitation = current["precipitation"]
    wind_speed = current["wind_speed_10m"]

    hourly = weather["hourly"]

    rain_probability = max(
        hourly["precipitation_probability"]
    )

    risks = []

    if rain_probability >= 70:
        risks.append(
            "Heavy rainfall may affect outdoor construction."
        )

    elif rain_probability >= 40:
        risks.append(
            "Rain is possible and outdoor work should be monitored."
        )

    if precipitation > 5:
        risks.append(
            "Current precipitation may create wet and slippery surfaces."
        )

    if wind_speed >= 40:
        risks.append(
            "Strong winds may affect lifting and elevated work."
        )

    if temperature >= 35:
        risks.append(
            "High temperature may increase heat stress risk."
        )

    if not risks:
        risks.append(
            "No major weather-related construction risk detected."
        )

    return {
        "temperature": temperature,
        "precipitation": precipitation,
        "wind_speed": wind_speed,
        "rain_probability": rain_probability,
        "risks": risks
    }