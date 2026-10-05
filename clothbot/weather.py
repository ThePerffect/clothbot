"""Погода через Open-Meteo (бесплатно, без ключа)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO = {
    0: ("☀️", "ясно"), 1: ("🌤", "преимущественно ясно"), 2: ("⛅️", "переменная облачность"),
    3: ("☁️", "пасмурно"), 45: ("🌫", "туман"), 48: ("🌫", "изморозь"),
    51: ("🌦", "морось"), 53: ("🌦", "морось"), 55: ("🌦", "сильная морось"),
    61: ("🌧", "небольшой дождь"), 63: ("🌧", "дождь"), 65: ("🌧", "ливень"),
    66: ("🌧", "ледяной дождь"), 67: ("🌧", "ледяной дождь"),
    71: ("🌨", "небольшой снег"), 73: ("🌨", "снег"), 75: ("❄️", "снегопад"), 77: ("🌨", "снежная крупа"),
    80: ("🌦", "ливни"), 81: ("🌧", "ливни"), 82: ("⛈", "сильные ливни"),
    85: ("🌨", "снегопад"), 86: ("❄️", "сильный снегопад"),
    95: ("⛈", "гроза"), 96: ("⛈", "гроза с градом"), 99: ("⛈", "гроза с градом"),
}


@dataclass
class Weather:
    temperature: float   # средняя «ощущается как» за день, °C
    t_min: float
    t_max: float
    precipitation: bool
    emoji: str
    text: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()

    def summary(self) -> str:
        return f"{self.emoji} {round(self.t_min):+d}…{round(self.t_max):+d}°, {self.text}"


@dataclass
class Place:
    name: str
    lat: float
    lon: float
    tz_offset: int  # минуты


class WeatherClient:
    def __init__(self, client: httpx.AsyncClient | None = None, ttl: int = 1800):
        self._client = client or httpx.AsyncClient(timeout=10)
        self._cache: dict[tuple[float, float], tuple[float, Weather]] = {}
        self._ttl = ttl

    async def geocode(self, query: str) -> Place | None:
        try:
            r = await self._client.get(GEO_URL, params={"name": query, "count": 1,
                                                        "language": "ru", "format": "json"})
            r.raise_for_status()
            res = (r.json().get("results") or [None])[0]
        except (httpx.HTTPError, ValueError):
            log.warning("geocode failed", exc_info=True)
            return None
        if not res:
            return None
        tz = await self._tz_offset(res["latitude"], res["longitude"])
        return Place(res["name"], res["latitude"], res["longitude"], tz)

    async def _tz_offset(self, lat: float, lon: float) -> int:
        try:
            r = await self._client.get(FORECAST_URL, params={
                "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
                "daily": "weather_code"})
            r.raise_for_status()
            return int(r.json().get("utc_offset_seconds", 10800)) // 60
        except (httpx.HTTPError, ValueError):
            return 180

    async def today(self, lat: float, lon: float) -> Weather | None:
        key = (round(lat, 2), round(lon, 2))
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < self._ttl:
            return cached[1]
        try:
            r = await self._client.get(FORECAST_URL, params={
                "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
                "daily": "apparent_temperature_max,apparent_temperature_min,"
                         "precipitation_probability_max,weather_code"})
            r.raise_for_status()
            d = r.json()["daily"]
        except (httpx.HTTPError, ValueError, KeyError):
            log.warning("weather failed", exc_info=True)
            return None
        t_max, t_min = d["apparent_temperature_max"][0], d["apparent_temperature_min"][0]
        code = d["weather_code"][0]
        prob = (d.get("precipitation_probability_max") or [0])[0] or 0
        emoji, text = WMO.get(code, ("🌡", ""))
        # Днём человек в основном на улице в тёплые часы — смещаем к максимуму.
        w = Weather(t_min + (t_max - t_min) * 0.6, t_min, t_max,
                    prob >= 50 or code >= 51, emoji, text)
        self._cache[key] = (time.time(), w)
        return w
