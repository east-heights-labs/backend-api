"""
OnStage — Events endpoint
GET /api/v1/events?lat=&lng=&radius=&date=

Returns events near a location for a given date.
Primary data source: Ticketmaster Discovery API.
Falls back to Songkick if Ticketmaster key not set.
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import date as date_type
from app.services.ticketmaster import ticketmaster_service
from app.services.songkick import songkick_service
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
    Get live events near a location.

    Returns a list of events sorted by distance, with venue pin data
    suitable for rendering on a MapKit map.

    Primary source: Ticketmaster (music + all live events).
    Fallback: Songkick (music only).
    """
    # Default to today
    if not date:
        date = date_type.today().isoformat()

    # Basic date format validation
    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Try Ticketmaster first
    if ticketmaster_service._has_key():
        events = await ticketmaster_service.get_events_near_location(
            lat=lat,
            lng=lng,
            radius_miles=radius,
            date=date,
        )
        source = "ticketmaster"
    else:
        # Fall back to Songkick
        logger.warning("Ticketmaster key not set — falling back to Songkick")
        events = await songkick_service.get_events_near_location(
            lat=lat,
            lng=lng,
            radius_miles=radius,
            date=date,
        )
        source = "songkick"

    return {
        "date": date,
        "location": {"lat": lat, "lng": lng},
        "radius_miles": radius,
        "source": source,
        "count": len(events),
        "events": events,
    }
