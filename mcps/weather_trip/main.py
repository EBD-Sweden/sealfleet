"""Weather trip planner MCP — the public example of pipeline → visualization.

Fetches real daily weather (past week + next ~10 days) for a list of cities and
deterministically ranks the cities against simple trip preferences (sunshine,
target temperature, low wind).

Data sources (all free, no API key):
- Primary: Open-Meteo forecast API (past + future daily in one call).
- Fallback: met.no locationforecast for the future days (sunshine estimated
  from cloud cover and astronomical day length) plus the Open-Meteo archive
  host for the past week — so one provider being unreachable doesn't kill the
  example.

No credentials, no sealed inputs, no write access anywhere — read-only public
weather data. Scoring is pure arithmetic so results are reproducible and the
ranking logic is unit-testable offline.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
METNO_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
METNO_USER_AGENT = "mcpfinder-weather-example/1.0 github.com/EBD-Sweden/sealfleet"

PAST_DAYS = 7
FORECAST_DAYS = 10
MAX_CITIES = 8

DAILY_FIELDS = ",".join(
    [
        "temperature_2m_max",
        "temperature_2m_min",
        "sunshine_duration",
        "precipitation_sum",
        "precipitation_probability_max",
        "wind_speed_10m_max",
        "weather_code",
    ]
)

app = FastAPI(title="Sealfleet Weather Trip Planner MCP")


class ToolCall(BaseModel):
    tool: str
    inputs: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Data fetching (Open-Meteo primary, met.no + archive fallback)
# ---------------------------------------------------------------------------

def _geocode(client: httpx.Client, city: str) -> dict[str, Any] | None:
    resp = client.get(GEOCODING_URL, params={"name": city, "count": 1})
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    top = results[0]
    return {
        "name": top.get("name", city),
        "country": top.get("country", ""),
        "latitude": top["latitude"],
        "longitude": top["longitude"],
    }


def _shape_openmeteo_days(daily: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn Open-Meteo's column-oriented daily block into one record per day."""
    dates = daily.get("time") or []
    days: list[dict[str, Any]] = []
    for i, date in enumerate(dates):
        def col(field: str):
            values = daily.get(field) or []
            return values[i] if i < len(values) else None

        sunshine_s = col("sunshine_duration") or 0
        days.append(
            {
                "date": date,
                # past vs forecast split by index: Open-Meteo returns exactly
                # past_days rows before today's row
                "is_forecast": i >= PAST_DAYS,
                "temp_max_c": col("temperature_2m_max"),
                "temp_min_c": col("temperature_2m_min"),
                "sunshine_hours": round(sunshine_s / 3600.0, 1),
                "precipitation_mm": col("precipitation_sum"),
                "precipitation_probability": col("precipitation_probability_max"),
                "wind_max_kph": col("wind_speed_10m_max"),
            }
        )
    return days


def _fetch_openmeteo(client: httpx.Client, latitude: float, longitude: float) -> list[dict[str, Any]]:
    resp = client.get(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "daily": DAILY_FIELDS,
            "past_days": PAST_DAYS,
            "forecast_days": FORECAST_DAYS,
            "timezone": "auto",
            "wind_speed_unit": "kmh",
        },
    )
    resp.raise_for_status()
    return _shape_openmeteo_days(resp.json().get("daily", {}))


def _day_length_hours(latitude: float, on_date: date_cls) -> float:
    """Astronomical day length (sunrise to sunset) — standard declination formula."""
    day_of_year = on_date.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + day_of_year) / 365.0)
    lat = math.radians(latitude)
    cos_hour_angle = -math.tan(lat) * math.tan(decl)
    if cos_hour_angle <= -1.0:
        return 24.0  # midnight sun
    if cos_hour_angle >= 1.0:
        return 0.0   # polar night
    return 2 * math.degrees(math.acos(cos_hour_angle)) / 15.0


def _fetch_metno_forecast(client: httpx.Client, latitude: float, longitude: float) -> list[dict[str, Any]]:
    """Aggregate met.no hourly timeseries into daily records (forecast only).

    met.no has no sunshine duration; estimate it as day length × (1 − cloud
    cover). Days are marked with their source so the UI can say "estimated".
    """
    resp = client.get(
        METNO_URL,
        params={"lat": round(latitude, 4), "lon": round(longitude, 4)},
        headers={"User-Agent": METNO_USER_AGENT},
    )
    resp.raise_for_status()
    series = resp.json().get("properties", {}).get("timeseries", [])

    per_day: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for entry in series:
        day = entry.get("time", "")[:10]
        details = entry.get("data", {}).get("instant", {}).get("details", {})
        if "air_temperature" in details:
            per_day[day]["temps"].append(details["air_temperature"])
        if "wind_speed" in details:
            per_day[day]["winds"].append(details["wind_speed"] * 3.6)  # m/s → km/h
        if "cloud_area_fraction" in details:
            per_day[day]["clouds"].append(details["cloud_area_fraction"])
        next_hour = entry.get("data", {}).get("next_1_hours", {}).get("details", {})
        if "precipitation_amount" in next_hour:
            per_day[day]["precip"].append(next_hour["precipitation_amount"])

    today = datetime.now(timezone.utc).date()
    days: list[dict[str, Any]] = []
    for day in sorted(per_day):
        bucket = per_day[day]
        if not bucket["temps"]:
            continue
        d = date_cls.fromisoformat(day)
        if d < today or len(days) >= FORECAST_DAYS:
            continue
        cloud_avg = sum(bucket["clouds"]) / len(bucket["clouds"]) if bucket["clouds"] else 50.0
        sun_est = _day_length_hours(latitude, d) * (1.0 - cloud_avg / 100.0)
        days.append(
            {
                "date": day,
                "is_forecast": True,
                "temp_max_c": round(max(bucket["temps"]), 1),
                "temp_min_c": round(min(bucket["temps"]), 1),
                "sunshine_hours": round(sun_est, 1),
                "precipitation_mm": round(sum(bucket["precip"]), 1) if bucket["precip"] else 0.0,
                "precipitation_probability": None,  # met.no compact has none
                "wind_max_kph": round(max(bucket["winds"]), 1) if bucket["winds"] else None,
            }
        )
    return days


def _fetch_archive_past(client: httpx.Client, latitude: float, longitude: float) -> list[dict[str, Any]]:
    """Past week from the Open-Meteo archive host (recent days may lag a little)."""
    today = datetime.now(timezone.utc).date()
    resp = client.get(
        ARCHIVE_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "start_date": (today - timedelta(days=PAST_DAYS)).isoformat(),
            "end_date": (today - timedelta(days=1)).isoformat(),
            "daily": "temperature_2m_max,temperature_2m_min,sunshine_duration,precipitation_sum,wind_speed_10m_max",
            "timezone": "auto",
            "wind_speed_unit": "kmh",
        },
    )
    resp.raise_for_status()
    daily = resp.json().get("daily", {})
    days = []
    for i, date in enumerate(daily.get("time") or []):
        def col(field: str):
            values = daily.get(field) or []
            return values[i] if i < len(values) else None

        if col("temperature_2m_max") is None:
            continue  # archive lags a few days; skip empty rows
        days.append(
            {
                "date": date,
                "is_forecast": False,
                "temp_max_c": col("temperature_2m_max"),
                "temp_min_c": col("temperature_2m_min"),
                "sunshine_hours": round((col("sunshine_duration") or 0) / 3600.0, 1),
                "precipitation_mm": col("precipitation_sum"),
                "precipitation_probability": None,
                "wind_max_kph": col("wind_speed_10m_max"),
            }
        )
    return days


def _fetch_city_days(
    client: httpx.Client, place: dict[str, Any], *, try_primary: bool = True
) -> tuple[list[dict[str, Any]], str, bool]:
    """Returns (days, source, primary_ok) — primary_ok feeds the circuit breaker."""
    lat, lon = place["latitude"], place["longitude"]
    if try_primary:
        try:
            return _fetch_openmeteo(client, lat, lon), "open-meteo.com", True
        except httpx.HTTPError:
            pass  # fall through to met.no + archive

    forecast = _fetch_metno_forecast(client, lat, lon)
    try:
        past = _fetch_archive_past(client, lat, lon)
    except httpx.HTTPError:
        past = []
    return past + forecast, "met.no (sun estimated from cloud cover)", False


def fetch_cities_weather(cities: list[str]) -> dict[str, Any]:
    if not cities or not isinstance(cities, list):
        raise HTTPException(status_code=400, detail="'cities' must be a non-empty list")
    if len(cities) > MAX_CITIES:
        raise HTTPException(status_code=400, detail=f"at most {MAX_CITIES} cities per request")

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    # Short connect timeout + circuit breaker: when the primary provider is
    # unreachable, only the first city pays the connect timeout — the rest go
    # straight to the fallback instead of stacking timeouts per city.
    primary_alive = True
    timeout = httpx.Timeout(15.0, connect=4.0)
    with httpx.Client(timeout=timeout) as client:
        for raw in cities:
            city = str(raw).strip()
            if not city:
                continue
            try:
                place = _geocode(client, city)
                if not place:
                    errors.append({"city": city, "error": "city not found"})
                    continue
                days, source, primary_alive = _fetch_city_days(
                    client, place, try_primary=primary_alive
                )
                results.append({**place, "query": city, "source": source, "days": days})
            except httpx.HTTPError as exc:
                errors.append({"city": city, "error": f"weather fetch failed: {exc}"})

    if not results:
        raise HTTPException(
            status_code=502,
            detail={"error": "no city data could be fetched", "cities": errors},
        )
    return {"past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS, "cities": results, "errors": errors}


# ---------------------------------------------------------------------------
# Deterministic ranking (pure arithmetic — unit-testable offline)
# ---------------------------------------------------------------------------

def _score_day(day: dict[str, Any], target_temp_c: float, max_wind_kph: float) -> dict[str, Any]:
    temp = day.get("temp_max_c")
    sun = day.get("sunshine_hours") or 0.0
    wind = day.get("wind_max_kph") or 0.0
    rain_prob = day.get("precipitation_probability")

    # Temperature: 1.0 at the target, fading to 0 when 10°C away.
    temp_score = 0.0 if temp is None else max(0.0, 1.0 - abs(temp - target_temp_c) / 10.0)
    # Sunshine: 8h+ of sun counts as a full score.
    sun_score = min(1.0, sun / 8.0)
    # Wind: full score at/below the limit, fading to 0 at double the limit.
    if wind <= max_wind_kph:
        wind_score = 1.0
    else:
        wind_score = max(0.0, 1.0 - (wind - max_wind_kph) / max_wind_kph)
    # Rain discounts the day: probability when the provider gives one,
    # otherwise expected millimetres (10mm+ ≈ write-off).
    if rain_prob is not None:
        rain_factor = 1.0 - rain_prob / 100.0
    else:
        rain_factor = max(0.0, 1.0 - (day.get("precipitation_mm") or 0.0) / 10.0)

    score = (0.4 * sun_score + 0.4 * temp_score + 0.2 * wind_score) * rain_factor
    is_perfect = (
        temp is not None
        and abs(temp - target_temp_c) <= 3.0
        and sun >= 8.0
        and wind <= max_wind_kph
        and (rain_prob or 0) <= 20
        and (day.get("precipitation_mm") or 0.0) <= 1.0
    )
    return {"score": round(score, 3), "perfect": is_perfect}


def rank_cities(
    weather: dict[str, Any],
    target_temp_c: float = 27.0,
    max_wind_kph: float = 20.0,
) -> dict[str, Any]:
    cities = (weather or {}).get("cities")
    if not cities:
        raise HTTPException(status_code=400, detail="'weather' must contain a 'cities' list")

    ranking: list[dict[str, Any]] = []
    for city in cities:
        forecast_days = [d for d in city.get("days", []) if d.get("is_forecast")]
        if not forecast_days:
            continue
        day_scores = [_score_day(d, target_temp_c, max_wind_kph) for d in forecast_days]
        avg = sum(s["score"] for s in day_scores) / len(day_scores)
        perfect = sum(1 for s in day_scores if s["perfect"])
        temps = [d["temp_max_c"] for d in forecast_days if d.get("temp_max_c") is not None]
        suns = [d.get("sunshine_hours") or 0 for d in forecast_days]
        winds = [d.get("wind_max_kph") or 0 for d in forecast_days]
        ranking.append(
            {
                "city": city.get("name"),
                "country": city.get("country"),
                "score": round(avg, 3),
                "perfect_days": perfect,
                "avg_temp_max_c": round(sum(temps) / len(temps), 1) if temps else None,
                "avg_sunshine_hours": round(sum(suns) / len(suns), 1),
                "max_wind_kph": round(max(winds), 1) if winds else None,
                "day_scores": [s["score"] for s in day_scores],
            }
        )

    ranking.sort(key=lambda r: (r["score"], r["perfect_days"]), reverse=True)
    best = ranking[0] if ranking else None
    summary = None
    if best:
        summary = (
            f"{best['city']} is the best pick for the next {FORECAST_DAYS} days: "
            f"{best['perfect_days']} near-perfect day(s), avg {best['avg_sunshine_hours']}h sun, "
            f"avg max {best['avg_temp_max_c']}°C (target {target_temp_c}°C)."
        )
    return {
        "preferences": {"target_temp_c": target_temp_c, "max_wind_kph": max_wind_kph},
        "ranking": ranking,
        "best_city": best["city"] if best else None,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# MCP HTTP surface (same contract as mcps/demo_sandbox)
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "weather-trip-mcp",
        "data_source": "open-meteo.com (met.no fallback)",
    }


@app.get("/tools")
def tools() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "fetch_cities_weather",
                "inputs": {"cities": "list[str] — up to 8 city names"},
            },
            {
                "name": "rank_cities",
                "inputs": {
                    "weather": "output of fetch_cities_weather",
                    "target_temp_c": "float, default 27",
                    "max_wind_kph": "float, default 20",
                },
            },
        ]
    }


@app.post("/call")
def call_tool(call: ToolCall) -> dict[str, Any]:
    if call.tool == "fetch_cities_weather":
        return fetch_cities_weather(**call.inputs)
    if call.tool == "rank_cities":
        return rank_cities(**call.inputs)
    raise HTTPException(status_code=404, detail="unknown weather trip tool")
