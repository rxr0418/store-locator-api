"""
Geocoding service with in-memory caching (TTL: 30 days).
Falls back gracefully if Nominatim is unavailable.
"""
import re
import time
import hashlib
import logging
import requests
from math import cos, radians
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Simple in-memory cache: 
_geocode_cache: dict = {}


def _cache_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()


def _cache_get(key: str, ttl: int) -> Optional[Tuple[float, float]]:
    entry = _geocode_cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["lat"], entry["lon"]
    return None


def _cache_set(key: str, lat: float, lon: float):
    _geocode_cache[key] = {"lat": lat, "lon": lon, "ts": time.time()}


def geocode_address(address: str, ttl: int = 60 * 60 * 24 * 30) -> Optional[Tuple[float, float]]:
    """
    Convert address string → (lat, lon) using Nominatim.
    Returns None if geocoding fails.
    """
    key = _cache_key(address)
    cached = _cache_get(key, ttl)
    if cached:
        logger.info(f"Geocode cache hit for: {address}")
        return cached

    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": address, "format": "json", "limit": 1, "countrycodes": "us"}
        headers = {"User-Agent": "store-locator-app/1.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            _cache_set(key, lat, lon)
            return lat, lon
    except requests.RequestException as e:
        logger.warning(f"Nominatim geocoding failed for '{address}': {e}")
    except (ValueError, KeyError) as e:
        logger.warning(f"Geocoding parse error for '{address}': {e}")

    return None


def geocode_postal_code(postal_code: str, ttl: int = 60 * 60 * 24 * 30) -> Optional[Tuple[float, float]]:
    """Convert ZIP code → (lat, lon)."""
    key = _cache_key(f"zip:{postal_code}")
    cached = _cache_get(key, ttl)
    if cached:
        return cached

    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"postalcode": postal_code, "country": "US", "format": "json", "limit": 1}
        headers = {"User-Agent": "store-locator-app/1.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            _cache_set(key, lat, lon)
            return lat, lon
    except Exception as e:
        logger.warning(f"ZIP geocoding failed for '{postal_code}': {e}")

    return None


# ─── Bounding box ──────────────────────────────────────────────────────────────

def calculate_bounding_box(lat: float, lon: float, radius_miles: float):
    """
    Returns (min_lat, max_lat, min_lon, max_lon) for a given radius in miles.
    ~69 miles per degree of latitude; longitude varies with cos(lat).
    """
    lat_delta = radius_miles / 69.0
    lon_delta = radius_miles / (69.0 * cos(radians(lat)))
    return (
        lat - lat_delta,
        lat + lat_delta,
        lon - lon_delta,
        lon + lon_delta,
    )


# ─── Hours parsing ─────────────────────────────────────────────────────────────

HOURS_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
DAY_MAP = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}


def parse_hours(hours_str: str) -> Optional[Tuple[int, int]]:
    """
    Parse 'HH:MM-HH:MM' → (open_minutes, close_minutes).
    Returns None if closed or invalid.
    """
    if not hours_str or hours_str.strip().lower() == "closed":
        return None
    m = HOURS_RE.match(hours_str.strip())
    if not m:
        return None
    h1, min1, h2, min2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    if h1 > 23 or min1 > 59 or h2 > 23 or min2 > 59:
        return None
    open_m = h1 * 60 + min1
    close_m = h2 * 60 + min2
    if open_m >= close_m:
        return None
    return open_m, close_m


def validate_hours_string(hours_str: str) -> bool:
    if hours_str is None:
        return True
    s = hours_str.strip().lower()
    if s == "closed":
        return True
    result = parse_hours(s)
    return result is not None


def is_store_open_now(store) -> bool:
    """
    Check if a store is currently open based on its hours fields and current UTC time.
    NOTE: Hours are treated as local store time; we use UTC as approximation.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    day_key = DAY_MAP[now.weekday()]
    hours_str = getattr(store, f"hours_{day_key}", None)
    parsed = parse_hours(hours_str)
    if parsed is None:
        return False
    current_minutes = now.hour * 60 + now.minute
    return parsed[0] <= current_minutes < parsed[1]