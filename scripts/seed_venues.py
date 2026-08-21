"""
Seed venue database from Ticketmaster.

Pulls all venues within 30 miles of each supported city center,
deduplicates by TM venue ID, and upserts into the venues table.

Usage:
  cd projects/platform/backend
  source venv/bin/activate
  python scripts/seed_venues.py [--dry-run]

Requires DATABASE_URL and TICKETMASTER_API_KEY in .env
"""

import asyncio
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import Base
from app.models.venue import Venue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED_CITIES = [
    {"id": "houston",    "name": "Houston",     "lat": 29.7604,  "lng": -95.3698},
    {"id": "austin",     "name": "Austin",      "lat": 30.2672,  "lng": -97.7431},
    {"id": "dallas",     "name": "Dallas",      "lat": 32.7767,  "lng": -96.7970},
    {"id": "nashville",  "name": "Nashville",   "lat": 36.1627,  "lng": -86.7816},
    {"id": "neworleans", "name": "New Orleans", "lat": 29.9511,  "lng": -90.0715},
    {"id": "atlanta",    "name": "Atlanta",     "lat": 33.7490,  "lng": -84.3880},
    {"id": "chicago",    "name": "Chicago",     "lat": 41.8781,  "lng": -87.6298},
    {"id": "newyork",    "name": "New York",    "lat": 40.7128,  "lng": -74.0060},
    {"id": "losangeles", "name": "Los Angeles", "lat": 34.0522,  "lng": -118.2437},
    {"id": "denver",     "name": "Denver",      "lat": 39.7392,  "lng": -104.9903},
    {"id": "seattle",    "name": "Seattle",     "lat": 47.6062,  "lng": -122.3321},
    {"id": "miami",      "name": "Miami",       "lat": 25.7617,  "lng": -80.1918},
]

TM_BASE = "https://app.ticketmaster.com/discovery/v2"
RADIUS_MILES = 30
PAGE_SIZE = 200  # TM max


async def fetch_tm_venues(client: httpx.AsyncClient, city: dict) -> list[dict]:
    """Fetch all music venues near a city from Ticketmaster."""
    venues = []
    page = 0

    while True:
        params = {
            "apikey": settings.TICKETMASTER_API_KEY,
            "latlong": f"{city['lat']},{city['lng']}",
            "radius": str(RADIUS_MILES),
            "unit": "miles",
            "size": PAGE_SIZE,
            "page": page,
            # Filter to music segment to reduce noise
            "segmentName": "Music",
            "keyword": "",
        }
        try:
            resp = await client.get(f"{TM_BASE}/venues.json", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"TM venues error for {city['name']} page {page}: {e}")
            break

        embedded = data.get("_embedded", {})
        page_venues = embedded.get("venues", [])
        venues.extend(page_venues)

        pagination = data.get("page", {})
        total_pages = pagination.get("totalPages", 1)
        logger.info(f"  {city['name']}: page {page+1}/{total_pages} — {len(page_venues)} venues")

        if page + 1 >= total_pages or page + 1 >= 5:  # cap at 5 pages (~1000 venues/city)
            break
        page += 1
        await asyncio.sleep(0.25)  # TM rate limit courtesy

    return venues


def normalize_tm_venue(raw: dict, city_name: str) -> dict | None:
    """Convert raw TM venue to our schema dict. Returns None if unusable."""
    venue_id = raw.get("id")
    name = raw.get("name", "").strip()
    if not venue_id or not name:
        return None

    location = raw.get("location", {})
    lat_str = location.get("latitude")
    lng_str = location.get("longitude")
    if not lat_str or not lng_str:
        return None

    try:
        lat = float(lat_str)
        lng = float(lng_str)
    except (ValueError, TypeError):
        return None

    city = raw.get("city", {}).get("name") or city_name
    state = raw.get("state", {}).get("stateCode")
    country = raw.get("country", {}).get("countryCode") or "US"
    address = raw.get("address", {}).get("line1")
    postal = raw.get("postalCode")

    url = raw.get("url")

    return {
        "id": f"tm_venue_{venue_id}",
        "source": "ticketmaster",
        "source_id": venue_id,
        "name": name,
        "city": city,
        "state": state,
        "country": country,
        "address": address,
        "zip_code": postal,
        "lat": lat,
        "lng": lng,
        "website": url,
        "phone": None,
        "is_claimed": False,
    }


async def seed(dry_run: bool = False):
    logger.info(f"Starting venue seed {'(DRY RUN)' if dry_run else ''}")

    db_url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(db_url, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    all_venues: dict[str, dict] = {}  # id → venue dict, deduped

    async with httpx.AsyncClient(timeout=30.0) as client:
        for city in SUPPORTED_CITIES:
            logger.info(f"Fetching venues for {city['name']}...")
            raw_venues = await fetch_tm_venues(client, city)
            for raw in raw_venues:
                normalized = normalize_tm_venue(raw, city["name"])
                if normalized and normalized["id"] not in all_venues:
                    all_venues[normalized["id"]] = normalized
            logger.info(f"  {city['name']}: {len(raw_venues)} raw → {len(all_venues)} total unique so far")

    logger.info(f"\nTotal unique venues to upsert: {len(all_venues)}")

    if dry_run:
        for v in list(all_venues.values())[:10]:
            logger.info(f"  Sample: {v['name']} | {v['city']}, {v['state']} | {v['lat']}, {v['lng']}")
        logger.info("Dry run complete — no DB writes.")
        return

    # Upsert in batches
    venues_list = list(all_venues.values())
    batch_size = 100
    upserted = 0

    async with SessionLocal() as session:
        for i in range(0, len(venues_list), batch_size):
            batch = venues_list[i:i + batch_size]
            stmt = pg_insert(Venue).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": stmt.excluded.name,
                    "city": stmt.excluded.city,
                    "state": stmt.excluded.state,
                    "address": stmt.excluded.address,
                    "zip_code": stmt.excluded.zip_code,
                    "lat": stmt.excluded.lat,
                    "lng": stmt.excluded.lng,
                    "website": stmt.excluded.website,
                    "updated_at": sa.text("NOW()"),
                },
            )
            await session.execute(stmt)
            upserted += len(batch)
            logger.info(f"  Upserted {upserted}/{len(venues_list)}...")

        await session.commit()

    logger.info(f"\n✅ Seed complete — {upserted} venues in DB")
    await engine.dispose()


if __name__ == "__main__":
    import sqlalchemy as sa  # noqa (needed in upsert block)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to DB")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run))
