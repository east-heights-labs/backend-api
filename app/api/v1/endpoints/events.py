"""
OnStage — Events endpoint
GET /api/v1/events?lat=&lng=&radius=&date=

Returns events near a location for a given date.
Sources: Ticketmaster (primary, broadest catalog) + Songkick (fills gaps TM geo misses).
Results are merged and deduped by venue name + date.
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import date as date_type
from app.services.ticketmaster import ticketmaster_service
from app.services.songkick import songkick_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _dedup_events(events: list[dict]) -> list[dict]:
    """
    Remove duplicate events across sources.
    Key: normalized venue name + headliner name + date.
    When a duplicate is found, prefer the Ticketmaster record (richer data).
    """
    seen: dict[str, dict] = {}
    for event in events:
        venue_name = (event.get("venue", {}).get("name") or "").lower().strip()
        artist_name = (event.get("headliner", {}).get("name") or event.get("title") or "").lower().strip()
        date = event.get("date") or ""
        key = f"{venue_name}|{artist_name}|{date}"

        if key not in seen:
            seen[key] = event
        else:
            # Prefer ticketmaster record for richer data
            if event.get("source") == "ticketmaster":
                seen[key] = event

    return list(seen.values())


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
    Get live events near a location.

    Merges Ticketmaster + Songkick results, deduped.
    TM has broader catalog; Songkick fills geo-indexing gaps TM sometimes has.
    """
    if not date:
        date = date_type.today().isoformat()

    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    import asyncio

    # Fetch both sources in parallel
    tasks = []
    source_labels = []

    if ticketmaster_service._has_key():
        tasks.append(ticketmaster_service.get_events_near_location(lat=lat, lng=lng, radius_miles=radius, date=date))
        source_labels.append("ticketmaster")

    if songkick_service._has_key():
        tasks.append(songkick_service.get_events_near_location(lat=lat, lng=lng, radius_miles=radius, date=date))
        source_labels.append("songkick")

    if not tasks:
        logger.warning("No API keys configured — returning empty list")
        return {
            "date": date,
            "location": {"lat": lat, "lng": lng},
            "radius_miles": radius,
            "sources": [],
            "count": 0,
            "events": [],
        }

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_events: list[dict] = []
    active_sources = []
    for label, result in zip(source_labels, results):
        if isinstance(result, Exception):
            logger.error(f"{label} fetch failed: {result}")
        else:
            all_events.extend(result)
            active_sources.append(label)

    deduped = _dedup_events(all_events)

    # Sort by distance if available, else by time
    deduped.sort(key=lambda e: (e.get("doors_time") or "99:99:99"))

    logger.info(f"Events: {len(deduped)} merged ({', '.join(active_sources)}) near ({lat},{lng}) for {date}")

    return {
        "date": date,
        "location": {"lat": lat, "lng": lng},
        "radius_miles": radius,
        "sources": active_sources,
        "count": len(deduped),
        "events": deduped,
    }
