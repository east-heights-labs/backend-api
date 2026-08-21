from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# Only wire up the engine if a real DATABASE_URL is configured
_raw_url = settings.DATABASE_URL or ""
# Strip sslmode param from URL — pass SSL via connect_args instead (asyncpg requirement)
import re as _re
_clean_url = _re.sub(r'[?&]sslmode=[^&]*', '', _raw_url).rstrip('?')
_db_url = _clean_url.replace("postgresql://", "postgresql+asyncpg://")
_db_ready = bool(_db_url and "localhost" not in _db_url and "user:password" not in _db_url and _clean_url)

if _db_ready:
    # Railway requires SSL; pass ssl=True via connect_args for asyncpg
    _connect_args = {"ssl": True} if ("railway" in _db_url or "rlwy.net" in _db_url) else {}
    engine = create_async_engine(_db_url, echo=settings.DEBUG, connect_args=_connect_args)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
else:
    logger.warning("DATABASE_URL not configured or is placeholder — DB features disabled")
    engine = None
    AsyncSessionLocal = None


async def get_db():
    """Yield a DB session, or None if DB is not configured."""
    if AsyncSessionLocal is None:
        yield None
        return
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
