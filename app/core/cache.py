import redis.asyncio as aioredis
from app.core.config import settings

_redis_client = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def cache_get(key: str) -> str | None:
    r = await get_redis()
    return await r.get(key)


async def cache_set(key: str, value: str, ttl_seconds: int = 3600) -> None:
    r = await get_redis()
    await r.setex(key, ttl_seconds, value)


async def cache_delete(key: str) -> None:
    r = await get_redis()
    await r.delete(key)
