# Project Specification & Roadmap: TRIVIO

> **Target Audience:** AI Coding Agents (Antigravity), Automated Scaffolds & Developers  
> **Project Name:** Trivio (Trip + Via / Cruzamento de 3 caminhos: Ar, Terra e Melhor Custo-Benefício)  
> **Domain:** Intermodal Travel Intelligence, Virtual Interlining & Price Aggregation (Brazil - National Scope)  
> **Primary Stack:**
>   - **Backend & Collectors:** Python 3.11+, SQLAlchemy 2.0+, Httpx (REST/JSON), Protego (robots.txt compliance), Tenacity (Resilience), NetworkX (Virtual Interlining Graph), GeoPy & RapidFuzz (Geo & Entity Matching), Polars (Price Thermometer), FastAPI (REST + WebSockets Streaming)
>   - **Frontend Web:** Next.js 14+ (App Router), React, TypeScript, Tailwind CSS, Lucide Icons
>   - **Persistence:** PostgreSQL / SQLite (Dev). O cache de consultas pagas fica no próprio banco (tabela `provider_queries`); um cache externo (ex.: Redis) só entra quando houver mais de uma instância da API.

---

## 1. System Context & Product Vision

**Trivio** é uma plataforma nacional (Brasil) de inteligência de mobilidade intermodal (Aéreo + Rodoviário). Diferente de agregadores comuns que tratam cada modal em silos isolados, o Trivio calcula o **custo real efetivo**, o **tempo porta a porta**, costura conexões mistas (**Ônibus + Avião**), transmite os resultados em **tempo real via WebSockets** e gera uma matriz detalhada de **prós, contras e possíveis contratempos** de cada rota e empresa operadora.

### Core Differentiators & Search Engine Mechanics:

1. **Custo Efetivo Total:** Soma tarifa base + taxas de embarque + estimativa de bagagem + custo de deslocamento até o terminal (aeroporto distante vs. rodoviária central).
2. **Mapeamento de Divergência de Rotas & Contratempos:** Identifica se uma viação faz rota expressa ou "pinga-pinga" (paradas intermediárias excessivas), trechos de serra/obras, ou conexões aéreas curtas com risco de perda de voo ou troca de aeroporto.
3. **Coleta Identificada e em Conformidade:** Consumo assíncrono de APIs REST/JSON com `httpx`, sempre como um agente identificado (`TrivioBot/…` com canal de contato), verificando o `robots.txt` de cada host antes da requisição, respeitando `Crawl-delay` e `Retry-After`. **HTTP 401/403 é tratado como negativa definitiva do operador do site:** sem retentativa, sem navegador headless, sem contorno. Provedor que recusa acesso automatizado só é liberado por via comercial (API oficial ou programa de parceiros).
4. **Streaming Progressivo (WebSockets):** As cotações não esperam todos os provedores terminarem; elas vão "pipocando" na interface em tempo real assim que cada modal ou empresa responde.
5. **Smart Cache & TTL Dinâmico (Decolar Pattern):** TTL baseado na proximidade da viagem ($\le 2$ dias: 30 min; $> 21$ dias: 24 horas), eliminando a maior parte das requisições repetidas, o que reduz o custo das APIs pagas e a carga sobre os provedores (*Look-to-Book optimization*).
6. **Deduplicação & "Code-Share" Multi-Canal via SHA-256:** Agrupamento de ofertas idênticas comercializadas por múltiplos canais (ex: a mesma partida vendida por dois distribuidores, ou um voo em code-share) usando assinatura `dedup_signature`, exibindo o canal mais barato.
7. **Virtual Interlining Intermodal (Kiwi.com Pattern):** Grafo em memória (`networkx`) que combina trechos rodoviários e aéreos independentes quando a conexão híbrida (ex: Ônibus até capital vizinha + Voo direto) supera o custo-benefício das rotas diretas tradicionais.
8. **Hub Catchment Area / Raio Geográfico (Rome2Rio / Skyscanner Pattern):** Expansão geodésica (`geopy`) que busca terminais alternativos em um raio configurável (ex: 100-120 km da cidade de origem/destino), somando o deslocamento terrestre na equação.
9. **Price Thermometer / Compra Preditiva (Hopper / Google Flights Pattern):** Análise estatística de séries históricas ($P_{25}, P_{50}, P_{75}$) via `polars` que classifica se a tarifa atual está *Barata*, *Na Média* ou *Cara* para aquela rota e época. A série de referência do rodoviário vem dos **dados abertos da ANTT (Monitriip)**: bilhetes efetivamente comercializados, publicados mensalmente sob CC-BY — preço transacionado real, não estimativa.
10. **Intermodal Hacker Fares (Kayak Pattern):** Combinação assimétrica de bilhetes *one-way* independentes (ex: Ida de Avião para ganhar tempo + Volta de Ônibus Leito noturno para economizar diária e passagem).
11. **Trivio Score / Índice de Atrito (Hipmunk "Agony Index" Pattern):** Algoritmo de ranqueamento que pondera Preço Efetivo + Tempo Porta a Porta + Penalidades de Atrito (madrugada, baldeações, excesso de paradas).

---

## 2. Target Directory Structure

O agente no Antigravity deve criar e manter a seguinte árvore de diretórios no workspace:

```text
trivio/
├── app/
│   ├── config.py                  # Variáveis de ambiente (.env) e constantes do motor
│   ├── database.py                # SQLAlchemy Engine, SessionLocal e migração leve de colunas
│   ├── models.py                  # Hub, Terminal, ProviderMapping, FareQuote, RouteMonthlyFare, ProviderQuery, BusSchedule, GtfsRouteFare
│   ├── cache/                     # Smart Cache e Deduplicação (Decolar Pattern)
│   │   ├── ttl_policy.py          # TTL dinâmico baseado na data de embarque
│   │   └── deduplicator.py        # Merge de viagens idênticas entre provedores (SHA-256)
│   ├── geo/                       # Inteligência Geográfica e Resolução de Entidades
│   │   ├── catchment.py           # Terminais em raio geodésico (Rome2Rio Pattern)
│   │   └── entity_resolver.py     # Fuzzy matching de cidades/terminais via RapidFuzz
│   ├── api/                       # Camada de API e Streaming
│   │   ├── main.py                # FastAPI, lifespan, CORS e tratamento de exceções
│   │   ├── routes.py              # REST: hubs, terminais, autocomplete, catchment, aéreo e projeção rodoviária
│   │   ├── schemas.py             # Schemas Pydantic v2
│   │   └── websocket.py           # /ws/search: streaming de cotações em tempo real
│   ├── collectors/                # Ingestão de preços e dados abertos
│   │   ├── base.py                # BaseCollector, StandardizedQuote e retry (Tenacity)
│   │   ├── compliance.py          # robots.txt (Protego), User-Agent identificado, ritmo e recusas
│   │   ├── air/                   # Aéreo ao vivo: Ignav (API contratada), companhias e taxas de embarque
│   │   ├── anac/                  # Microdados de tarifas aéreas da ANAC (régua do Termômetro)
│   │   ├── antt/                  # Dados abertos da ANTT (CC-BY): Monitriip, série mensal e grade de horários
│   │   ├── gtfs/                  # Feeds GTFS de órgãos/operadores (horário e tarifa por linha)
│   │   └── road/                  # ClickBus (desligada até a API B2B) e taxonomia de classes
│   ├── services/                  # Orquestração do Pipeline de Dados
│   │   ├── air_quotes.py          # Aéreo: cache de consultas, orçamento de requests e coalescência
│   │   ├── road_quotes.py         # Ônibus estimado pela série da ANTT
│   │   ├── gtfs_road_quotes.py    # Ônibus a partir de horários e tarifas GTFS
│   │   └── quote_pipeline.py      # Resolução canônica, transfer urbano, dedup e persistência
│   └── analyzers/                 # Motores Analíticos, Grafos e Regras de Negócio
│       ├── route_evaluator.py     # Prós, Contras e Contratempos da rota
│       ├── trivio_scorer.py       # Trivio Score (Agony Index) e Hacker Fares
│       ├── interliner.py          # Grafo NetworkX para conexões híbridas (Ônibus + Avião)
│       ├── price_thermometer.py   # Percentis históricos de preço (Hopper Pattern)
│       ├── road_fare_projection.py# Projeção de tarifa rodoviária sobre a série da ANTT
│       └── road_reference.py      # Faixa histórica de preço por rota e classe (ANTT), fora do ranking
├── frontend/                      # Aplicação Web (Next.js + React + Tailwind CSS)
│   ├── src/
│   │   ├── app/                   # App Router (página, layout, estilos)
│   │   ├── components/            # Cards de cotação, Hacker Fares, barra de busca, autocomplete
│   │   ├── data/                  # Cliente do autocomplete (localidades vêm só da API)
│   │   ├── hooks/                 # useSearchWebSocket (streaming em tempo real)
│   │   └── types/                 # Contratos TypeScript da API e do WebSocket
│   └── public/                    # Logotipos (logo_clean.png, logo_icon.png)
├── scripts/
│   ├── init_db.py                 # Cria tabelas e faz o seed inicial de hubs e terminais
│   ├── hub_candidates.py          # Hubs fora da semente (FLN, CNF, BSB), aplicados sob demanda
│   ├── import_antt_fares.py       # Tarifas históricas reais da ANTT (Monitriip)
│   ├── import_antt_schedules.py   # Grade de horários interestaduais da ANTT
│   ├── import_anac_fares.py       # Tarifas aéreas domésticas vendidas (ANAC)
│   ├── import_gtfs.py             # Importa um feed GTFS
│   ├── report_hub_coverage.py     # Cobertura de dados rodoviários até os hubs
│   └── test_collector.py          # CLI (Rich) que roda a busca real do /ws/search no terminal
├── tests/                         # Suíte pytest (banco SQLite temporário em conftest.py)
├── .env.example
├── requirements.txt
└── roteiro_trivio.md
```

---

## 3. Dependencies (`requirements.txt`)

```text
# Core & Database
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
websockets>=12.0
sqlalchemy>=2.0.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
python-dotenv>=1.0.0
tzdata>=2024.1

# HTTP, Compliance & Resilience
httpx>=0.26.0
tenacity>=8.2.0
protego>=0.3.0   # Parser de robots.txt (spec do Google: regra mais específica vence)

# Graph, Geo & Entity Resolution (Kiwi / Rome2Rio / Kayak Patterns)
networkx>=3.2.0
geopy>=2.4.0
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

### Estratégia de Coleta (`app/collectors/compliance.py`):

Toda requisição a sites de terceiros passa pelo `CompliantHTTPClient`, que garante quatro condições:

1. **Identificação honesta:** o `User-Agent` declara o robô e oferece contato
   (`TrivioBot/1.0 (+https://…; contato@…)`). O Trivio nunca se passa por navegador.
2. **Autorização pelo `robots.txt`:** cada URL é verificada antes da requisição com `protego`.
   > ⚠️ Não usar `urllib.robotparser` da stdlib: ele aplica a *primeira* regra que casa, então
   > um `Allow: /` no topo anula todos os `Disallow` seguintes. A especificação do Google
   > (implementada pelo `protego`) usa a regra *mais específica*.
   > Se o `robots.txt` não puder ser lido (5xx ou falha de rede), assume-se proibição.
3. **Ritmo educado:** respeita `Crawl-delay`, mantém intervalo mínimo por host e honra `Retry-After` (HTTP 429).
4. **Obediência a recusas:** HTTP 401/403 vira `NotAuthorizedError` e encerra a coleta. O coletor marca
   `access_denied` e devolve lista vazia. **Não existe escalonamento para navegador headless:**
   contornar a recusa seria evasão de detecção de bot.

APIs oficiais e credenciadas (ex: Ignav) usam `get_default_async_client`, que também se identifica,
mas dispensa a checagem de `robots.txt` por serem acessos contratados.

**Estado dos provedores rodoviários:** a ClickBus proíbe no `robots.txt` as páginas de resultado
(`Disallow: /*?departureDate=`) e seu WAF responde 403 ao endpoint interno. Portanto não há coleta
autorizada hoje; o acesso depende da API B2B de parceiros. Sem provedor real, o Trivio exibe
estimativas explicitamente rotuladas como simulação, que nunca são persistidas no banco.

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
Para proteger o *Look-to-Book ratio* e evitar bloqueio de IP em scrapers ($\Delta t = \text{Data da Viagem} - \text{Hoje}$):
* **Viagem em $\le 2$ dias:** $\text{TTL} = 30 \text{ minutos}$ (1.800s)
* **Viagem entre $3$ e $7$ dias:** $\text{TTL} = 2 \text{ horas}$ (7.200s)
* **Viagem entre $8$ e $21$ dias:** $\text{TTL} = 6 \text{ horas}$ (21.600s)
* **Viagem em $> 21$ dias:** $\text{TTL} = 24 \text{ horas}$ (86.400s)

### 6.2. Trivio Score Formula (`app/analyzers/trivio_scorer.py`)
O **Trivio Score** classifica opções aéreas e rodoviárias na mesma régua (quanto menor o score, melhor a opção):

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
- [x] **Step 1.1:** Criar a estrutura de diretórios do projeto `trivio/` e instalar o `requirements.txt`.
- [x] **Step 1.2:** Implementar `app/config.py` e `app/database.py` (SQLite `sqlite:///./trivio_dev.db` por padrão, compatível com PostgreSQL via `.env`).
- [x] **Step 1.3:** Implementar `app/models.py` contendo `Hub`, `Terminal`, `ProviderMapping` e `FareQuote` conforme especificado na Seção 4.
- [x] **Step 1.4:** Implementar `app/geo/entity_resolver.py` (usando `rapidfuzz`) e `app/geo/catchment.py` (usando `geopy` para localizar terminais num raio de até 120 km).
- [x] **Step 1.5:** Criar `scripts/init_db.py` com *seed* inicial de Hubs (São Paulo, Campinas, Rio de Janeiro, Curitiba, Porto Alegre, Pelotas, Rio Grande), seus aeroportos e rodoviárias, custos de transfer e mapeamentos de provedores.

### Phase 2: Smart Cache, Deduplication & Collector Core
- [x] **Step 2.1:** Implementar `app/cache/ttl_policy.py` e `app/cache/deduplicator.py` para filtrar cotações repetidas por assinatura SHA-256 mantendo a melhor oferta de compra.
- [x] **Step 2.2:** Implementar `app/collectors/base.py` com `StandardizedQuote`, cálculo de `dedup_signature` e resiliência via `tenacity`.
- [x] **Step 2.3:** Implementar o primeiro coletor rodoviário em `app/collectors/road/` usando `httpx` através do `CompliantHTTPClient` (robots.txt, identificação e ritmo), tratando 401/403 como negativa definitiva do provedor.
- [x] **Step 2.4:** Implementar o coletor aéreo em `app/collectors/air/` normalizando taxas de embarque, antecedência de check-in e franquia de bagagem.

### Phase 3: Intelligence Engines (Trivio Score, Pros/Cons & Price Thermometer)
- [x] **Step 3.1:** Implementar `app/analyzers/route_evaluator.py` para popular automaticamente as listas de `pros`, `cons` e `potential_setbacks` com base nos metadados da rota.
- [x] **Step 3.2:** Implementar `app/analyzers/trivio_scorer.py` aplicando a equação do **Trivio Score** e o gerador de combinações **Hacker Fares** (Ida Modal A + Volta Modal B).
- [x] **Step 3.3:** Implementar `app/analyzers/price_thermometer.py` usando `polars` para comparar o preço coletado com os quartis históricos do banco.

### Phase 4: Virtual Interlining Graph & CLI Runner
- [x] **Step 4.1:** Implementar `app/analyzers/interliner.py` usando `networkx` para construir rotas conectadas híbridas ($A \xrightarrow{\text{Ônibus}} H \xrightarrow{\text{Avião}} B$) respeitando janelas mínimas de conexão e tempo de deslocamento entre rodoviária e aeroporto.
- [x] **Step 4.2:** Criar `scripts/test_collector.py` usando `rich` para exibir no terminal o comparativo completo da rota: Tabela de Preços Efetivos, Tempo Porta a Porta, Trivio Score, Termômetro de Preço, referência histórica da ANTT e Hacker Fares. A CLI roda a mesma busca do `/ws/search`, sem dados de demonstração; o Ignav só é consultado com `--live-air`.

### Phase 5: API Layer & Real-Time WebSockets Streaming
- [x] **Step 5.1:** Implementar `app/api/main.py` com FastAPI, middlewares de CORS e tratamento de exceções.
- [x] **Step 5.2:** Implementar `app/api/routes.py` com endpoints REST para busca de hubs, terminais, catchment areas e consulta a cotações em cache.
- [x] **Step 5.3:** Implementar `app/api/websocket.py` (`/ws/search`) para orquestrar buscas simultâneas em múltiplos coletores:
  - Dispara tarefas assíncronas concorrentes para aéreo, rodoviário e virtual interlining.
  - Conforme cada coletor finaliza seu lote, emite eventos WebSocket (`QUOTE_BATCH`) para a interface em tempo real, sem aguardar os demais provedores terminarem.
  - Armazena os resultados consolidados no cache temporário com TTL dinâmico.

### Phase 6: Frontend Web Intermodal (Next.js + React + Tailwind CSS)
- [x] **Step 6.1:** Inicializar projeto frontend com Next.js 14+ (App Router), TypeScript e Tailwind CSS.
- [x] **Step 6.2:** Desenvolver a barra de busca intermodal (Origem, Destino, Data de Ida/Volta, Valor da Hora e, na Conexão Híbrida, Raio Geodésico). A bagagem de 23kg entra sempre no custo efetivo do aéreo, sem filtro.
- [x] **Step 6.3:** Implementar o hook `useSearchWebSocket` que conecta ao endpoint `/ws/search` do FastAPI, exibindo skeleton loaders e renderizando os cards de passagens progressivamente ("pipocando" na tela).
- [x] **Step 6.4:** Desenvolver os cards de cotação com o comparativo de **Custo Efetivo Real vs. Tarifa Base**, badges de **Prós & Contras**, **Termômetro de Preço** e alertas visuais de **Contratempos de Rota** e **Conexões Híbridas (Virtual Interlining)**.
