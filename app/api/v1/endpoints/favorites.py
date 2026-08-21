"""
OnStage — Favorites endpoints

GET    /api/v1/favorites?user_uuid=<uuid>         — list all favorited venues for a user
POST   /api/v1/favorites                          — favorite a venue
DELETE /api/v1/favorites/{venue_id}?user_uuid=<uuid>  — unfavorite a venue

Users are anonymous (device UUID). No auth required.
The iOS app generates a stable UUID on first launch and persists it locally.
"""

import uuid as uuid_lib
import logging
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.venue import Venue
from app.models.favorite import UserVenueFavorite

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class FavoriteRequest(BaseModel):
    user_uuid: str
    venue_id: str


class FavoriteVenueResponse(BaseModel):
    venue_id: str
    name: str
    city: str
    state: Optional[str]
    address: Optional[str]
    lat: float
    lng: float
    website: Optional[str]
    phone: Optional[str]
    is_claimed: bool
    favorited_at: str  # ISO datetime


# ---------------------------------------------------------------------------
# GET /favorites — list user's favorited venues
# ---------------------------------------------------------------------------

@router.get("")
async def list_favorites(
    user_uuid: str = Query(..., min_length=8, description="Device UUID"),
    db: AsyncSession = Depends(get_db),
):
    """Return all venues favorited by this user, ordered by most recently favorited."""
    result = await db.execute(
        select(UserVenueFavorite)
        .options(selectinload(UserVenueFavorite.venue))
        .where(UserVenueFavorite.user_uuid == user_uuid)
        .order_by(UserVenueFavorite.created_at.desc())
    )
    favorites = result.scalars().all()

    items = []
    for fav in favorites:
        if not fav.venue:
            continue
        v = fav.venue
        items.append({
            "venue_id": v.id,
            "name": v.name,
            "city": v.city,
            "state": v.state,
            "address": v.address,
            "lat": v.lat,
            "lng": v.lng,
            "website": v.website,
            "phone": v.phone,
            "is_claimed": v.is_claimed,
            "favorited_at": fav.created_at.isoformat(),
        })

    return {"user_uuid": user_uuid, "count": len(items), "favorites": items}


# ---------------------------------------------------------------------------
# POST /favorites — add a favorite
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def add_favorite(
    body: FavoriteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Favorite a venue. Idempotent — returns 200 if already favorited."""
    # Verify venue exists
    venue = await db.get(Venue, body.venue_id)
    if not venue:
        raise HTTPException(status_code=404, detail=f"Venue '{body.venue_id}' not found.")

    # Check if already favorited
    existing = await db.execute(
        select(UserVenueFavorite).where(
            UserVenueFavorite.user_uuid == body.user_uuid,
            UserVenueFavorite.venue_id == body.venue_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "already_favorited", "venue_id": body.venue_id}

    fav = UserVenueFavorite(
        id=str(uuid_lib.uuid4()),
        user_uuid=body.user_uuid,
        venue_id=body.venue_id,
    )
    db.add(fav)
    await db.commit()

    logger.info(f"Favorited: user={body.user_uuid[:8]}... venue={body.venue_id}")
    return {"status": "favorited", "venue_id": body.venue_id}


# ---------------------------------------------------------------------------
# DELETE /favorites/{venue_id} — remove a favorite
# ---------------------------------------------------------------------------

@router.delete("/{venue_id}", status_code=200)
async def remove_favorite(
    venue_id: str,
    user_uuid: str = Query(..., description="Device UUID"),
    db: AsyncSession = Depends(get_db),
):
    """Unfavorite a venue. Idempotent — returns 200 even if not favorited."""
    await db.execute(
        delete(UserVenueFavorite).where(
            UserVenueFavorite.user_uuid == user_uuid,
            UserVenueFavorite.venue_id == venue_id,
        )
    )
    await db.commit()
    logger.info(f"Unfavorited: user={user_uuid[:8]}... venue={venue_id}")
    return {"status": "unfavorited", "venue_id": venue_id}


# ---------------------------------------------------------------------------
# GET /favorites/ids — lightweight sync endpoint for iOS
# Returns just venue IDs the user has favorited. Fast for startup sync.
# ---------------------------------------------------------------------------

@router.get("/ids")
async def list_favorite_ids(
    user_uuid: str = Query(..., min_length=8, description="Device UUID"),
    db: AsyncSession = Depends(get_db),
):
    """Return just the set of venue IDs favorited by this user. Lightweight for sync."""
    result = await db.execute(
        select(UserVenueFavorite.venue_id)
        .where(UserVenueFavorite.user_uuid == user_uuid)
    )
    ids = [row[0] for row in result.all()]
    return {"user_uuid": user_uuid, "venue_ids": ids}
