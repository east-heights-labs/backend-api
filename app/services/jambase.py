"""
JamBase Data API service — event discovery source.
Complements Ticketmaster with independent venues and jam band / touring acts.

API: https://api.data.jambase.com/v3
Auth: Bearer token (JAMBASE_API_KEY)
Trial: 14-day free, converts to 1,000 calls/mo Developer tier
"""

import httpx
from typing import Optional
from datetime import date as date_type
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

JAMBASE_BASE = "https://api.data.jambase.com/v3"


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
        Returns normalized events in our internal schema.
        """
        if not self._has_key():
            logger.debug("JamBase API key not set — skipping")
            return []

        if not date:
            target_date = date_type.today()
        else:
            try:
                target_date = date_type.fromisoformat(date)
            except ValueError:
                return []

        params = {
            "geoLatitude": lat,
            "geoLongitude": lng,
            "geoRadiusMile": int(radius_miles),
            "dateFrom": target_date.isoformat(),
            "dateTo": target_date.isoformat(),
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

        logger.info(f"JamBase: {len(normalized)} events near ({lat},{lng}) for {date}")
        return normalized

    def _normalize_event(self, raw: dict) -> Optional[dict]:
        """Normalize a JamBase event to our internal schema."""
        name = raw.get("name", "").strip()
        if not name:
            return None

        # Venue
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
        state = addr.get("addressRegion", {}).get("alternateName", "")
        city_display = f"{city}, {state}" if state else city
        street = addr.get("streetAddress", "")

        # Venue website
        venue_website = None
        for link in venue_raw.get("sameAs", []):
            if link.get("identifier") == "officialSite":
                venue_website = link.get("url")
                break

        # Date/time
        start_date = raw.get("startDate", "")          # YYYY-MM-DD
        start_time = raw.get("startTime")               # HH:MM:SS or None
        time_tbd = not bool(start_time)

        # Performers — first is headliner
        performers = raw.get("performer", [])
        headliner_raw = performers[0] if performers else {}
        headliner_name = headliner_raw.get("name", name)

        # Ticket URL
        ticket_url = raw.get("url")

        # JamBase identifier
        jb_id = raw.get("identifier", "")

        return {
            "id": f"jb_{jb_id}",
            "source": "jambase",
            "source_id": jb_id,
            "title": name,
            "date": start_date[:10] if start_date else None,
            "doors_time": start_time,
            "time_tbd": time_tbd,
            "status": "onsale",
            "category": "music",  # JamBase is music-only
            "genre": "",
            "type": "Concert",
            "venue": {
                "id": f"jb_venue_{venue_raw.get('identifier', '').replace('jambase:', '')}",
                "source_id": venue_raw.get("identifier", ""),
                "name": venue_name,
                "lat": lat,
                "lng": lng,
                "city": city_display,
                "address": street,
                "url": venue_website,
            },
            "headliner": {
                "id": f"jb_artist_{headliner_raw.get('identifier', 'unknown')}",
                "name": headliner_name,
                "image_url": None,
            },
            "image_url": None,
            "ticket_url": ticket_url,
            "price_min": None,
            "price_max": None,
            "popularity": 0,
            "venue_website": venue_website,
        }


jambase_service = JambaseService()
