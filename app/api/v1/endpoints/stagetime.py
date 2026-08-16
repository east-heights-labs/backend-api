"""
Live Near Me — Stage Time Intelligence endpoint
GET /api/v1/stagetime/{artist_id}
Returns historical stage times from Setlist.fm + confidence score
"""

from fastapi import APIRouter, Path

router = APIRouter()


@router.get("/{artist_id}")
async def get_stage_time(artist_id: str = Path(..., description="Artist ID")):
    # TODO: implement — query Setlist.fm, parse timestamps, compute avg + confidence
    return {
        "artist_id": artist_id,
        "history": [],
        "estimated_stage_time": None,
        "confidence": "low",
    }
