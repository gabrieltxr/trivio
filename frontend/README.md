# Trivio — Frontend

Interface web do Trivio em Next.js 14 (App Router), TypeScript e Tailwind CSS. A visão geral do
projeto, o backend e as fontes de dados estão no [README principal](../README.md).

## Rodar localmente

O frontend depende da API do Trivio rodando (por padrão em `http://localhost:8000`).

```bash
npm install
npm run dev        # http://localhost:3000
```

Antes de publicar, rode `npm run lint` e `npm run build` (o build também valida os tipos).

## Variáveis de ambiente

| Variável | Padrão | Uso |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | API REST (autocomplete de localidades, projeção rodoviária) |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000/ws/search` | WebSocket da busca |

Em produção, o domínio do frontend precisa estar em `CORS_ORIGINS` no backend: a mesma lista
libera o REST e o WebSocket, que recusa conexões de outras origens.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `src/app/` | Página única (`page.tsx`), layout e estilos globais |
| `src/components/` | Barra de busca, autocomplete, cards de cotação e de Hacker Fares, cabeçalho |
| `src/hooks/useSearchWebSocket.ts` | Conexão com `/ws/search` e tratamento dos eventos da busca |
| `src/data/locations.ts` | Cliente do autocomplete: as localidades vêm só da API |
| `src/types/` | Contratos TypeScript da API e do WebSocket |

## Códigos de localidade

A busca envia `HUB:<código>` para uma cidade inteira (aeroportos e rodoviárias) e o código do
terminal (`CGH`, `TIETE`) para um terminal só. `CWB` sozinho é o aeroporto de Curitiba, não a
cidade. As sugestões do autocomplete já trazem o código certo.
