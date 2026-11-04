"""Trivio Configuration Settings.

Uses pydantic-settings to validate and load environment variables with robust
defaults for local development and production.
"""

from functools import lru_cache
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General App Settings
    PROJECT_NAME: str = "Trivio"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Database Configuration
    DATABASE_URL: str = "sqlite:///./trivio_dev.db"
    DB_ECHO: bool = False

    # Cache Configuration (Smart Cache & Redis)
    REDIS_URL: Optional[str] = None
    CACHE_DEFAULT_TTL_SECONDS: int = 1800  # 30 minutes fallback

    # Engine Heuristics & Mathematical Defaults (Trivio Score & Geodesic Radius)
    DEFAULT_HOURLY_TIME_VALUE_BRL: float = 35.0  # R$ 35,00/h base
    DEFAULT_CATCHMENT_RADIUS_KM: float = 120.0  # Raio geodésico de busca
    AIRPORT_DOMESTIC_CHECKIN_BUFFER_MINUTES: int = 90  # Antecedência aeroporto
    BUS_TERMINAL_CHECKIN_BUFFER_MINUTES: int = 20  # Antecedência rodoviária
    DEFAULT_FLIGHT_BAGGAGE_FEE_ESTIMATE_BRL: float = 110.0  # Estimativa bagagem 23kg

    # Provider API Credentials (Optional / Phase 3)
    AMADEUS_CLIENT_ID: Optional[str] = None
    AMADEUS_CLIENT_SECRET: Optional[str] = None
    AMADEUS_ENV: str = "test"


@lru_cache()
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()


settings = get_settings()
