"""
OnStage — Venues endpoint

GET /api/v1/venues/{venue_id}    — venue detail (from DB, with tonight's show from TM)
GET /api/v1/venues/claim/{venue_id} — claim a venue (stub, future)
"""

import logging
from datetime import date
from typing import Optional

import httpx
from fastapi import APIRouter, Path, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import settings
from app.models.venue import Venue

logger = logging.getLogger(__name__)
router = APIRouter()

TM_BASE = "https://app.ticketmaster.com/discovery/v2"


async def _get_tonight_tm(tm_venue_id: str) -> Optional[dict]:
    """Fetch tonight's event for a TM venue. Returns None if no show or error."""
    today = date.today().isoformat()
    params = {
        "apikey": settings.TICKETMASTER_API_KEY,
        "venueId": tm_venue_id,
        "startDateTime": f"{today}T00:00:00Z",
        "endDateTime": f"{today}T23:59:59Z",
        "size": 5,
        "sort": "date,asc",
        "segmentName": "Music",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{TM_BASE}/events.json", params=params)
            resp.raise_for_status()
            data = resp.json()
        events = data.get("_embedded", {}).get("events", [])
        if not events:
            return None
        e = events[0]
        attractions = e.get("_embedded", {}).get("attractions", [])
        headliner = attractions[0].get("name", e.get("name", "")) if attractions else e.get("name", "")
        start = e.get("dates", {}).get("start", {})
        return {
            "event_id": f"tm_{e.get('id')}",
            "title": e.get("name", ""),
            "headliner": headliner,
            "date": start.get("localDate"),
            "doors_time": start.get("localTime"),
            "time_tbd": start.get("timeTBA", False),
            "ticket_url": e.get("url"),
        }
    except Exception as ex:
        logger.warning(f"TM tonight lookup failed for venue {tm_venue_id}: {ex}")
        return None


@router.get("/{venue_id}")
async def get_venue(
    venue_id: str = Path(..., description="Venue ID (e.g. tm_venue_KovZ...)"),
    db=Depends(get_db),
):
    """
    Return venue detail from our DB, enriched with tonight's show from TM.
    If venue not in DB, or DB not configured, returns 404.
    """
    if db is None:
        raise HTTPException(status_code=404, detail={"status": "not_found", "venue_id": venue_id})
    venue: Optional[Venue] = await db.get(Venue, venue_id)

    if not venue:
        raise HTTPException(
            status_code=404,
            detail={"status": "not_found", "venue_id": venue_id}
        )

    # Enrich with tonight's show if we have a TM source ID
    tonight = None
    if venue.source == "ticketmaster" and venue.source_id:
        tonight = await _get_tonight_tm(venue.source_id)

    return {
        "venue": venue.to_dict(),
        "tonight": tonight,
        "claim_cta": None if venue.is_claimed else {
            "text": "Own this venue? Claim it",
            "action": "claim",
            "venue_id": venue_id,
        },
    }
