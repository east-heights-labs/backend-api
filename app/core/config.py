from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List
import os


class Settings(BaseSettings):
    # App
    APP_ENV: str = "development"
    APP_SECRET_KEY: str = "change-me"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS — accepts comma-separated string or JSON list
    ALLOWED_ORIGINS: str = "*"

    # External APIs — Live Near Me
    SONGKICK_API_KEY: str = ""
    BANDSINTOWN_APP_ID: str = ""
    TICKETMASTER_API_KEY: str = ""
    SETLIST_FM_API_KEY: str = ""
    EVENTBRITE_API_KEY: str = ""
    JAMBASE_API_KEY: str = ""

    # Pre-fetch cron job
    PREFETCH_SECRET: str = ""  # Set in Vercel env; cron requests must include x-prefetch-secret header

    # Google
    GOOGLE_MAPS_API_KEY: str = ""
    GOOGLE_STREET_VIEW_API_KEY: str = ""

    # Reddit
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = "EastHeightsLabs/1.0"

    @property
    def allowed_origins_list(self) -> List[str]:
        """Parse ALLOWED_ORIGINS as comma-separated or wildcard."""
        if self.ALLOWED_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    class Config:
        # Only load .env file in local dev — not on Vercel (env vars set directly)
        env_file = ".env" if os.path.exists(".env") else None
        case_sensitive = True


settings = Settings()
