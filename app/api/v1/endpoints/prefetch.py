"""
Pre-fetch endpoint — called by Vercel cron job 3x/day.
Populates Redis cache for all 12 supported cities for today + tomorrow.

Cron schedule (vercel.json):
  - 8:00 AM CT  = 13:00 UTC
  - 2:00 PM CT  = 19:00 UTC
  - 8:00 PM CT  = 01:00 UTC (next day)

This endpoint is protected by a shared secret (PREFETCH_SECRET env var).
"""

import asyncio
import logging
from fastapi import APIRouter, Header, HTTPException, status
from app.core.config import settings
from app.services.ticketmaster import ticketmaster_service
from app.services.jambase import jambase_service
from app.services.stage_estimator import enrich_event_with_stage_time
from app.services.event_cache import (
    PREFETCH_CITIES,
    PREFETCH_RADIUS_MILES,
    get_prefetch_dates,
    set_cached_events,
    EVENT_CACHE_TTL,
)
import re
import math

logger = logging.getLogger(__name__)

router = APIRouter()


def _normalize_artist_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'\(([^)]+)\)', r' \1', name)
    name = re.sub(r'[^a-z0-9\s]', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def _normalize_venue_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'\s*-\s*[a-z]{2}$', '', name)
    name = re.sub(r',\s*[a-z]{2}$', '', name)
    name = re.sub(r'[^a-z0-9\s]', '', name)
    return name.strip()


def _normalize_address(addr: str) -> str:
    if not addr:
        return ""
    addr = addr.lower().strip()
    addr = re.sub(r',.*$', '', addr)
    addr = re.sub(r'#.*$', '', addr)
    addr = re.sub(r'\bste\.?\s*\d*', '', addr)
    addr = re.sub(r'[^a-z0-9\s]', '', addr)
    road_types = {
        'freeway': 'fwy', 'highway': 'hwy', 'boulevard': 'blvd',
        'avenue': 'ave', 'street': 'st', 'drive': 'dr',
        'road': 'rd', 'lane': 'ln', 'court': 'ct', 'place': 'pl',
        'parkway': 'pkwy', 'circle': 'cir', 'way': 'way',
    }
    tokens = addr.split()
    tokens = [road_types.get(t, t) for t in tokens]
    return ' '.join(tokens[:3]).strip()


def _merge_and_dedup(tm_events: list[dict], jb_events: list[dict]) -> list[dict]:
    """Merge TM + JamBase events and deduplicate. Same logic as events.py endpoint."""
    events = tm_events + jb_events

    tm_headliner_venues: dict[str, set[str]] = {}
    for e in tm_events:
        h = _normalize_artist_name((e.get('headliner') or {}).get('name', ''))
        v = _normalize_venue_name((e.get('venue') or {}).get('name', ''))
        if h and v:
            tm_headliner_venues.setdefault(h, set()).add(v)

    seen_source_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    seen_addr_fingerprints: set[str] = set()
    _coord_seen: list[tuple] = []
    unique_events: list[dict] = []

    for event in events:
        if event.get("source") == "jambase":
            h = _normalize_artist_name((event.get("headliner") or {}).get("name", ""))
            jb_venue = _normalize_venue_name((event.get("venue") or {}).get("name", ""))
            if h and jb_venue and h in tm_headliner_venues:
                if any(
                    tv in jb_venue or jb_venue in tv or tv == jb_venue
                    for tv in tm_headliner_venues[h]
                ):
                    continue

        sid = event.get("source_id")
        if sid:
            if sid in seen_source_ids:
                continue
            seen_source_ids.add(sid)

        headliner = (event.get("headliner") or {}).get("name", "").lower().strip()
        venue_name = _normalize_venue_name((event.get("venue") or {}).get("name", ""))
        time = event.get("doors_time") or ""
        fingerprint = f"{headliner}|{venue_name}|{time}"
        if fingerprint in seen_fingerprints:
            continue

        venue = event.get("venue") or {}
        v_lat = venue.get("lat")
        v_lng = venue.get("lng")
        is_coord_dupe = False
        if v_lat and v_lng:
            coord_key = f"{headliner}|{time}"
            for (stored_lat, stored_lng, stored_key) in _coord_seen:
                if stored_key != coord_key:
                    continue
                if abs(stored_lat - v_lat) < 0.005 and abs(stored_lng - v_lng) < 0.005:
                    is_coord_dupe = True
                    break
            if not is_coord_dupe:
                _coord_seen.append((v_lat, v_lng, coord_key))
        if is_coord_dupe:
            continue

        addr = _normalize_address((event.get("venue") or {}).get("address", ""))
        if addr:
            addr_fp = f"{headliner}|{addr}|{time}"
            if addr_fp in seen_addr_fingerprints:
                continue
            seen_addr_fingerprints.add(addr_fp)

        seen_fingerprints.add(fingerprint)
        unique_events.append(event)

    for event in unique_events:
        enrich_event_with_stage_time(event)

    unique_events.sort(key=lambda e: (e.get("doors_time") or "99:99:99"))
    return unique_events


async def _prefetch_city_date(city_label: str, lat: float, lng: float, date: str) -> dict:
    """Fetch and cache events for one city+date. Returns result metadata."""
    try:
        tm_events, jb_events = await asyncio.gather(
            ticketmaster_service.get_events_near_location(
                lat=lat, lng=lng, radius_miles=PREFETCH_RADIUS_MILES, date=date
            ),
            jambase_service.get_events_near_location(
                lat=lat, lng=lng, radius_miles=PREFETCH_RADIUS_MILES, date=date
            ),
        )
        merged = _merge_and_dedup(tm_events, jb_events)
        await set_cached_events(
            lat=lat, lng=lng, radius=PREFETCH_RADIUS_MILES, date=date,
            events=merged, ttl_seconds=EVENT_CACHE_TTL,
        )
        return {
            "city": city_label,
            "date": date,
            "status": "ok",
            "events": len(merged),
            "tm": len(tm_events),
            "jb": len(jb_events),
        }
    except Exception as e:
        logger.error(f"Prefetch failed for {city_label} on {date}: {e}")
        return {"city": city_label, "date": date, "status": "error", "error": str(e)}


@router.post("")
async def run_prefetch(
    x_vercel_cron: str = Header(default="", alias="x-vercel-cron"),
    x_prefetch_secret: str = Header(default="", alias="x-prefetch-secret"),
):
    """
    Trigger pre-fetch for all cities, today + tomorrow.
    Auth: Vercel cron requests include x-vercel-cron=1 header automatically.
    Optionally also check x-prefetch-secret for manual/external triggers.
    Called by Vercel cron 3x/day.
    """
    is_vercel_cron = x_vercel_cron == "1"
    secret_ok = not settings.PREFETCH_SECRET or x_prefetch_secret == settings.PREFETCH_SECRET

    if not is_vercel_cron and not secret_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: provide x-vercel-cron=1 (Vercel cron) or valid x-prefetch-secret",
        )

    dates = get_prefetch_dates()
    tasks = []
    for city_label, lat, lng in PREFETCH_CITIES:
        for date in dates:
            tasks.append(_prefetch_city_date(city_label, lat, lng, date))

    results = await asyncio.gather(*tasks)

    ok = [r for r in results if r.get("status") == "ok"]
    errors = [r for r in results if r.get("status") == "error"]
    total_events = sum(r.get("events", 0) for r in ok)

    logger.info(
        f"Prefetch complete: {len(ok)}/{len(results)} city-dates ok, "
        f"{total_events} total events cached, {len(errors)} errors"
    )

    return {
        "status": "complete",
        "cities": len(PREFETCH_CITIES),
        "dates": dates,
        "city_date_pairs": len(tasks),
        "successful": len(ok),
        "errors": len(errors),
        "total_events_cached": total_events,
        "results": results,
    }
