"""
Venue model — OnStage venue database.

A venue record exists independently of whether it has upcoming shows.
Sourced from Ticketmaster/JamBase at seed time, supplemented by claimed venue data.
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Boolean, DateTime, Text, Index
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class Venue(Base):
    __tablename__ = "venues"

    # Primary key — our internal stable ID (e.g. "tm_venue_KovZpZAE6elA")
    id = Column(String(128), primary_key=True)

    # Source tracking
    source = Column(String(32), nullable=False)          # "ticketmaster" | "jambase" | "manual"
    source_id = Column(String(128), nullable=True)       # original API ID

    # Core identity
    name = Column(String(256), nullable=False)
    city = Column(String(128), nullable=False)           # "Houston"
    state = Column(String(8), nullable=True)             # "TX"
    country = Column(String(8), nullable=False, default="US")
    address = Column(String(256), nullable=True)
    zip_code = Column(String(16), nullable=True)

    # Location
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)

    # Contact / web
    website = Column(String(512), nullable=True)
    phone = Column(String(32), nullable=True)

    # Claim status — for future venue partnership funnel
    is_claimed = Column(Boolean, nullable=False, default=False)
    claimed_by_email = Column(String(256), nullable=True)
    claimed_at = Column(DateTime, nullable=True)

    # Metadata
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    favorites = relationship("UserVenueFavorite", back_populates="venue", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_venues_city", "city"),
        Index("ix_venues_lat_lng", "lat", "lng"),
        Index("ix_venues_source_id", "source", "source_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "city": self.city,
            "state": self.state,
            "address": self.address,
            "lat": self.lat,
            "lng": self.lng,
            "website": self.website,
            "phone": self.phone,
            "is_claimed": self.is_claimed,
        }
