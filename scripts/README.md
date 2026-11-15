# Scripts do Trivio

Rode tudo a partir da raiz do projeto, com o ambiente virtual ativo. Os scripts usam o banco de
`DATABASE_URL` (padrão: `trivio_dev.db`). Os testes não dependem de nenhum deles: a suíte cria e
semeia um banco temporário em `tests/conftest.py`.

## Ordem para uma instalação nova

1. `init_db.py`: cria as tabelas e carrega os hubs da semente.
2. `hub_candidates.py --apply`: opcional, acrescenta Florianópolis, Belo Horizonte e Brasília.
3. Importadores: sem eles, a busca não tem ônibus nem régua de preço.
   - `import_anac_fares.py`: régua aérea do Termômetro.
   - `import_antt_fares.py`: série de preços do ônibus.
   - `import_antt_schedules.py`: horários das linhas.
   - `import_gtfs.py`: horários e tarifas de linhas que a ANTT não cobre.
4. `report_hub_coverage.py`: confere quais cidades têm dados até os hubs.

## Referência

| Script | Para que serve | Uso |
|---|---|---|
| `init_db.py` | Cria as tabelas e carrega hubs, terminais e mapeamentos de provedor da semente. `--reset` apaga e recria tudo. | `python scripts/init_db.py` |
| `hub_candidates.py` | Hubs fora da semente (FLN, CNF, BSB). Aplicação idempotente; coordenadas e nomes das rodoviárias ainda são aproximações. | `python scripts/hub_candidates.py --check` ou `--apply` |
| `import_anac_fares.py` | Tarifas aéreas domésticas vendidas (ANAC), agregadas por rota e mês. | `python scripts/import_anac_fares.py --months 12` (também `--list`, `--inspect CSV`, `--file CSV`) |
| `import_antt_fares.py` | Bilhetes de ônibus vendidos (ANTT Monitriip), agregados por rota, classe e mês de viagem. Respeita o `Crawl-delay` de 10 s do portal, então a importação é lenta. | `python scripts/import_antt_fares.py --months 3` (também `--list`, `--inspect CSV`, `--inspect-remote AAAA-MM`, `--file CSV`) |
| `import_antt_schedules.py` | Grade de horários interestaduais da ANTT (CSV "Horários - SIGMA" baixado à mão). Reimportar o mesmo período substitui as linhas dele. | `python scripts/import_antt_schedules.py --file <csv>` |
| `import_gtfs.py` | Feed GTFS de um órgão ou operador. As fontes conhecidas estão em `app/collectors/gtfs/feeds.py`. | `python scripts/import_gtfs.py --feed artesp --file <zip>` |
| `cleanup_legacy_data.py` | Apaga mapeamentos de provedores removidos (Amadeus, Kiwi...), cotações vencidas ou que não deveriam estar gravadas e, com `--prune-queries`, consultas do Ignav vencidas de antes da leitura do saldo (ou, com `--strip-payloads`, só a resposta guardada delas, mantendo o registro). Depois atualiza esquema e `ANALYZE`. Sem `--apply`, só simula. | `python scripts/cleanup_legacy_data.py` e depois `--apply` |
| `report_hub_coverage.py` | Cidades que têm rota com dados (ANTT ou GTFS) até os hubs atuais: ajuda a escolher novos hubs. | `python scripts/report_hub_coverage.py --out relatorio.csv` |
| `test_collector.py` | Roda no terminal a mesma busca do site (`/ws/search`), sem navegador. Não é um teste do pytest. | `python scripts/test_collector.py --origin HUB:SAO --dest HUB:RIO --date AAAA-MM-DD` |

## Códigos de origem e destino (`test_collector.py`)

- `HUB:CWB`: a cidade inteira, com aeroportos e rodoviárias.
- `CWB`, `CGH`, `TIETE`: só aquele terminal. Vários hubs têm o mesmo código do próprio aeroporto.
- Um código não cadastrado (ex.: `SSA`) é recusado.

Sem `--live-air`, o Ignav não é consultado. Com `--live-air`, cada sentido gasta uma request paga,
e cada hub da conexão híbrida (`--interline`) gasta mais uma.
