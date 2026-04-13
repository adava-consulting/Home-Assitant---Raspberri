from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from zoneinfo import ZoneInfo


BRIEFING_PATTERNS = (
    re.compile(r"^\s*good\s+(?:morning|afternoon|evening)\s*[.!?]*\s*$", re.IGNORECASE),
    re.compile(r"\b(?:weather|forecast|temperature|rain|raining|clima|temperatura|lluvia)\b", re.IGNORECASE),
)

RAINY_CONDITIONS = {"rainy", "pouring", "lightning-rainy", "snowy-rainy"}

CONDITION_LABELS = {
    "clear-night": "clear",
    "cloudy": "cloudy",
    "fog": "foggy",
    "hail": "hailing",
    "lightning": "stormy",
    "lightning-rainy": "stormy and rainy",
    "partlycloudy": "partly cloudy",
    "pouring": "pouring rain",
    "rainy": "rainy",
    "snowy": "snowy",
    "snowy-rainy": "snowy and rainy",
    "sunny": "sunny",
    "windy": "windy",
    "windy-variant": "windy and cloudy",
}


class WeatherBriefingService:
    def __init__(self, settings: Any, home_assistant: Any) -> None:
        self._settings = settings
        self._home_assistant = home_assistant
        self._timezone = ZoneInfo(settings.local_timezone)
        self._weather_entity_id = "weather.forecast_home"

    def should_handle(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in BRIEFING_PATTERNS)

    async def build_briefing(self) -> dict[str, Any]:
        now = datetime.now(self._timezone)
        current = await self._home_assistant.get_state(self._weather_entity_id)
        daily = await self._home_assistant.get_weather_forecast(self._weather_entity_id, "daily")
        hourly = await self._home_assistant.get_weather_forecast(self._weather_entity_id, "hourly")

        today = self._today_forecast(daily, now) or (daily[0] if daily else {})
        current_attributes = current.get("attributes", {})
        current_condition = str(current.get("state") or "").strip().lower()
        condition_text = self._condition_text(current_condition, now)
        temperature = self._number(current_attributes.get("temperature"))
        high = self._number(today.get("temperature"))
        low = self._number(today.get("templow"))
        precipitation = self._number(today.get("precipitation"))
        humidity = self._number(current_attributes.get("humidity"))
        wind_speed = self._number(current_attributes.get("wind_speed"))
        rain_message = self._rain_message(current_condition, hourly, now)

        response_parts = [
            f"{self._greeting(now)}.",
            self._current_weather_sentence(condition_text, temperature),
        ]

        if high is not None and low is not None:
            response_parts.append(
                f"Today's high is about {self._format_number(high)} degrees, with a low near {self._format_number(low)}."
            )
        elif high is not None:
            response_parts.append(f"Today's high is about {self._format_number(high)} degrees.")

        if precipitation is not None:
            if precipitation > 0:
                response_parts.append(
                    f"The forecast shows around {self._format_number(precipitation)} millimeters of rain today."
                )
            else:
                response_parts.append("I don't see meaningful rain in today's forecast.")

        if rain_message:
            response_parts.append(rain_message)

        if wind_speed is not None and wind_speed >= 25:
            response_parts.append(f"It's also breezy, with wind around {self._format_number(wind_speed)} kilometers per hour.")

        if humidity is not None and humidity >= 85:
            response_parts.append(f"Humidity is high, around {self._format_number(humidity)} percent.")

        assistant_response = " ".join(response_parts)
        spoken_response = self._compact_spoken_response(
            greeting=self._greeting(now),
            condition_text=condition_text,
            temperature=temperature,
            high=high,
            low=low,
            rain_message=rain_message,
        )

        return {
            "assistant_response": assistant_response,
            "spoken_response": spoken_response,
            "weather_entity_id": self._weather_entity_id,
            "current_condition": current_condition,
            "current_temperature": temperature,
            "daily_forecast": today,
            "hourly_forecast_points": len(hourly),
        }

    def _greeting(self, now: datetime) -> str:
        hour = now.hour
        if 5 <= hour < 12:
            return "Good morning"
        if 12 <= hour < 17:
            return "Good afternoon"
        if 17 <= hour < 21:
            return "Good evening"
        return "Good night"

    def _current_weather_sentence(self, condition_text: str, temperature: float | None) -> str:
        if temperature is None:
            return f"Right now, the sky is {condition_text}."
        return f"Right now, it's {condition_text} and about {self._format_number(temperature)} degrees outside."

    def _condition_text(self, condition: str, now: datetime) -> str:
        label = CONDITION_LABELS.get(condition, condition.replace("_", " ").replace("-", " ") or "unknown")
        if now.hour >= 21 or now.hour < 5:
            if condition in {"clear-night", "sunny"}:
                return "clear, so it may be a starry night"
        return label

    def _today_forecast(self, daily: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
        for forecast in daily:
            forecast_dt = self._parse_datetime(forecast.get("datetime"))
            if forecast_dt and forecast_dt.astimezone(self._timezone).date() == now.date():
                return forecast
        return None

    def _rain_message(
        self,
        current_condition: str,
        hourly: list[dict[str, Any]],
        now: datetime,
    ) -> str | None:
        upcoming = [
            forecast
            for forecast in hourly
            if (forecast_dt := self._parse_datetime(forecast.get("datetime")))
            and forecast_dt.astimezone(self._timezone) >= now
        ]
        if not upcoming:
            return None

        currently_wet = current_condition in RAINY_CONDITIONS or self._is_rainy_hour(upcoming[0])
        if currently_wet:
            dry_time = self._first_sustained_dry_time(upcoming)
            if dry_time is not None:
                return f"Rain looks like it may ease {self._format_relative_time(dry_time, now)}."
            return "Rain may continue for the next several hours."

        next_rain = self._next_rain_time(upcoming)
        if next_rain is not None:
            return f"Rain may return {self._format_relative_time(next_rain, now)}."
        return None

    def _first_sustained_dry_time(self, hourly: list[dict[str, Any]]) -> datetime | None:
        for index, forecast in enumerate(hourly):
            forecast_dt = self._parse_datetime(forecast.get("datetime"))
            if forecast_dt is None or self._is_rainy_hour(forecast):
                continue

            next_forecast = hourly[index + 1] if index + 1 < len(hourly) else None
            if next_forecast is None or not self._is_rainy_hour(next_forecast):
                return forecast_dt
        return None

    def _next_rain_time(self, hourly: list[dict[str, Any]]) -> datetime | None:
        for forecast in hourly:
            if not self._is_rainy_hour(forecast):
                continue
            return self._parse_datetime(forecast.get("datetime"))
        return None

    def _is_rainy_hour(self, forecast: dict[str, Any]) -> bool:
        condition = str(forecast.get("condition") or "").strip().lower()
        precipitation = self._number(forecast.get("precipitation")) or 0.0
        return condition in RAINY_CONDITIONS or precipitation > 0.05

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _number(self, value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _format_number(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.1f}"

    def _format_relative_time(self, value: datetime, now: datetime) -> str:
        local_value = value.astimezone(self._timezone)
        if local_value.date() == now.date():
            return f"around {local_value.strftime('%H:%M')}"
        return f"tomorrow around {local_value.strftime('%H:%M')}"

    def _compact_spoken_response(
        self,
        *,
        greeting: str,
        condition_text: str,
        temperature: float | None,
        high: float | None,
        low: float | None,
        rain_message: str | None,
    ) -> str:
        parts = [f"{greeting}."]
        if temperature is not None:
            parts.append(f"{condition_text.capitalize()}, {self._format_number(temperature)} degrees.")
        else:
            parts.append(f"{condition_text.capitalize()}.")

        if high is not None and low is not None:
            parts.append(f"High {self._format_number(high)}, low {self._format_number(low)}.")
        elif high is not None:
            parts.append(f"High {self._format_number(high)}.")

        if rain_message:
            parts.append(self._compact_rain_message(rain_message))

        return " ".join(parts)

    def _compact_rain_message(self, rain_message: str) -> str:
        compact = rain_message.replace("Rain looks like it may ease ", "Rain may ease ")
        compact = compact.replace("around ", "at ")
        return compact
