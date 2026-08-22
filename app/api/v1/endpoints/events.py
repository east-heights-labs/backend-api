"""
OnStage — Events endpoint
GET /api/v1/events?lat=&lng=&radius=&date=

Returns events near a location for a given date.
Sources: Ticketmaster (primary) + JamBase (supplement for independent venues).
Results are merged and deduplicated.
"""

import asyncio
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import date as date_type
from app.services.ticketmaster import ticketmaster_service
from app.services.jambase import jambase_service
from app.services.stage_estimator import enrich_event_with_stage_time
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_events(
    lat: float = Query(..., description="Latitude", ge=-90, le=90),
    lng: float = Query(..., description="Longitude", ge=-180, le=180),
    radius: float = Query(default=10.0, description="Radius in miles", ge=0.5, le=50),
    date: Optional[str] = Query(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today.",
    ),
):
    """
    Get live events near a location via Ticketmaster Discovery API.
    Returns events sorted by start time.
    """
    if not date:
        date = date_type.today().isoformat()

    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if not ticketmaster_service._has_key():
        logger.error("TICKETMASTER_API_KEY not configured")
        raise HTTPException(status_code=503, detail="Event service not configured.")

    # Fetch from TM and JamBase in parallel
    tm_events, jb_events = await asyncio.gather(
        ticketmaster_service.get_events_near_location(lat=lat, lng=lng, radius_miles=radius, date=date),
        jambase_service.get_events_near_location(lat=lat, lng=lng, radius_miles=radius, date=date),
    )

    # Merge — TM first (preferred on conflict), JamBase supplements
    # JamBase events without a time get matched against TM events by headliner+venue only
    events = tm_events + jb_events

    # Deduplicate events.
    # Pass 0: JamBase vs TM cross-source dedup (headliner+venue)
    # Pass 1-4: same-source dedup (source_id, fingerprint, coords, address)

    import re

    def _normalize_venue_name(name: str) -> str:
        """Strip state suffixes and punctuation for fuzzy venue matching."""
        name = name.lower().strip()
        name = re.sub(r'\s*-\s*[a-z]{2}$', '', name)
        name = re.sub(r',\s*[a-z]{2}$', '', name)
        name = re.sub(r'[^a-z0-9\s]', '', name)
        return name.strip()

    def _normalize_address(addr: str) -> str:
        """Normalize street address for matching — house number + street name only."""
        if not addr:
            return ""
        addr = addr.lower().strip()
        addr = re.sub(r',.*$', '', addr)           # drop everything after first comma
        addr = re.sub(r'#.*$', '', addr)            # drop unit numbers
        addr = re.sub(r'\bste\.?\s*\d*', '', addr) # drop suite
        addr = re.sub(r'[^a-z0-9\s]', '', addr)    # strip punctuation
        # Normalize common road type abbreviations
        road_types = {
            'freeway': 'fwy', 'highway': 'hwy', 'boulevard': 'blvd',
            'avenue': 'ave', 'street': 'st', 'drive': 'dr',
            'road': 'rd', 'lane': 'ln', 'court': 'ct', 'place': 'pl',
            'parkway': 'pkwy', 'circle': 'cir', 'way': 'way',
        }
        tokens = addr.split()
        tokens = [road_types.get(t, t) for t in tokens]
        # Keep house number + first 2 street tokens: "7620 katy fwy"
        return ' '.join(tokens[:3]).strip()

    # Build TM headliner → normalized venue names index (now that _normalize_venue_name is defined)
    tm_headliner_venues: dict[str, set[str]] = {}
    for e in tm_events:
        h = (e.get('headliner') or {}).get('name', '').lower().strip()
        v = _normalize_venue_name((e.get('venue') or {}).get('name', ''))
        if h and v:
            tm_headliner_venues.setdefault(h, set()).add(v)

    seen_source_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    seen_addr_fingerprints: set[str] = set()
    _coord_seen: list[tuple] = []  # (lat, lng, headliner|time key)
    unique_events: list[dict] = []

    for event in events:
        # Pass 0: suppress JamBase events when TM already has same headliner+venue
        # JamBase often lacks time data so we can't match on time — just headliner+venue.
        # Match strategy: JamBase venue name contains or is contained by TM venue name
        if event.get("source") == "jambase":
            h = (event.get("headliner") or {}).get("name", "").lower().strip()
            jb_venue = _normalize_venue_name((event.get("venue") or {}).get("name", ""))
            if h and jb_venue and h in tm_headliner_venues:
                # Check if any TM venue name for this headliner overlaps with JamBase venue
                is_dupe = False
                for tm_venue in tm_headliner_venues[h]:
                    if (tm_venue in jb_venue or jb_venue in tm_venue or
                            tm_venue == jb_venue):
                        is_dupe = True
                        break
                if is_dupe:
                    continue  # TM has this show, skip JamBase duplicate

        # Pass 1: exact TM event ID dedup
        sid = event.get("source_id")
        if sid:
            if sid in seen_source_ids:
                continue
            seen_source_ids.add(sid)

        # Pass 2: fuzzy fingerprint — headliner + normalized venue name + time
        headliner = (event.get("headliner") or {}).get("name", "").lower().strip()
        venue_name = _normalize_venue_name((event.get("venue") or {}).get("name", ""))
        time = event.get("doors_time") or ""
        fingerprint = f"{headliner}|{venue_name}|{time}"
        if fingerprint in seen_fingerprints:
            continue

        # Pass 3: coordinate proximity — headliner + time + venue within ~0.5km
        # Catches same venue with two TM records at slightly different lat/lng
        venue = event.get("venue") or {}
        v_lat = venue.get("lat")
        v_lng = venue.get("lng")
        is_coord_dupe = False
        if v_lat and v_lng:
            coord_key = f"{headliner}|{time}"
            for (stored_lat, stored_lng, stored_key) in _coord_seen:
                if stored_key != coord_key:
                    continue
                # ~0.005 degrees ≈ 550m — same block
                if abs(stored_lat - v_lat) < 0.005 and abs(stored_lng - v_lng) < 0.005:
                    is_coord_dupe = True
                    break
            if not is_coord_dupe:
                _coord_seen.append((v_lat, v_lng, coord_key))

        if is_coord_dupe:
            continue

        # Pass 4: headliner + normalized street address + time
        # Catches same venue with bad coordinates but same street address
        # e.g. 'Houston Improv' vs 'Improv Comedy Club- Houston' both at 7620 Katy Fwy
        addr = _normalize_address((event.get("venue") or {}).get("address", ""))
        if addr:
            addr_fp = f"{headliner}|{addr}|{time}"
            if addr_fp in seen_addr_fingerprints:
                continue
            seen_addr_fingerprints.add(addr_fp)

        seen_fingerprints.add(fingerprint)
        unique_events.append(event)

    # Enrich each event with estimated stage time
    for event in unique_events:
        enrich_event_with_stage_time(event)

    unique_events.sort(key=lambda e: (e.get("doors_time") or "99:99:99"))

    sources = ["ticketmaster"]
    if jb_events:
        sources.append("jambase")

    logger.info(f"Events: {len(unique_events)} unique ({len(tm_events)} TM + {len(jb_events)} JamBase) near ({lat},{lng}) for {date}")

    return {
        "date": date,
        "location": {"lat": lat, "lng": lng},
        "radius_miles": radius,
        "sources": sources,
        "count": len(unique_events),
        "events": unique_events,
    }
