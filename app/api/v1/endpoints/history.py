"""
WUTBT — History endpoints
GET /api/v1/history?lat=&lng=&radius=
GET /api/v1/history/address?q=
GET /api/v1/history/business?name=&city=
"""

from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter()


@router.get("")
async def get_history_by_location(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    radius: float = Query(0.1, description="Radius in miles"),
):
    # TODO: implement — query OSM, Wikipedia, user contributions
    return {"lat": lat, "lng": lng, "radius": radius, "locations": []}


@router.get("/address")
async def get_history_by_address(
    q: str = Query(..., description="Address string"),
):
    # TODO: implement — geocode address, return history timeline
    return {"query": q, "timeline": []}


@router.get("/business")
async def get_history_by_business(
    name: str = Query(..., description="Business name"),
    city: str = Query(..., description="City"),
):
    # TODO: implement — search business name across all locations in city
    return {"name": name, "city": city, "locations": []}
