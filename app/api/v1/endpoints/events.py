"""
OnStage — Events endpoint
GET /api/v1/events?lat=&lng=&radius=&date=

Returns events near a location for a given date.
Primary source: Ticketmaster Discovery API.
Songkick removed — requires paid licensing, not worth it.
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import date as date_type
from app.services.ticketmaster import ticketmaster_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_events(
    lat: float = Query(..., description="Latitude", ge=-90, le=90),
    lng: float = Query(..., description="Longitude", ge=-180, le=180),
    radius: float = Query(default=10.0, description="Radius in miles", ge=0.5, le=50),
    date: Optional[str] = Query(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today.",
    ),
):
    """
    Get live events near a location via Ticketmaster Discovery API.
    Returns events sorted by start time.
    """
    if not date:
        date = date_type.today().isoformat()

    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if not ticketmaster_service._has_key():
        logger.error("TICKETMASTER_API_KEY not configured")
        raise HTTPException(status_code=503, detail="Event service not configured.")

    events = await ticketmaster_service.get_events_near_location(
        lat=lat,
        lng=lng,
        radius_miles=radius,
        date=date,
    )

    # Deduplicate events.
    # Two passes:
    # 1. By source_id (TM event ID) — catches pagination returning same event twice
    # 2. By (headliner, normalized_venue, time) — catches same show with two TM venue records
    #    e.g. 'Toyota Center' vs 'Toyota Center - TX' (same place, two TM venue IDs)

    import re

    def _normalize_venue_name(name: str) -> str:
        """Strip state suffixes and punctuation for fuzzy venue matching."""
        name = name.lower().strip()
        # Remove trailing " - TX", " - CA", etc.
        name = re.sub(r'\s*-\s*[a-z]{2}$', '', name)
        # Remove trailing state abbreviations with comma: ", tx"
        name = re.sub(r',\s*[a-z]{2}$', '', name)
        # Strip punctuation
        name = re.sub(r'[^a-z0-9\s]', '', name)
        return name.strip()

    seen_source_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    _coord_seen: list[tuple] = []  # (lat, lng, headliner|time key)
    unique_events: list[dict] = []

    for event in events:
        # Pass 1: exact TM event ID dedup
        sid = event.get("source_id")
        if sid:
            if sid in seen_source_ids:
                continue
            seen_source_ids.add(sid)

        # Pass 2: fuzzy fingerprint — headliner + normalized venue name + time
        headliner = (event.get("headliner") or {}).get("name", "").lower().strip()
        venue_name = _normalize_venue_name((event.get("venue") or {}).get("name", ""))
        time = event.get("doors_time") or ""
        fingerprint = f"{headliner}|{venue_name}|{time}"
        if fingerprint in seen_fingerprints:
            continue

        # Pass 3: coordinate proximity — headliner + time + venue within ~0.5km
        # Catches same venue with two TM records at slightly different lat/lng
        venue = event.get("venue") or {}
        v_lat = venue.get("lat")
        v_lng = venue.get("lng")
        is_coord_dupe = False
        if v_lat and v_lng:
            coord_key = f"{headliner}|{time}"
            for (stored_lat, stored_lng, stored_key) in _coord_seen:
                if stored_key != coord_key:
                    continue
                # ~0.005 degrees ≈ 550m — same block
                if abs(stored_lat - v_lat) < 0.005 and abs(stored_lng - v_lng) < 0.005:
                    is_coord_dupe = True
                    break
            if not is_coord_dupe:
                _coord_seen.append((v_lat, v_lng, coord_key))

        if is_coord_dupe:
            continue

        seen_fingerprints.add(fingerprint)
        unique_events.append(event)

    unique_events.sort(key=lambda e: (e.get("doors_time") or "99:99:99"))

    logger.info(f"Events: {len(unique_events)} unique ({len(events)} raw) via Ticketmaster near ({lat},{lng}) for {date}")

    return {
        "date": date,
        "location": {"lat": lat, "lng": lng},
        "radius_miles": radius,
        "sources": ["ticketmaster"],
        "count": len(unique_events),
        "events": unique_events,
    }
