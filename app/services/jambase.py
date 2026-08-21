"""
JamBase Data API service — event discovery source.
Complements Ticketmaster with independent venues and jam band / touring acts.

API: https://api.data.jambase.com/v3
Auth: Bearer token (JAMBASE_API_KEY)
Trial: 14-day free, converts to 1,000 calls/mo Developer tier
Attribution required: "Powered by JamBase"
"""

import asyncio
import httpx
from typing import Optional
from datetime import date as date_type
from app.core.config import settings
import logging
import math

logger = logging.getLogger(__name__)

JAMBASE_BASE = "https://api.data.jambase.com/v3"

# JamBase metro IDs for our supported cities (format: jambase:N)
# Determined by querying /v3/events?geoMetroId=jambase:N
METRO_MAP = [
    {"id": "houston",     "lat": 29.7604,  "lng": -95.3698,   "metro_id": "jambase:30"},
    {"id": "austin",      "lat": 30.2672,  "lng": -97.7431,   "metro_id": "jambase:21"},
    {"id": "dallas",      "lat": 32.7767,  "lng": -96.7970,   "metro_id": "jambase:11"},
    {"id": "nashville",   "lat": 36.1627,  "lng": -86.7816,   "metro_id": "jambase:9"},
    {"id": "neworleans",  "lat": 29.9511,  "lng": -90.0715,   "metro_id": "jambase:48"},
    {"id": "atlanta",     "lat": 33.7490,  "lng": -84.3880,   "metro_id": "jambase:10"},
    {"id": "chicago",     "lat": 41.8781,  "lng": -87.6298,   "metro_id": "jambase:2"},
    {"id": "newyork",     "lat": 40.7128,  "lng": -74.0060,   "metro_id": "jambase:1"},
    {"id": "losangeles",  "lat": 34.0522,  "lng": -118.2437,  "metro_id": "jambase:3"},
    {"id": "denver",      "lat": 39.7392,  "lng": -104.9903,  "metro_id": "jambase:8"},
    {"id": "seattle",     "lat": 47.6062,  "lng": -122.3321,  "metro_id": "jambase:12"},
    {"id": "miami",       "lat": 25.7617,  "lng": -80.1918,   "metro_id": "jambase:34"},
]


def _nearest_metro(lat: float, lng: float, radius_miles: float) -> Optional[str]:
    """Return the JamBase metro ID for the closest city center within radius."""
    radius_deg = radius_miles / 69.0  # rough degrees
    best = None
    best_dist = float("inf")
    for m in METRO_MAP:
        dlat = abs(m["lat"] - lat)
        dlng = abs(m["lng"] - lng) * math.cos(math.radians(lat))
        dist = math.sqrt(dlat**2 + dlng**2)
        if dist < best_dist and dist < radius_deg * 2:
            best_dist = dist
            best = m["metro_id"]
    return best


class JambaseService:
    def __init__(self):
        self.api_key = settings.JAMBASE_API_KEY

    def _has_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def get_events_near_location(
        self,
        lat: float,
        lng: float,
        radius_miles: float = 10.0,
        date: Optional[str] = None,
    ) -> list[dict]:
        """
        Fetch events near a lat/lng point for a given date from JamBase.
        Uses the nearest metro ID since JamBase doesn't support lat/lng directly.
        Returns normalized events in our internal schema.
        """
        if not self._has_key():
            logger.debug("JamBase API key not set — skipping")
            return []

        metro_id = _nearest_metro(lat, lng, radius_miles)
        if not metro_id:
            logger.debug(f"No JamBase metro found near ({lat},{lng})")
            return []

        if not date:
            target_date = date_type.today()
        else:
            try:
                target_date = date_type.fromisoformat(date)
            except ValueError:
                return []

        params = {
            "geoMetroId": metro_id,
            "eventDateFrom": target_date.isoformat(),
            "eventDateTo": target_date.isoformat(),
            "perPage": 50,
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(
                    f"{JAMBASE_BASE}/events",
                    params=params,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"JamBase HTTP error: {e.response.status_code} — {e.response.text[:200]}")
            return []
        except Exception as e:
            logger.warning(f"JamBase request error: {e}")
            return []

        raw_events = data.get("events", [])
        normalized = []
        for raw in raw_events:
            event = self._normalize_event(raw)
            if event:
                normalized.append(event)

        logger.info(f"JamBase: {len(normalized)} events for metro {metro_id} on {date}")
        return normalized

    def _normalize_event(self, raw: dict) -> Optional[dict]:
        """Normalize a JamBase event to our internal schema."""
        name = raw.get("name", "").strip()
        if not name:
            return None

        # Venue / location
        venue_raw = raw.get("location", {})
        geo = venue_raw.get("geo", {})
        lat_val = geo.get("latitude")
        lng_val = geo.get("longitude")
        if not lat_val or not lng_val:
            return None

        try:
            lat = float(lat_val)
            lng = float(lng_val)
        except (TypeError, ValueError):
            return None

        addr = venue_raw.get("address", {})
        venue_name = venue_raw.get("name", "")
        city = addr.get("addressLocality", "")
        state_obj = addr.get("addressRegion", {})
        state = state_obj.get("alternateName", "") if isinstance(state_obj, dict) else ""
        city_display = f"{city}, {state}" if state else city
        street = addr.get("streetAddress", "")

        # Venue website
        venue_website = None
        for link in venue_raw.get("sameAs", []):
            if isinstance(link, dict) and link.get("identifier") == "officialSite":
                venue_website = link.get("url")
                break

        # Date/time — JamBase uses doorTime field
        start_date = raw.get("startDate", "")[:10] if raw.get("startDate") else ""
        door_time = raw.get("doorTime", "")  # "HH:MM:SS" or ""
        time_tbd = not bool(door_time)

        # Performers — first is headliner
        performers = raw.get("performer", [])
        headliner_raw = performers[0] if performers else {}
        headliner_name = headliner_raw.get("name", name) if isinstance(headliner_raw, dict) else name

        # Ticket URL — from offers or direct url
        ticket_url = raw.get("url")
        offers = raw.get("offers", [])
        if offers and isinstance(offers, list) and isinstance(offers[0], dict):
            ticket_url = offers[0].get("url", ticket_url)

        # JamBase identifier
        jb_id = raw.get("identifier", "").replace("jambase:", "")
        venue_id = venue_raw.get("identifier", "").replace("jambase:", "")

        return {
            "id": f"jb_{jb_id}",
            "source": "jambase",
            "source_id": jb_id,
            "title": name,
            "date": start_date if start_date else None,
            "doors_time": door_time if door_time else None,
            "time_tbd": time_tbd,
            "status": "onsale",
            "category": "music",  # JamBase is music-only
            "genre": "",
            "type": "Concert",
            "venue": {
                "id": f"jb_venue_{venue_id}",
                "source_id": f"jambase:{venue_id}",
                "name": venue_name,
                "lat": lat,
                "lng": lng,
                "city": city_display,
                "address": street,
                "url": venue_website,
            },
            "headliner": {
                "id": f"jb_artist_{headliner_raw.get('identifier', 'unknown').replace('jambase:', '') if isinstance(headliner_raw, dict) else 'unknown'}",
                "name": headliner_name,
                "image_url": None,
            },
            "image_url": raw.get("image"),
            "ticket_url": ticket_url,
            "price_min": None,
            "price_max": None,
            "popularity": 0,
            "venue_website": venue_website,
        }


jambase_service = JambaseService()
