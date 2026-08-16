"""
Songkick API service
Primary source for event listings, venue data, and artist info.

Free tier: sufficient for v1 (no hard public rate limit documented,
be respectful — cache 6 hours per PRD).

Docs: https://www.songkick.com/developer/
"""

import httpx
from typing import Optional
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.songkick.com/api/3.0"


class SongkickService:
    def __init__(self):
        self.api_key = settings.SONGKICK_API_KEY

    def _has_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def get_events_near_location(
        self,
        lat: float,
        lng: float,
        radius_miles: float = 2.0,
        date: Optional[str] = None,  # YYYY-MM-DD, defaults to today
    ) -> list[dict]:
        """
        Fetch events near a lat/lng point within radius_miles.
        Returns normalized event objects.
        """
        if not self._has_key():
            logger.warning("Songkick API key not set — returning mock data")
            return self._mock_events(lat, lng)

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                params = {
                    "apikey": self.api_key,
                    "location": f"geo:{lat},{lng}",
                    "per_page": 50,
                    "page": 1,
                }
                if date:
                    params["min_date"] = date
                    params["max_date"] = date

                resp = await client.get(
                    f"{BASE_URL}/events.json",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                events = (
                    data.get("resultsPage", {})
                    .get("results", {})
                    .get("event", [])
                )
                return [self._normalize_event(e) for e in events]
            except httpx.HTTPStatusError as e:
                logger.error(f"Songkick events failed: {e.response.status_code}")
                return []
            except Exception as e:
                logger.error(f"Songkick events error: {e}")
                return []

    async def get_venue_events(
        self, venue_id: str, date: Optional[str] = None
    ) -> list[dict]:
        """Fetch events at a specific venue."""
        if not self._has_key():
            return []

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                params = {"apikey": self.api_key, "per_page": 20}
                if date:
                    params["min_date"] = date
                    params["max_date"] = date

                resp = await client.get(
                    f"{BASE_URL}/venues/{venue_id}/calendar.json",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                events = (
                    data.get("resultsPage", {})
                    .get("results", {})
                    .get("event", [])
                )
                return [self._normalize_event(e) for e in events]
            except Exception as e:
                logger.error(f"Songkick venue events error: {e}")
                return []

    def _normalize_event(self, raw: dict) -> dict:
        """Normalize a Songkick event object to our internal schema."""
        venue = raw.get("venue", {})
        location = venue.get("metroArea", {})
        performance = raw.get("performance", [{}])
        headliner = next(
            (p for p in performance if p.get("billing") == "headline"),
            performance[0] if performance else {},
        )
        artist = headliner.get("artist", {})

        return {
            "id": f"sk_{raw.get('id')}",
            "source": "songkick",
            "source_id": str(raw.get("id")),
            "title": raw.get("displayName", ""),
            "date": raw.get("start", {}).get("date"),
            "doors_time": raw.get("start", {}).get("time"),  # this is actually start/doors
            "status": raw.get("status", "ok"),
            "type": raw.get("type", "Concert"),
            "venue": {
                "id": f"sk_venue_{venue.get('id')}",
                "source_id": str(venue.get("id")),
                "name": venue.get("displayName"),
                "lat": venue.get("lat"),
                "lng": venue.get("lng"),
                "city": location.get("displayName"),
                "url": venue.get("uri"),
            },
            "headliner": {
                "id": f"sk_artist_{artist.get('id')}",
                "name": artist.get("displayName"),
                "uri": artist.get("uri"),
            },
            "ticket_url": raw.get("uri"),
            "popularity": raw.get("popularity", 0),
        }

    def _mock_events(self, lat: float, lng: float) -> list[dict]:
        """
        Mock events for development before Songkick key arrives.
        Uses real Houston Heights venues with plausible data.
        """
        from datetime import date
        today = date.today().isoformat()

        return [
            # --- Houston ---
            {
                "id": "mock_001",
                "source": "mock",
                "source_id": "mock_001",
                "title": "The Suffers at White Oak Music Hall",
                "date": today,
                "doors_time": "19:00:00",
                "status": "ok",
                "type": "Concert",
                "venue": {
                    "id": "mock_venue_001",
                    "source_id": "mock_venue_001",
                    "name": "White Oak Music Hall",
                    "lat": 29.7726,
                    "lng": -95.3989,
                    "city": "Houston",
                    "url": "https://whiteoakmusichall.com",
                },
                "headliner": {"id": "mock_artist_001", "name": "The Suffers", "uri": None},
                "ticket_url": "https://whiteoakmusichall.com",
                "popularity": 0.6,
            },
            {
                "id": "mock_002",
                "source": "mock",
                "source_id": "mock_002",
                "title": "Leon Bridges at House of Blues Houston",
                "date": today,
                "doors_time": "20:00:00",
                "status": "ok",
                "type": "Concert",
                "venue": {
                    "id": "mock_venue_002",
                    "source_id": "mock_venue_002",
                    "name": "House of Blues Houston",
                    "lat": 29.7512,
                    "lng": -95.3668,
                    "city": "Houston",
                    "url": "https://houseofblues.com/houston",
                },
                "headliner": {"id": "mock_artist_002", "name": "Leon Bridges", "uri": None},
                "ticket_url": "https://houseofblues.com/houston",
                "popularity": 0.85,
            },
            {
                "id": "mock_003",
                "source": "mock",
                "source_id": "mock_003",
                "title": "Khruangbin at 713 Music Hall",
                "date": today,
                "doors_time": "20:30:00",
                "status": "ok",
                "type": "Concert",
                "venue": {
                    "id": "mock_venue_003",
                    "source_id": "mock_venue_003",
                    "name": "713 Music Hall",
                    "lat": 29.7358,
                    "lng": -95.3677,
                    "city": "Houston",
                    "url": "https://713musichall.com",
                },
                "headliner": {"id": "mock_artist_003", "name": "Khruangbin", "uri": None},
                "ticket_url": "https://713musichall.com",
                "popularity": 0.9,
            },
            {
                "id": "mock_004",
                "source": "mock",
                "source_id": "mock_004",
                "title": "Gary Clark Jr. at Warehouse Live",
                "date": today,
                "doors_time": "19:30:00",
                "status": "ok",
                "type": "Concert",
                "venue": {
                    "id": "mock_venue_004",
                    "source_id": "mock_venue_004",
                    "name": "Warehouse Live",
                    "lat": 29.7468,
                    "lng": -95.3580,
                    "city": "Houston",
                    "url": "https://warehouselive.com",
                },
                "headliner": {"id": "mock_artist_004", "name": "Gary Clark Jr.", "uri": None},
                "ticket_url": "https://warehouselive.com",
                "popularity": 0.8,
            },
            # --- Austin ---
            {
                "id": "mock_005",
                "source": "mock",
                "source_id": "mock_005",
                "title": "Turnpike Troubadours at Stubb's Amphitheatre",
                "date": today,
                "doors_time": "19:00:00",
                "status": "ok",
                "type": "Concert",
                "venue": {
                    "id": "mock_venue_005",
                    "source_id": "mock_venue_005",
                    "name": "Stubb's Waller Creek Amphitheater",
                    "lat": 30.2669,
                    "lng": -97.7333,
                    "city": "Austin",
                    "url": "https://stubbsaustin.com",
                },
                "headliner": {"id": "mock_artist_005", "name": "Turnpike Troubadours", "uri": None},
                "ticket_url": "https://stubbsaustin.com",
                "popularity": 0.82,
            },
            {
                "id": "mock_006",
                "source": "mock",
                "source_id": "mock_006",
                "title": "Brandi Carlile at ACL Live at the Moody Theater",
                "date": today,
                "doors_time": "19:30:00",
                "status": "ok",
                "type": "Concert",
                "venue": {
                    "id": "mock_venue_006",
                    "source_id": "mock_venue_006",
                    "name": "ACL Live at the Moody Theater",
                    "lat": 30.2641,
                    "lng": -97.7499,
                    "city": "Austin",
                    "url": "https://acl-live.com",
                },
                "headliner": {"id": "mock_artist_006", "name": "Brandi Carlile", "uri": None},
                "ticket_url": "https://acl-live.com",
                "popularity": 0.88,
            },
            {
                "id": "mock_007",
                "source": "mock",
                "source_id": "mock_007",
                "title": "Charley Crockett at Emo's Austin",
                "date": today,
                "doors_time": "20:00:00",
                "status": "ok",
                "type": "Concert",
                "venue": {
                    "id": "mock_venue_007",
                    "source_id": "mock_venue_007",
                    "name": "Emo's Austin",
                    "lat": 30.2610,
                    "lng": -97.7404,
                    "city": "Austin",
                    "url": "https://emosaustin.com",
                },
                "headliner": {"id": "mock_artist_007", "name": "Charley Crockett", "uri": None},
                "ticket_url": "https://emosaustin.com",
                "popularity": 0.75,
            },
            {
                "id": "mock_008",
                "source": "mock",
                "source_id": "mock_008",
                "title": "Black Pumas at The Parish",
                "date": today,
                "doors_time": "20:30:00",
                "status": "ok",
                "type": "Concert",
                "venue": {
                    "id": "mock_venue_008",
                    "source_id": "mock_venue_008",
                    "name": "The Parish",
                    "lat": 30.2688,
                    "lng": -97.7404,
                    "city": "Austin",
                    "url": "https://theparishaustin.com",
                },
                "headliner": {"id": "mock_artist_008", "name": "Black Pumas", "uri": None},
                "ticket_url": "https://theparishaustin.com",
                "popularity": 0.78,
            },
        ]


songkick_service = SongkickService()
