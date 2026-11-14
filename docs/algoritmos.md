# Algoritmos do Trivio

Referência das regras de cálculo que o backend aplica hoje. Cada seção aponta o arquivo que é a fonte da verdade; se o código mudar, atualize aqui junto.

Este documento substitui as seções 6 e 7 do roteiro original, arquivado em [`historico/roteiro_trivio.md`](historico/roteiro_trivio.md). O roteiro descrevia a intenção inicial; os valores abaixo são os do código.

---

## 1. TTL dinâmico do cache de cotações

Fonte: [`app/cache/ttl_policy.py`](../app/cache/ttl_policy.py).

O tempo de vida de uma cotação no cache depende da antecedência da viagem, com Δ = data da viagem − data da coleta, em dias:

| Antecedência | Faixa (`TTLTier`) | TTL |
|---|---|---|
| Δ < 0 (viagem no passado) | `EXPIRED` | 0 (expira na hora) |
| Δ ≤ 2 dias | `IMMINENT` | 30 min (1.800 s) |
| 3 a 7 dias | `SHORT` | 2 h (7.200 s) |
| 8 a 21 dias | `MEDIUM` | 6 h (21.600 s) |
| > 21 dias | `LONG` | 24 h (86.400 s) |

O objetivo é proteger o *look-to-book ratio* dos provedores pagos: preços de viagens próximas mudam rápido, então são reconsultados com mais frequência.

`calculate_expiration_datetime` soma o TTL ao momento da coleta (UTC por padrão).

---

## 2. Trivio Score

Fonte: [`app/analyzers/trivio_scorer.py`](../app/analyzers/trivio_scorer.py) (`calculate_score_breakdown`).

O Trivio Score põe ônibus e avião na mesma régua, em reais. **Quanto menor, melhor.**

```
Score = P_efetivo + (T_porta_a_porta / 60) × V_hora × F_conforto + Σ Penalidades
```

O detalhamento de cada termo vai para `route_metadata.score_breakdown` (ver seção 4).

### 2.1. Preço efetivo (`P_efetivo`)

`total_effective_price` da cotação. Quando ausente ou zero, é recalculado como
`base_price + taxes_price + baggage_fee_estimate + transfer_cost_estimate`.

O `transfer_cost_estimate` é a soma do custo estimado de transfer urbano até o terminal de origem e a partir do terminal de destino (`Terminal.estimated_transfer_cost_brl`), injetada em [`app/services/quote_pipeline.py`](../app/services/quote_pipeline.py).

### 2.2. Tempo porta a porta (`T_porta_a_porta`)

`door_to_door_duration_minutes`, montado no pipeline como:

```
duração da viagem + transfer até a origem + transfer a partir do destino + antecedência de embarque
```

A antecedência é de 90 min no aéreo e 20 min no rodoviário (`AIRPORT_DOMESTIC_CHECKIN_BUFFER_MINUTES` e `BUS_TERMINAL_CHECKIN_BUFFER_MINUTES` em `app/config.py`). Se o campo vier vazio, o scorer usa a própria duração da viagem.

### 2.3. Valor da hora (`V_hora`)

`DEFAULT_HOURLY_TIME_VALUE_BRL` = R$ 35,00/h (`app/config.py`), substituível por parâmetro da busca.

### 2.4. Fator de conforto (`F_conforto`)

Definido em `determine_comfort_factor`. O horário é avaliado no fuso de Brasília (UTC−3).

| Situação | Fator |
|---|---|
| Ônibus **leito** saindo a partir das 20h **e** chegando entre 5h e 9h (pernoite que economiza diária) | 0,55 |
| Voo em classe executiva/primeira (`BUSINESS`, `FIRST`, `EXECUTIVA`) | 0,70 |
| Ônibus **leito** fora da janela noturna | 0,85 |
| Voo direto; ônibus semi-leito ou executivo de até 6 h | 1,00 |
| Voo com conexão | 1,15 |
| Ônibus semi-leito ou executivo com mais de 6 h (360 min) | 1,15 |
| Ônibus convencional (qualquer outra classe rodoviária) | 1,35 |
| Modal desconhecido | 1,00 |

A classe é comparada por substring: `SEMI_LEITO`, `SEMI-LEITO` e `EXECUTIVO` são testados antes de `LEITO`, então um semi-leito nunca cai na regra do leito.

### 2.5. Penalidades de atrito

Todas em pontos (equivalentes a reais) e somadas:

| Penalidade | Valor | Quando se aplica |
|---|---|---|
| Conexão / baldeação | 40 × max(1, `stops_count`) | `is_direct` falso ou `stops_count` > 0 |
| Madrugada | 60 | Partida **ou** chegada entre 01:00 e 04:59 (horário de Brasília) |
| Paradas rodoviárias excedentes | 10 por parada acima de 2 | Só ônibus, `stops_count` > 2 |
| Troca de aeroporto na conexão | 80 | Só aéreo: conexões passam por CGH e GRU, ou por SDU e GIG |
| Conexão apertada | soma de `connection_diagnostics.total_connection_penalty_points` | Ver 2.6 e 3 |

### 2.6. Conexão apertada no aéreo

Fonte: `diagnose_connections` em [`app/analyzers/route_evaluator.py`](../app/analyzers/route_evaluator.py). Cada escala do voo recebe uma severidade e uma penalidade; o total vai para `connection_diagnostics` e entra no score.

| Severidade | Hub principal | Outros aeroportos | Penalidade |
|---|---|---|---|
| `CRITICAL` | < 50 min | < 40 min | 100 |
| `WARNING` | 50 a 64 min | 40 a 49 min | 50 |
| `LONG` | > 300 min | > 300 min | 20 |
| `SAFE` | demais casos | demais casos | 0 |

Os hubs principais (`MAJOR_BRAZILIAN_HUBS`) são GRU, GIG, BSB, CNF, VCP e CGH. O tempo consumido na conexão é estimado em desembarque (15 min em hub, 10 nos demais) + caminhada (15 / 5 min) + fechamento do portão (15 min); o que sobra é a `safety_margin_min`. Quando o provedor não detalha a escala, ela é tratada como 60 min.

### 2.7. Hacker Fares

`generate_hacker_fares` combina ida e volta independentes (permanência mínima de 6 h entre a chegada da ida e a partida da volta). O score combinado é a soma dos scores das duas pernas, e a economia é medida contra o ida e volta aéreo mais barato (estratégia `HYBRID_MODAL`) ou contra a mesma companhia nos dois sentidos (`SPLIT_CARRIER`).

---

## 3. Interlining virtual (ônibus + avião)

Fonte: [`app/analyzers/interliner.py`](../app/analyzers/interliner.py) (`find_interline_routes`).

Uma rota híbrida junta a perna 1 e a perna 2 por um transfer entre terminais do mesmo hub.

**Validação da conexão** (tempo entre a chegada da perna 1 e a partida da perna 2):

- de 120 min (`MIN_INTERMODAL_CONNECTION_MINUTES`) a 720 min (`MAX_INTERMODAL_CONNECTION_MINUTES`);
- depois de descontado o transfer, sobram pelo menos 45 min livres;
- abaixo de 180 min (`RECOMMENDED_SAFE_CONNECTION_MINUTES`) o risco é `WARNING`; a partir daí, `SAFE`.

**Preço**: perna 1 + custo do transfer entre terminais + perna 2 (o padrão do transfer é R$ 40 e 45 min).

**Porta a porta**: acesso à origem da perna 1 (transfer urbano + antecedência) + tempo corrido da partida da perna 1 até a chegada da perna 2 (já inclui espera e transfer) + transfer urbano a partir do destino final.

**Score**: a rota é pontuada pela mesma fórmula da seção 2, como um voo com conexão, e por isso:

- `F_conforto` = 1,15 (aéreo com conexão), salvo se a perna 2 for executiva (0,70);
- penalidade de conexão = 40 (`stops_count` = 1);
- penalidade de conexão apertada = 50 (`PENALTY_VIRTUAL_INTERLINE`, pelos bilhetes não conjugados) **+ 30** quando o risco é `WARNING`;
- madrugada (60), se a partida da perna 1 ou a chegada da perna 2 cair entre 01:00 e 04:59.

---

## 4. Formato do `route_metadata`

`route_metadata` é um JSON livre em `StandardizedQuote`/`FareQuote`, preenchido em camadas ao longo do pipeline. Nem toda cotação tem todos os campos; os grupos abaixo dizem quem escreve cada um.

### 4.1. Estrutura da rota (coletores)

| Campo | Tipo | Origem |
|---|---|---|
| `is_direct` | bool | todos |
| `stops_count` | int | todos |
| `layover_airports` | lista de IATA | Ignav |
| `layover_details` | lista de `{airport, arrival_time, departure_time, duration_minutes}` | Ignav |
| `intermediate_stops` | lista de nomes | nenhum coletor atual; lido como alternativa a `layover_airports` |
| `is_virtual_interline` | bool | interliner |
| `origin_iata`, `destination_iata`, `aircraft`, `primary_flight_number`, `all_flight_numbers`, `operating_carrier_name`, `has_mixed_carriers` | — | Ignav |
| `baggage_included` | bool | Ignav (sempre `false`: a busca não informa franquia) |
| `price_total_brl`, `price_status`, `price_observed_at`, `taxes_are_estimated`, `ignav_id`, `booking_url` | — | Ignav |
| `price_suspect`, `price_suspect_flights` | bool, int | `flag_uniform_prices` (mesmo preço em ≥ 3 voos distintos) |
| `trip_id`, `company_id`, `amenities`, `seats_available`, `booking_url` | — | ClickBus (desligada); `amenities` alimenta os prós do `RouteEvaluator` |

### 4.2. Ônibus estimado (ANTT e GTFS)

Cotações rodoviárias sem venda ao vivo trazem `is_simulated: true` e explicam a base do preço:

| Campo | Valores / significado |
|---|---|
| `price_basis` | `antt_projection` (série Monitriip), `gtfs_fare_regulated` (tarifa GTFS) ou `antt_projection+ignav` (rota híbrida) |
| `simulation_notice` | texto exibido ao usuário |
| `antt_projection` | projeção da série ANTT (só `antt_projection`) |
| `schedule_source`, `schedule_period`, `line_prefix` | grade de horário usada |
| `schedule_is_estimated` | `true` quando não há grade e o horário é sintético |
| `duration_is_estimated`, `road_distance_km` | duração estimada pela distância rodoviária |
| `fare_source` | feed GTFS da tarifa |

### 4.3. Terminais e transfer (pipeline)

`origin_terminal_id`, `origin_terminal_name`, `origin_city`, `origin_state`, os equivalentes `destination_*`, e a decomposição do porta a porta: `origin_transfer_minutes`, `destination_transfer_minutes`, `checkin_buffer_minutes`.

### 4.4. Deduplicação entre vendedores

[`app/cache/deduplicator.py`](../app/cache/deduplicator.py): `is_code_share`, `sellers_count`, `cheapest_provider`, `cheapest_price`, `highest_price`, `savings_brl` e `available_sellers` (lista com `provider_source`, preços, `service_class` e `deep_link` de cada vendedor).

### 4.5. Avaliação qualitativa

[`RouteEvaluator.enrich_quote`](../app/analyzers/route_evaluator.py): `pros`, `cons` e `potential_setbacks` (listas de frases em português) e, no aéreo com escala, `connection_diagnostics`:

```json
{
  "has_connection": true,
  "connections_count": 1,
  "tightest_layover_minutes": 55,
  "worst_severity": "WARNING",
  "total_connection_penalty_points": 50,
  "is_tight": true,
  "connections": [
    {
      "airport": "GRU",
      "layover_minutes": 55,
      "is_major_hub": true,
      "severity": "WARNING",
      "timeline": {
        "deplaning_est_min": 15,
        "terminal_walk_est_min": 15,
        "gate_closing_buffer_min": 15,
        "safety_margin_min": 10
      },
      "penalty_points": 50,
      "recommendation": "Conexão curta (55 min) em GRU: …"
    }
  ]
}
```

Rotas híbridas trazem, em vez disso, `intermodal_hub`, `transfer_details`, `connection_window_minutes`, `buffer_minutes` e `risk_level`.

### 4.6. Termômetro de preço

[`app/analyzers/price_thermometer.py`](../app/analyzers/price_thermometer.py), em `price_thermometer`:

```json
{
  "status": "LOW",
  "percentile": 22,
  "historical_median_brl": 310.0,
  "p25_brl": 280.0,
  "p75_brl": 355.0,
  "sample_size": 18,
  "difference_percentage": -24.0,
  "recommendation": "Preço 24% abaixo da média histórica para esta rota. Excelente momento para compra.",
  "source": "antt_monitriip",
  "reference_period": "2024-01 a 2025-06"
}
```

- `status`: `LOW`, `MEDIUM`, `HIGH` ou `INSUFFICIENT_DATA`.
- Ordem das fontes: série oficial do modal (ANTT no ônibus, com pelo menos 6 meses de viagem; ANAC no aéreo, com pelo menos 3 meses e 300 assentos vendidos nos últimos 12), depois o histórico próprio em `fare_quotes` (últimos 90 dias, pelo menos 5 cotações). Sem nenhuma das duas, `INSUFFICIENT_DATA`.
- Na série ANTT, cada amostra é a média de um mês de viagem e `sample_size` é o total de bilhetes desses meses, não o número de meses.
- `source`: `antt_monitriip`, `anac_tarifas_domesticas` ou `coletas_trivio`.
- Na comparação com uma série de preços (`evaluate_price_distribution`: série ANTT ou coletas próprias), os quartis P25 e P75 usam **interpolação linear** (`QUANTILE_INTERPOLATION`). É a mesma regra da faixa "costuma ficar entre" da referência histórica de ônibus (`app/analyzers/road_reference.py`), que lê a mesma série mensal da ANTT: para uma rota e classe, a faixa exibida e os limites do Termômetro são os mesmos números. `percentile` é a fração de amostras estritamente menores que o preço, limitada a 1–99.
- No aéreo, a régua da ANAC (`evaluate_against_air_band`) interpola o percentil sobre as faixas de assentos vendidos, ponderadas pelo volume.
- Estimativas ANTT (`price_basis` começando com `antt_projection`) não são comparadas com nada: o preço já é a própria média da série, e o resultado é `INSUFFICIENT_DATA`.

### 4.7. Score

`score_breakdown`, escrito por `score_and_rank_quotes`:

```json
{
  "effective_price": 289.9,
  "door_to_door_duration_minutes": 640,
  "hourly_time_value": 35.0,
  "comfort_factor": 0.55,
  "time_cost": 205.33,
  "penalties": {
    "connection": 0.0,
    "madrugada": 0.0,
    "stops": 0.0,
    "airport_swap": 0.0,
    "tight_connection": 0.0,
    "total": 0.0
  },
  "trivio_score": 495.23
}
```

### 4.8. Coleta

`collector_stats` (`app/collectors/base.py`): `provider`, `tier` e `latency_ms` do coletor que produziu a cotação.
