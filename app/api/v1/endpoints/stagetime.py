"""
OnStage — Stage Time Intelligence endpoint
GET /api/v1/stagetime/{artist_name}

Returns historical stage times + estimated stage time with confidence score.
This is the core differentiator of OnStage vs. every other live music app.
"""

from fastapi import APIRouter, Path, Query, HTTPException
from app.services.setlistfm import setlistfm_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{artist_name}")
async def get_stage_time(
    artist_name: str = Path(..., description="Artist name to look up"),
    max_pages: int = Query(default=2, ge=1, le=5, description="Pages of setlists to fetch (20 per page)"),
):
    """
    Get stage time history and estimated stage time for an artist.

    The estimated_stage_time is the core OnStage feature — it tells users
    when the headliner actually takes the stage, not just when doors open.

    Confidence levels:
    - high: 5+ data points
    - medium: 2-4 data points
    - low: 1 data point
    - none: no stage time data available
    """
    if not artist_name or len(artist_name.strip()) < 1:
        raise HTTPException(status_code=400, detail="Artist name required")

    result = await setlistfm_service.get_stage_time_history(
        artist_name=artist_name.strip(),
        max_pages=max_pages,
    )

    return result
