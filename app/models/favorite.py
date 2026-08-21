"""
UserVenueFavorite model — tracks which venues a user has favorited.

Users are anonymous (UUID-based) for now — no auth required.
One row per (user_uuid, venue_id) pair. Unique constraint prevents dupes.
"""

from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from app.core.database import Base


class UserVenueFavorite(Base):
    __tablename__ = "user_venue_favorites"

    id = Column(String(128), primary_key=True)           # uuid4, generated on insert
    user_uuid = Column(String(64), nullable=False)       # anonymous device UUID from iOS
    venue_id = Column(String(128), ForeignKey("venues.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    venue = relationship("Venue", back_populates="favorites")

    __table_args__ = (
        UniqueConstraint("user_uuid", "venue_id", name="uq_user_venue_favorite"),
        Index("ix_favorites_user_uuid", "user_uuid"),
        Index("ix_favorites_venue_id", "venue_id"),
    )
