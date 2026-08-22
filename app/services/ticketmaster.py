"""
Ticketmaster Discovery API service
Primary source for event listings.

API docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
Free tier: 5000 req/day, 5 req/sec
Results: up to 200 per page (size param), paginated

Radius: Ticketmaster uses miles by default.
Category mapping: classificationName — Music, Sports, Arts & Theatre, Film, Miscellaneous
"""

import httpx
from typing import Optional
from datetime import datetime, timezone
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://app.ticketmaster.com/discovery/v2"


class TicketmasterService:
    def __init__(self):
        self.api_key = settings.TICKETMASTER_API_KEY

    def _has_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def get_events_near_location(
        self,
        lat: float,
        lng: float,
        radius_miles: float = 10.0,
        date: Optional[str] = None,  # YYYY-MM-DD
    ) -> list[dict]:
        """
        Fetch events near a lat/lng point for a given date.

        NOTE: Ticketmaster's startDateTime/endDateTime filter is unreliable —
        some events exist in their system but don't surface via date-range queries.
        Strategy: fetch a broader 7-day window, then filter client-side to the
        requested date. This catches events TM's date filter would miss.

        Pages through up to 3 pages (max 600 events). Filters to requested date.
        """
        if not self._has_key():
            logger.warning("Ticketmaster API key not set — returning empty list")
            return []

        from datetime import date as date_type
        if not date:
            target_date = date_type.today()
        else:
            target_date = date_type.fromisoformat(date)

        # NOTE: TM's startDateTime/endDateTime filter is unreliable and actively
        # drops some events (confirmed with Santana, The Amp at Lake Martin, others).
        # Fix: omit date params entirely, fetch by geo only, filter client-side.
        # We fetch 3 pages max (600 events) and keep only those matching target date.
        target_str = target_date.isoformat()  # YYYY-MM-DD for client-side filter

        all_events: list[dict] = []

        async with httpx.AsyncClient(timeout=12.0) as client:
            page = 0
            while page < 3:  # cap at 3 pages = up to 600 events
                params = {
                    "apikey": self.api_key,
                    "latlong": f"{lat},{lng}",
                    "radius": str(int(radius_miles)),
                    "unit": "miles",
                    "size": 200,
                    "page": page,
                    "sort": "date,asc",
                }

                try:
                    resp = await client.get(f"{BASE_URL}/events.json", params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as e:
                    logger.error(f"Ticketmaster events HTTP error: {e.response.status_code} — {e.response.text[:200]}")
                    break
                except Exception as e:
                    logger.error(f"Ticketmaster events error: {e}")
                    break

                page_info = data.get("page", {})
                embedded = data.get("_embedded", {})
                events_raw = embedded.get("events", [])

                for raw in events_raw:
                    normalized = self._normalize_event(raw)
                    # Client-side date filter — only keep events on requested date
                    if normalized and normalized.get("date") == target_str:
                        all_events.append(normalized)

                total_pages = page_info.get("totalPages", 1)
                if page + 1 >= total_pages:
                    break
                page += 1

        logger.info(f"Ticketmaster: {len(all_events)} events near ({lat},{lng}) for {date}")
        return all_events

    def _date_to_window(self, date: Optional[str]) -> tuple[str, str]:
        """Convert YYYY-MM-DD to Ticketmaster ISO8601 date window (full day)."""
        from datetime import date as date_type
        if not date:
            d = date_type.today()
        else:
            d = date_type.fromisoformat(date)

        # Ticketmaster format: 2026-08-18T00:00:00Z
        start = f"{d.isoformat()}T00:00:00Z"
        end = f"{d.isoformat()}T23:59:59Z"
        return start, end

    def _normalize_event(self, raw: dict) -> Optional[dict]:
        """
        Normalize a Ticketmaster event to our internal schema.
        Returns None if missing required fields (no venue coords).
        """
        # Venue
        embedded = raw.get("_embedded", {})
        venues = embedded.get("venues", [{}])
        venue_raw = venues[0] if venues else {}

        location = venue_raw.get("location", {})
        lat_str = location.get("latitude")
        lng_str = location.get("longitude")

        if not lat_str or not lng_str:
            return None  # can't pin without coords

        try:
            lat = float(lat_str)
            lng = float(lng_str)
        except (ValueError, TypeError):
            return None

        # Date / time
        dates = raw.get("dates", {})
        start = dates.get("start", {})
        event_date = start.get("localDate")       # YYYY-MM-DD
        event_time = start.get("localTime")       # HH:MM:SS or None
        time_tbd = start.get("timeTBA", False)

        # Classification → category
        classifications = raw.get("classifications", [{}])
        segment = classifications[0].get("segment", {}).get("name", "").lower() if classifications else ""
        genre = classifications[0].get("genre", {}).get("name", "") if classifications else ""
        category = "music" if segment == "music" else "other"

        # Headliner — first attraction
        attractions = embedded.get("attractions", [])
        headliner_raw = attractions[0] if attractions else {}

        # Images — grab the widest one
        images = raw.get("images", [])
        image_url = None
        if images:
            best = max(images, key=lambda i: i.get("width", 0))
            image_url = best.get("url")

        # Ticket URL
        ticket_url = raw.get("url")

        # Price range
        price_ranges = raw.get("priceRanges", [])
        price_min = price_ranges[0].get("min") if price_ranges else None
        price_max = price_ranges[0].get("max") if price_ranges else None

        venue_city = venue_raw.get("city", {}).get("name", "")
        venue_state = venue_raw.get("state", {}).get("stateCode", "")
        venue_city_display = f"{venue_city}, {venue_state}" if venue_state else venue_city

        return {
            "id": f"tm_{raw.get('id')}",
            "source": "ticketmaster",
            "source_id": raw.get("id"),
            "title": raw.get("name", ""),
            "date": event_date,
            "doors_time": event_time,
            "time_tbd": time_tbd,
            "performer_count": len(attractions),  # used by stage time estimator
            "status": raw.get("dates", {}).get("status", {}).get("code", "onsale"),
            "category": category,
            "genre": genre,
            "type": "Concert" if category == "music" else "Event",
            "venue": {
                "id": f"tm_venue_{venue_raw.get('id')}",
                "source_id": venue_raw.get("id"),
                "name": venue_raw.get("name"),
                "lat": lat,
                "lng": lng,
                "city": venue_city_display,
                "address": venue_raw.get("address", {}).get("line1"),
                "url": venue_raw.get("url"),
            },
            "headliner": {
                "id": f"tm_artist_{headliner_raw.get('id', 'unknown')}",
                "name": headliner_raw.get("name", raw.get("name", "")),
                "image_url": headliner_raw.get("images", [{}])[0].get("url") if headliner_raw.get("images") else None,
            },
            "image_url": image_url,
            "ticket_url": ticket_url,
            "price_min": price_min,
            "price_max": price_max,
            "popularity": raw.get("score", 0),
        }


ticketmaster_service = TicketmasterService()
