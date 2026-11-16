# Fontes de preço rodoviário ao vivo: busca por alternativas

*Levantamento de 2026-11-15. As condições dos fornecedores mudam: revisar antes de usar.*

## 1. Problema

O aéreo do Trivio tem preço ao vivo pelo Ignav: chave própria, cobrança por request e nenhum
contrato a negociar. O ônibus não tem equivalente: a ClickBus depende de acesso à API B2B, e o
site dela recusa coleta automatizada (`robots.txt` proíbe `?departureDate=`; o WAF responde 403).

Enquanto isso, o ônibus da busca é só estimativa: projeção sobre a série da ANTT/Monitriip
(`app/services/road_quotes.py`) ou tarifa regulada do GTFS (`app/services/gtfs_road_quotes.py`).
A estimativa não tem disponibilidade, não acompanha promoção nem preço dinâmico e não tem link
de compra.

Este documento reúne as fontes possíveis, avaliadas com critérios fixos, para que o modal
rodoviário não dependa de um único fornecedor.

## 2. O que queremos (perfil "tipo Ignav")

| # | Critério | Por quê |
|---|---|---|
| C1 | Acesso autorizado por contrato ou programa oficial | Política de coleta do projeto: nada de raspagem nem intermediário de raspagem |
| C2 | Cobertura de linhas interestaduais brasileiras, em BRL | É o mercado do produto |
| C3 | Busca de viagens com preço por data (não só grade) | É o que falta hoje |
| C4 | Termos que permitem exibir o preço num comparador | O Trivio compara, não necessariamente vende |
| C5 | Onboarding com prazo previsível (ideal: chave self-serve) | O gargalo atual é a espera |
| C6 | Custo compatível com MVP (por request, comissão ou grátis) | Sem receita ainda |
| C7 | Link de compra ou deep link | Fecha a jornada do usuário |

**Achado principal:** não encontramos, no Brasil, nenhuma API de ônibus com o modelo do Ignav
(cadastro self-serve, chave na hora, cobrança por request). Todas as fontes legítimas passam por
um acordo comercial, de distribuição ou afiliação. Por isso o código deve aceitar qualquer
fornecedor que libere acesso, sem ficar preso a um só.

## 3. Alternativas avaliadas

### APIs de distribuição

| Fornecedor | O que oferece | Cobertura no Brasil | Acesso | Observação |
|---|---|---|---|---|
| **Distribusion** | Retailer API pública e documentada: busca, preço e vagas, assento, reserva, cancelamento. Ambientes demo e produção com chave no header `Api-Key` | ~250 operadoras (dado da própria empresa). Em out/2026 comprou a **RJ Consultores (totalBUS)**, sistema de venda de boa parte das viações brasileiras | Chave demo e lista de inventário pedidas a `partner@distribusion.com` | Documentação aberta, processo de pedido descrito e chave demo |
| **Travelier Connect** (DeÔnibus, 12Go, Plataforma10) | API de distribuição com disponibilidade, preço e conteúdo em tempo real. Diz cobrir 12.000+ operadoras em 122 países | DeÔnibus: 300+ operadoras, mais de 80% do território. Já alimenta a compra de ônibus no Moovit (mai/2025) | Só por conversa comercial: formulário "Let's Talk" em [travelier.com/partners](https://www.travelier.com/partners/) ou [travelier.com/contact](https://www.travelier.com/contact/) | Sem base URL, endpoints ou autenticação públicos (conferido em 2026-11-16; o perfil do [APIs.io](https://apis.io/apis/travelier/travelier-connect/) é de terceiro e confirma que não há referência pública). A integração com o Moovit mostra que eles atendem parceiros de busca, que é o nosso caso. O SeatOS, outra API da Travelier, é sistema de operadora focado na Ásia-Pacífico e não serve |
| **Quero Passagem** | Portal B2B para agências (2025); integração direta por API citada por metabuscadores | 350 viações, 5 mil destinos | Comercial (time B2B) | Sem documentação pública de API; vale perguntar se o portal B2B tem API ou feed |

### Outras opções

| Fornecedor | O que oferece | Limite |
|---|---|---|
| **Busbud** (dono da Buson desde 2024) | Programa de distribuição: API ou white label, com divisão de receita | Sem documentação pública; acesso pelo formulário do programa de distribuição |
| **FlixBus** (afiliados pela Awin) | Comissão anunciada de 6%; o nível "dynamic" inclui acesso a API | Cobre só as linhas da Flix no Brasil (próprias desde dez/2025). |
| **CheckMyBus** | Metabuscador com parcerias sob medida | Não publica API de preços para terceiros; útil só como referência de quem integra quem |
| **Viações diretas** (ex.: Gontijo, citada como integração direta) | API própria da operadora | Uma negociação por empresa; só compensa para os corredores mais buscados |

### Descartadas

| Fonte | Motivo |
|---|---|
| Raspadores em marketplaces (Apify: Busbud, FlixBus, redBus), GeckoAPI, endpoints não oficiais do Rome2Rio | Usam a API interna dos sites sem autorização. Mesmo raciocínio já registrado para a GeckoAPI: contornar a recusa de um site é risco de licença e fere a política de coleta |
| GeckoAPI, extrator `clickbus.com.br` / `plp` ([docs](https://geckoapi.com.br/docs/clickbus-com-br-plp/)), conferida em 2026-11-16 | Busca por origem, destino e data com preço, desconto, vagas, classe ANTT e viação, cobrada em créditos. A própria documentação mostra que é coleta do site: o `nextPageUrl` é uma página da ClickBus com `?departureDate=`, exatamente o caminho que o `robots.txt` proíbe; o `requestUrl` "nunca expõe o endpoint interno consultado"; e há camada de proxy. A página de uso responsável diz que a GeckoAPI não é afiliada às plataformas, não menciona `robots.txt` e passa ao cliente a responsabilidade pela base legal |
| "Flixbus" no RapidAPI (3B Data, `rapidapi.com/3b-data-3b-data-default/api/flixbus2`), conferida em 2026-11-16 | Planos mensais de US$ 0 (100 requests) a US$ 45 (500 mil), com busca de viagens, preço e deep link. Mas quem publica é a 3B Data, não a FlixBus. A página não cita parceria nem a origem dos dados, que usam os IDs internos da Flix e apontam para o `shop.flixbus.com`, cujo `robots.txt` proíbe tudo (`Disallow: /`). É revenda de raspagem, como a GeckoAPI. O caminho autorizado para a Flix é o programa de afiliados na Awin |
| Lyko | Inventário de ônibus concentrado na Europa |
| Google Transport (parceiro do Maps) | Fluxo inverso: o parceiro **fornece** preço ao Google. Não é fonte para nós |
| Omio | Revende operadoras locais; o programa de afiliados encontrado é antigo e focado em trem na Europa |

## 4. Plano de ação

### 4.1 Código pronto para qualquer fornecedor

Hoje a oferta real de ônibus está amarrada à ClickBus em `app/api/websocket.py`: import do
`ClickBusCollector` e do `CLICKBUS_SLUG_MAP` (linha 41), a chave `ROAD_OFFERS_ENABLED` (linha 77),
`_clickbus_external_id` (linhas 110–120) e as tarefas da busca (linhas 712 e 756). O frontend
também nomeia a ClickBus em `frontend/src/app/page.tsx` (linhas 40 e 47).

Proposta, sem depender de qual fornecedor responder:

1. **Interface de provedor rodoviário**: um `RoadOfferProvider` com `search(origin_id, dest_id,
   date) -> List[StandardizedQuote]`, implementado pela ClickBus hoje e pelo próximo fornecedor
   depois. `StandardizedQuote` já é genérico.
2. **Configuração em vez de constante**: `ROAD_OFFERS_PROVIDER` no `.env` (vazio = desligado)
   substitui o `ROAD_OFFERS_ENABLED` fixo no código.
3. **`RoadOfferService`** no molde do `AirQuoteService`: cache em `provider_queries`, orçamento se
   o fornecedor cobrar por consulta e coalescência de buscas iguais.
4. **Vínculos só pelo `ProviderMapping`**: cada fornecedor com seus IDs de rodoviária na tabela,
   sem mapa estático no código (resolve também a pendência do `CLICKBUS_SLUG_MAP`).
5. **Convivência com a estimativa**: oferta real e estimativa ANTT lado a lado na busca; a
   estimativa só some para a rota que tiver oferta real.

O parser de cada fornecedor só deve ser escrito com uma resposta real em mãos (como foi feito com
o Ignav), para não repetir o caso do parser da ClickBus, montado sobre um formato suposto.

### 4.2 Melhorar a estimativa enquanto isso

1. **Importar mais meses da ANTT**: o Termômetro só classifica a partir de 6 meses de viagem por
   rota (`MIN_OFFICIAL_MONTHS`).
2. **Recuperar os dados do backup `bus-airport`**: 78.920 horários (ARTESP e feed antigo) e 819
   tarifas GTFS que não estão no `trivio_dev.db`. Reimportar pelos scripts é mais seguro que copiar
   as linhas com feed `None` do esquema antigo.
3. **Novos feeds GTFS** de órgãos estaduais, cadastrados em `app/collectors/gtfs/feeds.py`.
4. **Medir o erro da projeção**: comparar a projeção de um mês com o valor que a ANTT publicou
   depois para o mesmo mês. Isso dá à interface uma margem de erro honesta e servirá de base de
   comparação quando chegar preço real.

## 5. Estado do projeto nesta data

- `pytest`: 383 testes passando.
- Todos os branches `claude/*` locais (`blissful-kirch`, `front-animations-performance`,
  `project-analysis`, `redundant-code-analysis`, `road-fare-projection`, `cleanup-pendencias`) já
  estão no `origin/main`. Podem ser apagados localmente.
- O checkout principal está em `d650640`, atrás do `origin/main` (`b7fed2e`). Falta um `git pull`.
- O worktree `cleanup-pendencias-58b9dd` está em HEAD destacado, com o trabalho já mergeado, e pode
  ser removido.
- Na raiz do checkout principal, `diag_ws.txt` (saída de teste de 10/11, de um worktree que não
  existe mais) é o único arquivo fora do git que não está no `.gitignore`. Pode ser apagado.
- Pendências que continuam abertas: dados do backup `bus-airport` (4.2, item 2) e a troca do
  `CLICKBUS_SLUG_MAP` pelo `ProviderMapping` (4.1, item 4).

## Fontes

- [Distribusion: documentação da API](https://docs.distribusion.com/dt/api/getting-started/intro)
  e [autenticação](https://docs.distribusion.com/dt/api/getting-started/api-authentication)
- [Distribusion Brasil compra a RJ Consultores (Diário do Transporte, out/2026)](https://diariodotransporte.com.br/2026/10/05/distribusion-brasil-compra-rj-consultores-e-cria-plataforma-lider-de-tecnologia-para-transporte-de-passageiros-no-pais/)
- [Distribusion passa de 200 viações (jun/2024)](https://diariodotransporte.com.br/2024/06/12/distribusion-tecnologia-ultrapassa-a-marca-de-200-viacoes-rodoviarias-parceiras-e-lanca-solucao-customizada-de-vendas-online-para-as-empresas-do-seu-portfolio/)
- [Política de integração técnica da RJ Consultores](https://www.rjconsultores.com.br/politica-de-integracao-tecnica-da-rj-consultores.pdf)
- [Moovit e Travelier (DeÔnibus)](https://moovit.com/press-releases/moovit-travelier-partnership/)
- [Quero Passagem: novo portal B2B (Panrotas, jul/2025)](https://www.panrotas.com.br/mercado/transporte/2025/07/novo-portal-b2b-da-quero-passagem-amplia-controle-e-produtividade-das-agencias_219887.html)
- [Busbud: programa de distribuição](https://www.busbud.com/en/distribution) e [Busbud (Wikipedia)](https://en.wikipedia.org/wiki/Busbud)
- [FlixBus: afiliados](https://global.flixbus.com/company/partners/affiliate-partners),
  [FlixBus BR na Awin](https://www.affilitizer.com/programs/flixbus.com.br) e
  [rotas próprias no Brasil (dez/2025)](https://diariodotransporte.com.br/2025/12/15/flixbus-estreia-rotas-rodoviarias-proprias-no-brasil-e-amplia-conexoes-no-sudeste-e-sul/)
- [Parceiros de venda da BuscaOnibus](https://ajuda.buscaonibus.com.br/hc/en-us/articles/29688914106395-Who-are-BuscaOnibus-sales-partners)
- [CheckMyBus: parcerias](https://www.checkmybus.com/service/partner)
- [Google Transport: requisitos de parceiro](https://developers.google.com/travel/transport/guides/partner-integration/participation-requirements?hl=pt-BR)
- [Lyko: Bus API](https://lyko.tech/en/portfolio/long-distance-buses-api/)
- [Ignav: documentação](https://ignav.com/docs)
