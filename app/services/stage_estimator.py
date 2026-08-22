"""
OnStage — Stage Time Estimator
Heuristic-based stage time estimation for events.

Logic:
  1. If no doors_time → return None (can't estimate without an anchor)
  2. Parse doors_time as the base
  3. Determine offset from doors → headliner stage time based on:
     - Number of attractions (openers present = later start)
     - Venue capacity tier (arena vs club)
     - Genre signals (festival, country fair → longer gaps)
     - Event title keywords (opening act callouts)
  4. Return estimated stage time as HH:MM string

This populates the "OnStage Est" field on every event card.
Confidence metadata is also returned for future UI use.
"""

from datetime import datetime, timedelta
from typing import Optional
import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Venue capacity tier thresholds (estimated from venue name / TM data)
# We don't always have capacity — use heuristics from venue name keywords
# ---------------------------------------------------------------------------
ARENA_KEYWORDS = [
    "arena", "center", "stadium", "amphitheatre", "amphitheater",
    "coliseum", "colosseum", "pavilion", "garden", "toyota", "united",
    "toyota", "toyota", "dickies", "smoothie king", "kia", "crypto",
]
CLUB_KEYWORDS = [
    "club", "bar", "lounge", "tavern", "pub", "cafe", "hall", "room",
    "theatre", "theater", "house of blues", "live nation", "stubb",
    "parish", "scout", "white oak", "warehouse", "loft", "basement",
]

# ---------------------------------------------------------------------------
# Genre signals — certain genres run longer gaps between doors and headliner
# ---------------------------------------------------------------------------
LATE_GENRE_KEYWORDS = ["hip hop", "hip-hop", "r&b", "rap", "edm", "electronic", "dance"]
EARLY_GENRE_KEYWORDS = ["country", "bluegrass", "folk", "americana", "classical"]

# ---------------------------------------------------------------------------
# Title signals
# ---------------------------------------------------------------------------
FESTIVAL_KEYWORDS = ["festival", "fest ", " fest", "fair ", " fair", "outdoor"]
MULTI_ARTIST_KEYWORDS = ["featuring", " feat.", " ft.", " with ", " vs ", " & "]


def estimate_stage_time(event: dict) -> Optional[dict]:
    """
    Given a normalized event dict, return:
      {
        "estimated_stage_time": "21:30",   # HH:MM local time
        "confidence": "heuristic",
        "offset_minutes": 90,
        "reasoning": "2 openers detected, arena venue"
      }
    or None if we can't estimate.
    """
    doors_time = event.get("doors_time")  # "HH:MM:SS" or None
    if not doors_time:
        return None

    # Parse doors time
    try:
        doors_dt = datetime.strptime(doors_time, "%H:%M:%S")
    except ValueError:
        try:
            doors_dt = datetime.strptime(doors_time, "%H:%M")
        except ValueError:
            return None

    # Count performers — proxy for opener presence
    # TM gives us attractions list; in our normalized event it's just headliner
    # We embed performer_count in the event if available from TM raw
    performer_count = event.get("performer_count", 1)
    title = (event.get("title") or "").lower()
    genre = (event.get("genre") or "").lower()
    venue_name = (event.get("venue") or {}).get("name", "").lower()

    # Base offset: 60 min for solo, 90 min for 2 acts, 120 min for 3+
    if performer_count >= 3:
        offset_minutes = 120
        reasoning = "3+ attractions"
    elif performer_count == 2:
        offset_minutes = 90
        reasoning = "2 attractions (opener + headliner)"
    else:
        offset_minutes = 60
        reasoning = "solo headliner"

    # Venue tier adjustment
    venue_tier = _venue_tier(venue_name)
    if venue_tier == "arena":
        offset_minutes += 30
        reasoning += ", arena venue"
    elif venue_tier == "club":
        # clubs run tighter — slight reduction for solo acts
        if performer_count == 1:
            offset_minutes = max(45, offset_minutes - 15)
        reasoning += ", club venue"

    # Festival events — much longer gaps
    if any(kw in title for kw in FESTIVAL_KEYWORDS):
        offset_minutes = max(offset_minutes, 150)
        reasoning += ", festival"

    # Multi-artist title keywords suggest more performers than TM attracted list shows
    if any(kw in title for kw in MULTI_ARTIST_KEYWORDS) and performer_count < 2:
        offset_minutes = max(offset_minutes, 90)
        reasoning += ", multi-artist title"

    # Genre adjustments
    if any(kw in genre for kw in LATE_GENRE_KEYWORDS):
        offset_minutes += 15
        reasoning += ", late-start genre"
    elif any(kw in genre for kw in EARLY_GENRE_KEYWORDS):
        offset_minutes = max(45, offset_minutes - 10)
        reasoning += ", early-start genre"

    # Cap: never less than 30 min, never more than 180 min after doors
    offset_minutes = max(30, min(180, offset_minutes))

    estimated_dt = doors_dt + timedelta(minutes=offset_minutes)
    estimated_str = estimated_dt.strftime("%H:%M")

    return {
        "estimated_stage_time": estimated_str,
        "confidence": "heuristic",
        "offset_minutes": offset_minutes,
        "reasoning": reasoning,
    }


def _venue_tier(venue_name: str) -> str:
    """Classify venue as 'arena', 'club', or 'unknown' from name."""
    for kw in ARENA_KEYWORDS:
        if kw in venue_name:
            return "arena"
    for kw in CLUB_KEYWORDS:
        if kw in venue_name:
            return "club"
    return "unknown"


def enrich_event_with_stage_time(event: dict) -> dict:
    """
    Mutates event dict in-place to add estimated_stage_time field.
    Returns the event for chaining.
    """
    result = estimate_stage_time(event)
    if result:
        event["estimated_stage_time"] = result["estimated_stage_time"]
    else:
        event["estimated_stage_time"] = None
    return event
