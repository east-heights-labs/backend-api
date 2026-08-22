"""
OnStage — I'm Going endpoints

POST   /api/v1/going                              — mark user as attending an event
DELETE /api/v1/going/{event_source_id}?user_uuid= — unmark
GET    /api/v1/going/ids?user_uuid=&date=          — get event IDs user is going to (for sync)

Users are anonymous (device UUID). No auth required.
Event identity: source + source_id + date (stable composite key).
Event snapshot (headliner, venue, estimated_stage_time) stored at toggle time
for use in push notification delivery when APNs is wired up.
"""

import uuid as uuid_lib
import logging
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.core.database import get_db
from app.models.going import UserEventGoing

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_db(db) -> AsyncSession:
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    return db


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class GoingRequest(BaseModel):
    user_uuid: str
    event_source: str           # "ticketmaster" | "jambase"
    event_source_id: str        # provider event ID
    event_date: str             # YYYY-MM-DD
    headliner_name: Optional[str] = None
    venue_name: Optional[str] = None
    doors_time: Optional[str] = None              # HH:MM:SS from TM
    estimated_stage_time: Optional[str] = None    # HH:MM from heuristic
    device_token: Optional[str] = None            # APNs token if consent already granted


# ---------------------------------------------------------------------------
# POST /going — mark as attending
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def mark_going(
    body: GoingRequest,
    db=Depends(get_db),
):
    """Mark user as attending an event. Idempotent — updates snapshot if already exists."""
    db = _require_db(db)

    # Check if already marked
    existing = await db.execute(
        select(UserEventGoing).where(
            UserEventGoing.user_uuid == body.user_uuid,
            UserEventGoing.event_source == body.event_source,
            UserEventGoing.event_source_id == body.event_source_id,
        )
    )
    record = existing.scalar_one_or_none()

    if record:
        # Update snapshot in case estimated_stage_time changed
        record.headliner_name = body.headliner_name or record.headliner_name
        record.venue_name = body.venue_name or record.venue_name
        record.doors_time = body.doors_time or record.doors_time
        record.estimated_stage_time = body.estimated_stage_time or record.estimated_stage_time
        if body.device_token:
            record.device_token = body.device_token
        await db.commit()
        return {"status": "already_going", "event_source_id": body.event_source_id}

    going = UserEventGoing(
        id=str(uuid_lib.uuid4()),
        user_uuid=body.user_uuid,
        event_source=body.event_source,
        event_source_id=body.event_source_id,
        event_date=body.event_date,
        headliner_name=body.headliner_name,
        venue_name=body.venue_name,
        doors_time=body.doors_time,
        estimated_stage_time=body.estimated_stage_time,
        device_token=body.device_token,
    )
    db.add(going)
    await db.commit()

    logger.info(
        f"Going: user={body.user_uuid[:8]}... "
        f"event={body.event_source}/{body.event_source_id} "
        f"date={body.event_date} headliner={body.headliner_name}"
    )
    return {"status": "going", "event_source_id": body.event_source_id}


# ---------------------------------------------------------------------------
# DELETE /going/{event_source_id} — unmark
# ---------------------------------------------------------------------------

@router.delete("/{event_source_id}", status_code=200)
async def unmark_going(
    event_source_id: str,
    user_uuid: str = Query(..., description="Device UUID"),
    event_source: str = Query(default="ticketmaster", description="Event source"),
    db=Depends(get_db),
):
    """Unmark user as attending. Idempotent."""
    db = _require_db(db)
    await db.execute(
        delete(UserEventGoing).where(
            UserEventGoing.user_uuid == user_uuid,
            UserEventGoing.event_source == event_source,
            UserEventGoing.event_source_id == event_source_id,
        )
    )
    await db.commit()
    logger.info(f"Not going: user={user_uuid[:8]}... event={event_source}/{event_source_id}")
    return {"status": "not_going", "event_source_id": event_source_id}


# ---------------------------------------------------------------------------
# GET /going/ids — lightweight sync (returns event_source_ids for a date)
# iOS calls this on VenueDetailView open to check current state
# ---------------------------------------------------------------------------

@router.get("/ids")
async def list_going_ids(
    user_uuid: str = Query(..., min_length=8, description="Device UUID"),
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD filter (optional)"),
    db=Depends(get_db),
):
    """Return event_source_ids the user has marked as going. Optionally filtered by date."""
    db = _require_db(db)
    query = select(UserEventGoing.event_source_id, UserEventGoing.event_source).where(
        UserEventGoing.user_uuid == user_uuid
    )
    if date:
        query = query.where(UserEventGoing.event_date == date)

    result = await db.execute(query)
    rows = result.all()
    # Return as list of composite keys so iOS can match against event.source + event.source_id
    ids = [{"event_source": r.event_source, "event_source_id": r.event_source_id} for r in rows]
    return {"user_uuid": user_uuid, "going": ids}
