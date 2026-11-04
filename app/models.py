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
