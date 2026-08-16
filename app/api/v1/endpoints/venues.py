"""
Live Near Me — Venues endpoint
GET /api/v1/venues/{venue_id}
"""

from fastapi import APIRouter, Path

router = APIRouter()


@router.get("/{venue_id}")
async def get_venue(venue_id: str = Path(..., description="Venue ID")):
    # TODO: implement — return venue detail, tonight's show, photos
    return {"venue_id": venue_id, "data": None}
