"""
OnStage — Events endpoint
GET /api/v1/events?lat=&lng=&radius=&date=

Returns events near a location for a given date.
Primary data source: Songkick (falls back to mock data if key not set).
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import date as date_type
from app.services.songkick import songkick_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_events(
    lat: float = Query(..., description="Latitude", ge=-90, le=90),
    lng: float = Query(..., description="Longitude", ge=-180, le=180),
    radius: float = Query(default=2.0, description="Radius in miles", ge=0.5, le=25),
    date: Optional[str] = Query(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today.",
    ),
):
    """
    Get live music events near a location.

    Returns a list of events sorted by distance, with venue pins data
    suitable for rendering on a MapKit map.
    """
    # Default to today
    if not date:
        date = date_type.today().isoformat()

    # Basic date format validation
    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    events = await songkick_service.get_events_near_location(
        lat=lat,
        lng=lng,
        radius_miles=radius,
        date=date,
    )

    return {
        "date": date,
        "location": {"lat": lat, "lng": lng},
        "radius_miles": radius,
        "count": len(events),
        "events": events,
    }
