"""
Event cache service — pre-fetch and Redis caching for live event data.

Strategy:
- Pre-fetch runs 3x/day per city (8am, 2pm, 8pm CT) via Vercel cron
- Cached results stored with 8-hour TTL in Redis
- Events endpoint reads from cache first; falls through to live APIs on miss
- On-demand fallback is always available — cache is a speed+cost optimization

Cache key format: events:{lat_r}:{lng_r}:{radius}:{date}
  - lat/lng rounded to 2 decimal places (≈1km grid cells)
  - radius normalized to the nearest preset bucket (5, 10, 25 mi)
"""

import json
import logging
from datetime import date as date_type, timedelta
from typing import Optional
from app.core.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

# TTL for cached event lists — 8 hours
EVENT_CACHE_TTL = 8 * 3600

# Radius bucket: normalize incoming radius to nearest preset to improve cache hit rate
RADIUS_BUCKETS = [5, 10, 25]

# Cities pre-fetched by the scheduled job.
# Format: (label, lat, lng)  — lat/lng are city centers
PREFETCH_CITIES = [
    ("houston",     29.76,  -95.37),
    ("austin",      30.27,  -97.74),
    ("dallas",      32.78,  -96.80),
    ("nashville",   36.16,  -86.78),
    ("neworleans",  29.95,  -90.07),
    ("atlanta",     33.75,  -84.39),
    ("chicago",     41.88,  -87.63),
    ("newyork",     40.71,  -74.01),
    ("losangeles",  34.05,  -118.24),
    ("denver",      39.74,  -104.99),
    ("seattle",     47.61,  -122.33),
    ("miami",       25.76,  -80.19),
]

# Default pre-fetch radius
PREFETCH_RADIUS_MILES = 10.0


def _bucket_radius(radius: float) -> float:
    """Round radius to nearest preset bucket for cache hit consistency."""
    return min(RADIUS_BUCKETS, key=lambda b: abs(b - radius))


def _cache_key(lat: float, lng: float, radius: float, date: str) -> str:
    """Build a canonical cache key for an event list query."""
    lat_r = round(lat, 2)
    lng_r = round(lng, 2)
    radius_b = _bucket_radius(radius)
    return f"events:{lat_r}:{lng_r}:{radius_b}:{date}"


async def get_cached_events(
    lat: float,
    lng: float,
    radius: float,
    date: str,
) -> Optional[list[dict]]:
    """
    Retrieve cached events for a location+date.
    Returns None on cache miss; returns [] if no events were found (valid cache).
    """
    key = _cache_key(lat, lng, radius, date)
    raw = await cache_get(key)
    if raw is None:
        logger.debug(f"Cache miss: {key}")
        return None
    try:
        data = json.loads(raw)
        logger.info(f"Cache hit: {key} ({len(data)} events)")
        return data
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Cache decode error for {key}: {e}")
        return None


async def set_cached_events(
    lat: float,
    lng: float,
    radius: float,
    date: str,
    events: list[dict],
    ttl_seconds: int = EVENT_CACHE_TTL,
) -> None:
    """Store event list in Redis cache with TTL."""
    key = _cache_key(lat, lng, radius, date)
    try:
        await cache_set(key, json.dumps(events), ttl_seconds)
        logger.info(f"Cached {len(events)} events → {key} (TTL {ttl_seconds}s)")
    except Exception as e:
        logger.warning(f"Cache write failed for {key}: {e}")


def get_prefetch_dates() -> list[str]:
    """Return today and tomorrow — both days are pre-fetched."""
    today = date_type.today()
    return [today.isoformat(), (today + timedelta(days=1)).isoformat()]
