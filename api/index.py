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
import threading
import time
import jwt as _pyjwt  # PyJWT — RS256 Apple token verification

app = Flask(__name__)



# ---------------------------------------------------------------------------
# CORS — allow dashboard.eastheightslabs.com to call the API with credentials
# flask-cors 6.x does not support a callable for origins, so we use an
# after_request hook to set headers dynamically based on the request origin.
# ---------------------------------------------------------------------------
def _is_allowed_origin(origin: str) -> bool:
    if not origin:
        return False
    if origin in ("https://dashboard.eastheightslabs.com", "http://localhost:3000"):
        return True
    if origin.startswith("https://venue-dashboard-") and origin.endswith(".vercel.app"):
        return True
    return False

@app.after_request
def _apply_cors(response):
    from flask import request as _req
    origin = _req.headers.get("Origin", "")
    if _is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Secret"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response

@app.route("/api/venue/preflight", methods=["OPTIONS"])
def _global_options():
    """Catch-all OPTIONS handler — Flask returns 200 with after_request headers."""
    return "", 200

# Handle OPTIONS for all /api/venue/* routes
@app.before_request
def _handle_options():
    from flask import request as _req
    if _req.method == "OPTIONS":
        from flask import make_response
        resp = make_response("", 200)
        return resp

# ---------------------------------------------------------------------------
# Database + venue routes
# Vercel runs api/index.py as a top-level module (not a package).
# Insert the api/ directory into sys.path for absolute sibling imports.
# ---------------------------------------------------------------------------
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(__file__))

# ---------------------------------------------------------------------------
# Rate limiting — imported AFTER sys.path is patched so api/limiter.py is
# findable. Applied only to auth endpoints (login, accept-invite) — NOT
# globally. iOS /api/events gets hammered legitimately.
# ---------------------------------------------------------------------------
from limiter import limiter
limiter.init_app(app)

from db import init_db_pool
from venue_routes import venue_bp

init_db_pool(app)
app.register_blueprint(venue_bp)

TICKETMASTER_KEY = os.environ.get("TICKETMASTER_API_KEY", "")
SETLIST_FM_KEY = os.environ.get("SETLIST_FM_API_KEY", "")
JAMBASE_KEY = os.environ.get("JAMBASE_API_KEY", "")

# ---------------------------------------------------------------------------
# Upstash Redis cache — REST API, no redis client needed
# REDIS_URL format: rediss://default:<token>@<host>:6379
# We parse it to build the REST base URL and bearer token.
# ---------------------------------------------------------------------------
_REDIS_URL = os.environ.get("REDIS_URL", "")
_UPSTASH_BASE = ""
_UPSTASH_TOKEN = ""

if _REDIS_URL.startswith("rediss://"):
    # rediss://default:<token>@<host>:6379
    try:
        _without_scheme = _REDIS_URL[len("rediss://"):]
        _creds, _hostport = _without_scheme.rsplit("@", 1)
        _token = _creds.split(":", 1)[1]  # after "default:"
        _host = _hostport.split(":")[0]   # strip :6379
        _UPSTASH_BASE = f"https://{_host}"
        _UPSTASH_TOKEN = _token
    except Exception:
        pass

EVENT_CACHE_TTL = 8 * 3600  # 8 hours in seconds
RADIUS_BUCKETS = [5, 10, 25]

def _bucket_radius(radius: float) -> int:
    """Snap radius to nearest standard bucket for cache key consistency."""
    return min(RADIUS_BUCKETS, key=lambda b: abs(b - radius))

def _cache_key(lat: float, lng: float, radius: float, date: str) -> str:
    return f"events:{round(lat, 2)}:{round(lng, 2)}:{_bucket_radius(radius)}:{date}"

def _cache_get(key: str):
    """GET from Upstash REST API. Returns parsed value or None."""
    if not _UPSTASH_BASE:
        return None
    try:
        url = f"{_UPSTASH_BASE}/get/{key}"
        req = URLRequest(url, headers={"Authorization": f"Bearer {_UPSTASH_TOKEN}"})
        with urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            raw = data.get("result")
            return json.loads(raw) if raw else None
    except Exception as e:
        app.logger.debug(f"Cache GET failed for {key}: {e}")
        return None

def _cache_set(key: str, value, ttl: int = EVENT_CACHE_TTL):
    """SET with EX in Upstash REST API. Fire-and-forget."""
    if not _UPSTASH_BASE:
        return
    try:
        payload = json.dumps(["SET", key, json.dumps(value), "EX", ttl]).encode()
        req = URLRequest(
            _UPSTASH_BASE,
            data=payload,
            headers={
                "Authorization": f"Bearer {_UPSTASH_TOKEN}",
                "Content-Type": "application/json",
            }
        )
        with urlopen(req, timeout=3):
            pass
    except Exception as e:
        app.logger.debug(f"Cache SET failed for {key}: {e}")

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
            return [e for e in normalized if e is not None and e.get("distance_miles", 999) <= radius_miles]
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


def _normalize_jb_event_status(raw_status: str) -> str:
    """Map JamBase schema.org eventStatus URI to our internal status string.
    JamBase values: EventScheduled, EventCancelled, EventRescheduled, EventPostponed, EventMovedOnline.
    We normalize to: scheduled | cancelled | rescheduled."""
    s = raw_status.lower()
    if "cancelled" in s:
        return "cancelled"
    if "rescheduled" in s or "postponed" in s:
        return "rescheduled"
    return "scheduled"


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

    # Artists — JamBase uses 'performer' array (schema.org field name)
    performers_raw = raw.get("performer", [])
    # Sort by x-performanceRank ascending; unranked go to end
    performers_sorted = sorted(performers_raw, key=lambda p: p.get("x-performanceRank") or 999)
    headliner = performers_sorted[0] if performers_sorted else {}
    headliner_name = headliner.get("name", "") or raw.get("name", "")
    # Strip venue from name if format is "Artist at Venue" and no performers listed
    if " at " in headliner_name and not performers_sorted:
        headliner_name = headliner_name.split(" at ")[0].strip()

    # Normalize performers to consistent schema
    jb_performers = [
        {
            "rank": p.get("x-performanceRank") or (i + 1),
            "name": p.get("name", ""),
            "is_headliner": bool(p.get("x-isHeadliner", False)),
            # genre may be a string, list, or None depending on JamBase response.
            # Normalize to [str] in all cases to avoid iOS Codable type mismatch.
            "genres": (lambda g: g if isinstance(g, list) else ([g] if isinstance(g, str) and g else []))(p.get("genre")),
            "url": p.get("url"),
        }
        for i, p in enumerate(performers_sorted)
        if p.get("name")
    ]

    # Category — JamBase type field
    event_type = raw.get("@type", "Concert")
    category = "music" if event_type in ("Concert", "Festival") else "other"
    # Secondary name-based check: override to "other" when JamBase tags a non-music
    # event as Concert (e.g. comedy shows at music venues).
    # Uses pre-compiled word-boundary patterns to avoid substring false matches.
    if category == "music":
        _ev_name_lower = raw.get("name", "").lower()
        if any(p.search(_ev_name_lower) for p in _NON_MUSIC_PATTERNS):
            category = "other"

    # Offers / tickets
    offers = raw.get("offers", [])
    ticket_url = offers[0].get("url") if offers else raw.get("url", "")
    # Price — JamBase primary offer has priceSpecification with minPrice/maxPrice
    primary_offer = next((o for o in offers if o.get("category") == "ticketingLinkPrimary"), offers[0] if offers else {})
    price_spec = primary_offer.get("priceSpecification", {})
    jb_min_price = float(price_spec["minPrice"]) if price_spec.get("minPrice") else None
    jb_max_price = float(price_spec["maxPrice"]) if price_spec.get("maxPrice") else None

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
        "min_price": jb_min_price,
        "max_price": jb_max_price,
        "performers": jb_performers,
        "popularity": 0,
        "distance_miles": haversine_miles(user_lat, user_lng, vlat, vlng),
        "category": category,
        "venue_website": get_venue_website(venue_raw.get("name", "")),
        # x-subtitle: opener or supporting act string (e.g. "with Zakk Sabbath")
        # Present only when JamBase has supporting act info; pass through as-is.
        "subtitle": raw.get("x-subtitle") or None,
        # Hero image — JamBase top-level 'image' field; x-promoImage is consistently empty
        "image_url": raw.get("image") or None,
        # Free show flag — JamBase boolean; False when not present
        "is_accessible_for_free": bool(raw.get("isAccessibleForFree", False)),
        # Venue capacity — JamBase location.maximumAttendeeCapacity; None when absent
        "venue_capacity": venue_raw.get("maximumAttendeeCapacity") or None,
        # Event status normalization — JamBase uses schema.org event status URIs
        # For rescheduled: startDate = new date, previousStartDate = original date
        "event_status": _normalize_jb_event_status(raw.get("eventStatus", "")),
        "rescheduled_date": start_dt[:10] if _normalize_jb_event_status(raw.get("eventStatus", "")) == "rescheduled" else None,
        "original_date": raw.get("previousStartDate") if _normalize_jb_event_status(raw.get("eventStatus", "")) == "rescheduled" else None,
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

import re as _re

NON_MUSIC_KEYWORDS = [
    # Sports
    "volleyball", "basketball", "football", "baseball", "soccer", "hockey",
    "tennis", "golf", "wrestling", "boxing", "mma", "ufc", "gymnastics",
    "swimming", "lacrosse", "softball", "rugby", "cricket",
    # Performing arts (non-music)
    "comedy", "comedian", "stand-up", "standup", "theater", "theatre", "ballet",
    "opera", "circus", "magic show",
    # Events
    "conference", "expo", "convention", "seminar",
    # Visual arts / attractions
    "museum", "gallery", "exhibition", "art show", "art fair",
    "escape room", "trivia", "bingo",
]
# Pre-compile word-boundary patterns for each keyword to avoid substring false matches
# e.g. "track" should not match "soundtrack" or "tracking"
_NON_MUSIC_PATTERNS = [
    _re.compile(r'\b' + _re.escape(kw) + r'\b', _re.IGNORECASE)
    for kw in NON_MUSIC_KEYWORDS
]

def _classify_venue_event_title(title: str) -> str:
    """Classify a venue-submitted event as music or other based on title keywords.
    Uses word-boundary matching to avoid substring false positives.
    Defaults to music since most venue dashboard events are concerts.
    Note: 'family', 'kids', 'children' intentionally excluded — kids concerts are music.
    Long-term fix: add category column to venue_events table (P2 backlog)."""
    if not title:
        return "music"
    for pattern in _NON_MUSIC_PATTERNS:
        if pattern.search(title):
            return "other"
    return "music"

# Segments that are explicitly non-music. Everything else — including "undefined"
# and empty — defaults to music. TM frequently omits classification for legitimate
# concerts; OnStage is music-first, so misclassifying a concert as "other" is
# worse UX than the reverse.
NON_MUSIC_SEGMENTS = {"sports", "arts & theatre", "film", "miscellaneous"}

def _classify_event(raw: dict) -> str:
    """Return 'music' or 'other' based on Ticketmaster classifications."""
    classifications = raw.get("classifications", [{}])
    for c in classifications:
        segment = c.get("segment", {}).get("name", "").lower()
        genre = c.get("genre", {}).get("name", "").lower()
        if segment in NON_MUSIC_SEGMENTS:
            return "other"
        if segment == "music" or genre in MUSIC_GENRES:
            return "music"
    # No explicit non-music segment found — default to music
    return "music"

def normalize_tm_event(raw, user_lat, user_lng):
    """Normalize a Ticketmaster event to our internal schema."""
    venues = raw.get("_embedded", {}).get("venues", [{}])
    venue = venues[0] if venues else {}
    attractions = raw.get("_embedded", {}).get("attractions", [{}])
    headliner = attractions[0] if attractions else {}

    # Normalize performers array — TM attractions[], position implies rank (index 0 = headliner)
    # TM has no genre per-attraction at event level; omit genres for TM performers
    tm_performers = [
        {
            "rank": i + 1,
            "name": a.get("name", ""),
            "is_headliner": i == 0,
            "genres": [],
            "url": a.get("url"),
        }
        for i, a in enumerate(attractions)
        if a.get("name")
    ]

    loc = venue.get("location", {})
    try:
        vlat = float(loc.get("latitude", 0))
        vlng = float(loc.get("longitude", 0))
    except (ValueError, TypeError):
        vlat, vlng = user_lat, user_lng

    start = raw.get("dates", {}).get("start", {})
    local_time = start.get("localTime", "")  # "20:00:00"
    local_date = start.get("localDate", "")

    # Price range — TM provides priceRanges array with min and max per tier
    price_ranges = raw.get("priceRanges", [])
    min_price = price_ranges[0].get("min") if price_ranges else None
    max_price = price_ranges[0].get("max") if price_ranges else None

    # Hero image — TM provides images[] with width/height; pick the largest by pixel area
    tm_images = raw.get("images", [])
    image_url = None
    if tm_images:
        largest = max(tm_images, key=lambda i: (i.get("width") or 0) * (i.get("height") or 0))
        image_url = largest.get("url") or None

    # Event status — TM uses dates.status.code: onsale, offsale, cancelled, rescheduled, postponed
    tm_status_code = raw.get("dates", {}).get("status", {}).get("code", "onsale").lower()
    if tm_status_code == "cancelled":
        event_status = "cancelled"
    elif tm_status_code in ("rescheduled", "postponed"):
        event_status = "rescheduled"
    else:
        event_status = "scheduled"

    # For rescheduled events:
    # - local_date = new/current date (dates.start.localDate)
    # - original_date = original date before reschedule (dates.initialStartDate.localDate, not always present)
    original_date = None
    if event_status == "rescheduled":
        original_date = raw.get("dates", {}).get("initialStartDate", {}).get("localDate")  # may be None

    return {
        "id": f"tm_{raw.get('id')}",
        "source": "ticketmaster",
        "source_id": raw.get("id"),
        "title": raw.get("name", ""),
        "date": local_date,
        "doors_time": local_time if local_time else None,
        "status": raw.get("dates", {}).get("status", {}).get("code", "onsale"),
        "event_status": event_status,       # scheduled | cancelled | rescheduled
        "rescheduled_date": local_date if event_status == "rescheduled" else None,  # new date
        "original_date": original_date,       # original date before reschedule (TM: initialStartDate, may be None)
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
        "max_price": max_price,
        "image_url": image_url,
        "is_accessible_for_free": False,  # TM has no free show flag
        "venue_capacity": None,             # TM events endpoint has no venue capacity
        "performers": tm_performers,
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

    # Bounds validation
    if not (-90 <= lat <= 90):
        return jsonify({"error": "lat must be between -90 and 90"}), 400
    if not (-180 <= lng <= 180):
        return jsonify({"error": "lng must be between -180 and 180"}), 400
    if not (1 <= radius <= 100):
        radius = max(1, min(radius, 100))  # clamp silently rather than error

    # --- Cache check ---
    cache_key = _cache_key(lat, lng, radius, req_date)
    cached = _cache_get(cache_key)
    if cached is not None:
        app.logger.info(f"Cache HIT: {cache_key} ({len(cached)} events)")
        return jsonify({
            "date": req_date,
            "location": {"lat": lat, "lng": lng},
            "radius_miles": radius,
            "count": len(cached),
            "sources": ["cache"],
            "cached": True,
            "events": cached,
        })

    # --- Cache miss: fetch live ---
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
                "category": _classify_venue_event_title(r[1]),
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
                SELECT event_id, artist_name, stage_time
                FROM venue_stage_reports
                WHERE event_date = %s
                  AND status IN ('pending', 'confirmed')
                ORDER BY submitted_at DESC
            """, (req_date,))
            report_rows = cur.fetchall()

        # Build lookup: event_id -> stage_time, and artist_name -> stage_time (fallback)
        reports_by_event_id = {}
        reports_by_artist = {}
        for row in report_rows:
            eid, aname, stime = row
            stage_str = str(stime)[:5] if stime else None
            if not stage_str:
                continue
            if eid and eid not in reports_by_event_id:
                reports_by_event_id[eid] = stage_str
            if aname:
                key = aname.lower().strip()
                if key not in reports_by_artist:
                    reports_by_artist[key] = stage_str

        for e in event_list:
            stage_str = reports_by_event_id.get(e.get("id")) or \
                        reports_by_artist.get((e.get("headliner") or {}).get("name", "").lower().strip())
            if stage_str:
                e["stage_time"] = stage_str
                e["estimated_stage_time"] = stage_str
                e["stage_time_source"] = "venue_confirmed"
    except Exception as _sr:
        app.logger.error(f"Stage report overlay failed: {_sr}")

    # Sort by start time
    event_list.sort(key=lambda e: e.get("doors_time") or "99:99:99")

    # Write to cache (non-blocking — skip if no Redis configured)
    _cache_set(cache_key, event_list)

    return jsonify({
        "date": req_date,
        "location": {"lat": lat, "lng": lng},
        "radius_miles": radius,
        "count": len(event_list),
        "sources": sources_used,
        "cached": False,
        "events": event_list,
    })


# ---------------------------------------------------------------------------
# /api/prefetch — Vercel cron job (3x/day) populates event cache for all cities
# ---------------------------------------------------------------------------

PREFETCH_SECRET = os.environ.get("PREFETCH_SECRET", "")

# Cities to pre-fetch — matches JamBase METRO_MAP
# 16 cities × 2 dates × 31 days = 992 JamBase calls/month (free tier limit: 1,000)
# Cron runs daily at 5 PM CT (22:00 UTC) — caches today + tomorrow
# To expand: upgrade JamBase to paid tier first
PREFETCH_CITIES = [
    # Original 12
    ("houston",      29.7604,  -95.3698),
    ("austin",       30.2672,  -97.7431),
    ("dallas",       32.7767,  -96.7970),
    ("nashville",    36.1627,  -86.7816),
    ("neworleans",   29.9511,  -90.0715),
    ("atlanta",      33.7490,  -84.3880),
    ("chicago",      41.8781,  -87.6298),
    ("newyork",      40.7128,  -74.0060),
    ("losangeles",   34.0522,  -118.2437),
    ("denver",       39.7392,  -104.9903),
    ("seattle",      47.6062,  -122.3321),
    ("miami",        25.7617,  -80.1918),
    # Expansion cities (added 2026-08-29; capped at 16 for free tier)
    ("portland",     45.5051,  -122.6750),
    ("sanfrancisco", 37.7749,  -122.4194),
    ("minneapolis",  44.9778,  -93.2650),
    ("boston",       42.3601,  -71.0589),
]
PREFETCH_RADIUS = 10.0


def _prefetch_city_date(city_label, lat, lng, date_str):
    """Fetch + cache events for one city on one date. Returns a result dict."""
    try:
        tm_events = fetch_ticketmaster_events(lat, lng, PREFETCH_RADIUS, date_str) if TICKETMASTER_KEY else []
        jb_events = fetch_jambase_events(lat, lng, PREFETCH_RADIUS, date_str) if JAMBASE_KEY else []

        all_raw = tm_events + jb_events
        source_priority = {"ticketmaster": 0, "jambase": 1}
        all_raw.sort(key=lambda e: source_priority.get(e.get("source", ""), 9))

        def _nv(name):
            n = (name or "").lower().strip()
            n = n.replace("\u2019", "").replace("\u2018", "").replace("'", "")
            for s in [" - tx", " - ca", " - ny", " - fl", " - il", " - wa", " - co",
                      " atx", " htx", " nyc", " la", " chi"]:
                if n.endswith(s): n = n[:-len(s)].strip()
            for city_sfx in [" houston", " austin", " dallas", " nashville", " chicago",
                             " new york", " los angeles", " denver", " seattle"]:
                if n.endswith(city_sfx): n = n[:-len(city_sfx)].strip()
            return n.replace(".", "").replace("-", " ")

        def _na(name):
            n = (name or "").lower().strip()
            return n.replace("\u2019", "").replace("\u2018", "").replace("'", "").replace(".", "").replace("-", " ")

        seen = {}
        for e in all_raw:
            artist = _na((e.get("headliner") or {}).get("name", ""))
            venue = _nv((e.get("venue") or {}).get("name", ""))
            ak = f"artist|{artist}|{date_str}" if artist else None
            vk = f"venue|{venue}|{date_str}" if venue else None
            if (ak and ak in seen) or (vk and vk in seen):
                continue
            if ak: seen[ak] = e
            if vk: seen[vk] = e

        unique = {}
        for e in seen.values():
            eid = e.get("id", id(e))
            if eid not in unique:
                unique[eid] = e
        merged = sorted(unique.values(), key=lambda e: e.get("doors_time") or "99:99:99")

        key = _cache_key(lat, lng, PREFETCH_RADIUS, date_str)
        _cache_set(key, list(merged))

        return {"city": city_label, "date": date_str, "status": "ok",
                "events": len(merged), "tm": len(tm_events), "jb": len(jb_events)}
    except Exception as ex:
        app.logger.error(f"Prefetch error {city_label}/{date_str}: {ex}")
        return {"city": city_label, "date": date_str, "status": "error", "error": str(ex)}


@app.route("/api/prefetch", methods=["GET", "POST"])
def prefetch():
    """
    Pre-fetch event cache for all 22 cities, today + tomorrow.
    Runs daily at 5 PM CT (22:00 UTC) via Vercel cron.
    Auth: Vercel cron sends GET with x-vercel-cron=1 header; manual callers may POST with x-prefetch-secret.
    Both GET and POST are accepted so Vercel's cron scheduler doesn't get a 405.
    """
    is_vercel_cron = request.headers.get("x-vercel-cron") == "1"
    secret_ok = not PREFETCH_SECRET or request.headers.get("x-prefetch-secret") == PREFETCH_SECRET
    if not is_vercel_cron and not secret_ok:
        return jsonify({"error": "Unauthorized"}), 401

    if not _UPSTASH_BASE:
        return jsonify({"error": "Redis not configured"}), 503

    import datetime as _dt
    today = _dt.date.today().isoformat()
    tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
    dates = [today, tomorrow]

    results = []
    for city_label, lat, lng in PREFETCH_CITIES:
        for d in dates:
            results.append(_prefetch_city_date(city_label, lat, lng, d))

    ok = [r for r in results if r.get("status") == "ok"]
    errors = [r for r in results if r.get("status") == "error"]
    total_events = sum(r.get("events", 0) for r in ok)

    app.logger.info(f"Prefetch complete: {len(ok)}/{len(results)} ok, {total_events} events cached")
    return jsonify({
        "status": "complete",
        "dates": dates,
        "city_date_pairs": len(results),
        "successful": len(ok),
        "errors": len(errors),
        "total_events_cached": total_events,
        "results": results,
    })


# ---------------------------------------------------------------------------
# /api/search/venues and /api/search/artists
# ---------------------------------------------------------------------------

SEARCH_CITIES = [
    # Original 12
    # supplemental_venue_ids: TM venue IDs force-included in results regardless of distance.
    # Used for large metro venues just outside the 25mi radius (e.g. The Woodlands for Houston).
    {"id": "houston",      "name": "Houston",       "lat": 29.7604,  "lng": -95.3698,
     "supplemental_venue_ids": [
         "KovZpZAE6k6A",   # The Cynthia Woods Mitchell Pavilion sponsored by Huntsman
         "KovZ917AJfi",    # Event Center at The Cynthia Woods Mitchell Pavilion
     ]},
    {"id": "austin",       "name": "Austin",        "lat": 30.2672,  "lng": -97.7431},
    {"id": "dallas",       "name": "Dallas",        "lat": 32.7767,  "lng": -96.7970},
    {"id": "nashville",    "name": "Nashville",     "lat": 36.1627,  "lng": -86.7816},
    {"id": "neworleans",   "name": "New Orleans",   "lat": 29.9511,  "lng": -90.0715},
    {"id": "atlanta",      "name": "Atlanta",       "lat": 33.7490,  "lng": -84.3880},
    {"id": "chicago",      "name": "Chicago",       "lat": 41.8781,  "lng": -87.6298},
    {"id": "newyork",      "name": "New York",      "lat": 40.7128,  "lng": -74.0060},
    {"id": "losangeles",   "name": "Los Angeles",   "lat": 34.0522,  "lng": -118.2437},
    {"id": "denver",       "name": "Denver",        "lat": 39.7392,  "lng": -104.9903},
    {"id": "seattle",      "name": "Seattle",       "lat": 47.6062,  "lng": -122.3321},
    {"id": "miami",        "name": "Miami",         "lat": 25.7617,  "lng": -80.1918},
    # Expansion cities (added 2026-08-29; capped at 16 for free tier)
    {"id": "portland",     "name": "Portland",      "lat": 45.5051,  "lng": -122.6750},
    {"id": "sanfrancisco", "name": "San Francisco", "lat": 37.7749,  "lng": -122.4194},
    {"id": "minneapolis",  "name": "Minneapolis",   "lat": 44.9778,  "lng": -93.2650},
    {"id": "boston",       "name": "Boston",        "lat": 42.3601,  "lng": -71.0589},
]


def _tm_keyword_search(keyword, lat, lng, start_dt, end_dt):
    """Search TM events by keyword near a lat/lng."""
    import time as _time
    if not TICKETMASTER_KEY:
        return []
    params = {
        "apikey": TICKETMASTER_KEY,
        "keyword": keyword,
        "latlong": f"{lat},{lng}",
        "radius": "30",
        "unit": "miles",
        "startDateTime": start_dt,
        "endDateTime": end_dt,
        "size": "50",
        "sort": "date,asc",
    }
    url = "https://app.ticketmaster.com/discovery/v2/events.json?" + urlencode(params)
    try:
        req = URLRequest(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            _time.sleep(0.12)  # 120ms between TM calls — stay under 5 req/s limit
            return data.get("_embedded", {}).get("events", [])
    except Exception as ex:
        app.logger.warning(f"TM keyword search error ({lat},{lng}): {ex}")
        _time.sleep(0.5)  # back off longer on error
        return []


def _search_date_window(date_str=None, single_day=False):
    import datetime as _dt
    start = _dt.date.fromisoformat(date_str) if date_str else _dt.date.today()
    # When single_day=True (artist search scoped to a specific night), search only that date.
    # When False (venue search), use a 30-day window to surface upcoming events.
    end = start if single_day else start + _dt.timedelta(days=30)
    return f"{start.isoformat()}T00:00:00Z", f"{end.isoformat()}T23:59:59Z"


@app.route("/api/search/venues")
def search_venues():
    """Search venues by name — DB first, then TM supplement."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"query": q, "count": 0, "venues": []})

    # Strip leading articles so "The Ryman" matches "Ryman Auditorium"
    _articles = ("the ", "a ", "an ")
    q_search = q.lower()
    for _art in _articles:
        if q_search.startswith(_art):
            q_search = q_search[len(_art):]
            break

    date_str = request.args.get("date")
    # When a specific date is requested, search TM for that day only (single_day=True).
    # When no date, use 30-day window to surface upcoming events.
    start_dt, end_dt = _search_date_window(date_str, single_day=bool(date_str))
    venues = []
    seen_ids = set()
    seen_names = set()  # name-based dedup across phases

    # Phase 1: DB search
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, name, city, state, lat, lng, address FROM venues "
                "WHERE lower(name) LIKE %s ORDER BY length(name) LIMIT 50",
                (f"%{q_search}%",)
            )
            rows = cur.fetchall()
            for row in rows:
                vid, vname, city, state, lat, lng, address = row
                city_display = f"{city}, {state}" if state else city
                venues.append({
                    "venue_id": vid,
                    "venue_name": vname,
                    "city": city_display,
                    "lat": float(lat) if lat else 0,
                    "lng": float(lng) if lng else 0,
                    "address": address,
                    "next_event": None,
                })
                seen_ids.add(vid)
                seen_names.add(vname.lower())
    except Exception as ex:
        app.logger.warning(f"Venue search DB error: {ex}")

    # Phase 2: TM supplement if fewer than 10 DB results
    if len(venues) < 10 and TICKETMASTER_KEY:
        all_raw = []
        for city in SEARCH_CITIES:
            events = _tm_keyword_search(q, city["lat"], city["lng"], start_dt, end_dt)
            for raw in events:
                all_raw.append((city["name"], raw))

        seen_tm = set()
        for city_name, raw in all_raw:
            embedded = raw.get("_embedded", {})
            venue_list = embedded.get("venues", [{}])
            venue_raw = venue_list[0] if venue_list else {}
            venue_id = venue_raw.get("id")
            if not venue_id or venue_id in seen_tm:
                continue
            if q_search not in venue_raw.get("name", "").lower():
                continue
            our_id = f"tm_venue_{venue_id}"
            if our_id in seen_ids:
                continue
            tm_venue_name = venue_raw.get("name", "").lower()
            if tm_venue_name in seen_names:
                continue
            seen_tm.add(venue_id)
            seen_names.add(tm_venue_name)
            location = venue_raw.get("location", {})
            try:
                lat = float(location.get("latitude", 0))
                lng = float(location.get("longitude", 0))
            except (ValueError, TypeError):
                continue
            city = venue_raw.get("city", {}).get("name", "")
            state = venue_raw.get("state", {}).get("stateCode", "")
            attractions = embedded.get("attractions", [])
            headliner = attractions[0].get("name", raw.get("name", "")) if attractions else raw.get("name", "")
            dates = raw.get("dates", {}).get("start", {})
            event_date = dates.get("localDate")
            # When a specific date was requested, only populate next_event if the
            # TM event is on that exact date. Venue still appears in results either
            # way — row just has no artist line when there's no show that night.
            if date_str and event_date != date_str:
                next_event = None
            else:
                next_event = {
                    "title": raw.get("name", ""),
                    "date": event_date,
                    "doors_time": dates.get("localTime"),
                    "headliner": headliner,
                    "ticket_url": raw.get("url"),
                    "event_id": f"tm_{raw.get('id')}",
                }
            venues.append({
                "venue_id": our_id,
                "venue_name": venue_raw.get("name", ""),
                "city": f"{city}, {state}" if state else city,
                "lat": lat,
                "lng": lng,
                "address": venue_raw.get("address", {}).get("line1"),
                "next_event": next_event,
            })
            seen_ids.add(our_id)

    return jsonify({"query": q, "count": len(venues), "venues": venues})


# In-memory cache for artist search results.
# Key: (query_lower, date_str). Value: (timestamp, result_list)
# LIMITATION: Vercel serverless spins up multiple independent instances.
# This cache is NOT shared across instances — each cold start gets an empty dict.
# It protects against rapid repeated searches within one warm instance only.
# For true cross-instance caching, replace with Upstash Redis (same pattern as event cache).
_artist_search_cache: dict = {}
_ARTIST_CACHE_TTL = 300  # 5 minutes


@app.route("/api/search/artists")
def search_artists_route():
    """Search for an artist's upcoming shows across all supported cities."""
    import time as _time
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"query": q, "count": 0, "shows": []})

    date_str = request.args.get("date") or ""
    cache_key = (q.lower(), date_str)

    # Check cache
    cached = _artist_search_cache.get(cache_key)
    if cached:
        ts, shows = cached
        if _time.time() - ts < _ARTIST_CACHE_TTL:
            return jsonify({"query": q, "count": len(shows), "shows": shows, "cached": True})
        else:
            del _artist_search_cache[cache_key]

    # Artist search is always scoped to the specific date when provided
    # (user is searching who is playing tonight / on their selected night)
    start_dt, end_dt = _search_date_window(date_str or None, single_day=bool(date_str))

    # ---------------------------------------------------------------------------
    # Step 1: Try attraction ID lookup for better supporting-act coverage.
    # Resolve artist name → TM attraction ID → query events by ID (global).
    # This surfaces shows where the artist is a supporting act, not just headliner.
    # Fallback to keyword search if attraction lookup fails or finds no results.
    # ---------------------------------------------------------------------------
    attraction_id = None
    if TICKETMASTER_KEY:
        try:
            _att_params = urlencode({
                "apikey": TICKETMASTER_KEY,
                "keyword": q,
                "size": 5,
            })
            _att_url = f"https://app.ticketmaster.com/discovery/v2/attractions.json?{_att_params}"
            _att_req = URLRequest(_att_url, headers={"Accept": "application/json"})
            with urlopen(_att_req, timeout=8) as _att_resp:
                _att_data = json.loads(_att_resp.read())
            _att_list = _att_data.get("_embedded", {}).get("attractions", [])
            if _att_list:
                _top = _att_list[0]
                _att_name = _top.get("name", "").lower().strip()
                _q_norm = q.lower().strip()
                # Name match guard: query must appear in attraction name or vice versa.
                # Prevents blindly using an unrelated top result.
                if _q_norm in _att_name or _att_name in _q_norm:
                    attraction_id = _top.get("id")
        except Exception as _att_ex:
            app.logger.warning(f"Attraction lookup failed for '{q}': {_att_ex}")

    all_raw = []

    if attraction_id:
        # Step 2: Fetch events by attraction ID — global, no city loop needed.
        # size=50 to capture touring artists with many dates.
        try:
            _ev_params = urlencode({
                "apikey": TICKETMASTER_KEY,
                "attractionId": attraction_id,
                "startDateTime": start_dt,
                "endDateTime": end_dt,
                "size": 50,
            })
            _ev_url = f"https://app.ticketmaster.com/discovery/v2/events.json?{_ev_params}"
            _ev_req = URLRequest(_ev_url, headers={"Accept": "application/json"})
            with urlopen(_ev_req, timeout=10) as _ev_resp:
                _ev_data = json.loads(_ev_resp.read())
            _ev_list = _ev_data.get("_embedded", {}).get("events", [])
            # Filter to supported cities by distance (25mi radius) or supplemental venue IDs.
            # supplemental_venue_ids: force-include specific out-of-radius venues per city
            # (e.g. Cynthia Woods Mitchell Pavilion for Houston at 28mi).
            for _raw in _ev_list:
                _venues = _raw.get("_embedded", {}).get("venues", [{}])
                _venue_raw = _venues[0] if _venues else {}
                _venue_id = _venue_raw.get("id", "")
                _loc = _venue_raw.get("location", {})
                try:
                    _vlat = float(_loc.get("latitude", 0))
                    _vlng = float(_loc.get("longitude", 0))
                except (ValueError, TypeError):
                    continue
                if not _vlat or not _vlng:
                    continue
                for _city in SEARCH_CITIES:
                    _supp = _city.get("supplemental_venue_ids", [])
                    _in_radius = haversine_miles(_city["lat"], _city["lng"], _vlat, _vlng) <= 25
                    _is_supplemental = _venue_id in _supp
                    if _in_radius or _is_supplemental:
                        all_raw.append((_city["name"], _raw))
                        break  # Only add once even if near multiple cities
        except Exception as _ev_ex:
            app.logger.warning(f"Attraction event fetch failed for id={attraction_id}: {_ev_ex}")
            attraction_id = None  # Fall through to keyword search

    if not attraction_id or not all_raw:
        # Keyword search fallback — used when attraction lookup fails or finds nothing.
        for city in SEARCH_CITIES:
            events = _tm_keyword_search(q, city["lat"], city["lng"], start_dt, end_dt)
            for raw in events:
                all_raw.append((city["name"], raw))

    seen_ids = set()
    shows = []
    for city_name, raw in all_raw:
        event_id = raw.get("id")
        if not event_id or event_id in seen_ids:
            continue
        seen_ids.add(event_id)

        classifications = raw.get("classifications", [{}])
        segment = classifications[0].get("segment", {}).get("name", "").lower() if classifications else ""
        # Use denylist: only exclude explicitly non-music segments.
        # "Undefined" and empty pass through — TM omits classification for real concerts.
        if segment in NON_MUSIC_SEGMENTS:
            continue

        embedded = raw.get("_embedded", {})
        venues_list = embedded.get("venues", [{}])
        venue_raw = venues_list[0] if venues_list else {}
        location = venue_raw.get("location", {})
        try:
            lat = float(location.get("latitude", 0))
            lng = float(location.get("longitude", 0))
        except (ValueError, TypeError):
            continue

        attractions = embedded.get("attractions", [])
        headliner = attractions[0].get("name", raw.get("name", "")) if attractions else raw.get("name", "")
        dates = raw.get("dates", {}).get("start", {})
        city = venue_raw.get("city", {}).get("name", "")
        state = venue_raw.get("state", {}).get("stateCode", "")

        shows.append({
            "event_id": f"tm_{event_id}",
            "title": raw.get("name", ""),
            "headliner": headliner,
            "date": dates.get("localDate"),
            "doors_time": dates.get("localTime"),
            "time_tbd": dates.get("timeTBA", False),
            "ticket_url": raw.get("url"),
            "venue": {
                "id": f"tm_venue_{venue_raw.get('id')}",
                "name": venue_raw.get("name", ""),
                "lat": lat,
                "lng": lng,
                "city": f"{city}, {state}" if state else city,
                "address": venue_raw.get("address", {}).get("line1"),
            },
            "city_name": city_name,
        })

    shows.sort(key=lambda s: s.get("date") or "9999-99-99")
    # Store in cache
    _artist_search_cache[cache_key] = (_time.time(), shows)
    # Evict old entries if cache grows large
    if len(_artist_search_cache) > 200:
        oldest_key = min(_artist_search_cache, key=lambda k: _artist_search_cache[k][0])
        del _artist_search_cache[oldest_key]
    return jsonify({"query": q, "count": len(shows), "shows": shows})


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
    recent_setlists = []  # populated below if Setlist.fm fetch succeeds
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

            # Extract recent setlists with song lists (Build 20 — surface on iOS detail page)
            # Pull from the same all_setlists fetch — no additional API calls.
            recent_setlists = []
            for s in all_setlists:
                if len(recent_setlists) >= 3:
                    break
                event_date_raw = s.get("eventDate")  # "DD-MM-YYYY"
                if not event_date_raw:
                    continue
                # Flatten all sets into a single song name list
                songs = []
                for st in s.get("sets", {}).get("set", []):
                    for song in st.get("song", []):
                        name = song.get("name", "").strip()
                        if name:
                            songs.append(name)
                if not songs:  # skip stubs with no song data
                    continue
                try:
                    d, m, y = event_date_raw.split("-")
                    venue = s.get("venue", {})
                    city = venue.get("city", {})
                    recent_setlists.append({
                        "date": f"{y}-{m}-{d}",
                        "venue_name": venue.get("name"),
                        "city": city.get("name"),
                        "state": city.get("stateCode"),
                        "songs": songs,
                        "setlist_url": s.get("url"),
                    })
                except Exception:
                    continue

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

    # Outlier rejection: drop points more than 90 min from median
    # Protects against single-user manipulation and accidental wrong submissions
    def reject_outliers(points):
        if len(points) < 3:
            return points  # not enough data to reject outliers
        mins_list = [r["stage_minutes_from_midnight"] for r in points]
        mins_list.sort()
        median = mins_list[len(mins_list) // 2]
        return [r for r in points if abs(r["stage_minutes_from_midnight"] - median) <= 90]

    recent = reject_outliers(recent)

    recent_minutes = []
    for r in recent:
        weight = 2 if r.get("source") == "fan" else 1
        recent_minutes.extend([r["stage_minutes_from_midnight"]] * weight)

    estimated = None
    confidence = "none"
    heuristic = False
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
    else:
        # Fallback: billing-position heuristic.
        # Headliners typically take the stage 75-90 min after doors.
        # We don't have doors time here, so return a generic estimate
        # that the iOS client can combine with the event's doors_time.
        # Confidence = none signals "heuristic only, no real data."
        estimated = None  # iOS will apply its own heuristic using doors_time
        confidence = "none"
        heuristic = True

    return jsonify({
        "artist_name": artist_display_name,
        "mbid": mbid,
        "history": all_history[:10],
        "estimated_stage_time": estimated,
        "confidence": confidence,
        "data_points": total_data_points,
        "fan_reports": len(fan_data_points),
        "setlistfm_points": len(setlist_data_points),
        "heuristic": heuristic,
        "recent_setlists": recent_setlists,  # up to 3 recent setlists with song lists
    })


@app.route("/api/stagetime/report/check", methods=["GET"])
def check_stagetime_report():
    """
    Check whether a fan has already submitted a stage time for a given artist+date.
    Pure read — no side effects.
    GET /api/stagetime/report/check?artist_name=&event_date=&fan_uuid=
    Returns: { has_report: bool, stage_time: "HH:MM" | null, can_edit: bool }
    """
    artist_name = request.args.get("artist_name", "").strip()
    event_date = request.args.get("event_date", "").strip()
    fan_uuid = request.args.get("fan_uuid", "").strip()

    if not artist_name or not event_date or not fan_uuid:
        return jsonify({"error": "artist_name, event_date, and fan_uuid required"}), 400

    try:
        import datetime as _dt
        _dt.date.fromisoformat(event_date)  # validate format
    except ValueError:
        return jsonify({"error": "event_date must be YYYY-MM-DD"}), 400

    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("""
                SELECT stage_time, edit_count
                FROM fan_stagetime_reports
                WHERE fan_uuid = %s
                  AND lower(artist_name) = lower(%s)
                  AND event_date = %s
                ORDER BY submitted_at ASC
                LIMIT 1
            """, (fan_uuid, artist_name, event_date))
            row = cur.fetchone()
    except Exception as exc:
        app.logger.error(f"check_stagetime_report DB error: {exc}")
        return jsonify({"error": "DB error"}), 500

    if not row:
        return jsonify({"has_report": False, "stage_time": None, "can_edit": True})

    stage_time = str(row[0])[:5]  # HH:MM
    edit_count = row[1]
    return jsonify({
        "has_report": True,
        "stage_time": stage_time,
        "can_edit": edit_count < 5
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

    is_edit = bool(data.get("is_edit"))  # True when user is editing an existing submission

    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            # Check for existing submission from this UUID for this artist+date
            existing_row = None
            if fan_uuid and parsed_date:
                cur.execute("""
                    SELECT id, stage_time, edit_count
                    FROM fan_stagetime_reports
                    WHERE fan_uuid = %s
                      AND lower(artist_name) = lower(%s)
                      AND event_date = %s
                    ORDER BY submitted_at ASC
                    LIMIT 1
                """, (fan_uuid, artist_name, parsed_date))
                row = cur.fetchone()
                if row:
                    existing_row = {"id": row[0], "stage_time": str(row[1])[:5], "edit_count": row[2]}

            if existing_row and not is_edit:
                # Already submitted — return status so iOS shows Edit button
                return jsonify({
                    "ok": True,
                    "already_submitted": True,
                    "existing_time": existing_row["stage_time"],
                    "edit_count": existing_row["edit_count"],
                    "can_edit": existing_row["edit_count"] < 5,
                    "message": "Already reported for this show today."
                }), 200

            if existing_row and is_edit:
                # Edit request — enforce 5-edit cap
                if existing_row["edit_count"] >= 5:
                    return jsonify({
                        "ok": False,
                        "error": "Edit limit reached. You can edit up to 5 times per show."
                    }), 429
                # Overwrite row, increment edit_count
                cur.execute("""
                    UPDATE fan_stagetime_reports
                    SET stage_time = %s,
                        submitted_at = now(),
                        edit_count = edit_count + 1
                    WHERE id = %s
                """, (stage_time, existing_row["id"]))
                db.commit()
                return jsonify({
                    "ok": True,
                    "updated": True,
                    "edit_count": existing_row["edit_count"] + 1,
                    "can_edit": existing_row["edit_count"] + 1 < 5,
                    "message": f"Stage time updated for {artist_name}"
                })

            # No existing row — fresh insert
            cur.execute("""
                INSERT INTO fan_stagetime_reports
                  (artist_name, venue_name, city, event_date, stage_time, fan_uuid, edit_count)
                VALUES (%s, %s, %s, %s, %s, %s, 0)
            """, (artist_name, venue_name, city, parsed_date, stage_time, fan_uuid))
            db.commit()
    except Exception as exc:
        app.logger.error(f"submit_stagetime_report DB error: {exc}")
        return jsonify({"error": "Failed to save report"}), 500

    return jsonify({"ok": True, "message": f"Stage time reported for {artist_name}"})


# ---------------------------------------------------------------------------
# /api/auth/apple — Sign in with Apple
#
# Flow:
#   iOS sends identityToken (JWT from Apple SDK) + optional device_uuid.
#   Backend verifies token against Apple JWKS (RS256), creates or returns
#   user record, upserts device UUID for future backfill.
#   Stores device UUID in user_device_uuids for backfill of anonymous
#   activity (favorites, going, stage reports). Linking of existing anonymous
#   activity to the authenticated user is deferred post-alpha.
# ---------------------------------------------------------------------------

APPLE_JWKS_URL  = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER    = "https://appleid.apple.com"
APPLE_CLIENT_ID = "com.eastheightslabs.onstage"  # must match Xcode bundle ID exactly

# JWKS cache — module-level, shared across requests
# Double-checked locking: check before lock, check again after acquire
# to prevent duplicate fetches on concurrent cold-start requests.
_jwks_cache: dict = {}       # {kid: jwk_dict}
_jwks_fetched_at: float = 0  # monotonic seconds
_jwks_lock = threading.Lock()
_JWKS_TTL = 3600  # 1 hour


def _fetch_apple_jwks() -> dict:
    """Fetch Apple public keys from JWKS endpoint. Returns {kid: key_dict}."""
    req = URLRequest(APPLE_JWKS_URL, headers={"Accept": "application/json"})
    with urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return {k["kid"]: k for k in data.get("keys", [])}


def _get_apple_jwks(force_refresh: bool = False) -> dict:
    """
    Return cached JWKS. Double-checked lock prevents duplicate fetches.
    force_refresh=True bypasses TTL (used on unknown kid).
    """
    global _jwks_cache, _jwks_fetched_at
    now = time.monotonic()
    # First check — outside lock (fast path for hot cache)
    if not force_refresh and _jwks_cache and (now - _jwks_fetched_at) < _JWKS_TTL:
        return _jwks_cache
    # Acquire lock
    with _jwks_lock:
        # Second check — inside lock (another thread may have fetched already)
        now = time.monotonic()
        if force_refresh or not _jwks_cache or (now - _jwks_fetched_at) >= _JWKS_TTL:
            _jwks_cache = _fetch_apple_jwks()
            _jwks_fetched_at = time.monotonic()
    return _jwks_cache


def _verify_apple_token(id_token: str) -> dict:
    """
    Verify Apple identity token. Returns decoded claims on success.
    Raises ValueError on any verification failure.
    """
    try:
        header = _pyjwt.get_unverified_header(id_token)
    except _pyjwt.exceptions.DecodeError as e:
        raise ValueError(f"Cannot read token header: {e}")

    kid = header.get("kid")
    if not kid:
        raise ValueError("Token missing kid in header")

    jwks = _get_apple_jwks()
    if kid not in jwks:
        # Possibly a new key — force refresh once
        jwks = _get_apple_jwks(force_refresh=True)
    if kid not in jwks:
        raise ValueError(f"Unknown kid={kid} not found in Apple JWKS after refresh")

    public_key = _pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwks[kid]))
    try:
        claims = _pyjwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=APPLE_CLIENT_ID,  # com.eastheightslabs.onstage
            issuer=APPLE_ISSUER,
            leeway=300,  # 300s tolerance — covers Vercel cold start + network latency
        )
    except _pyjwt.exceptions.ExpiredSignatureError:
        raise ValueError("Apple identity token has expired")
    except _pyjwt.exceptions.InvalidTokenError as e:
        raise ValueError(f"Apple token verification failed: {e}")

    if not claims.get("sub"):
        raise ValueError("Token missing sub claim")

    return claims


@app.route("/api/auth/apple", methods=["POST"])
def auth_apple():
    """
    Sign in with Apple endpoint.
    Verifies Apple identity token, creates or returns user record.
    Safe to call on every app launch after initial sign-in.
    """
    body = request.get_json(force=True, silent=True) or {}
    id_token = (body.get("identity_token") or "").strip()
    device_uuid = (body.get("device_uuid") or "").strip() or None

    if not id_token:
        return jsonify({"error": "identity_token required"}), 400

    try:
        claims = _verify_apple_token(id_token)
    except ValueError as e:
        app.logger.warning(f"Apple auth failed: {e}")
        return jsonify({"error": "Invalid or expired identity token"}), 401

    apple_id    = claims["sub"]
    email       = claims.get("email")  # None on repeat sign-ins or private relay
    display_name = claims.get("name")  # Only present on very first Apple sign-in

    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:

            # Look up existing user by apple_id
            cur.execute(
                "SELECT id, tier, tier_source, tier_expires_at, email, display_name "
                "FROM users WHERE apple_id = %s",
                (apple_id,)
            )
            row = cur.fetchone()

            if row:
                user_id = str(row["id"])
            else:
                # First sign-in — create user (tier defaults to 'free' via column default)
                cur.execute(
                    "INSERT INTO users (apple_id, email, display_name) "
                    "VALUES (%s, %s, %s) RETURNING id, tier, tier_source, "
                    "tier_expires_at, email, display_name",
                    (apple_id, email, display_name)
                )
                row = cur.fetchone()
                user_id = str(row["id"])

            # Upsert device UUID — stores device history for future backfill.
            # Existing anonymous activity linking is deferred post-alpha.
            if device_uuid:
                cur.execute(
                    "INSERT INTO user_device_uuids (user_id, device_uuid) "
                    "VALUES (%s, %s) "
                    "ON CONFLICT (user_id, device_uuid) "
                    "DO UPDATE SET last_seen_at = NOW()",
                    (user_id, device_uuid)
                )

        db.commit()

        return jsonify({
            "user_id":        user_id,
            "tier":           row["tier"],
            "tier_source":    row["tier_source"],
            "tier_expires_at": row["tier_expires_at"].isoformat() if row["tier_expires_at"] else None,
            "email":          row["email"],
            "display_name":   row["display_name"],
        })

    except Exception as exc:
        app.logger.error(f"auth_apple DB error: {exc}")
        return jsonify({"error": "Server error"}), 500


# ---------------------------------------------------------------------------
# /api/favorites — venue favorites (anonymous, keyed by device UUID)
# ---------------------------------------------------------------------------

@app.route("/api/favorites/ids")
def get_favorite_ids():
    """Return venue IDs this user has favorited (lightweight sync check)."""
    user_uuid = request.args.get("user_uuid", "").strip()
    if not user_uuid:
        return jsonify({"error": "user_uuid required"}), 400
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT venue_id FROM user_favorites WHERE user_uuid = %s", (user_uuid,))
            ids = [row[0] for row in cur.fetchall()]
        return jsonify({"venue_ids": ids})
    except Exception as exc:
        app.logger.error(f"get_favorite_ids error: {exc}")
        return jsonify({"error": "Database error"}), 500


@app.route("/api/favorites")
def get_favorites():
    """Return full list of favorited venues for a user."""
    user_uuid = request.args.get("user_uuid", "").strip()
    if not user_uuid:
        return jsonify({"error": "user_uuid required"}), 400
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("""
                SELECT uf.venue_id, v.name, v.city, v.state, v.lat, v.lng
                FROM user_favorites uf
                LEFT JOIN venues v ON v.id::text = uf.venue_id
                WHERE uf.user_uuid = %s
                ORDER BY uf.created_at DESC
            """, (user_uuid,))
            rows = cur.fetchall()
        venues = [
            {
                "venue_id": r[0],
                "venue_name": r[1],
                "city": f"{r[2]}, {r[3]}" if r[3] else r[2],
                "lat": float(r[4]) if r[4] else None,
                "lng": float(r[5]) if r[5] else None,
            }
            for r in rows
        ]
        return jsonify({"favorites": venues, "count": len(venues)})
    except Exception as exc:
        app.logger.error(f"get_favorites error: {exc}")
        return jsonify({"error": "Database error"}), 500


@app.route("/api/favorites", methods=["POST"])
def add_favorite():
    """Add a venue to user's favorites."""
    data = request.get_json() or {}
    user_uuid = (data.get("user_uuid") or "").strip()
    venue_id = (data.get("venue_id") or "").strip()
    if not user_uuid or not venue_id:
        return jsonify({"error": "user_uuid and venue_id required"}), 400
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO user_favorites (user_uuid, venue_id)
                VALUES (%s, %s)
                ON CONFLICT (user_uuid, venue_id) DO NOTHING
            """, (user_uuid, venue_id))
            db.commit()
        return jsonify({"ok": True, "venue_id": venue_id})
    except Exception as exc:
        app.logger.error(f"add_favorite error: {exc}")
        return jsonify({"error": "Database error"}), 500


@app.route("/api/favorites/<venue_id>", methods=["DELETE"])
def remove_favorite(venue_id):
    """Remove a venue from user's favorites."""
    user_uuid = request.args.get("user_uuid", "").strip()
    if not user_uuid:
        return jsonify({"error": "user_uuid required"}), 400
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM user_favorites WHERE user_uuid = %s AND venue_id = %s",
                (user_uuid, venue_id)
            )
            db.commit()
        return jsonify({"ok": True, "venue_id": venue_id})
    except Exception as exc:
        app.logger.error(f"remove_favorite error: {exc}")
        return jsonify({"error": "Database error"}), 500


# ---------------------------------------------------------------------------
# /api/going — "I'm Going" event attendance (anonymous, keyed by device UUID)
# ---------------------------------------------------------------------------

@app.route("/api/going/ids")
def get_going_ids():
    """Return event source IDs the user is attending on a given date."""
    user_uuid = request.args.get("user_uuid", "").strip()
    date_str = request.args.get("date", "").strip()
    if not user_uuid:
        return jsonify({"error": "user_uuid required"}), 400
    try:
        import datetime as _dt
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            if date_str:
                cur.execute(
                    "SELECT event_source_id FROM user_going WHERE user_uuid = %s AND event_date = %s",
                    (user_uuid, date_str)
                )
            else:
                cur.execute(
                    "SELECT event_source_id FROM user_going WHERE user_uuid = %s",
                    (user_uuid,)
                )
            ids = [row[0] for row in cur.fetchall()]
        return jsonify({"event_ids": ids})
    except Exception as exc:
        app.logger.error(f"get_going_ids error: {exc}")
        return jsonify({"error": "Database error"}), 500


@app.route("/api/going", methods=["POST"])
def mark_going():
    """Mark user as attending an event."""
    data = request.get_json() or {}
    user_uuid = (data.get("user_uuid") or data.get("userUuid") or "").strip()
    event_source = (data.get("event_source") or data.get("eventSource") or "").strip()
    event_source_id = (data.get("event_source_id") or data.get("eventSourceId") or "").strip()
    event_date = (data.get("event_date") or data.get("eventDate") or "").strip()
    headliner_name = (data.get("headliner_name") or data.get("headlinerName") or "").strip() or None
    venue_name = (data.get("venue_name") or data.get("venueName") or "").strip() or None
    doors_time = (data.get("doors_time") or data.get("doorsTime") or "").strip() or None
    estimated_stage_time = (data.get("estimated_stage_time") or data.get("estimatedStageTime") or "").strip() or None

    if not user_uuid or not event_source or not event_source_id or not event_date:
        return jsonify({"error": "user_uuid, event_source, event_source_id, event_date required"}), 400
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO user_going
                  (user_uuid, event_source, event_source_id, event_date,
                   headliner_name, venue_name, doors_time, estimated_stage_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_uuid, event_source, event_source_id) DO UPDATE
                  SET event_date = EXCLUDED.event_date,
                      headliner_name = EXCLUDED.headliner_name,
                      venue_name = EXCLUDED.venue_name,
                      doors_time = EXCLUDED.doors_time,
                      estimated_stage_time = EXCLUDED.estimated_stage_time
            """, (user_uuid, event_source, event_source_id, event_date,
                  headliner_name, venue_name, doors_time, estimated_stage_time))
            db.commit()
        return jsonify({"ok": True, "event_source_id": event_source_id})
    except Exception as exc:
        app.logger.error(f"mark_going error: {exc}")
        return jsonify({"error": "Database error"}), 500


@app.route("/api/going/<event_source_id>", methods=["DELETE"])
def unmark_going(event_source_id):
    """Unmark user as attending an event."""
    user_uuid = request.args.get("user_uuid", "").strip()
    event_source = request.args.get("event_source", "").strip()
    if not user_uuid:
        return jsonify({"error": "user_uuid required"}), 400
    try:
        from db import get_db
        db = get_db()
        with db.cursor() as cur:
            if event_source:
                cur.execute(
                    "DELETE FROM user_going WHERE user_uuid = %s AND event_source = %s AND event_source_id = %s",
                    (user_uuid, event_source, event_source_id)
                )
            else:
                cur.execute(
                    "DELETE FROM user_going WHERE user_uuid = %s AND event_source_id = %s",
                    (user_uuid, event_source_id)
                )
            db.commit()
        return jsonify({"ok": True, "event_source_id": event_source_id})
    except Exception as exc:
        app.logger.error(f"unmark_going error: {exc}")
        return jsonify({"error": "Database error"}), 500


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


@app.route("/privacy")
def privacy_policy():
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Privacy Policy — OnStage Live</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 720px; margin: 48px auto; padding: 0 24px; color: #1a1a1a; line-height: 1.7; }
    h1 { font-size: 28px; font-weight: 700; margin-bottom: 4px; }
    h2 { font-size: 18px; font-weight: 600; margin-top: 32px; }
    p, li { font-size: 15px; }
    ul { padding-left: 20px; }
    .meta { color: #666; font-size: 13px; margin-bottom: 40px; }
    a { color: #5856d6; }
  </style>
</head>
<body>
  <h1>Privacy Policy</h1>
  <p class="meta">OnStage Live &mdash; East Heights Labs, LLC &mdash; Effective August 31, 2026</p>

  <h2>Information We Collect</h2>
  <ul>
    <li><strong>Location data</strong> &mdash; Used to find live music events near you. Your location is sent to our servers only to retrieve nearby events and is not stored or logged.</li>
    <li><strong>Anonymous device identifier</strong> &mdash; A randomly generated UUID stored on your device and used to associate your saved favorites. This identifier is not linked to your name, email, or any personal information.</li>
    <li><strong>Stage time reports</strong> &mdash; If you submit a stage time, we store the reported time and your anonymous device UUID to associate contributions with a device.</li>
  </ul>

  <h2>How We Use Your Information</h2>
  <ul>
    <li>Location is used only to surface nearby venues and events in the app.</li>
    <li>Your anonymous device UUID is used solely to save and retrieve your favorites across sessions.</li>
    <li>Stage time reports are used to improve the accuracy of show schedules for all users.</li>
  </ul>

  <h2>Third-Party Services</h2>
  <p>OnStage Live uses the following third-party services to provide event data:</p>
  <ul>
    <li>Ticketmaster Discovery API</li>
    <li>JamBase</li>
    <li>Setlist.fm</li>
    <li>Railway (database hosting)</li>
    <li>Vercel (API hosting)</li>
  </ul>
  <p>Each of these services has its own privacy policy. We share only the minimum data necessary (your location coordinates) to retrieve relevant results.</p>

  <h2>Data Retention</h2>
  <p>Favorites and stage time reports are retained as long as necessary to provide the service. We do not sell, rent, or share your data with third parties for advertising or marketing purposes.</p>

  <h2>Children's Privacy</h2>
  <p>OnStage Live is not directed at children under 13. We do not knowingly collect personal information from children.</p>

  <h2>Contact</h2>
  <p>Questions about this policy? Contact us at <a href="mailto:info@eastheightslabs.com">info@eastheightslabs.com</a></p>
  <p>East Heights Labs, LLC &mdash; Houston, TX</p>
</body>
</html>
"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}

