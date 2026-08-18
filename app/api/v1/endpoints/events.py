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

    events.sort(key=lambda e: (e.get("doors_time") or "99:99:99"))

    logger.info(f"Events: {len(events)} via Ticketmaster near ({lat},{lng}) for {date}")

    return {
        "date": date,
        "location": {"lat": lat, "lng": lng},
        "radius_miles": radius,
        "sources": ["ticketmaster"],
        "count": len(events),
        "events": events,
    }
