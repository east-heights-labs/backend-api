from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# Only wire up the engine if a real DATABASE_URL is configured
_db_url = (settings.DATABASE_URL or "").replace("postgresql://", "postgresql+asyncpg://")
_db_ready = bool(_db_url and "localhost" not in _db_url and "user:password" not in _db_url)

if _db_ready:
    engine = create_async_engine(_db_url, echo=settings.DEBUG)
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
