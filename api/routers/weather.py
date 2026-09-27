"""
Weather endpoint — Open-Meteo free API (no key needed).
Monterrey, Nuevo León coords: 25.6866, -100.3161
"""
from fastapi import APIRouter
import urllib.request
import json
import time

router = APIRouter()

# ECCSA Monterrey coordinates
LAT = 25.6866
LON = -100.3161

# Cache 15 min (weather doesn't change fast)
_cache = {}
_cache_ttl = 900


@router.get("")
def get_weather():
    now = time.time()
    if "weather" in _cache and now - _cache["ts"] < _cache_ttl:
        return _cache["weather"]

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={LAT}&longitude={LON}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,is_day"
            f"&daily=temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
            f"&timezone=America/Mexico_City&forecast_days=3"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "ECCSA-Dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read())

        current = raw.get("current", {})
        daily = raw.get("daily", {})

        wmo = current.get("weather_code", 0)
        is_day = current.get("is_day", 1)

        result = {
            "actual": {
                "temperatura": current.get("temperature_2m"),
                "humedad": current.get("relative_humidity_2m"),
                "viento": current.get("wind_speed_10m"),
                "codigo_clima": wmo,
                "descripcion": _wmo_desc(wmo),
                "icono": _wmo_icon(wmo, is_day),
                "es_dia": bool(is_day),
            },
            "hoy": {
                "max": daily.get("temperature_2m_max", [None])[0],
                "min": daily.get("temperature_2m_min", [None])[0],
                "prob_lluvia": daily.get("precipitation_probability_max", [None])[0],
            },
            "manana": {
                "max": daily.get("temperature_2m_max", [None])[1] if len(daily.get("temperature_2m_max", [])) > 1 else None,
                "min": daily.get("temperature_2m_min", [None])[1] if len(daily.get("temperature_2m_min", [])) > 1 else None,
                "prob_lluvia": daily.get("precipitation_probability_max", [None])[1] if len(daily.get("precipitation_probability_max", [])) > 1 else None,
            },
        }
        _cache["weather"] = result
        _cache["ts"] = now
        return result
    except Exception as e:
        return {"actual": {"temperatura": None, "descripcion": "No disponible", "icono": "❓", "es_dia": True}, "hoy": {}, "manana": {}, "error": str(e)}


def _wmo_desc(code: int) -> str:
    """WMO weather code to Spanish description."""
    mapping = {
        0: "Despejado", 1: "Mayormente despejado", 2: "Parcial nublado", 3: "Nublado",
        45: "Niebla", 48: "Niebla con escarcha",
        51: "Lluvia ligera", 53: "Lluvia moderada", 55: "Lluvia intensa",
        56: "Lluvia helada", 57: "Lluvia helada fuerte",
        61: "Llovizna", 63: "Lluvia moderada", 65: "Lluvia fuerte",
        66: "Lluvia helada", 67: "Lluvia helada fuerte",
        71: "Nevada ligera", 73: "Nevada moderada", 75: "Nevada fuerte",
        77: "Granizo", 80: "Chubascos ligeros", 81: "Chubascos moderados", 82: "Chubascos fuertes",
        85: "Chubascos de nieve", 86: "Chubascos de nieve fuertes",
        95: "Tormenta", 96: "Tormenta con granizo", 99: "Tormenta fuerte con granizo",
    }
    return mapping.get(code, f"Código {code}")


def _wmo_icon(code: int, is_day: int) -> str:
    """WMO weather code to emoji icon."""
    if code == 0:
        return "☀️" if is_day else "🌙"
    if code <= 2:
        return "⛅" if is_day else "☁️"
    if code == 3:
        return "☁️"
    if code in (45, 48):
        return "🌫️"
    if code in (51, 53, 55, 56, 57):
        return "🌦️"
    if code in (61, 63, 65, 66, 67):
        return "🌧️"
    if code in (71, 73, 75, 77):
        return "❄️"
    if code in (80, 81, 82, 85, 86):
        return "⛈️"
    if code in (95, 96, 99):
        return "⛈️"
    return "🌤️"
