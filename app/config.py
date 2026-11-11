"""Trivio Configuration Settings.

Uses pydantic-settings to validate and load environment variables with robust
defaults for local development and production.
"""

from functools import lru_cache
from typing import List, Optional
from pydantic import Field, field_validator
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

    # Identificação e Conformidade da Coleta Automatizada
    # O Trivio se identifica honestamente; nunca se passa por um navegador.
    # Preencha BOT_INFO_URL/BOT_CONTACT_EMAIL para que os sites possam falar com você.
    BOT_NAME: str = "TrivioBot"
    BOT_VERSION: str = "1.0"
    BOT_INFO_URL: str = ""        # Ex: https://trivio.com.br/bot
    BOT_CONTACT_EMAIL: str = ""   # Ex: bot@trivio.com.br
    RESPECT_ROBOTS_TXT: bool = True
    DEFAULT_CRAWL_DELAY_SECONDS: float = 2.0  # Usado quando o robots.txt não declara Crawl-delay

    # Cache Configuration (Smart Cache & Redis)
    REDIS_URL: Optional[str] = None
    CACHE_DEFAULT_TTL_SECONDS: int = 1800  # 30 minutes fallback

    # Engine Heuristics & Mathematical Defaults (Trivio Score & Geodesic Radius)
    DEFAULT_HOURLY_TIME_VALUE_BRL: float = 35.0  # R$ 35,00/h base
    DEFAULT_CATCHMENT_RADIUS_KM: float = 120.0  # Raio geodésico de busca
    AIRPORT_DOMESTIC_CHECKIN_BUFFER_MINUTES: int = 90  # Antecedência aeroporto
    BUS_TERMINAL_CHECKIN_BUFFER_MINUTES: int = 20  # Antecedência rodoviária
    DEFAULT_FLIGHT_BAGGAGE_FEE_ESTIMATE_BRL: float = 110.0  # Estimativa bagagem 23kg

    # Endereço público da API, usado para montar links que o navegador abre
    # (ex: o link de compra do aéreo, resolvido sob demanda).
    PUBLIC_API_BASE_URL: str = "http://localhost:8000"

    # Preços aéreos ao vivo (Ignav Flight Prices API)
    IGNAV_API_KEY: Optional[str] = None
    IGNAV_BASE_URL: str = "https://ignav.com"
    IGNAV_MARKET: str = "BR"                  # Mercado brasileiro: preços em BRL
    IGNAV_TIMEOUT_SECONDS: float = 30.0

    # Saldo de requests do Ignav lido no painel do provedor. O plano gratuito tem 1.000
    # requests únicas (não renovam por mês). O Trivio desconta as requests cobradas desde
    # a leitura (busca, link de compra) e para de consultar ao chegar na margem de
    # segurança, avisando o frontend. Ao conferir o painel, atualize os dois valores.
    # 0 desliga o aéreo ao vivo.
    AIR_REQUESTS_REMAINING: int = 980
    AIR_REQUESTS_REMAINING_AS_OF: str = "2026-11-10T23:54:36"   # UTC, momento da leitura
    AIR_REQUESTS_SAFETY_MARGIN: int = 20
    # Teto de validade do cache de consultas aéreas, acima das faixas por data de viagem.
    AIR_LIVE_CACHE_MAX_SECONDS: int = 7200
    # Grava as cotações ao vivo no histórico permanente (FareQuote). Desligado até
    # confirmar que os termos de uso do provedor permitem guardar preços.
    AIR_PERSIST_LIVE_QUOTES: bool = False

    # Taxa de embarque doméstica estimada, para separar tarifa e taxa no preço total
    # do provedor (a régua da ANAC usa a tarifa sem taxa). Os valores de 2026 ficam
    # entre R$ 33 e R$ 55 conforme o aeroporto; por aeroporto em app/collectors/air/boarding_fees.py.
    DEFAULT_DOMESTIC_BOARDING_FEE_BRL: float = 40.0

    @field_validator("IGNAV_API_KEY", mode="before")
    @classmethod
    def _strip_secret(cls, value):
        """Remove espaços e quebras de linha (ex: .env salvo com CRLF); vazio vira None."""
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


@lru_cache()
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()


settings = get_settings()
