"""
UserEventGoing model — tracks which events a user plans to attend.

Anonymous UUID-based, same pattern as favorites.
Event identity: source + source_id + date (stable composite key).
Snapshot fields stored at toggle time for use in push notification delivery.
"""

from datetime import datetime
from sqlalchemy import Column, String, DateTime, UniqueConstraint, Index
from app.core.database import Base


class UserEventGoing(Base):
    __tablename__ = "user_event_going"

    id = Column(String(128), primary_key=True)
    user_uuid = Column(String(64), nullable=False)

    # Event identity
    event_source = Column(String(32), nullable=False)       # "ticketmaster" | "jambase"
    event_source_id = Column(String(128), nullable=False)   # provider event ID
    event_date = Column(String(16), nullable=False)         # YYYY-MM-DD

    # Event snapshot at toggle time
    headliner_name = Column(String(256), nullable=True)
    venue_name = Column(String(256), nullable=True)
    doors_time = Column(String(16), nullable=True)              # HH:MM:SS
    estimated_stage_time = Column(String(8), nullable=True)     # HH:MM

    # Push notification state (populated after APNs consent)
    device_token = Column(String(512), nullable=True)
    notified_at = Column(DateTime, nullable=True)
    notification_scheduled_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_uuid", "event_source", "event_source_id", name="uq_user_event_going"),
        Index("ix_going_user_uuid", "user_uuid"),
        Index("ix_going_event_source_id", "event_source", "event_source_id"),
        Index("ix_going_event_date", "event_date"),
        Index("ix_going_notify", "notification_scheduled_at", "notified_at"),
    )
