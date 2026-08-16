from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # App
    APP_ENV: str = "development"
    APP_SECRET_KEY: str = "change-me"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/eastheightslabs"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000"]

    # External APIs — Live Near Me
    SONGKICK_API_KEY: str = ""
    BANDSINTOWN_APP_ID: str = ""
    TICKETMASTER_API_KEY: str = ""
    SETLIST_FM_API_KEY: str = ""
    EVENTBRITE_API_KEY: str = ""

    # Google
    GOOGLE_MAPS_API_KEY: str = ""
    GOOGLE_STREET_VIEW_API_KEY: str = ""

    # Reddit
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = "EastHeightsLabs/1.0"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
