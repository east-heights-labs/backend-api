"""
Setlist.fm API service
Fetches historical setlists to extract actual stage times (not door times)

Key insight: Setlist.fm stores setlists with event start times in UTC.
We compare event start time vs. door time (from other sources) to derive
when the headliner actually took the stage.

Free tier: 2 req/sec, 1500 req/day — cache aggressively.
"""

import httpx
import asyncio
from datetime import datetime, timezone
from typing import Optional
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.setlist.fm/rest/1.0"
CACHE_TTL_SETLISTS = 86400  # 24 hours — setlists don't change after the show


class SetlistFMService:
    def __init__(self):
        self.api_key = settings.SETLIST_FM_API_KEY
        self.headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
        }

    async def search_artist(self, artist_name: str) -> Optional[dict]:
        """
        Search for an artist by name.
        Returns the best match (first result).
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(
                    f"{BASE_URL}/search/artists",
                    headers=self.headers,
                    params={"artistName": artist_name, "sort": "relevance", "p": 1},
                )
                resp.raise_for_status()
                data = resp.json()
                artists = data.get("artist", [])
                return artists[0] if artists else None
            except httpx.HTTPStatusError as e:
                logger.error(f"Setlist.fm artist search failed: {e.response.status_code} — {artist_name}")
                return None
            except Exception as e:
                logger.error(f"Setlist.fm artist search error: {e}")
                return None

    async def get_setlists(self, mbid: str, page: int = 1) -> list[dict]:
        """
        Fetch setlists for an artist by MusicBrainz ID.
        Returns list of setlist objects.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(
                    f"{BASE_URL}/artist/{mbid}/setlists",
                    headers=self.headers,
                    params={"p": page},
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("setlist", [])
            except httpx.HTTPStatusError as e:
                logger.error(f"Setlist.fm setlists failed: {e.response.status_code} — mbid={mbid}")
                return []
            except Exception as e:
                logger.error(f"Setlist.fm setlists error: {e}")
                return []

    def extract_stage_time(self, setlist: dict) -> Optional[dict]:
        """
        Extract stage time data from a setlist object.

        Setlist.fm provides eventDate (YYYY-MM-DD) and sometimes startTime.
        When startTime is present, that's when the headliner actually hit the stage.

        Returns a normalized stage time record or None if insufficient data.
        """
        event_date = setlist.get("eventDate")  # "16-08-2026" (DD-MM-YYYY)
        start_time = setlist.get("startTime")  # "21:45:00" or None
        venue = setlist.get("venue", {})
        city = venue.get("city", {})

        if not event_date or not start_time:
            return None

        try:
            # Parse DD-MM-YYYY
            dt = datetime.strptime(event_date, "%d-%m-%Y")
            time_parts = start_time.split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1])

            return {
                "date": dt.strftime("%Y-%m-%d"),
                "stage_time": start_time[:5],  # "21:45"
                "stage_hour": hour,
                "stage_minute": minute,
                "stage_minutes_from_midnight": hour * 60 + minute,
                "venue_name": venue.get("name"),
                "city": city.get("name"),
                "state": city.get("stateCode"),
                "country": city.get("country", {}).get("code"),
                "setlist_url": setlist.get("url"),
            }
        except (ValueError, IndexError) as e:
            logger.debug(f"Failed to parse stage time from setlist: {e}")
            return None

    async def get_stage_time_history(
        self, artist_name: str, max_pages: int = 2
    ) -> dict:
        """
        Full pipeline: artist name → stage time history + intelligence.

        Returns:
        {
            "artist_name": str,
            "mbid": str,
            "history": [...],           # list of stage time records
            "estimated_stage_time": str | None,  # "21:45"
            "avg_minutes_after_doors": int | None,
            "confidence": "high" | "medium" | "low" | "none",
            "data_points": int,
        }
        """
        # Step 1: find artist
        artist = await self.search_artist(artist_name)
        if not artist:
            return self._empty_result(artist_name)

        mbid = artist.get("mbid")
        if not mbid:
            return self._empty_result(artist_name)

        # Step 2: fetch setlists (up to max_pages * 20 = 40 shows)
        all_setlists = []
        for page in range(1, max_pages + 1):
            setlists = await self.get_setlists(mbid, page=page)
            if not setlists:
                break
            all_setlists.extend(setlists)
            # Rate limit: 2 req/sec
            if page < max_pages:
                await asyncio.sleep(0.6)

        # Step 3: extract stage times
        history = []
        for setlist in all_setlists:
            record = self.extract_stage_time(setlist)
            if record:
                history.append(record)

        # Step 4: compute intelligence
        data_points = len(history)

        if data_points == 0:
            return {
                "artist_name": artist.get("name", artist_name),
                "mbid": mbid,
                "history": [],
                "estimated_stage_time": None,
                "avg_minutes_after_doors": None,
                "confidence": "none",
                "data_points": 0,
            }

        # Average stage time (in minutes from midnight)
        avg_minutes = sum(r["stage_minutes_from_midnight"] for r in history) / data_points
        avg_hour = int(avg_minutes // 60)
        avg_min = int(avg_minutes % 60)
        estimated = f"{avg_hour:02d}:{avg_min:02d}"

        # Confidence scoring
        if data_points >= 5:
            confidence = "high"
        elif data_points >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "artist_name": artist.get("name", artist_name),
            "mbid": mbid,
            "history": history[:10],  # return last 10 data points
            "estimated_stage_time": estimated,
            "avg_minutes_after_doors": None,  # requires door time from event source
            "confidence": confidence,
            "data_points": data_points,
        }

    def _empty_result(self, artist_name: str) -> dict:
        return {
            "artist_name": artist_name,
            "mbid": None,
            "history": [],
            "estimated_stage_time": None,
            "avg_minutes_after_doors": None,
            "confidence": "none",
            "data_points": 0,
        }


# Singleton
setlistfm_service = SetlistFMService()
