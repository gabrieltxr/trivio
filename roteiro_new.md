# Project Specification & Roadmap: TRIVIO

> **Target Audience:** AI Coding Agents (Antigravity), Automated Scaffolds & Developers
> **Project Name:** Trivio (Trip + Via / Cruzamento de 3 caminhos: Ar, Terra e Melhor Custo-Benefício)
> **Domain:** Intermodal Travel Intelligence, Virtual Interlining & Price Aggregation (Brazil - National Scope)
> **Primary Stack:** Python 3.11+, SQLAlchemy 2.0+, Httpx, Playwright, NetworkX, GeoPy, RapidFuzz, Tenacity, Polars/Pandas, Redis (Cache), FastAPI (Future API Layer), PostgreSQL / SQLite (Dev)

---

## 1. System Context & Product Vision

**Trivio** é uma plataforma nacional (Brasil) de inteligência de mobilidade intermodal (Aéreo + Rodoviário). Diferente de agregadores comuns que tratam cada modal em silos isolados, o Trivio calcula o **custo real efetivo**, o **tempo porta a porta**, costura conexões mistas (**Ônibus + Avião**) e gera uma matriz detalhada de **prós, contras e possíveis contratempos** de cada rota e empresa operadora.

### Core Differentiators & Search Engine Mechanics:

1. **Custo Efetivo Total:** Soma tarifa base + taxas de embarque + estimativa de bagagem + custo de deslocamento até o terminal (aeroporto distante vs. rodoviária central).
2. **Mapeamento de Divergência de Rotas & Contratempos:** Identifica se uma viação faz rota expressa ou "pinga-pinga" (paradas intermediárias excessivas), trechos de serra/obras, ou conexões aéreas curtas com risco de perda de voo ou troca de aeroporto.
3. **Smart Cache & Deduplicação (Decolar/Despegar Pattern):** TTL dinâmico baseado na antecedência da viagem (*Look-to-Book optimization*) e agrupamento de ofertas idênticas vendidas por múltiplos canais (*code-share* / *brokers*), mantendo sempre o menor preço.
4. **Virtual Interlining Intermodal (Kiwi.com Pattern):** Grafo em memória (`networkx`) que combina trechos rodoviários e aéreos independentes quando a conexão híbrida (ex: Ônibus até capital vizinha + Voo direto) supera o custo-benefício das rotas diretas tradicionais.
5. **Hub Catchment Area / Raio Geográfico (Rome2Rio / Skyscanner Pattern):** Expansão geodésica (`geopy` / `h3`) que busca terminais alternativos em um raio configurável (ex: 100 km da cidade de origem/destino), somando o deslocamento terrestre na equação.
6. **Price Thermometer / Compra Preditiva (Hopper / Google Flights Pattern):** Análise estatística de séries históricas ($P_{25}, P_{50}, P_{75}$) que classifica se a tarifa atual está *Barata*, *Na Média* ou *Cara* para aquela rota e época.
7. **Intermodal Hacker Fares (Kayak Pattern):** Combinação assimétrica de bilhetes *one-way* independentes (ex: Ida de Avião para ganhar tempo + Volta de Ônibus Leito noturno para economizar diária e passagem).
8. **Trivio Score / Índice de Atrito (Hipmunk "Agony Index" Pattern):** Algoritmo de ranqueamento que pondera Preço Efetivo + Tempo Porta a Porta + Penalidades de Atrito (madrugada, baldeações, excesso de paradas).

---

## 2. Target Directory Structure

O agente no Antigravity deve criar e manter a seguinte árvore de diretórios no workspace:

```text
trivio/
├── app/
│   ├── __init__.py
│   ├── config.py                  # Carregamento de variáveis de ambiente (.env) e constantes do motor
│   ├── database.py                # Configuração do SQLAlchemy Engine e SessionLocal
│   ├── models.py                  # Modelos ORM (Hub, Terminal, ProviderMapping, FareQuote)
│   ├── cache/                     # Camada de Smart Cache e Deduplicação (Decolar Pattern)
│   │   ├── __init__.py
│   │   ├── ttl_policy.py          # Cálculo de TTL dinâmico baseado na data de embarque
│   │   └── deduplicator.py        # Merge de viagens idênticas entre múltiplos provedores
│   ├── geo/                       # Inteligência Geográfica e Resolução de Entidades
│   │   ├── __init__.py
│   │   ├── catchment.py           # Busca de terminais em raio geodésico (Rome2Rio Pattern)
│   │   └── entity_resolver.py     # Fuzzy matching de nomes de cidades/terminais via RapidFuzz
│   ├── collectors/                # Motores de ingestão e scraping de preços (com Tenacity Retry)
│   │   ├── __init__.py
│   │   ├── base.py                # Contrato abstrato (BaseCollector) e DTO (StandardizedQuote)
│   │   ├── road/                  # Coletores Rodoviários (Gateways J3, RjConsultores, Agregadores, ANTT)
│   │   │   └── __init__.py
│   │   └── air/                   # Coletores Aéreos (Amadeus, Duffel, Tequila, ANAC Microdados)
│   │       └── __init__.py
│   └── analyzers/                 # Motores Analíticos, Grafos e Regras de Negócio
│       ├── __init__.py
│       ├── route_evaluator.py     # Geração de Prós, Contras e Contratempos da rota
│       ├── trivio_scorer.py       # Cálculo do Trivio Score (Agony Index) e Hacker Fares
│       ├── interliner.py          # Grafo NetworkX para conexões híbridas (Ônibus + Avião)
│       └── price_thermometer.py   # Cálculo de percentis históricos de preço (Hopper Pattern)
├── scripts/
│   ├── init_db.py                 # Cria tabelas e executa seed inicial de localidades
│   └── test_collector.py          # CLI runner (Rich) para testar busca, cache e ranking no terminal
├── tests/
│   └── __init__.py
├── .env.example
├── requirements.txt
└── TRIVIO_SPEC.md
```

---

## 3. Dependencies (`requirements.txt`)

```text
# Core & Database
sqlalchemy>=2.0.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
python-dotenv>=1.0.0
redis>=5.0.0

# HTTP, Scraping & Resilience (Decolar / OTA Patterns)
httpx>=0.26.0
playwright>=1.41.0
tenacity>=8.2.0

# Graph, Geo & Entity Resolution (Kiwi / Rome2Rio / Kayak Patterns)
networkx>=3.2.0
geopy>=2.4.0
h3>=3.7.6
rapidfuzz>=3.6.0

# Data Analysis & Price Thermometer (Hopper Pattern)
polars>=0.20.0

# CLI Visualization & Testing
rich>=13.7.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

---

## 4. Database Schema Specification (`app/models.py`)

A resolução de entidades entre diferentes provedores (códigos IATA no aéreo, slugs em agregadores rodoviários e códigos municipais IBGE) é estruturada através da relação `Hub -> Terminal -> ProviderMapping`.

```python
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Enum, JSON, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship
import enum

Base = declarative_base()

class ModalType(enum.Enum):
    AIR = "AIR"
    ROAD = "ROAD"

class Hub(Base):
    """Município ou Região Metropolitana âncora (Base IBGE)."""
    __tablename__ = "hubs"

    id = Column(Integer, primary_key=True)
    ibge_code = Column(String(7), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    state = Column(String(2), nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    terminals = relationship("Terminal", back_populates="hub", cascade="all, delete-orphan")

class Terminal(Base):
    """Aeroporto ou Rodoviária específica dentro de um Hub."""
    __tablename__ = "terminals"

    id = Column(Integer, primary_key=True)
    hub_id = Column(Integer, ForeignKey("hubs.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    modal = Column(Enum(ModalType), nullable=False)
    official_code = Column(String(20), index=True)  # Ex: IATA ('GRU', 'POA') ou sigla rodoviária
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    avg_city_center_distance_km = Column(Float, default=10.0)
    estimated_transfer_cost_brl = Column(Float, default=25.0)
    estimated_transfer_minutes = Column(Integer, default=30)

    hub = relationship("Hub", back_populates="terminals")
    provider_mappings = relationship("ProviderMapping", back_populates="terminal", cascade="all, delete-orphan")

class ProviderMapping(Base):
    """De-Para de códigos de terminais em cada plataforma/agregador."""
    __tablename__ = "provider_mappings"

    id = Column(Integer, primary_key=True)
    terminal_id = Column(Integer, ForeignKey("terminals.id"), nullable=False)
    provider_name = Column(String(50), nullable=False, index=True)  # Ex: 'clickbus', 'rjconsultores', 'amadeus'
    external_id = Column(String(100), nullable=False)               # Ex: 'sao-paulo-tiete-sp' ou '4812'

    terminal = relationship("Terminal", back_populates="provider_mappings")

    __table_args__ = (
        UniqueConstraint("provider_name", "external_id", name="uq_provider_external_id"),
    )

class FareQuote(Base):
    """Registro de preço, score de atrito e detalhes técnicos de uma viagem coletada."""
    __tablename__ = "fare_quotes"

    id = Column(Integer, primary_key=True)
    dedup_signature = Column(String(120), index=True, nullable=False)  # Hash único: modal+carrier+orig+dest+dep_time
    origin_terminal_id = Column(Integer, ForeignKey("terminals.id"), nullable=False, index=True)
    destination_terminal_id = Column(Integer, ForeignKey("terminals.id"), nullable=False, index=True)
    
    modal = Column(Enum(ModalType), nullable=False, index=True)
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
```

---

## 5. Collector Interface & Resilience Contract (`app/collectors/base.py`)

Todos os coletores implementam `BaseCollector`, utilizando `tenacity` para retentativas automáticas com *exponential backoff* e gerando uma assinatura de deduplicação padronizada.

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any
from abc import ABC, abstractmethod
import hashlib

@dataclass
class StandardizedQuote:
    origin_external_id: str
    destination_external_id: str
    modal: str                  # 'AIR' ou 'ROAD'
    provider_source: str
    carrier_name: str
    service_class: str
    departure_time: datetime
    arrival_time: datetime
    duration_minutes: int
    base_price: float
    taxes_price: float = 0.0
    baggage_fee_estimate: float = 0.0
    transfer_cost_estimate: float = 0.0
    door_to_door_duration_minutes: int = 0
    trivio_score: float = 0.0
    route_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_effective_price(self) -> float:
        return round(
            self.base_price + self.taxes_price + self.baggage_fee_estimate + self.transfer_cost_estimate, 2
        )

    @property
    def dedup_signature(self) -> str:
        """Gera chave única da viagem para evitar duplicatas vindas de múltiplos brokers."""
        normalized_carrier = self.carrier_name.strip().upper()
        dep_str = self.departure_time.strftime("%Y-%m-%dT%H:%M")
        raw_key = f"{self.modal}|{normalized_carrier}|{self.origin_external_id}|{self.destination_external_id}|{dep_str}|{self.service_class.upper()}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]

class BaseCollector(ABC):
    provider_name: str

    @abstractmethod
    async def fetch_quotes(
        self, origin_ext_id: str, dest_ext_id: str, travel_date: str
    ) -> List[StandardizedQuote]:
        """
        Recebe os códigos externos do provedor e a data (YYYY-MM-DD)
        e retorna uma lista de StandardizedQuote.
        Deve ser decorado com @retry do pacote tenacity nas classes filhas.
        """
        pass
```

---

## 6. Mathematical Formulas & Core Algorithms

### 6.1. Dynamic Cache TTL Policy (`app/cache/ttl_policy.py`)
Para proteger o *Look-to-Book ratio* e evitar bloqueio de IP em scrapers:
* **Viagem em $\le 2$ dias:** $\text{TTL} = 30 \text{ minutos}$
* **Viagem entre $3$ e $7$ dias:** $\text{TTL} = 2 \text{ horas}$
* **Viagem entre $8$ e $21$ dias:** $\text{TTL} = 6 \text{ horas}$
* **Viagem em $> 21$ dias:** $\text{TTL} = 24 \text{ horas}$

### 6.2. Trivio Score Formula (`app/analyzers/trivio_scorer.py`)
O **Trivio Score** classifica opções aéreos e rodoviárias na mesma régua (quanto menor o score, melhor a opção):

$$\text{Trivio Score} = P_{\text{efetivo}} + \left( \frac{T_{\text{porta-a-porta}}}{60} \times V_{\text{hora}} \times F_{\text{conforto}} \right) + \sum \text{Penalidades}_{\text{atrito}}$$

Onde:
* $P_{\text{efetivo}}$: Tarifa + Taxas + Bagagem + Deslocamento urbano até o terminal.
* $T_{\text{porta-a-porta}}$: Tempo de voo/estrada + antecedência de embarque (90 min Aéreo vs. 20 min Rodoviário) + tempo de deslocamento até o terminal.
* $V_{\text{hora}}$: Valor base da hora do viajante (default configurável, ex: $\text{R\$ } 35,00/\text{h}$).
* $F_{\text{conforto}}$: Multiplicador de desgaste da viagem:
  * Ônibus Leito / Leito-Cama Noturno: $0.55$ (viajante dorme e economiza diária de hotel).
  * Voo Direto / Ônibus Semi-Leito: $1.00$.
  * Ônibus Convencional / Executivo Diurno Longo: $1.35$.
* $\sum \text{Penalidades}_{\text{atrito}}$:
  * $+40$ pontos por conexão/baldeação.
  * $+60$ pontos se o embarque ou desembarque ocorrer na janela crítica da madrugada (01:00 às 04:59).
  * $+10$ pontos por parada intermediária excedente na rodovia (acima de 2 paradas).
  * $+80$ pontos se houver troca de aeroporto na conexão (ex: CGH $\leftrightarrow$ GRU).

---

## 7. Pros, Cons & Setbacks Schema (`route_metadata` JSON Standard)

```json
{
  "line_code": "ANTT-12345 ou LA3456",
  "is_direct": false,
  "is_virtual_interline": false,
  "stops_count": 4,
  "intermediate_stops": ["Cidade A", "Cidade B"],
  "route_divergence_note": "Via litoral (adiciona 55 min em relação à rodovia principal)",
  "price_thermometer": {
    "status": "LOW",
    "percentile": 22,
    "historical_median_brl": 310.0,
    "recommendation": "Preço 24% abaixo da média histórica para esta rota. Bom momento para compra."
  },
  "pros": [
    "Embarque central (economia estimada de R$ 65 em transfer)",
    "Bagagem despachada de 23kg inclusa gratuitamente",
    "Poltrona Leito 180 graus (viagem noturna produtiva)"
  ],
  "cons": [
    "Rota paradora (4 paradas intermediárias no trajeto)",
    "Horário de chegada de madrugada (03:40)"
  ],
  "potential_setbacks": [
    "Trecho de serra com histórico de retenção por obras (BR-116)",
    "Conexão curta de 45 minutos em GRU (alto risco em dias chuvosos)"
  ]
}
```

---

## 8. Step-by-Step Implementation Plan for Antigravity

Siga a ordem exata das fases abaixo durante a implementação assistida no **Antigravity**:

### Phase 1: Foundation, Geo & Database Setup
- [ ] **Step 1.1:** Criar a estrutura de diretórios do projeto `trivio/` e instalar o `requirements.txt`.
- [ ] **Step 1.2:** Implementar `app/config.py` e `app/database.py` (SQLite `sqlite:///./trivio_dev.db` por padrão, compatível com PostgreSQL via `.env`).
- [ ] **Step 1.3:** Implementar `app/models.py` contendo `Hub`, `Terminal`, `ProviderMapping` e `FareQuote`.
- [ ] **Step 1.4:** Implementar `app/geo/entity_resolver.py` (usando `rapidfuzz`) e `app/geo/catchment.py` (usando `geopy` para localizar terminais num raio de até 120 km).
- [ ] **Step 1.5:** Criar `scripts/init_db.py` com *seed* inicial de Hubs (ex: São Paulo, Campinas, Rio de Janeiro, Curitiba, Porto Alegre, Pelotas, Rio Grande), seus respectivos aeroportos e rodoviárias, custos médios de transfer urbano e mapeamentos.

### Phase 2: Smart Cache, Deduplication & Collector Core
- [ ] **Step 2.1:** Implementar `app/cache/ttl_policy.py` e `app/cache/deduplicator.py` para filtrar cotações repetidas mantendo o menor `total_effective_price`.
- [ ] **Step 2.2:** Implementar `app/collectors/base.py` com `StandardizedQuote` e suporte a `tenacity` para retentativas resilientes.
- [ ] **Step 2.3:** Implementar o primeiro coletor rodoviário em `app/collectors/road/` (interceptação de JSON de gateways rodoviários/agregadores).
- [ ] **Step 2.4:** Implementar o coletor aéreo em `app/collectors/air/` normalizando taxas de embarque, antecedência de check-in e franquia de bagagem.

### Phase 3: Intelligence Engines (Trivio Score, Pros/Cons & Price Thermometer)
- [ ] **Step 3.1:** Implementar `app/analyzers/route_evaluator.py` para popular automaticamente as listas de `pros`, `cons` e `potential_setbacks` com base nos metadados da rota.
- [ ] **Step 3.2:** Implementar `app/analyzers/trivio_scorer.py` aplicando a equação do **Trivio Score** e o gerador de combinações **Hacker Fares** (Ida Modal A + Volta Modal B).
- [ ] **Step 3.3:** Implementar `app/analyzers/price_thermometer.py` usando `polars` para comparar o preço coletado com os quartis históricos do banco.

### Phase 4: Virtual Interlining Graph & CLI Runner
- [ ] **Step 4.1:** Implementar `app/analyzers/interliner.py` usando `networkx` para construir rotas conectadas híbridas ($A \xrightarrow{\text{Ônibus}} H \xrightarrow{\text{Avião}} B$) respeitando janelas mínimas de conexão e tempo de deslocamento entre rodoviária e aeroporto.
- [ ] **Step 4.2:** Criar `scripts/test_collector.py` usando `rich` para exibir no terminal o comparativo completo da rota: Tabela de Preços Efetivos, Tempo Porta a Porta, Trivio Score, Termômetro de Preço e a Matriz de Prós/Contras/Contratempos.