# East Heights Labs — Backend API

Shared FastAPI backend serving both **Live Near Me** and **What Used To Be There (WUTBT)**.

## Stack

- **Framework:** FastAPI (Python)
- **Database:** PostgreSQL (async via SQLAlchemy + asyncpg)
- **Cache:** Redis
- **Storage:** S3-compatible (TBD)

## Local Setup

```bash
# 1. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your local values

# 4. Run database migrations
alembic upgrade head

# 5. Start the dev server
uvicorn app.main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

## Project Structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── api/v1/
│   │   ├── router.py        # Route registration
│   │   └── endpoints/
│   │       ├── events.py    # Live Near Me: show listings
│   │       ├── venues.py    # Live Near Me: venue detail
│   │       ├── stagetime.py # Live Near Me: stage time intelligence
│   │       └── history.py   # WUTBT: location history
│   ├── core/
│   │   ├── config.py        # Settings (pydantic-settings)
│   │   ├── database.py      # Async SQLAlchemy engine
│   │   └── cache.py         # Redis helpers
│   ├── models/              # SQLAlchemy ORM models
│   ├── schemas/             # Pydantic request/response schemas
│   ├── services/            # Business logic + external API clients
│   └── utils/               # Shared utilities
├── alembic/                 # Database migrations
├── tests/                   # pytest test suite
├── .env.example             # Environment template (commit this)
├── .gitignore               # Never commit .env
└── requirements.txt
```

## API Routes

### Live Near Me
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/events` | Shows near a location (`lat`, `lng`, `radius`, `date`) |
| GET | `/api/v1/venues/{id}` | Venue detail + tonight's show |
| GET | `/api/v1/stagetime/{artist_id}` | Stage time history + confidence |

### WUTBT
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/history` | Location history by lat/lng |
| GET | `/api/v1/history/address` | History by address string |
| GET | `/api/v1/history/business` | All locations of a business by name + city |

### Shared
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
