# Trivio — Travel Intelligence & Intermodal Mobility Platform

> **O primeiro comparador de viagens de longa distância do Brasil que coloca Avião e Ônibus na mesma régua.**  
> Cálculo de Custo Efetivo Real de porta a porta, **Trivio Score** (Índice de Agonia com bônus de sono produtivo no leito noturno), **Hacker Fares**, **Virtual Interlining** e streaming em tempo real via WebSockets.

---

## 🎯 Visão Geral do Produto

O mercado brasileiro de mobilidade de passageiros sofre com assimetria de informações e custos ocultos:
- Uma passagem aérea de **R$ 200** frequentemente ultrapassa **R$ 550** ao somar taxas de embarque, franquia de bagagem de 23kg e transporte urbano até aeroportos distantes (ex: Viracopos ou Guarulhos).
- Voos com conexões curtas geram alto risco de perda e estresse desnecessário.
- Por outro lado, um ônibus **Leito Cama Noturno** permite dormir com conforto pleno, economizar uma diária de hotel e chegar ao centro da cidade no início da manhã.

O **Trivio** resolve essa assimetria através de um pipeline algorítmico avançado:
1. **Custo Efetivo Real Porta a Porta:** Desagrega e recalcula taxas ANAC/ANTT, estimativas de bagagem e deslocamentos urbanos.
2. **Trivio Score (Agony Index):** Métrica proprietária que quantifica tempo, custo, cansaço físico e conveniência horária.
3. **Termômetro de Preços (Polars Engine):** Identificação instantânea de tarifas (*LOW*, *MEDIUM*, *HIGH*) baseadas em quartis estatísticos (P25/P50/P75).
4. **Virtual Interlining & Conexões Híbridas:** Algoritmo em grafo NetworkX que viabiliza rotas combinadas (ônibus regional + voo de capital) com garantia de tempo mínimo de conexão (MCT).
5. **Hacker Fares:** Geração automática de opções desdobradas (ex: ida de avião e volta de leito noturno), desbloqueando economias de até 60%.

---

## 🏗️ Arquitetura do Sistema

```mermaid
flowchart TD
    subgraph Frontend["Frontend (Next.js 14 App Router)"]
        UI["Interface White Clean\n(Tailwind CSS + Lucide)"]
        WSClient["useSearchWebSocket\n(Hook Reativo)"]
    end

    subgraph API["Backend API (FastAPI)"]
        WS["WebSocket Endpoint\n(/ws/search)"]
        REST["REST API v1\n(/api/v1/hubs, /catchment)"]
    end

    subgraph Engines["Intelligence & Processing Engines"]
        Evaluator["Route Evaluator\n(Pros, Cons & Setbacks)"]
        Scorer["Trivio Scorer\n(Índice de Agonia & Hacker Fares)"]
        Thermometer["Price Thermometer\n(Polars Quartis P25/P50/P75)"]
        Interliner["Virtual Interlining\n(NetworkX MultiDiGraph)"]
    end

    subgraph Collectors["Smart Collectors & Cache"]
        Compliance["Compliance Layer\n(robots.txt + User-Agent identificado)"]
        ClickBus["ClickBus Collector\n(requer autorização do provedor)"]
        Ignav["AirQuoteService + Ignav\n(Aéreo ao vivo, cache e orçamento)"]
        Deduplicator["Broker Deduplicator\n(SHA-256 Canonical Signatures)"]
        TTLPolicy["Smart TTL Policy\n(D-1 a D-30+ Caching)"]
    end

    subgraph Storage["Storage Foundation"]
        DB[(SQLite / PostgreSQL\nSQLAlchemy 2.0)]
    end

    UI --> WSClient
    WSClient <-->|Streaming em Tempo Real| WS
    WS --> Compliance
    Compliance --> ClickBus
    ClickBus & Ignav --> Deduplicator
    Deduplicator --> TTLPolicy
    TTLPolicy --> DB
    ClickBus & Ignav --> Evaluator
    Evaluator --> Scorer
    Scorer --> Thermometer
    Thermometer --> Interliner
    Interliner --> WS
```

---

## 🛠️ Stack Tecnológica

### Backend (Python 3.13)
- **Framework Web:** FastAPI (com ciclo de vida assíncrono via `lifespan` e suporte a WebSockets).
- **ORM & Banco de Dados:** SQLAlchemy 2.0 (sessões síncronas), compatível com SQLite e PostgreSQL.
- **Engenharia de Dados:** Polars (cálculo vetorial ultrarrápido de quartis estatísticos de preços).
- **Grafos & Conexões:** NetworkX (algoritmo de interconexão modal com validação de MCT).
- **Geolocalização:** Geopy / Geographiclib (distâncias geodésicas e raios de abrangência regional).
- **Resiliência de Rede:** HTTPX + Tenacity (exponential backoff com jitter e isolamento bulkhead).
- **Conformidade de Coleta:** Protego (robots.txt pela spec do Google), agente identificado e respeito a `Crawl-delay` / `Retry-After`.
- **Qualidade & Testes:** Pytest, Pytest-Asyncio (106 testes automatizados).

### Frontend (Node.js 20+ / Next.js 14)
- **Arquitetura:** Next.js 14 (App Router, Server & Client Components).
- **Linguagem:** TypeScript com tipagem estrita de contratos de API e WebSockets.
- **Estilização:** Tailwind CSS (tema corporativo *White Clean* com esquema oficial de cores da marca).
- **Ícones & UI:** Lucide React.

---

## 🤝 Política de Coleta de Dados

O Trivio só obtém preços de fontes que autorizam o acesso. Toda requisição a sites de
terceiros passa por [`app/collectors/compliance.py`](app/collectors/compliance.py), que garante:

| Princípio | Como é aplicado |
|---|---|
| **Identificação honesta** | `User-Agent: TrivioBot/1.0 (+url; contato)`. Nunca imitamos um navegador. |
| **Autorização** | O `robots.txt` de cada host é verificado antes da requisição, com `protego`. |
| **Ritmo educado** | Respeita `Crawl-delay`, intervalo mínimo por host e `Retry-After` (HTTP 429). |
| **Obediência a recusas** | HTTP 401/403 encerra a coleta. Sem retentativa, sem navegador headless, sem contorno. |

> ⚠️ **Não use `urllib.robotparser`.** A implementação da stdlib aplica a *primeira* regra que
> casa, então um `Allow: /` no topo do arquivo anula todos os `Disallow` seguintes — ela chega a
> liberar `/login` e `/checkout/`. A spec do Google usa a regra *mais específica*, que é o que o
> `protego` implementa.

Se um provedor recusa acesso automatizado, o desbloqueio é **comercial** (API oficial ou programa
de parceiros), nunca técnico.

### Estado atual dos provedores

| Provedor | Modal | Situação |
|---|---|---|
| **Ignav** | Aéreo (preço ao vivo) | ✅ API contratada, cobrada por request. Configure `IGNAV_API_KEY` no `.env`. |
| **ANAC** (microdados de tarifas) | Aéreo (régua do Termômetro) | ✅ Dados abertos oficiais. Importe com `scripts/import_anac_fares.py`. |
| **ANTT (dados abertos)** | Rodoviário | ✅ Público e sem chave. Tarifas **históricas reais** sob CC-BY (a API CKAN é proibida pelo `robots.txt`; usamos os caminhos permitidos). |
| **ClickBus** | Rodoviário | ⛔ Sem autorização. O `robots.txt` proíbe as páginas de resultado (`Disallow: /*?departureDate=`) e o WAF responde 403 ao endpoint interno. Depende da API B2B de parceiros. |

### Tarifas históricas reais (ANTT / Monitriip)

O [Portal de Dados Abertos da ANTT](https://dados.antt.gov.br) publica, sob licença Creative
Commons Attribution, os **bilhetes efetivamente comercializados** no transporte rodoviário
interestadual e internacional (conjunto `monitriip-bilhetes-de-passagem`, um CSV por mês desde
2019, atualizado mensalmente).

O portal roda CKAN e teria uma Action API em `/api/`, mas o `robots.txt` dele a proíbe
(`Disallow: /api/`). Então a descoberta dos arquivos é feita pela **página pública do conjunto**
e o download pelas **URLs de recurso**, ambas permitidas. O `Crawl-Delay: 10` do portal é
respeitado, o que deixa a importação propositalmente lenta.

O Trivio usa essa série para alimentar o **Termômetro de Preço** com percentis reais por
corredor, em vez de depender de um histórico próprio que leva meses para acumular:

```bash
# Ver os arquivos mensais publicados
python scripts/import_antt_fares.py --list

# Conferir o cabeçalho real antes de importar (o esquema da ANTT já mudou entre safras)
python scripts/import_antt_fares.py --inspect venda_passagem_09_2024.csv

# Baixar e importar os 3 meses mais recentes
python scripts/import_antt_fares.py --months 3
```

Os bilhetes individuais **não** são persistidos. O importador grava uma série mensal por rota,
classe e mês de viagem (tabela `route_monthly_fares`), com o preço médio ponderado pela quantidade
de bilhetes. Só a **tarifa normal** entra: gratuidades e descontos de grupo ficam de fora.

O Termômetro calcula os percentis sobre os **meses de viagem** da rota, e só classifica quando há
pelo menos **6 meses** (`MIN_OFFICIAL_MONTHS`). Abaixo disso, recorre ao histórico coletado pelo
próprio Trivio ou mostra `INSUFFICIENT_DATA`. Um arquivo mensal sozinho quase não cobre rota
nenhuma: a cobertura cresce com cada mês importado.

> A comparação usa o **preço do bilhete** (tarifa + taxas), não o custo efetivo porta a porta:
> a ANTT mede a passagem, então somar transfer urbano classificaria toda cotação como cara.

**Limite:** a ANTT cobre o histórico, não a disponibilidade. Preço comprável em tempo real
continua dependendo de acordo com um distribuidor.

Quando nenhum provedor real responde, o Trivio exibe **estimativas calculadas pela distância
geodésica**, sempre marcadas na interface como *"Estimativa simulada"*, sem link de compra e
**nunca gravadas no banco** — para não contaminar o histórico do Termômetro de Preço.

---

## 📁 Estrutura de Diretórios

```
trivio/
├── app/
│   ├── analyzers/              # Motores de Inteligência do Trivio
│   │   ├── interliner.py       # Virtual Interlining com NetworkX
│   │   ├── price_thermometer.py# Termômetro de preços com Polars
│   │   ├── route_evaluator.py  # Avaliador de prós, contras e contratempos
│   │   └── trivio_scorer.py    # Algoritmo do Trivio Score e Hacker Fares
│   ├── api/                    # Camada de Apresentação e Comunicação
│   │   ├── main.py             # Instância do FastAPI, Lifespan e CORS
│   │   ├── routes.py           # Endpoints REST (Hubs, Terminais, Catchment)
│   │   ├── schemas.py          # Schemas de validação Pydantic v2
│   │   └── websocket.py        # Orquestrador de streaming em tempo real
│   ├── cache/                  # Camada de Cache Inteligente
│   │   ├── deduplicator.py     # Deduplicação cross-broker e assinaturas SHA-256
│   │   └── ttl_policy.py       # Política dinâmica de TTL conforme antecedência
│   ├── collectors/             # Coletores de Provedores Modais
│   │   ├── base.py             # Classe abstrata, retries e modelo StandardizedQuote
│   │   ├── compliance.py       # robots.txt, User-Agent identificado, ritmo e recusas
│   │   ├── air/ignav.py        # Coletor aéreo ao vivo (Ignav)
│   │   ├── anac/               # Microdados de tarifas aéreas da ANAC (régua)
│   │   ├── antt/               # Dados abertos da ANTT (Monitriip)
│   │   │   ├── portal.py       # Descoberta e download pelos caminhos permitidos
│   │   │   └── monitriip.py    # Detecção de esquema e agregação de tarifas
│   │   └── road/
│   │       ├── clickbus.py     # Coletor ClickBus (requer autorização do provedor)
│   │       └── taxonomy.py     # Normalização de classes (Leito, Semi-Leito, …)
│   ├── geo/                    # Inteligência Geográfica
│   │   ├── catchment.py        # Identificação de aeroportos/rodoviárias vizinhas
│   │   └── entity_resolver.py  # Normalização de códigos IATA, ANTT e Nomes
│   ├── services/               # Orquestração do Pipeline de Dados
│   │   └── quote_pipeline.py   # Resolução canônica, transfer urbano, dedup e persistência
│   ├── config.py               # Configurações centralizadas Pydantic Settings
│   ├── database.py             # Sessões assíncronas do SQLAlchemy 2.0
│   └── models.py               # Modelos relacionais (Hub, Terminal, FareQuote)
├── frontend/                   # Aplicação Web Next.js 14
│   ├── src/
│   │   ├── app/                # App Router (page.tsx, layout.tsx, globals.css)
│   │   ├── components/         # Header, SearchBar, TrustPillars, SkeletonCard
│   │   ├── hooks/              # useSearchWebSocket (streaming reativo)
│   │   └── types/              # Interfaces TypeScript unificadas
│   └── public/                 # Ativos estáticos e logotipos oficiais
├── scripts/                    # Scripts utilitários e de verificação
│   ├── init_db.py              # Inicialização e carga inicial dos Hubs brasileiros
│   ├── import_antt_fares.py    # Importa tarifas históricas reais da ANTT (Monitriip)
│   ├── test_collector.py       # CLI Runner interativo com saída rica no terminal
│   └── capture_preview.py      # Automação Playwright do frontend próprio (preview visual)
├── tests/                      # Suíte completa de testes automatizados (106 testes)
├── .env.example                # Template de configuração de variáveis de ambiente
├── .gitignore                  # Regras de exclusão para Git
├── requirements.txt            # Dependências Python travadas
└── roteiro_trivio.md           # Roteiro canônico e roadmap de desenvolvimento
```

---

## 🚀 Como Executar Localmente

### Pré-requisitos
- **Python 3.11+** (recomendado Python 3.13)
- **Node.js 18+** (recomendado Node.js 20+)
- **Git**

---

### 1. Configuração do Backend (Python)

1. Clone o repositório ou acesse a pasta raiz:
   ```bash
   cd trivio
   ```

2. Crie e ative o ambiente virtual:
   ```bash
   # Windows (PowerShell)
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure as variáveis de ambiente:
   ```bash
   cp .env.example .env
   ```
   No `.env`, vale preencher:
   - `IGNAV_API_KEY` — chave da [Ignav Flight Prices API](https://ignav.com). **Sem ela o aéreo ao vivo
     fica desligado** e o frontend recebe `PROVIDER_ERROR` com `reason: "not_configured"`.
     Cada busca nova custa uma request (1.000 grátis uma única vez; depois US$ 2 a cada 1.000).
     `AIR_MONTHLY_REQUEST_BUDGET` (padrão 800) limita o gasto do mês; buscas repetidas saem do cache
     (`provider_queries`) sem custo. Consumo do mês: `GET /api/v1/air/usage`.
   - `AIR_PERSIST_LIVE_QUOTES` — desligado por padrão: as cotações ao vivo não entram no histórico
     permanente até você confirmar que os termos do provedor permitem.
   - Régua do aéreo: `python scripts/import_anac_fares.py --months 12` (tarifas reais vendidas, ANAC).
   - `BOT_INFO_URL` / `BOT_CONTACT_EMAIL` — identificam o robô do Trivio no `User-Agent`
     e dão aos sites um canal de contato. Veja *Política de Coleta de Dados* acima.
   - `DATABASE_URL` — troque para PostgreSQL em produção.

5. Inicialize o banco de dados e faça a carga dos hubs nacionais (SP, Rio, Curitiba, etc.):
   ```bash
   python scripts/init_db.py
   ```

6. Inicie o servidor FastAPI:
   ```bash
   uvicorn app.api.main:app --reload --port 8000
   ```
   - Swagger / Documentação Interativa: `http://localhost:8000/docs`
   - Healthcheck: `http://localhost:8000/health`
   - WebSocket de busca: `ws://localhost:8000/ws/search`

---

### 2. Configuração do Frontend (Next.js)

1. Em um novo terminal, entre na pasta do frontend:
   ```bash
   cd frontend
   ```

2. Instale as dependências:
   ```bash
   npm install
   ```

3. Inicie o servidor de desenvolvimento:
   ```bash
   npm run dev
   ```
   - Acesse a plataforma no navegador: `http://localhost:3000`

---

## 🧪 Execução de Testes Automatizados

O projeto conta com cobertura de testes unitários e de integração cobrindo coletores, motores de inteligência, banco de dados, API REST e canais WebSocket.

### Testes do Backend
```bash
pytest tests/ -v
```
*(Todos os 106 testes devem passar com 100% de sucesso)*

### Verificação e Build do Frontend
```bash
cd frontend
npm run build
```
*(Valida tipagem TypeScript, ESLint e geração estática do App Router sem erros)*

---

## 📜 Licença

Distribuído sob licença comercial proprietária. Todos os direitos reservados à Trivio Travel Intelligence.
