"""
OnStage — Search endpoint
GET /api/v1/search/venues?q=<query>&date=<YYYY-MM-DD>
GET /api/v1/search/artists?q=<query>&date=<YYYY-MM-DD>

Global search across all supported cities.
Venues: returns matching venues with upcoming shows.
Artists: returns upcoming shows for matching artists across all cities.
"""

import asyncio
import logging
from datetime import date as date_type
from typing import Optional

import httpx
from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.sql.expression import func

from app.core.config import settings
from app.core.database import get_db
from app.models.venue import Venue

logger = logging.getLogger(__name__)

router = APIRouter()

# All cities we support — mirrors CityOption.swift
SUPPORTED_CITIES = [
    {"id": "houston",     "name": "Houston",      "lat": 29.7604,  "lng": -95.3698},
    {"id": "austin",      "name": "Austin",       "lat": 30.2672,  "lng": -97.7431},
    {"id": "dallas",      "name": "Dallas",       "lat": 32.7767,  "lng": -96.7970},
    {"id": "nashville",   "name": "Nashville",    "lat": 36.1627,  "lng": -86.7816},
    {"id": "neworleans",  "name": "New Orleans",  "lat": 29.9511,  "lng": -90.0715},
    {"id": "atlanta",     "name": "Atlanta",      "lat": 33.7490,  "lng": -84.3880},
    {"id": "chicago",     "name": "Chicago",      "lat": 41.8781,  "lng": -87.6298},
    {"id": "newyork",     "name": "New York",     "lat": 40.7128,  "lng": -74.0060},
    {"id": "losangeles",  "name": "Los Angeles",  "lat": 34.0522,  "lng": -118.2437},
    {"id": "denver",      "name": "Denver",       "lat": 39.7392,  "lng": -104.9903},
    {"id": "seattle",     "name": "Seattle",      "lat": 47.6062,  "lng": -122.3321},
    {"id": "miami",       "name": "Miami",        "lat": 25.7617,  "lng": -80.1918},
]

TM_BASE = "https://app.ticketmaster.com/discovery/v2"
SEARCH_RADIUS_MILES = 30  # wide radius per city for search


async def _tm_search(client: httpx.AsyncClient, keyword: str, lat: float, lng: float,
                     start_dt: str, end_dt: str) -> list[dict]:
    """Single Ticketmaster keyword search near one lat/lng."""
    params = {
        "apikey": settings.TICKETMASTER_API_KEY,
        "keyword": keyword,
        "latlong": f"{lat},{lng}",
        "radius": str(SEARCH_RADIUS_MILES),
        "unit": "miles",
        "startDateTime": start_dt,
        "endDateTime": end_dt,
        "size": 50,
        "sort": "date,asc",
    }
    try:
        resp = await client.get(f"{TM_BASE}/events.json", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("_embedded", {}).get("events", [])
    except Exception as e:
        logger.warning(f"TM search error ({lat},{lng}): {e}")
        return []


def _normalize_event_slim(raw: dict, city_name: str) -> Optional[dict]:
    """Slim event normalization for search results — no stage time, just location data."""
    embedded = raw.get("_embedded", {})
    venues = embedded.get("venues", [{}])
    venue_raw = venues[0] if venues else {}

    location = venue_raw.get("location", {})
    lat_str = location.get("latitude")
    lng_str = location.get("longitude")
    if not lat_str or not lng_str:
        return None

    try:
        lat = float(lat_str)
        lng = float(lng_str)
    except (ValueError, TypeError):
        return None

    dates = raw.get("dates", {})
    start = dates.get("start", {})
    event_date = start.get("localDate")
    event_time = start.get("localTime")
    time_tbd = start.get("timeTBA", False)

    classifications = raw.get("classifications", [{}])
    segment = classifications[0].get("segment", {}).get("name", "").lower() if classifications else ""
    category = "music" if segment == "music" else "other"

    attractions = embedded.get("attractions", [])
    headliner_raw = attractions[0] if attractions else {}

    venue_city = venue_raw.get("city", {}).get("name", "")
    venue_state = venue_raw.get("state", {}).get("stateCode", "")
    venue_city_display = f"{venue_city}, {venue_state}" if venue_state else venue_city

    return {
        "id": f"tm_{raw.get('id')}",
        "source_id": raw.get("id"),
        "title": raw.get("name", ""),
        "date": event_date,
        "doors_time": event_time,
        "time_tbd": time_tbd,
        "category": category,
        "ticket_url": raw.get("url"),
        "city_name": city_name,
        "venue": {
            "id": f"tm_venue_{venue_raw.get('id')}",
            "source_id": venue_raw.get("id"),
            "name": venue_raw.get("name", ""),
            "lat": lat,
            "lng": lng,
            "city": venue_city_display,
            "address": venue_raw.get("address", {}).get("line1"),
        },
        "headliner": {
            "id": f"tm_artist_{headliner_raw.get('id', 'unknown')}",
            "name": headliner_raw.get("name", raw.get("name", "")),
        },
    }


def _date_window(date_str: Optional[str]) -> tuple[str, str]:
    """Return 30-day window starting from date (or today) for forward-looking search."""
    from datetime import timedelta
    if date_str:
        start = date_type.fromisoformat(date_str)
    else:
        start = date_type.today()
    end = start + timedelta(days=30)
    return f"{start.isoformat()}T00:00:00Z", f"{end.isoformat()}T23:59:59Z"


# ---------------------------------------------------------------------------
# Venue Search
# ---------------------------------------------------------------------------

@router.get("/venues")
async def search_venues(
    q: str = Query(..., min_length=2, description="Venue name query"),
    date: Optional[str] = Query(default=None, description="Start date YYYY-MM-DD (defaults to today)"),
    db: Optional[AsyncSession] = Depends(get_db),
):
    """
    Two-phase venue search:
    1. Query our venue DB by name (returns venues even with no upcoming shows)
    2. Supplement with Ticketmaster for any additional venues not yet in our DB
    Results are deduplicated by venue ID.
    Phase 1 is skipped gracefully if DB is unavailable.
    """
    # --- Phase 1: DB search (skipped if DB not available) ---
    db_venues = []
    db_venue_ids: set[str] = set()
    venues: list[dict] = []

    if db is not None:
        try:
            db_results = await db.execute(
                select(Venue)
                .where(func.lower(Venue.name).contains(q.lower()))
                .order_by(Venue.name)
                .limit(50)
            )
            db_venues = db_results.scalars().all()
            db_venue_ids = {v.id for v in db_venues}
            venues = [
                {
                    "venue_id": v.id,
                    "venue_name": v.name,
                    "city": f"{v.city}, {v.state}" if v.state else v.city,
                    "lat": v.lat,
                    "lng": v.lng,
                    "address": v.address,
                    "source": "db",
                    "next_event": None,
                }
                for v in db_venues
            ]
        except Exception as e:
            logger.warning(f"DB search unavailable, falling through to TM only: {e}")
            db_venues = []
            db_venue_ids = set()
            venues = []

    # --- Phase 2: TM supplement (always runs when DB has < 10 results) ---
    if len(db_venues) >= 10 or not settings.TICKETMASTER_API_KEY:
        logger.info(f"Venue search '{q}': {len(venues)} DB results (skipping TM supplement)")
        return {"query": q, "count": len(venues), "venues": venues}
    
    # TM supplement — catch venues not yet in our DB
    start_dt, end_dt = _date_window(date)
    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks = [
            _tm_search(client, q, city["lat"], city["lng"], start_dt, end_dt)
            for city in SUPPORTED_CITIES
        ]
        results = await asyncio.gather(*tasks)

    all_raw = []
    for city, city_events in zip(SUPPORTED_CITIES, results):
        for raw in city_events:
            all_raw.append((city["name"], raw))

    def event_sort_key(item):
        _, raw = item
        return raw.get("dates", {}).get("start", {}).get("localDate") or "9999-99-99"
    all_raw.sort(key=event_sort_key)

    seen_tm_ids: set[str] = set()
    for city_name, raw in all_raw:
        embedded = raw.get("_embedded", {})
        venue_list = embedded.get("venues", [{}])
        venue_raw = venue_list[0] if venue_list else {}
        venue_id = venue_raw.get("id")
        if not venue_id or venue_id in seen_tm_ids:
            continue

        venue_name = venue_raw.get("name", "").lower()
        if q.lower() not in venue_name:
            continue

        our_id = f"tm_venue_{venue_id}"
        if our_id in db_venue_ids:
            continue  # already returned from DB

        seen_tm_ids.add(venue_id)
        normalized = _normalize_event_slim(raw, city_name)
        if normalized:
            venues.append({
                "venue_id": our_id,
                "venue_name": venue_raw.get("name", ""),
                "city": normalized["venue"]["city"],
                "lat": normalized["venue"]["lat"],
                "lng": normalized["venue"]["lng"],
                "address": normalized["venue"]["address"],
                "source": "ticketmaster",
                "next_event": {
                    "title": normalized["title"],
                    "date": normalized["date"],
                    "doors_time": normalized["doors_time"],
                    "headliner": normalized["headliner"]["name"],
                    "ticket_url": normalized["ticket_url"],
                    "event_id": normalized["id"],
                },
            })

    logger.info(f"Venue search '{q}': {len(venues)} results ({len(db_venues)} DB + {len(seen_tm_ids)} TM)")
    return {"query": q, "count": len(venues), "venues": venues}


# ---------------------------------------------------------------------------
# Artist Search
# ---------------------------------------------------------------------------

@router.get("/artists")
async def search_artists(
    q: str = Query(..., min_length=2, description="Artist name query"),
    date: Optional[str] = Query(default=None, description="Start date YYYY-MM-DD (defaults to today)"),
):
    """
    Search for an artist's upcoming shows across all supported cities.
    Returns all upcoming shows (deduplicated by event ID), sorted by date.
    """
    if not settings.TICKETMASTER_API_KEY:
        raise HTTPException(status_code=503, detail="Search service not configured.")

    start_dt, end_dt = _date_window(date)

    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks = [
            _tm_search(client, q, city["lat"], city["lng"], start_dt, end_dt)
            for city in SUPPORTED_CITIES
        ]
        results = await asyncio.gather(*tasks)

    # Flatten + normalize, deduplicate by TM event ID
    seen_event_ids: set[str] = set()
    shows: list[dict] = []

    for city, city_events in zip(SUPPORTED_CITIES, results):
        for raw in city_events:
            event_id = raw.get("id")
            if not event_id or event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)

            normalized = _normalize_event_slim(raw, city["name"])
            if not normalized:
                continue

            # Only return music events for artist search
            if normalized.get("category") != "music":
                continue

            shows.append({
                "event_id": normalized["id"],
                "title": normalized["title"],
                "headliner": normalized["headliner"]["name"],
                "date": normalized["date"],
                "doors_time": normalized["doors_time"],
                "time_tbd": normalized["time_tbd"],
                "ticket_url": normalized["ticket_url"],
                "venue": normalized["venue"],
                "city_name": city["name"],
            })

    # Sort by date
    shows.sort(key=lambda s: s.get("date") or "9999-99-99")

    logger.info(f"Artist search '{q}': {len(shows)} shows across all cities")
    return {"query": q, "count": len(shows), "shows": shows}
