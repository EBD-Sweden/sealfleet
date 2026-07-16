"""Offline unit tests for the weather trip planner MCP's deterministic ranking."""

import pytest
from fastapi import HTTPException

from mcps.weather_trip.main import _score_day, rank_cities


def _day(date="2026-06-12", *, forecast=True, temp=27.0, sun=10.0, wind=10.0, rain=0):
    return {
        "date": date,
        "is_forecast": forecast,
        "temp_max_c": temp,
        "temp_min_c": temp - 8,
        "sunshine_hours": sun,
        "precipitation_mm": 0.0,
        "precipitation_probability": rain,
        "wind_max_kph": wind,
        "weather_code": 0,
    }


def test_score_day_perfect_conditions():
    result = _score_day(_day(), target_temp_c=27.0, max_wind_kph=20.0)
    assert result["score"] == 1.0
    assert result["perfect"] is True


def test_score_day_penalizes_cold_wind_and_rain():
    bad = _score_day(
        _day(temp=12.0, sun=1.0, wind=45.0, rain=80),
        target_temp_c=27.0,
        max_wind_kph=20.0,
    )
    assert bad["score"] < 0.2
    assert bad["perfect"] is False


def test_rank_cities_orders_by_score_and_summarizes():
    weather = {
        "cities": [
            {
                "name": "Windyville",
                "country": "SE",
                "days": [_day(temp=15.0, sun=3.0, wind=40.0, rain=60) for _ in range(3)],
            },
            {
                "name": "Sunnytown",
                "country": "ES",
                "days": [_day(temp=27.0, sun=11.0, wind=8.0, rain=0) for _ in range(3)],
            },
        ]
    }
    result = rank_cities(weather, target_temp_c=27.0, max_wind_kph=20.0)

    assert result["best_city"] == "Sunnytown"
    assert [r["city"] for r in result["ranking"]] == ["Sunnytown", "Windyville"]
    assert result["ranking"][0]["perfect_days"] == 3
    assert "Sunnytown" in result["summary"]
    # forecast-only: past days must not affect the ranking
    assert len(result["ranking"][0]["day_scores"]) == 3


def test_rank_cities_ignores_past_days():
    weather = {
        "cities": [
            {
                "name": "OnlyHistory",
                "country": "SE",
                "days": [_day(forecast=False) for _ in range(7)],
            },
            {
                "name": "HasForecast",
                "country": "PT",
                "days": [_day(forecast=False, temp=5.0, sun=0.0)] + [_day()],
            },
        ]
    }
    result = rank_cities(weather)
    # OnlyHistory has no forecast days at all → excluded from the ranking
    assert [r["city"] for r in result["ranking"]] == ["HasForecast"]
    assert result["ranking"][0]["score"] == 1.0


def test_rank_cities_rejects_missing_weather():
    with pytest.raises(HTTPException):
        rank_cities({})
