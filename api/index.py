"""
OnStage Backend API
Flask app served by Vercel as a serverless function.

Routes:
  GET /api/health
  GET /api/events?lat=&lng=&radius=&date=
  GET /api/stagetime?artist=<name>
"""

from flask import Flask, jsonify, request
from datetime import date as date_type
from urllib.request import urlopen, Request as URLRequest
from urllib.parse import urlencode
import json
import os
import math

app = Flask(__name__)

# ---------------------------------------------------------------------------
# CORS — allow dashboard.eastheightslabs.com to call the API with credentials
# ---------------------------------------------------------------------------
from flask_cors import CORS
CORS(app,
     origins=[
         "https://dashboard.eastheightslabs.com",
         "http://localhost:3000",
         "https://venue-dashboard-*.vercel.app",
     ],
     supports_credentials=True,
     allow_headers=["Content-Type", "Authorization", "X-Admin-Secret"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

# ---------------------------------------------------------------------------
# Database + venue routes
# Vercel runs api/index.py as a top-level module (not a package).
# Insert the api/ directory into sys.path for absolute sibling imports.
# ---------------------------------------------------------------------------
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(__file__))

from db import init_db_pool
from venue_routes import venue_bp

init_db_pool(app)
app.register_blueprint(venue_bp)

TICKETMASTER_KEY = os.environ.get("TICKETMASTER_API_KEY", "")
SETLIST_FM_KEY = os.environ.get("SETLIST_FM_API_KEY", "")
JAMBASE_KEY = os.environ.get("JAMBASE_API_KEY", "")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def miles_to_km(miles: float) -> int:
    return max(1, int(miles * 1.60934))

def haversine_miles(lat1, lng1, lat2, lng2) -> float:
    """Approximate distance in miles between two lat/lng points."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

# ---------------------------------------------------------------------------
# Mock data (fallback when no API key)
# ---------------------------------------------------------------------------

MOCK_EVENTS = [
    {"id": "mock_001", "source": "mock", "title": "The Suffers at White Oak Music Hall",
     "doors_time": "19:00:00", "status": "ok", "type": "Concert", "popularity": 0.6,
     "venue": {"id": "mock_venue_001", "name": "White Oak Music Hall", "lat": 29.7726, "lng": -95.3989, "city": "Houston", "url": "https://whiteoakmusichall.com"},
     "headliner": {"id": "mock_artist_001", "name": "The Suffers", "uri": None}, "ticket_url": "https://whiteoakmusichall.com"},
    {"id": "mock_002", "source": "mock", "title": "Leon Bridges at House of Blues Houston",
     "doors_time": "20:00:00", "status": "ok", "type": "Concert", "popularity": 0.85,
     "venue": {"id": "mock_venue_002", "name": "House of Blues Houston", "lat": 29.7512, "lng": -95.3668, "city": "Houston", "url": "https://houseofblues.com/houston"},
     "headliner": {"id": "mock_artist_002", "name": "Leon Bridges", "uri": None}, "ticket_url": "https://houseofblues.com/houston"},
    {"id": "mock_003", "source": "mock", "title": "Khruangbin at 713 Music Hall",
     "doors_time": "20:30:00", "status": "ok", "type": "Concert", "popularity": 0.9,
     "venue": {"id": "mock_venue_003", "name": "713 Music Hall", "lat": 29.7358, "lng": -95.3677, "city": "Houston", "url": "https://713musichall.com"},
     "headliner": {"id": "mock_artist_003", "name": "Khruangbin", "uri": None}, "ticket_url": "https://713musichall.com"},
    {"id": "mock_004", "source": "mock", "title": "Gary Clark Jr. at Warehouse Live",
     "doors_time": "19:30:00", "status": "ok", "type": "Concert", "popularity": 0.8,
     "venue": {"id": "mock_venue_004", "name": "Warehouse Live", "lat": 29.7468, "lng": -95.3580, "city": "Houston", "url": "https://warehouselive.com"},
     "headliner": {"id": "mock_artist_004", "name": "Gary Clark Jr.", "uri": None}, "ticket_url": "https://warehouselive.com"},
    {"id": "mock_005", "source": "mock", "title": "Turnpike Troubadours at Stubb's",
     "doors_time": "19:00:00", "status": "ok", "type": "Concert", "popularity": 0.82,
     "venue": {"id": "mock_venue_005", "name": "Stubb's Waller Creek Amphitheater", "lat": 30.2669, "lng": -97.7333, "city": "Austin", "url": "https://stubbsaustin.com"},
     "headliner": {"id": "mock_artist_005", "name": "Turnpike Troubadours", "uri": None}, "ticket_url": "https://stubbsaustin.com"},
    {"id": "mock_006", "source": "mock", "title": "Brandi Carlile at ACL Live",
     "doors_time": "19:30:00", "status": "ok", "type": "Concert", "popularity": 0.88,
     "venue": {"id": "mock_venue_006", "name": "ACL Live at the Moody Theater", "lat": 30.2641, "lng": -97.7499, "city": "Austin", "url": "https://acl-live.com"},
     "headliner": {"id": "mock_artist_006", "name": "Brandi Carlile", "uri": None}, "ticket_url": "https://acl-live.com"},
    {"id": "mock_007", "source": "mock", "title": "Charley Crockett at Emo's Austin",
     "doors_time": "20:00:00", "status": "ok", "type": "Concert", "popularity": 0.75,
     "venue": {"id": "mock_venue_007", "name": "Emo's Austin", "lat": 30.2610, "lng": -97.7404, "city": "Austin", "url": "https://emosaustin.com"},
     "headliner": {"id": "mock_artist_007", "name": "Charley Crockett", "uri": None}, "ticket_url": "https://emosaustin.com"},
    {"id": "mock_008", "source": "mock", "title": "Black Pumas at The Parish",
     "doors_time": "20:30:00", "status": "ok", "type": "Concert", "popularity": 0.78,
     "venue": {"id": "mock_venue_008", "name": "The Parish", "lat": 30.2688, "lng": -97.7404, "city": "Austin", "url": "https://theparishaustin.com"},
     "headliner": {"id": "mock_artist_008", "name": "Black Pumas", "uri": None}, "ticket_url": "https://theparishaustin.com"},
]

# ---------------------------------------------------------------------------
# Ticketmaster
# ---------------------------------------------------------------------------


def _next_day(date_str):
    from datetime import date, timedelta
    d = date.fromisoformat(date_str)
    return (d + timedelta(days=1)).isoformat()

def fetch_ticketmaster_events(lat, lng, radius_miles, req_date):
    """Fetch real events from Ticketmaster Discovery API."""
    params = {
        "apikey": TICKETMASTER_KEY,
        "latlong": f"{lat},{lng}",
        "radius": int(radius_miles),
        "unit": "miles",
        # Fetch a 2-day window around the requested date to catch all timezones,
        # then filter by localDate in Python
        # Fetch upcoming events; filter by localDate in Python to avoid UTC confusion
        # Events at 6pm CT = 11pm UTC = technically next day UTC
        # Ignore client date — use server today to avoid timezone issues
        "startDateTime": f"{req_date}T00:00:00Z",
        "endDateTime": f"{_next_day(req_date)}T05:59:59Z",
        "size": 200,  # max results per page
        "sort": "distance,asc",
    }
    url = "https://app.ticketmaster.com/discovery/v2/events.json?" + urlencode(params)
    req = URLRequest(url, headers={"Accept": "application/json"})

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            raw_events = data.get("_embedded", {}).get("events", [])
            normalized = [normalize_tm_event(e, lat, lng) for e in raw_events]
            # Filter: correct date, not cancelled, not a voucher/access pass
            return [e for e in normalized if (
                (not e.get("date") or e.get("date") == req_date) and
                e.get("status") != "cancelled" and
                not any(skip in e.get("title", "").lower() for skip in [
                    "not a concert ticket", "food & beverage", "fast lane",
                    "parking", "vip package", "meet & greet"
                ])
            )]
    except Exception as ex:
        print(f"Ticketmaster error: {ex}")
        return []




# ---------------------------------------------------------------------------
# JamBase
# ---------------------------------------------------------------------------

JAMBASE_BASE = "https://api.data.jambase.com/v3"

def fetch_jambase_events(lat, lng, radius_miles, req_date):
    """Fetch events from JamBase Data API v3.
    Returns normalized events in our internal schema.
    JamBase is strong on jam bands, indie, and smaller venues TM misses.
    """
    if not JAMBASE_KEY:
        return []

    params = {
        "geoLatitude": lat,
        "geoLongitude": lng,
        "eventDateFrom": req_date,
        "eventDateTo": req_date,
        "perPage": 50,
        "page": 1,
    }
    url = f"{JAMBASE_BASE}/events?" + urlencode(params)
    req = URLRequest(url, headers={
        "Authorization": f"Bearer {JAMBASE_KEY}",
        "Accept": "application/json",
        "User-Agent": "OnStage/1.0",
    })

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            raw_events = data.get("events", [])
            normalized = [normalize_jambase_event(e, lat, lng) for e in raw_events]
            return [e for e in normalized if e is not None]
    except Exception as ex:
        print(f"JamBase error: {ex}")
        return []


def clean_venue_display_name(name: str) -> str:
    """Strip city abbreviation and name suffixes from venue display names.
    e.g. 'Moody Center ATX' -> 'Moody Center'
         'House of Blues Houston' -> 'House of Blues'
    """
    n = name.strip()
    # Strip city abbreviation suffixes (case-insensitive)
    for abbr in [" ATX", " HTX", " NYC", " LA", " CHI"]:
        if n.upper().endswith(abbr.upper()):
            n = n[:-len(abbr)].strip()
            break
    # Strip full city name suffixes
    for city in [" Houston", " Austin", " Dallas", " Nashville", " Chicago",
                 " New York", " Los Angeles", " Denver", " Seattle"]:
        if n.endswith(city):
            n = n[:-len(city)].strip()
            break
    return n


def normalize_jambase_event(raw, user_lat, user_lng):
    """Normalize JamBase event to our internal schema."""
    venue_raw = raw.get("location", {})
    geo = venue_raw.get("geo", {})
    try:
        vlat = float(geo.get("latitude", 0))
        vlng = float(geo.get("longitude", 0))
    except (ValueError, TypeError):
        return None

    if not vlat or not vlng:
        return None

    address = venue_raw.get("address", {})
    city_name = address.get("addressLocality", "")
    state = address.get("addressRegion", {}).get("alternateName", "")

    # Start time
    start_dt = raw.get("startDate", "")  # "2026-08-18T19:00:00"
    event_date = start_dt[:10] if start_dt else ""
    event_time = start_dt[11:19] if len(start_dt) > 10 else None

    # Artists — JamBase uses 'performers' array
    performers = raw.get("performers", [])
    headliner = performers[0] if performers else {}
    headliner_name = headliner.get("name", "") or raw.get("name", "")
    # Strip venue from name if format is "Artist at Venue"
    if " at " in headliner_name and not performers:
        headliner_name = headliner_name.split(" at ")[0].strip()

    # Category — JamBase type field
    event_type = raw.get("@type", "Concert")
    category = "music" if event_type in ("Concert", "Festival") else "other"

    # Offers / tickets
    offers = raw.get("offers", [])
    ticket_url = offers[0].get("url") if offers else raw.get("url", "")

    return {
        "id": f"jb_{raw.get('identifier', '').replace('jambase:', '')}",
        "source": "jambase",
        "source_id": raw.get("identifier", ""),
        "title": raw.get("name", ""),
        "date": event_date,
        "doors_time": event_time,
        "status": raw.get("eventStatus", "scheduled").replace("EventScheduled", "scheduled"),
        "type": event_type,
        "venue": {
            "id": f"jb_venue_{venue_raw.get('identifier','').replace('jambase:','')}",
            "name": clean_venue_display_name(venue_raw.get("name", "")),
            "lat": vlat,
            "lng": vlng,
            "city": f"{city_name}, {state}" if state else city_name,
            "url": venue_raw.get("url", ""),
        },
        "headliner": {
            "id": f"jb_artist_{headliner.get('identifier','').replace('jambase:','')}",
            "name": headliner_name,
            "uri": headliner.get("url"),
        },
        "ticket_url": ticket_url,
        "min_price": None,
        "popularity": 0,
        "distance_miles": haversine_miles(user_lat, user_lng, vlat, vlng),
        "category": category,
        "venue_website": get_venue_website(venue_raw.get("name", "")),
    }


# Known venue websites — grows over time
VENUE_WEBSITES = {
    # Houston
    "alley theatre": "https://www.alleytheatre.org",
    "alley theater": "https://www.alleytheatre.org",
    "white oak music hall": "https://whiteoakmusichall.com",
    "house of blues houston": "https://www.houseofblues.com/houston",
    "house of blues": "https://www.houseofblues.com/houston",
    "bronze peacock": "https://www.houseofblues.com/houston",
    "713 music hall": "https://713musichall.com",
    "warehouse live": "https://warehouselive.com",
    "bayou music center": "https://www.livenation.com/venue/KovZpZAEkleA/bayou-music-center-events",
    "smart financial centre": "https://www.smartfinancialcentre.net",
    "houston improv": "https://improv.com/houston",
    "improv comedy club": "https://improv.com/houston",
    "punch line houston": "https://punchlinehoustoncomedy.com",
    "the emerald theatre": "https://emeraldtheatrehouston.com",
    "daikin park": "https://www.mlb.com/astros/ballpark",
    "toyota center": "https://www.toyotacenter.com",
    "scout bar": "https://www.scoutbar.com",
    "main street crossing": "https://www.mainstreetcrossing.com",
    "old quarter acoustic cafe": "https://oldquarterhouston.com",
    "the rustic": "https://therustic.com/houston",
    # Austin
    "stubb's": "https://stubbsaustin.com",
    "acl live": "https://acl-live.com",
    "3ten": "https://acl-live.com/3ten",
    "emo's": "https://emosaustin.com",
    "the parish": "https://theparishaustin.com",
    "moody center": "https://moodycenteratx.com",
    "antone's": "https://antonesnightclub.com",
    "antones": "https://antonesnightclub.com",
    "austin city limits live": "https://acl-live.com",
    "the mohawk": "https://mohawkaustin.com",
    "come and take it live": "https://comeandtakeitlive.com",
    "cap city comedy": "https://capcitycomedy.com",
    "hole in the wall": "https://holeinthewallaustin.com",
    "germania insurance amphitheater": "https://www.germaniainsuranceamphitheater.com",
    "q2 stadium": "https://www.austinfc.com/q2-stadium",
    "zach theatre": "https://www.zachtheatre.org",
    "gruene hall": "https://gruenehall.com",
    "the concourse project": "https://theconcourseproject.com",
    "radio east": "https://radioeast.com",
}

def get_venue_website(venue_name: str):
    def _norm(s):
        # Strip both straight and curly apostrophes, slashes, hyphens
        return (s.lower().strip()
                .replace("\u2019", "")  # right single quotation mark '
                .replace("\u2018", "")  # left single quotation mark '
                .replace("'", "")
                .replace("/", " ")
                .replace("-", " "))
    key = _norm(venue_name)
    for k, v in VENUE_WEBSITES.items():
        nk = _norm(k)
        if nk in key or key in nk:
            return v
    return None

MUSIC_GENRES = {
    "rock", "pop", "hip-hop", "rap", "country", "jazz", "blues", "r&b",
    "soul", "electronic", "dance", "folk", "indie", "metal", "classical",
    "reggae", "latin", "alternative", "punk", "music",
}

def _classify_event(raw: dict) -> str:
    """Return 'music' or 'other' based on Ticketmaster classifications."""
    classifications = raw.get("classifications", [{}])
    for c in classifications:
        segment = c.get("segment", {}).get("name", "").lower()
        genre = c.get("genre", {}).get("name", "").lower()
        if segment == "music" or genre in MUSIC_GENRES:
            return "music"
    return "other"

def normalize_tm_event(raw, user_lat, user_lng):
    """Normalize a Ticketmaster event to our internal schema."""
    venues = raw.get("_embedded", {}).get("venues", [{}])
    venue = venues[0] if venues else {}
    attractions = raw.get("_embedded", {}).get("attractions", [{}])
    headliner = attractions[0] if attractions else {}

    loc = venue.get("location", {})
    try:
        vlat = float(loc.get("latitude", 0))
        vlng = float(loc.get("longitude", 0))
    except (ValueError, TypeError):
        vlat, vlng = user_lat, user_lng

    start = raw.get("dates", {}).get("start", {})
    local_time = start.get("localTime", "")  # "20:00:00"
    local_date = start.get("localDate", "")

    # Price range
    price_ranges = raw.get("priceRanges", [])
    min_price = price_ranges[0].get("min") if price_ranges else None

    return {
        "id": f"tm_{raw.get('id')}",
        "source": "ticketmaster",
        "source_id": raw.get("id"),
        "title": raw.get("name", ""),
        "date": local_date,
        "doors_time": local_time if local_time else None,
        "status": raw.get("dates", {}).get("status", {}).get("code", "onsale"),
        "type": "Concert",
        "venue": {
            "id": f"tm_venue_{venue.get('id')}",
            "name": venue.get("name", ""),
            "lat": vlat,
            "lng": vlng,
            "city": venue.get("city", {}).get("name"),
            "state": venue.get("state", {}).get("stateCode"),
            "url": venue.get("url"),
        },
        "headliner": {
            "id": f"tm_artist_{headliner.get('id', '')}",
            "name": headliner.get("name", raw.get("name", "")),
            "uri": headliner.get("url"),
        },
        "ticket_url": raw.get("url"),
        "min_price": min_price,
        "popularity": raw.get("score", 0),
        "distance_miles": haversine_miles(user_lat, user_lng, vlat, vlng),
        "category": _classify_event(raw),
        "venue_website": get_venue_website(venue.get("name", "")),
    }


# --- Stage time report storage (Postgres — persistent across Vercel cold starts) ---
# Written to fan_stagetime_reports table via migration 004.
# Legacy /tmp storage removed 2026-08-26.

def _get_reports_for_artist(artist_name: str) -> list:
    """Fetch fan-submitted stage time reports for an artist from Postgres."""
    key = artist_name.lower().strip()
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("""
                SELECT artist_name, venue_name, city,
                       event_date, stage_time
                FROM fan_stagetime_reports
                WHERE lower(artist_name) = %s
                ORDER BY submitted_at DESC
                LIMIT 50
            """, (key,))
            rows = cur.fetchall()
        results = []
        for r in rows:
            stage_time_str = str(r[4])[:5] if r[4] else None  # 'HH:MM'
            if not stage_time_str:
                continue
            try:
                h, m = int(stage_time_str.split(':')[0]), int(stage_time_str.split(':')[1])
            except (ValueError, IndexError):
                continue
            results.append({
                'artist_name': r[0],
                'venue_name': r[1],
                'city': r[2],
                'date': r[3].isoformat() if r[3] else '',
                'stage_time': stage_time_str,
                'stage_minutes': h * 60 + m,
                'source': 'fan',
            })
        return results
    except Exception as exc:
        app.logger.warning(f'_get_reports_for_artist DB error: {exc}')
        return []

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "OnStage API",
        "ticketmaster": bool(TICKETMASTER_KEY),
        "jambase": bool(JAMBASE_KEY),
        "setlistfm": bool(SETLIST_FM_KEY),
    })


@app.route("/api/events")
def events():
    try:
        lat = float(request.args.get("lat", 29.7604))
        lng = float(request.args.get("lng", -95.3698))
        radius = float(request.args.get("radius", 25.0))  # default 25 miles
        req_date = request.args.get("date", (__import__('datetime').datetime.utcnow() - __import__('datetime').timedelta(hours=6)).strftime('%Y-%m-%d'))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid parameters"}), 400

    # Fetch from all available sources in parallel (sequential here — Vercel serverless)
    all_raw = []
    sources_used = []

    if TICKETMASTER_KEY:
        tm_events = fetch_ticketmaster_events(lat, lng, radius, req_date)
        all_raw.extend(tm_events)
        sources_used.append("ticketmaster")

    if JAMBASE_KEY:
        jb_events = fetch_jambase_events(lat, lng, radius, req_date)
        all_raw.extend(jb_events)
        sources_used.append("jambase")

    # Venue-created events from DB
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("""
                SELECT ve.id, ve.title, ve.event_date, ve.doors_time, ve.stage_time,
                       ve.ticket_url, ve.price_min, ve.price_max, ve.image_url,
                       v.id AS venue_id, v.name, v.lat, v.lng, v.city
                FROM venue_events ve
                JOIN venues v ON v.id = ve.venue_id
                WHERE ve.event_date = %s
                  AND ve.is_cancelled = FALSE
                  AND ve.is_hidden = FALSE
                  AND (
                    (v.lat - %s)*(v.lat - %s) + (v.lng - %s)*(v.lng - %s)
                    < (%s / 69.0)^2
                  )
            """, (req_date, lat, lat, lng, lng, radius))
            venue_rows = cur.fetchall()
        for r in venue_rows:
            vlat, vlng = float(r[11]) if r[11] else lat, float(r[12]) if r[12] else lng
            all_raw.append({
                "id": f"venue_{r[0]}",
                "source": "venue",
                "source_id": str(r[0]),
                "title": r[1],
                "date": r[2].isoformat() if r[2] else req_date,
                "doors_time": str(r[3]) if r[3] else None,
                "stage_time": str(r[4]) if r[4] else None,
                "status": "scheduled",
                "type": "Concert",
                "venue": {
                    "id": str(r[9]),
                    "name": r[10],
                    "lat": vlat,
                    "lng": vlng,
                    "city": r[13],
                },
                "headliner": {"id": None, "name": r[1], "uri": None},
                "ticket_url": r[5],
                "min_price": float(r[6]) if r[6] else None,
                "popularity": 0.5,
                "distance_miles": haversine_miles(lat, lng, vlat, vlng),
                "category": "music",
                "venue_website": get_venue_website(r[10]),
                "image_url": r[8],
            })
        if venue_rows:
            sources_used.append("venue")
    except Exception as _ve:
        # DB not configured or query error — skip silently
        app.logger.warning(f"Venue events DB query skipped: {_ve}")

    if not sources_used:
        event_list = [{**e, "date": req_date} for e in MOCK_EVENTS]
        sources_used = ["mock"]
    else:
        # Dedup: one entry per venue+date. Prefer TM > JamBase > others.
        # Sort so TM comes first (higher priority in seen dict)
        source_priority = {"ticketmaster": 0, "jambase": 1}
        all_raw.sort(key=lambda e: source_priority.get(e.get("source", ""), 9))

        def normalize_venue(name):
            """Normalize venue name for dedup — strip city suffixes, punctuation."""
            n = (name or "").lower().strip()
            # Strip curly and straight apostrophes (JamBase uses curly)
            n = n.replace("\u2019", "").replace("\u2018", "").replace("'", "")
            # Strip state/city abbreviation suffixes: "Toyota Center - TX" -> "Toyota Center"
            for abbr in [" - tx", " - ca", " - ny", " - fl", " - il", " - wa", " - co",
                         " atx", " htx", " nyc", " la", " chi"]:
                if n.endswith(abbr):
                    n = n[:-len(abbr)].strip()
            # Strip common city suffixes: "at House of Blues Houston" -> "at House of Blues"
            for city in [" houston", " austin", " dallas", " nashville", " chicago",
                         " new york", " los angeles", " denver", " seattle"]:
                if n.endswith(city):
                    n = n[:-len(city)].strip()
            # Normalize punctuation
            n = n.replace(".", "").replace("-", " ")
            return n

        def normalize_artist(name):
            """Normalize artist name for dedup."""
            n = (name or "").lower().strip()
            n = n.replace("\u2019", "").replace("\u2018", "").replace("'", "")
            n = n.replace(".", "").replace("-", " ")
            return n

        seen = {}       # key -> event
        seen_keys = {}  # event id -> set of keys (for reverse lookup)

        for e in all_raw:
            date_key = e.get("date", "")
            artist = normalize_artist(e.get("headliner", {}).get("name", ""))
            venue = normalize_venue(e.get("venue", {}).get("name", ""))
            event_id = e.get("id", "")

            artist_key = f"artist|{artist}|{date_key}" if artist else None
            venue_key = f"venue|{venue}|{date_key}" if venue else None

            # Skip if either key already seen (deduplicates across name variants)
            if (artist_key and artist_key in seen) or (venue_key and venue_key in seen):
                continue

            # Register both keys so future dupes are caught by either
            if artist_key:
                seen[artist_key] = e
            if venue_key:
                seen[venue_key] = e

        # Deduplicated events are values — but we may have stored same event twice
        # under both keys. Collect unique events by id.
        unique = {}
        for e in seen.values():
            eid = e.get("id", id(e))
            if eid not in unique:
                unique[eid] = e
        event_list = list(unique.values())

    # Overlay venue-confirmed stage times from venue_stage_reports.
    # A venue operator has explicitly set a stage time for this event;
    # treat it as authoritative and surface it in the event response.
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("""
                SELECT event_id, artist_name, stage_time, doors_time
                FROM venue_stage_reports
                WHERE event_date = %s
                  AND status IN ('pending', 'confirmed')
                ORDER BY submitted_at DESC
            """, (req_date,))
            report_rows = cur.fetchall()

        # Build lookup: event_id -> report, and artist_name -> report (fallback)
        reports_by_event_id = {}
        reports_by_artist = {}
        for row in report_rows:
            eid, aname, stime, dtime = row
            stage_str = str(stime)[:5] if stime else None
            doors_str = str(dtime)[:5] if dtime else None
            if eid and eid not in reports_by_event_id:
                reports_by_event_id[eid] = (stage_str, doors_str)
            if aname:
                key = aname.lower().strip()
                if key not in reports_by_artist:
                    reports_by_artist[key] = (stage_str, doors_str)

        for e in event_list:
            report = reports_by_event_id.get(e.get("id")) or \
                     reports_by_artist.get((e.get("headliner") or {}).get("name", "").lower().strip())
            if report:
                stage_str, doors_str = report
                if stage_str:
                    e["stage_time"] = stage_str
                    e["estimated_stage_time"] = stage_str
                    e["stage_time_source"] = "venue_confirmed"
                if doors_str and not e.get("doors_time"):
                    e["doors_time"] = doors_str
    except Exception as _sr:
        import traceback
        app.logger.error(f"Stage report overlay FAILED: {_sr}\n{traceback.format_exc()}")
        overlay_error = str(_sr)
    else:
        overlay_error = None

    # Sort by start time
    event_list.sort(key=lambda e: e.get("doors_time") or "99:99:99")

    return jsonify({
        "date": req_date,
        "location": {"lat": lat, "lng": lng},
        "radius_miles": radius,
        "count": len(event_list),
        "sources": sources_used,
        "events": event_list,
        "_debug_overlay_error": overlay_error,
    })


@app.route("/api/stagetime")
def stagetime():
    """
    Stage time intelligence for an artist.

    Strategy (in priority order):
    1. Fan-submitted reports for this artist (most specific, highest weight)
    2. Setlist.fm startTime field across last 3 pages of setlists (60 shows)

    Returns average stage time + confidence based on data point count.
    Also returns last 10 history records for display.
    """
    artist_name = request.args.get("artist", "").strip()
    if not artist_name:
        return jsonify({"error": "artist parameter required"}), 400

    # --- Source 1: Fan-submitted reports ---
    fan_reports = _get_reports_for_artist(artist_name)
    fan_data_points = []
    for r in fan_reports:
        try:
            parts = r["stage_time"].split(":")
            h, m = int(parts[0]), int(parts[1])
            fan_data_points.append({
                "date": r.get("date", ""),
                "stage_time": r["stage_time"],
                "stage_minutes_from_midnight": h * 60 + m,
                "venue_name": r.get("venue_name"),
                "city": r.get("city"),
                "state": None,
                "source": "fan",
                "setlist_url": None,
            })
        except Exception:
            continue

    # --- Source 2: Setlist.fm ---
    setlist_data_points = []
    artist_display_name = artist_name
    mbid = None

    if SETLIST_FM_KEY:
        # Step 1: Resolve artist to mbid with name similarity validation
        # Reject if returned name shares no significant words with search query
        # (prevents "Jason Schmidt" matching "Jason Loewith" etc.)
        try:
            encoded = artist_name.replace(" ", "%20")
            req = URLRequest(
                f"https://api.setlist.fm/rest/1.0/search/artists?artistName={encoded}&sort=relevance&p=1",
                headers={"x-api-key": SETLIST_FM_KEY, "Accept": "application/json"}
            )
            with urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
                artists = data.get("artist", [])
                skip_words = {"the", "a", "an", "and", "or", "of", "in", "at", "tour"}
                # Common first names — matching only on these is not sufficient
                common_first_names = {"jason", "john", "james", "michael", "david", "chris",
                                      "mark", "matt", "ryan", "brian", "eric", "adam", "tyler"}
                search_words = set(artist_name.lower().split())
                search_sig = search_words - skip_words
                # Last word of search query (usually last name) must match
                search_last = artist_name.lower().split()[-1] if artist_name.strip() else ""
                for candidate in artists[:3]:
                    candidate_name = candidate.get("name", "")
                    candidate_words = set(candidate_name.lower().split())
                    candidate_sig = candidate_words - skip_words
                    shared = search_sig & candidate_sig
                    # Reject if only shared word is a common first name
                    if shared and not (shared <= common_first_names):
                        mbid = candidate.get("mbid")
                        artist_display_name = candidate_name
                        break
                    # Also accept exact last-name match
                    if search_last and search_last in candidate_words and search_last not in common_first_names:
                        mbid = candidate.get("mbid")
                        artist_display_name = candidate_name
                        break
        except Exception:
            pass

        # Step 2: Fetch up to 3 pages of setlists (60 shows)
        if mbid:
            all_setlists = []
            for page in range(1, 4):
                try:
                    req = URLRequest(
                        f"https://api.setlist.fm/rest/1.0/artist/{mbid}/setlists?p={page}",
                        headers={"x-api-key": SETLIST_FM_KEY, "Accept": "application/json"}
                    )
                    with urlopen(req, timeout=8) as resp:
                        data = json.loads(resp.read())
                        page_setlists = data.get("setlist", [])
                        all_setlists.extend(page_setlists)
                        # Stop if we got fewer than 20 (last page)
                        if len(page_setlists) < 20:
                            break
                except Exception:
                    break

            for s in all_setlists:
                event_date = s.get("eventDate")   # "DD-MM-YYYY"
                start_time = s.get("startTime")   # "HH:MM" or None
                if not event_date or not start_time:
                    continue
                try:
                    t_parts = start_time.split(":")
                    hour, minute = int(t_parts[0]), int(t_parts[1])
                    # Sanity check — ignore times outside 15:00–03:00 (shows don't start at 6 AM)
                    mins = hour * 60 + minute
                    if not (15 * 60 <= mins <= 24 * 60 + 3 * 60):
                        continue
                    d, m, y = event_date.split("-")
                    venue = s.get("venue", {})
                    city = venue.get("city", {})
                    setlist_data_points.append({
                        "date": f"{y}-{m}-{d}",
                        "stage_time": f"{hour:02d}:{minute:02d}",
                        "stage_minutes_from_midnight": mins,
                        "venue_name": venue.get("name"),
                        "city": city.get("name"),
                        "state": city.get("stateCode"),
                        "source": "setlistfm",
                        "setlist_url": s.get("url"),
                    })
                except Exception:
                    continue

    # --- Merge and compute estimate ---
    # Fan reports weighted 2x (more reliable — fan was there)
    weighted_minutes = []
    all_history = []

    for r in fan_data_points:
        weighted_minutes.extend([r["stage_minutes_from_midnight"]] * 2)
        all_history.append(r)

    for r in setlist_data_points:
        weighted_minutes.append(r["stage_minutes_from_midnight"])
        all_history.append(r)

    # Sort history newest first
    all_history.sort(key=lambda x: x.get("date", ""), reverse=True)

    # Use only last 10 data points for the estimate (recency bias)
    recent = sorted(
        fan_data_points + setlist_data_points,
        key=lambda x: x.get("date", ""),
        reverse=True
    )[:10]

    recent_minutes = []
    for r in recent:
        weight = 2 if r.get("source") == "fan" else 1
        recent_minutes.extend([r["stage_minutes_from_midnight"]] * weight)

    estimated = None
    confidence = "none"
    total_data_points = len(fan_data_points) + len(setlist_data_points)

    if recent_minutes:
        avg = sum(recent_minutes) / len(recent_minutes)
        h, m = int(avg // 60) % 24, int(avg % 60)
        estimated = f"{h:02d}:{m:02d}"
        if total_data_points >= 5:
            confidence = "high"
        elif total_data_points >= 2:
            confidence = "medium"
        else:
            confidence = "low"

    return jsonify({
        "artist_name": artist_display_name,
        "mbid": mbid,
        "history": all_history[:10],
        "estimated_stage_time": estimated,
        "confidence": confidence,
        "data_points": total_data_points,
        "fan_reports": len(fan_data_points),
        "setlistfm_points": len(setlist_data_points),
    })


@app.route("/api/stagetime/report", methods=["POST"])
def submit_stagetime_report():
    """
    Fan-submitted stage time report.
    POST body: { artist_name, venue_name, city, date, stage_time (HH:MM), fan_uuid (optional) }

    Writes to fan_stagetime_reports table in Postgres (migration 004).
    Previously wrote to /tmp/stagetime_reports.json — removed 2026-08-26 because
    Vercel wipes /tmp on every cold start, silently losing all submissions.
    """
    import datetime as _dt
    data = request.get_json() or {}

    artist_name = (data.get("artist_name") or "").strip()
    venue_name = (data.get("venue_name") or "").strip() or None
    city = (data.get("city") or "").strip() or None
    date_str = (data.get("date") or "").strip() or None
    stage_time = (data.get("stage_time") or "").strip()
    fan_uuid = (data.get("fan_uuid") or "").strip() or None

    if not artist_name or not stage_time:
        return jsonify({"error": "artist_name and stage_time required"}), 400

    # Validate time format HH:MM
    try:
        parts = stage_time.split(":")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        stage_time = f"{hour:02d}:{minute:02d}"
    except (ValueError, IndexError):
        return jsonify({"error": "stage_time must be HH:MM format"}), 400

    # Parse optional date
    parsed_date = None
    if date_str:
        try:
            parsed_date = _dt.date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD format"}), 400

    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO fan_stagetime_reports
                  (artist_name, venue_name, city, event_date, stage_time, fan_uuid)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (artist_name, venue_name, city, parsed_date, stage_time, fan_uuid))
            db.commit()
    except Exception as exc:
        app.logger.error(f"submit_stagetime_report DB error: {exc}")
        return jsonify({"error": "Failed to save report"}), 500

    return jsonify({"ok": True, "message": f"Stage time reported for {artist_name}"})


@app.route("/api/stagetime/search")
def search_artists():
    """Search Setlist.fm for artists by name (for the report form autocomplete)."""
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"artists": []})

    if not SETLIST_FM_KEY:
        return jsonify({"artists": []})

    try:
        encoded = query.replace(" ", "%20")
        req = URLRequest(
            f"https://api.setlist.fm/rest/1.0/search/artists?artistName={encoded}&sort=relevance&p=1",
            headers={"x-api-key": SETLIST_FM_KEY, "Accept": "application/json"}
        )
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            artists = data.get("artist", [])[:10]
            return jsonify({"artists": [{"name": a["name"], "mbid": a["mbid"]} for a in artists]})
    except Exception:
        return jsonify({"artists": []})

