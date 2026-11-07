"""Trivio ORM Database Models.

Defines the entity hierarchy for multi-modal travel intelligence:
Hub -> Terminal -> ProviderMapping and FareQuote.
"""

from datetime import datetime, timezone
import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    Enum as SQLEnum,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.database import Base


class ModalType(str, enum.Enum):
    """Travel transport mode."""
    AIR = "AIR"
    ROAD = "ROAD"


class Hub(Base):
    """Município ou Região Metropolitana âncora (Base IBGE)."""
    __tablename__ = "hubs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ibge_code = Column(String(7), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    state = Column(String(2), nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    terminals = relationship(
        "Terminal",
        back_populates="hub",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Hub(id={self.id}, name='{self.name}', state='{self.state}')>"


class Terminal(Base):
    """Aeroporto ou Rodoviária específica dentro de um Hub."""
    __tablename__ = "terminals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hub_id = Column(Integer, ForeignKey("hubs.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    modal = Column(SQLEnum(ModalType), nullable=False, index=True)
    official_code = Column(String(20), index=True)  # Ex: IATA ('GRU', 'POA') ou sigla rodoviária
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    avg_city_center_distance_km = Column(Float, default=10.0)
    estimated_transfer_cost_brl = Column(Float, default=25.0)
    estimated_transfer_minutes = Column(Integer, default=30)

    hub = relationship("Hub", back_populates="terminals")
    provider_mappings = relationship(
        "ProviderMapping",
        back_populates="terminal",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Terminal(id={self.id}, name='{self.name}', modal='{self.modal}', code='{self.official_code}')>"


class ProviderMapping(Base):
    """De-Para de códigos de terminais em cada plataforma/agregador."""
    __tablename__ = "provider_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    terminal_id = Column(Integer, ForeignKey("terminals.id"), nullable=False)
    provider_name = Column(String(50), nullable=False, index=True)  # Ex: 'clickbus', 'rjconsultores', 'amadeus'
    external_id = Column(String(100), nullable=False)               # Ex: 'sao-paulo-tiete-sp' ou '4812'

    terminal = relationship("Terminal", back_populates="provider_mappings")

    __table_args__ = (
        UniqueConstraint("provider_name", "external_id", name="uq_provider_external_id"),
    )

    def __repr__(self) -> str:
        return f"<ProviderMapping(provider='{self.provider_name}', external_id='{self.external_id}')>"


class FareQuote(Base):
    """Registro de preço, score de atrito e detalhes técnicos de uma viagem coletada."""
    __tablename__ = "fare_quotes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_signature = Column(String(120), index=True, nullable=False)  # Hash único: modal+carrier+orig+dest+dep_time
    origin_terminal_id = Column(Integer, ForeignKey("terminals.id"), nullable=False, index=True)
    destination_terminal_id = Column(Integer, ForeignKey("terminals.id"), nullable=False, index=True)
    
    modal = Column(SQLEnum(ModalType), nullable=False, index=True)
    provider_source = Column(String(50), nullable=False)  # Quem forneceu o dado (ex: 'amadeus', 'j3_gateway')
    carrier_name = Column(String(100), nullable=False)    # Quem opera (ex: 'Azul', 'Viação Guanabara')
    service_class = Column(String(50))                    # Ex: 'ECONOMY', 'SEMI_LEITO', 'LEITO'
    
    departure_time = Column(DateTime, nullable=False, index=True)
    arrival_time = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    door_to_door_duration_minutes = Column(Integer, nullable=False)
    
    base_price = Column(Float, nullable=False)
    taxes_price = Column(Float, default=0.0)
    baggage_fee_estimate = Column(Float, default=0.0)
    transfer_cost_estimate = Column(Float, default=0.0)
    total_effective_price = Column(Float, nullable=False, index=True)
    
    # Métrica composta do Trivio (Menor valor = Melhor relação Custo x Tempo x Conforto)
    trivio_score = Column(Float, index=True)
    
    # Armazena paradas intermediárias, conexões, termômetro de preço e prós/contras gerados
    route_metadata = Column(JSON, default=dict)
    
    collected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False, index=True)  # Controlado pelo Smart Cache TTL Policy

    origin_terminal = relationship("Terminal", foreign_keys=[origin_terminal_id])
    destination_terminal = relationship("Terminal", foreign_keys=[destination_terminal_id])

    def __repr__(self) -> str:
        return (
            f"<FareQuote(id={self.id}, carrier='{self.carrier_name}', modal='{self.modal}', "
            f"price={self.total_effective_price}, score={self.trivio_score})>"
        )


class RouteMonthlyFare(Base):
    """Preço médio ponderado de uma rota, classe e mês de viagem, num mês de emissão.

    É a série que alimenta o Termômetro. Duas fontes oficiais:

    - ANTT/Monitriip (ROAD): rota entre municípios; os percentis são calculados
      sobre as médias dos meses de viagem.
    - ANAC/tarifas domésticas (AIR): rota entre aeroportos ICAO (origin_city e
      destination_city guardam o código, ex: 'SBSP'); trip_month e emission_period
      são o mesmo mês de VENDA; e os percentis ponderados por assento vêm prontos
      em p10_brl..p90_brl, porque a dispersão dentro do mês é o que importa.
    """
    __tablename__ = "route_monthly_fares"

    id = Column(Integer, primary_key=True, autoincrement=True)

    route_key = Column(String(160), nullable=False, index=True)
    origin_city = Column(String(120), nullable=False)
    origin_state = Column(String(2))
    destination_city = Column(String(120), nullable=False)
    destination_state = Column(String(2))

    modal = Column(SQLEnum(ModalType), nullable=False, default=ModalType.ROAD, index=True)
    service_class = Column(String(50), nullable=False)

    trip_month = Column(String(7), nullable=False, index=True)       # 'AAAA-MM'
    emission_period = Column(String(7), nullable=False, index=True)  # 'AAAA-MM'
    source = Column(String(50), nullable=False, default="antt_monitriip")

    sample_size = Column(Integer, nullable=False)  # bilhetes
    mean_brl = Column(Float, nullable=False)
    min_brl = Column(Float, nullable=False)
    max_brl = Column(Float, nullable=False)

    # Percentis ponderados pela amostra (assentos, na ANAC). Nulos na série da ANTT.
    p10_brl = Column(Float, nullable=True)
    p25_brl = Column(Float, nullable=True)
    p50_brl = Column(Float, nullable=True)
    p75_brl = Column(Float, nullable=True)
    p90_brl = Column(Float, nullable=True)

    imported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint(
            "route_key", "service_class", "trip_month", "emission_period", "source",
            name="uq_route_monthly_fare",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<RouteMonthlyFare(route='{self.route_key}', class='{self.service_class}', "
            f"viagem={self.trip_month}, emissao={self.emission_period}, n={self.sample_size})>"
        )


class ProviderQuery(Base):
    """Uma consulta feita (ou evitada) a um provedor pago de preços ao vivo.

    Faz três papéis:
      - cache: uma consulta 'ok' ou 'empty' ainda válida é respondida daqui, sem
        nova request. Guardar a consulta vazia evita pagar de novo por uma rota
        sem voos;
      - orçamento: as linhas 'ok'/'empty' do mês são as requests cobradas;
      - auditoria: erros ficam registrados (não contam no orçamento nem viram cache).

    As cotações guardadas aqui expiram junto com a consulta; o histórico
    permanente (FareQuote) é controlado por AIR_PERSIST_LIVE_QUOTES.
    """
    __tablename__ = "provider_queries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False, index=True)
    kind = Column(String(30), nullable=False, default="search")  # 'search' ou 'booking_link'
    query_key = Column(String(64), nullable=False, index=True)    # SHA-256 dos parâmetros normalizados
    origin = Column(String(10))
    destination = Column(String(10))
    travel_date = Column(String(10))                             # 'AAAA-MM-DD'
    requested_at = Column(DateTime, nullable=False, index=True, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True, index=True)
    status = Column(String(20), nullable=False)                  # 'ok', 'empty', 'error'
    billed = Column(Integer, nullable=False, default=0)          # 1 quando a request conta como cobrada
    quotes_count = Column(Integer, nullable=False, default=0)
    elapsed_ms = Column(Float)
    error = Column(String(500))
    payload = Column(JSON)                                       # cotações serializadas (cache) ou link

    def __repr__(self) -> str:
        return (
            f"<ProviderQuery({self.provider} {self.kind} {self.origin}->{self.destination} "
            f"{self.travel_date} status={self.status} n={self.quotes_count})>"
        )
