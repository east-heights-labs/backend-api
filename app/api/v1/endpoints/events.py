"""
Live Near Me — Events endpoint
GET /api/v1/events?lat=&lng=&radius=&date=
"""

from fastapi import APIRouter, Query
from datetime import date as Date
from typing import Optional

router = APIRouter()


@router.get("")
async def get_events(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    radius: float = Query(2.0, description="Radius in miles"),
    date: Optional[Date] = Query(None, description="Date (defaults to today)"),
):
    # TODO: implement — pull from Songkick, Bandsintown, Ticketmaster
    return {
        "lat": lat,
        "lng": lng,
        "radius": radius,
        "date": str(date or Date.today()),
        "events": [],
    }
