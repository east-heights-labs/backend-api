"""
Seed venue database from OpenStreetMap (Overpass API).

Pulls music venues, bars, and live music locations from OSM across
US metro areas. Completely free, no rate limits (within reason).

OSM tags used:
  amenity=music_venue
  amenity=nightclub  (with live music indicators)
  venue=concert_hall / music_venue / club
  live_music=yes

Usage:
  cd projects/platform/backend
  source venv/bin/activate
  DATABASE_URL=<url> python scripts/seed_venues_osm.py [--dry-run] [--metro <name>]

OSM data is © OpenStreetMap contributors (ODbL license).
"""

import asyncio
import argparse
import logging
import os
import sys
import ssl
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert
import sqlalchemy as sa

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Inline Venue model to avoid config loading issues
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Float, Boolean, DateTime, Index
from datetime import datetime

class Base(DeclarativeBase): pass

class Venue(Base):
    __tablename__ = "venues"
    id = Column(String(128), primary_key=True)
    source = Column(String(32), nullable=False)
    source_id = Column(String(128), nullable=True)
    name = Column(String(256), nullable=False)
    city = Column(String(128), nullable=False)
    state = Column(String(8), nullable=True)
    country = Column(String(8), nullable=False, default="US")
    address = Column(String(256), nullable=True)
    zip_code = Column(String(16), nullable=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    website = Column(String(512), nullable=True)
    phone = Column(String(32), nullable=True)
    is_claimed = Column(Boolean, nullable=False, default=False)
    claimed_by_email = Column(String(256), nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# US Metro areas — broad coverage, not just our 12 cities
# Format: (name, lat, lng, radius_km)
US_METROS = [
    # Our 12 cities
    ("Houston",       29.7604,  -95.3698,  40),
    ("Austin",        30.2672,  -97.7431,  30),
    ("Dallas",        32.7767,  -96.7970,  40),
    ("Nashville",     36.1627,  -86.7816,  30),
    ("New Orleans",   29.9511,  -90.0715,  30),
    ("Atlanta",       33.7490,  -84.3880,  40),
    ("Chicago",       41.8781,  -87.6298,  40),
    ("New York",      40.7128,  -74.0060,  50),
    ("Los Angeles",   34.0522, -118.2437,  50),
    ("Denver",        39.7392, -104.9903,  30),
    ("Seattle",       47.6062, -122.3321,  30),
    ("Miami",         25.7617,  -80.1918,  30),
    # Extended national coverage
    ("San Francisco", 37.7749, -122.4194,  30),
    ("Portland",      45.5051, -122.6750,  25),
    ("Phoenix",       33.4484, -112.0740,  40),
    ("Las Vegas",     36.1699, -115.1398,  30),
    ("San Diego",     32.7157, -117.1611,  25),
    ("Minneapolis",   44.9778,  -93.2650,  30),
    ("Detroit",       42.3314,  -83.0458,  30),
    ("Cleveland",     41.4993,  -81.6944,  25),
    ("Columbus",      39.9612,  -82.9988,  25),
    ("Pittsburgh",    40.4406,  -79.9959,  25),
    ("Philadelphia",  39.9526,  -75.1652,  30),
    ("Boston",        42.3601,  -71.0589,  30),
    ("Washington DC", 38.9072,  -77.0369,  30),
    ("Baltimore",     39.2904,  -76.6122,  25),
    ("Charlotte",     35.2271,  -80.8431,  25),
    ("Raleigh",       35.7796,  -78.6382,  25),
    ("Richmond",      37.5407,  -77.4360,  20),
    ("Indianapolis",  39.7684,  -86.1581,  25),
    ("St. Louis",     38.6270,  -90.1994,  25),
    ("Kansas City",   39.0997,  -94.5786,  25),
    ("Oklahoma City", 35.4676,  -97.5164,  25),
    ("San Antonio",   29.4241,  -98.4936,  30),
    ("Memphis",       35.1495,  -90.0490,  25),
    ("Louisville",    38.2527,  -85.7585,  25),
    ("Cincinnati",    39.1031,  -84.5120,  25),
    ("Milwaukee",     43.0389,  -87.9065,  25),
    ("Sacramento",    38.5816, -121.4944,  25),
    ("Salt Lake City",40.7608, -111.8910,  25),
    ("Albuquerque",   35.0844, -106.6504,  20),
    ("Tucson",        32.2226, -110.9747,  20),
    ("El Paso",       31.7619, -106.4850,  20),
    ("Jacksonville",  30.3322,  -81.6557,  25),
    ("Tampa",         27.9506,  -82.4572,  25),
    ("Orlando",       28.5383,  -81.3792,  25),
    ("New Orleans",   29.9511,  -90.0715,  30),  # already in 12, broader radius ok
    ("Asheville",     35.5951,  -82.5515,  15),
    ("Boise",         43.6150, -116.2023,  20),
    ("Omaha",         41.2565,  -95.9345,  20),
    ("Des Moines",    41.5868,  -93.6250,  20),
    ("Spokane",       47.6588, -117.4260,  20),
    ("Tucson",        32.2226, -110.9747,  20),
    ("Knoxville",     35.9606,  -83.9207,  20),
    ("Chattanooga",   35.0456,  -85.3097,  15),
    ("Birmingham",    33.5186,  -86.8104,  20),
    ("Jackson MS",    32.2988,  -90.1848,  15),
    ("Little Rock",   34.7465,  -92.2896,  15),
    ("Baton Rouge",   30.4515,  -91.1871,  20),
    ("Lafayette LA",  30.2241,  -92.0198,  15),
    ("Corpus Christi",27.8006,  -97.3964,  15),
    ("Lubbock",       33.5779, -101.8552,  15),
    ("Amarillo",      35.2220, -101.8313,  15),
    ("Fort Worth",    32.7555,  -97.3308,  20),
    ("Waco",          31.5493,  -97.1467,  15),
    ("Colorado Springs",38.8339,-104.8214, 15),
    ("Fort Collins",  40.5853, -105.0844,  15),
    ("Boulder",       40.0150, -105.2705,  15),
    ("Flagstaff",     35.1983, -111.6513,  10),
    ("Santa Fe",      35.6870, -105.9378,  10),
    ("Missoula",      46.8721, -113.9940,  10),
    ("Fargo",         46.8772,  -96.7898,  10),
    ("Sioux Falls",   43.5446,  -96.7311,  10),
    ("Billings",      45.7833, -108.5007,  10),
]

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM query: music venues, nightclubs, concert halls within radius
OSM_QUERY_TEMPLATE = """
[out:json][timeout:60];
(
  node["amenity"="music_venue"](around:{radius},{lat},{lng});
  way["amenity"="music_venue"](around:{radius},{lat},{lng});
  node["amenity"="nightclub"]["live_music"="yes"](around:{radius},{lat},{lng});
  node["amenity"="bar"]["live_music"="yes"](around:{radius},{lat},{lng});
  node["venue"="concert_hall"](around:{radius},{lat},{lng});
  node["venue"="music_venue"](around:{radius},{lat},{lng});
  node["venue"="club"](around:{radius},{lat},{lng});
  way["venue"="concert_hall"](around:{radius},{lat},{lng});
  way["venue"="music_venue"](around:{radius},{lat},{lng});
);
out center tags;
"""


def normalize_osm_venue(element: dict, city_name: str, state: str = ""):
    tags = element.get("tags", {})
    name = tags.get("name", "").strip()
    if not name:
        return None

    # Get coordinates
    if element["type"] == "node":
        lat = element.get("lat")
        lng = element.get("lon")
    else:
        center = element.get("center", {})
        lat = center.get("lat")
        lng = center.get("lon")

    if not lat or not lng:
        return None

    osm_id = f"osm_{element['type'][0]}{element['id']}"

    addr_parts = []
    if tags.get("addr:housenumber") and tags.get("addr:street"):
        addr_parts.append(f"{tags['addr:housenumber']} {tags['addr:street']}")
    elif tags.get("addr:street"):
        addr_parts.append(tags["addr:street"])
    address = addr_parts[0] if addr_parts else None

    city = tags.get("addr:city") or city_name
    state_tag = tags.get("addr:state") or state
    postal = tags.get("addr:postcode")
    website = tags.get("website") or tags.get("contact:website") or tags.get("url")
    phone = tags.get("phone") or tags.get("contact:phone")

    return {
        "id": osm_id,
        "source": "osm",
        "source_id": str(element["id"]),
        "name": name,
        "city": city,
        "state": state_tag,
        "country": "US",
        "address": address,
        "zip_code": postal,
        "lat": float(lat),
        "lng": float(lng),
        "website": website[:512] if website else None,
        "phone": phone[:32] if phone else None,
        "is_claimed": False,
    }


async def fetch_osm_venues(client: httpx.AsyncClient, metro: tuple) -> list[dict]:
    name, lat, lng, radius_km = metro
    radius_m = radius_km * 1000
    query = OSM_QUERY_TEMPLATE.format(lat=lat, lng=lng, radius=radius_m)

    try:
        resp = await client.post(OVERPASS_URL, data={"data": query}, timeout=90.0)
        resp.raise_for_status()
        data = resp.json()
        elements = data.get("elements", [])
        logger.info(f"  {name}: {len(elements)} OSM elements")
        return elements
    except Exception as e:
        logger.warning(f"  {name}: OSM error — {e}")
        return []


async def seed(dry_run: bool = False, metro_filter: str = None):
    db_url_raw = os.environ.get("DATABASE_URL", "")
    if not db_url_raw or "user:password" in db_url_raw:
        logger.error("DATABASE_URL not set. Export it before running.")
        sys.exit(1)

    import re
    db_url = re.sub(r'[?&]sslmode=[^&]*', '', db_url_raw).rstrip('?')
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    engine = create_async_engine(db_url, echo=False, connect_args={"ssl": ssl_ctx})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    metros = US_METROS
    if metro_filter:
        metros = [m for m in metros if metro_filter.lower() in m[0].lower()]
        logger.info(f"Filtered to {len(metros)} metro(s) matching '{metro_filter}'")

    all_venues: dict[str, dict] = {}

    # Process metros in batches to avoid hammering Overpass
    async with httpx.AsyncClient() as client:
        for i, metro in enumerate(metros):
            logger.info(f"[{i+1}/{len(metros)}] Fetching {metro[0]}...")
            elements = await fetch_osm_venues(client, metro)

            for el in elements:
                normalized = normalize_osm_venue(el, metro[0])
                if normalized and normalized["id"] not in all_venues:
                    all_venues[normalized["id"]] = normalized

            # Polite delay between requests
            if i < len(metros) - 1:
                await asyncio.sleep(2.0)

    logger.info(f"\nTotal unique OSM venues: {len(all_venues)}")

    if dry_run:
        sample = list(all_venues.values())[:15]
        for v in sample:
            logger.info(f"  {v['name']} | {v['city']}, {v['state']} | {v['lat']:.4f},{v['lng']:.4f}")
        logger.info("Dry run — no DB writes.")
        await engine.dispose()
        return

    # Upsert in batches — skip on conflict if TM record already exists (TM has better data)
    batch_size = 100
    venues_list = list(all_venues.values())
    upserted = skipped = 0

    async with Session() as session:
        for i in range(0, len(venues_list), batch_size):
            batch = venues_list[i:i + batch_size]
            stmt = pg_insert(Venue).values(batch)
            # Only insert if not already in DB — don't overwrite TM/JamBase records
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            await session.execute(stmt)
            upserted += len(batch)
            if upserted % 500 == 0 or upserted == len(venues_list):
                logger.info(f"  Processed {upserted}/{len(venues_list)}...")
        await session.commit()

    logger.info(f"\n✅ OSM seed complete — {len(venues_list)} venues processed")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--metro", type=str, help="Filter to metros matching this string")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run, metro_filter=args.metro))
