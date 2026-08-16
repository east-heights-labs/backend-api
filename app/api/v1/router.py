from fastapi import APIRouter

from app.api.v1.endpoints import events, venues, stagetime, history, health

api_router = APIRouter()

# Live Near Me
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(venues.router, prefix="/venues", tags=["venues"])
api_router.include_router(stagetime.router, prefix="/stagetime", tags=["stagetime"])

# WUTBT
api_router.include_router(history.router, prefix="/history", tags=["history"])

# Shared
api_router.include_router(health.router, prefix="/health", tags=["health"])
