"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { FareQuote, HackerFareCombination, SearchParams } from "@/types";

export interface SearchCompletedStats {
  totalQuotes: number;
  totalElapsedMs: number;
  cheapestPrice: number;
  bestTrivioScore: number;
  timestamp: string;
}

export interface UseSearchWebSocketReturn {
  isSearching: boolean;
  isConnected: boolean;
  quotes: FareQuote[];
  hackerFares: HackerFareCombination[];
  completedStats: SearchCompletedStats | null;
  error: string | null;
  activeProviders: string[];
  startSearch: (params: SearchParams) => void;
  cancelSearch: () => void;
  clearResults: () => void;
}

const DEFAULT_WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/search";

export function useSearchWebSocket(): UseSearchWebSocketReturn {
  const [isSearching, setIsSearching] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [quotes, setQuotes] = useState<FareQuote[]>([]);
  const [hackerFares, setHackerFares] = useState<HackerFareCombination[]>([]);
  const [completedStats, setCompletedStats] = useState<SearchCompletedStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeProviders, setActiveProviders] = useState<string[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const pendingParamsRef = useRef<SearchParams | null>(null);

  // Fecha conexão WebSocket de forma segura
  const closeWebSocket = useCallback(() => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
  }, []);

  // Limpa os resultados atuais
  const clearResults = useCallback(() => {
    setQuotes([]);
    setHackerFares([]);
    setCompletedStats(null);
    setError(null);
    setActiveProviders([]);
  }, []);

  // Cancela a busca em andamento
  const cancelSearch = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      try {
        wsRef.current.send(JSON.stringify({ action: "cancel" }));
      } catch {
        // Ignora erro de envio no cancelamento
      }
    }
    setIsSearching(false);
  }, []);

  // Processa as mensagens recebidas do servidor
  const handleWebSocketMessage = useCallback((event: MessageEvent) => {
    try {
      const data = JSON.parse(event.data);
      const eventType = data.event;

      switch (eventType) {
        case "SEARCH_STARTED": {
          setIsSearching(true);
          setError(null);
          setCompletedStats(null);
          setActiveProviders(data.providers || ["clickbus", "amadeus"]);
          break;
        }

        case "QUOTE_BATCH": {
          // Chegada de novo lote de passagens ("pipocando" na interface)
          const newBatch: FareQuote[] = data.quotes || [];
          if (newBatch.length > 0) {
            setQuotes((prevQuotes) => {
              // Deduplica por dedup_signature mantendo a cotação mais recente ou barata
              const quoteMap = new Map<string, FareQuote>();
              for (const q of prevQuotes) {
                quoteMap.set(q.dedup_signature, q);
              }
              for (const q of newBatch) {
                const existing = quoteMap.get(q.dedup_signature);
                if (!existing || q.total_effective_price < existing.total_effective_price) {
                  quoteMap.set(q.dedup_signature, q);
                }
              }

              // Reordena pelo menor Trivio Score (melhor custo-benefício e menor atrito no topo)
              const sorted = Array.from(quoteMap.values()).sort((a, b) => {
                const scoreA = a.trivio_score ?? Number.MAX_VALUE;
                const scoreB = b.trivio_score ?? Number.MAX_VALUE;
                if (scoreA !== scoreB) {
                  return scoreA - scoreB;
                }
                return a.total_effective_price - b.total_effective_price;
              });

              return sorted;
            });
          }
          break;
        }

        case "HACKER_FARES": {
          const combinations: HackerFareCombination[] = data.combinations || [];
          if (combinations.length > 0) {
            setHackerFares(combinations);
          }
          break;
        }

        case "PROVIDER_ERROR": {
          // Erro pontual de um provedor (ex: timeout Amadeus) não derruba a busca
          console.warn(`[Trivio WS] Aviso do provedor ${data.provider}:`, data.error);
          break;
        }

        case "SEARCH_COMPLETED": {
          setIsSearching(false);
          setCompletedStats({
            totalQuotes: data.total_quotes,
            totalElapsedMs: data.total_elapsed_ms,
            cheapestPrice: data.cheapest_effective_price,
            bestTrivioScore: data.best_trivio_score,
            timestamp: data.timestamp,
          });
          break;
        }

        case "pong":
          // Heartbeat de resposta recebido
          break;

        default:
          break;
      }
    } catch (err) {
      console.error("[Trivio WS] Erro ao processar mensagem do WebSocket:", err);
    }
  }, []);

  // Inicia uma nova busca via WebSocket
  const startSearch = useCallback(
    (params: SearchParams) => {
      clearResults();
      setIsSearching(true);
      setError(null);
      pendingParamsRef.current = params;

      // Se já houver um socket aberto, reutiliza
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({
            action: "start_search",
            origin: params.origin,
            destination: params.destination,
            date: params.date,
            return_date: params.return_date,
            hourly_value: params.hourly_value,
            radius_km: params.radius_km,
            include_baggage: params.include_baggage,
            enable_virtual_interlining: params.enable_virtual_interlining,
          })
        );
        return;
      }

      // Caso contrário, abre nova conexão WebSocket
      closeWebSocket();

      try {
        const query = new URLSearchParams({
          origin: params.origin,
          destination: params.destination,
          date: params.date,
          hourly_value: params.hourly_value.toString(),
          radius_km: params.radius_km.toString(),
          enable_virtual_interlining: params.enable_virtual_interlining.toString(),
        });

        if (params.return_date) {
          query.set("return_date", params.return_date);
        }

        const socketUrl = `${DEFAULT_WS_URL}?${query.toString()}`;
        const ws = new WebSocket(socketUrl);
        wsRef.current = ws;

        ws.onopen = () => {
          setIsConnected(true);
          // Configura heartbeat a cada 25 segundos
          pingIntervalRef.current = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ action: "ping" }));
            }
          }, 25000);
        };

        ws.onmessage = handleWebSocketMessage;

        ws.onerror = (evt) => {
          console.warn("[Trivio WS] Falha de conexão WebSocket com", DEFAULT_WS_URL, evt);
          setError(
            "Não foi possível conectar ao motor em tempo real (FastAPI). Verifique se o servidor está ativo na porta 8000."
          );
          setIsSearching(false);
          setIsConnected(false);
        };

        ws.onclose = () => {
          setIsConnected(false);
          setIsSearching(false);
        };
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        setError(`Erro ao inicializar conexão: ${message}`);
        setIsSearching(false);
      }
    },
    [clearResults, closeWebSocket, handleWebSocketMessage]
  );

  // Limpeza ao desmontar componente
  useEffect(() => {
    return () => {
      closeWebSocket();
    };
  }, [closeWebSocket]);

  return {
    isSearching,
    isConnected,
    quotes,
    hackerFares,
    completedStats,
    error,
    activeProviders,
    startSearch,
    cancelSearch,
    clearResults,
  };
}
