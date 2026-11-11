"use client";

import { useState, useMemo } from "react";
import Image from "next/image";
import Header from "@/components/Header";
import SearchBar from "@/components/SearchBar";
import TrustPillars from "@/components/TrustPillars";
import QuoteCard from "@/components/QuoteCard";
import HackerFareCard from "@/components/HackerFareCard";
import SkeletonCard from "@/components/SkeletonCard";
import { useSearchWebSocket, ProviderNotice } from "@/hooks/useSearchWebSocket";
import { SearchParams } from "@/types";
import { displayCode } from "@/data/locations";
import {
  Sparkles,
  ArrowUpDown,
  Filter,
  CheckCircle2,
  Layers,
  Plane,
  Bus,
  RefreshCw,
  X,
} from "lucide-react";

type SortOption = "TRIVIO_SCORE" | "PRICE_ASC" | "DURATION_ASC";
type ModalFilter = "ALL" | "AIR" | "ROAD" | "HYBRID";

const NAMES: Record<string, string> = { clickbus: "A ClickBus", ignav: "O provedor aéreo" };

const ROAD_CLASS_LABELS: Record<string, string> = {
  CONVENCIONAL: "Convencional",
  EXECUTIVO: "Executivo",
  SEMI_LEITO: "Semi-leito",
  LEITO: "Leito",
};

const brl = (value: number) => `R$ ${value.toFixed(2).replace(".", ",")}`;

/** Texto do aviso exibido quando um provedor não respondeu, conforme o motivo da API. */
function providerNoticeText(notice: ProviderNotice): string {
  const isAir = notice.provider.startsWith("ignav");
  const name = isAir ? "Preços aéreos ao vivo" : "Esta fonte";
  switch (notice.reason) {
    case "not_configured":
      return `${name} estão indisponíveis: a chave de acesso não está configurada no servidor. Os resultados abaixo não incluem voos ao vivo.`;
    case "disabled":
      return `${name} estão desligados nesta instalação. Os resultados abaixo não incluem voos ao vivo.`;
    case "access_denied":
      return `${NAMES[notice.provider] ?? "Esta fonte"} recusou o acesso automatizado, então os resultados dela não aparecem nesta busca.`;
    case "budget_exhausted":
      return `O saldo de consultas aéreas do provedor chegou ao limite de segurança, então os voos ao vivo não foram consultados. Os resultados abaixo não incluem voos.`;
    case "billing_required":
      return `A conta do provedor aéreo não tem crédito ou forma de pagamento ativa, então os voos ao vivo não foram consultados. Os resultados abaixo não incluem voos aéreos. Regularize a cobrança no painel do provedor.`;
    case "no_airport":
      return `Voos não consultados: a origem ou o destino não tem aeroporto cadastrado. Os resultados abaixo não incluem voos.`;
    case "no_bus_station":
      return `Ônibus não consultado: a origem ou o destino não tem rodoviária cadastrada. Os resultados abaixo não incluem ônibus.`;
    case "no_antt_data":
      return `Não há vendas de ônibus registradas na ANTT para esta rota, então não há estimativa de ônibus. A ANTT cobre linhas interestaduais; rotas dentro do mesmo estado não aparecem.`;
    case "spend_limit_reached":
      return `A conta do provedor aéreo atingiu o teto de gasto do mês, então os voos ao vivo não foram consultados. Os resultados abaixo não incluem voos aéreos. O teto precisa ser aumentado no painel do provedor.`;
    default:
      return `${name} não responderam a esta busca. Tente pesquisar de novo em instantes.`;
  }
}

export default function Home() {
  const [activeParams, setActiveParams] = useState<SearchParams | null>(null);
  const [sortBy, setSortBy] = useState<SortOption>("TRIVIO_SCORE");
  const [modalFilter, setModalFilter] = useState<ModalFilter>("ALL");

  const {
    isSearching,
    quotes,
    hackerFares,
    completedStats,
    error,
    providerNotices,
    roadReferences,
    activeProviders,
    startSearch,
    cancelSearch,
  } = useSearchWebSocket();

  const handleSearch = (params: SearchParams) => {
    setActiveParams(params);
    startSearch(params);

    // Rola suavemente para a área de resultados
    const resultsElem = document.getElementById("results-section");
    if (resultsElem) {
      resultsElem.scrollIntoView({ behavior: "smooth" });
    }
  };

  // Filtra e ordena as cotações reativamente conforme chegam no streaming
  const filteredAndSortedQuotes = useMemo(() => {
    let result = [...quotes];

    // 1. Filtro Modal
    if (modalFilter === "AIR") {
      result = result.filter((q) => q.modal === "AIR");
    } else if (modalFilter === "ROAD") {
      result = result.filter((q) => q.modal === "ROAD");
    } else if (modalFilter === "HYBRID") {
      result = result.filter(
        (q) =>
          q.modal === "ROAD_AIR" ||
          q.modal === "HYBRID" ||
          q.route_metadata?.is_virtual_interline
      );
    }

    // 2. Ordenação
    result.sort((a, b) => {
      if (sortBy === "TRIVIO_SCORE") {
        const scoreA = a.trivio_score ?? Number.MAX_VALUE;
        const scoreB = b.trivio_score ?? Number.MAX_VALUE;
        if (scoreA !== scoreB) return scoreA - scoreB;
        return a.total_effective_price - b.total_effective_price;
      }
      if (sortBy === "PRICE_ASC") {
        return a.total_effective_price - b.total_effective_price;
      }
      if (sortBy === "DURATION_ASC") {
        return a.door_to_door_duration_minutes - b.door_to_door_duration_minutes;
      }
      return 0;
    });

    return result;
  }, [quotes, modalFilter, sortBy]);

  return (
    <div className="flex flex-col min-h-screen bg-white text-slate-900">
      {/* 1. Header Corporativo com Utility Bar */}
      <Header onSearchClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} />

      {/* 2. Hero Section Imersiva (White Clean com Acentos do Logo Trivio) */}
      <section className="relative pt-12 pb-24 px-4 sm:px-8 bg-gradient-to-b from-white via-cyan-50/20 to-slate-50/80 border-b border-slate-200/80 overflow-hidden">
        {/* Glow de Fundo e Padrão de Grade Geodésica Clean */}
        <div className="absolute inset-0 bg-[radial-gradient(#cbd5e1_1px,transparent_1px)] [background-size:24px_24px] opacity-40 pointer-events-none" />
        <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[350px] bg-cyan-400/10 rounded-full blur-[130px] pointer-events-none" />
        <div className="absolute top-1/3 right-1/4 w-[400px] h-[300px] bg-orange-400/10 rounded-full blur-[110px] pointer-events-none" />

        <div className="max-w-7xl mx-auto relative z-10">
          {/* Título Principal de Impacto */}
          <div className="text-center max-w-4xl mx-auto mb-10">
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-cyan-50 border border-cyan-200 text-cyan-800 text-xs font-bold mb-6 shadow-sm">
              <Sparkles className="w-3.5 h-3.5 text-orange-500" />
              <span>A Revolução da Mobilidade Intermodal Brasileira</span>
            </div>

            <h1 className="text-4xl sm:text-6xl font-black text-slate-950 tracking-tight leading-[1.15]">
              Viaje com mais inteligência e <br />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-cyan-600 via-sky-600 to-orange-500">
                zero atrito de porta a porta
              </span>
            </h1>

            <p className="text-slate-600 text-base sm:text-lg mt-5 max-w-2xl mx-auto leading-relaxed">
              O primeiro comparador que coloca <strong className="text-slate-900 font-bold">Avião</strong> e{" "}
              <strong className="text-slate-900 font-bold">Ônibus</strong> na mesma régua: cálculo de custo real,
              bônus de sono produtivo no leito noturno e combinações Hacker Fares de até 60% de economia.
            </p>
          </div>

          {/* 3. Floating Interactive Search Widget White Clean */}
          <SearchBar onSearch={handleSearch} isLoading={isSearching} />
        </div>
      </section>

      {/* 4. Pilares de Confiança e Metodologia (Design Clean) */}
      <TrustPillars />

      {/* 5. Área de Resultados da Busca Intermodal (Streaming Reativo WebSockets) */}
      <section id="results-section" className="w-full max-w-7xl mx-auto px-4 sm:px-8 py-12 scroll-mt-20">
        
        {/* Barra de Status e Progresso de Streaming */}
        {activeParams && (
          <div className="mb-8 p-5 rounded-2xl bg-slate-50 border border-slate-200/90 shadow-sm">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
              
              <div className="flex items-center gap-3">
                {isSearching ? (
                  <div className="w-9 h-9 rounded-xl bg-cyan-100 flex items-center justify-center text-cyan-700 animate-spin">
                    <RefreshCw className="w-4 h-4" />
                  </div>
                ) : (
                  <div className="w-9 h-9 rounded-xl bg-emerald-100 flex items-center justify-center text-emerald-700">
                    <CheckCircle2 className="w-5 h-5" />
                  </div>
                )}

                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-lg font-black text-slate-950">
                      {displayCode(activeParams.origin)} ➔ {displayCode(activeParams.destination)}
                    </h2>
                    <span className="text-xs px-2.5 py-0.5 rounded-full bg-slate-200/80 font-bold text-slate-700">
                      {quotes.length} {quotes.length === 1 ? "opção" : "opções"}
                    </span>
                  </div>
                  
                  <p className="text-xs text-slate-500 mt-0.5">
                    {isSearching ? (
                      <span className="flex items-center gap-1.5 text-cyan-800 font-semibold">
                        <span className="w-2 h-2 rounded-full bg-cyan-600 animate-ping" />
                        Buscando em tempo real nos coletores... (
                        {activeProviders.join(" • ") || "ClickBus, Ignav"})
                      </span>
                    ) : completedStats ? (
                      <span>
                        Busca concluída em {(completedStats.totalElapsedMs / 1000).toFixed(1)}s • Menor tarifa:{" "}
                        <strong className="text-slate-900">
                          R$ {completedStats.cheapestPrice.toFixed(2).replace(".", ",")}
                        </strong>
                      </span>
                    ) : (
                      <span>Data: {activeParams.date}</span>
                    )}
                  </p>
                </div>
              </div>

              {/* Botão de Cancelamento se Busca estiver em andamento */}
              {isSearching && (
                <button
                  type="button"
                  onClick={cancelSearch}
                  className="px-3.5 py-1.5 rounded-xl border border-slate-300 hover:bg-slate-200 text-xs font-semibold text-slate-700 transition-colors flex items-center gap-1.5 self-start md:self-auto"
                >
                  <X className="w-3.5 h-3.5" />
                  <span>Cancelar</span>
                </button>
              )}
            </div>

            {/* Barra de Filtros e Ordenação */}
            {quotes.length > 0 && (
              <div className="mt-5 pt-4 border-t border-slate-200/80 flex flex-wrap items-center justify-between gap-4 text-xs font-semibold">
                
                {/* Filtro por Modal */}
                <div className="flex items-center gap-1.5">
                  <span className="text-slate-500 flex items-center gap-1 mr-1">
                    <Filter className="w-3.5 h-3.5" />
                    Filtrar:
                  </span>
                  
                  {[
                    { id: "ALL", label: "Todos", icon: null },
                    { id: "AIR", label: "Aéreo", icon: Plane },
                    { id: "ROAD", label: "Ônibus", icon: Bus },
                    { id: "HYBRID", label: "Conexões Híbridas", icon: Layers },
                  ].map((f) => {
                    const Icon = f.icon;
                    return (
                      <button
                        key={f.id}
                        type="button"
                        onClick={() => setModalFilter(f.id as ModalFilter)}
                        className={`px-3 py-1.5 rounded-xl transition-all flex items-center gap-1.5 ${
                          modalFilter === f.id
                            ? "bg-slate-900 text-white shadow-sm"
                            : "bg-white text-slate-600 border border-slate-200 hover:bg-slate-100"
                        }`}
                      >
                        {Icon && <Icon className="w-3 h-3" />}
                        <span>{f.label}</span>
                      </button>
                    );
                  })}
                </div>

                {/* Ordenação */}
                <div className="flex items-center gap-2">
                  <span className="text-slate-500 flex items-center gap-1">
                    <ArrowUpDown className="w-3.5 h-3.5" />
                    Ordenar por:
                  </span>
                  <select
                    value={sortBy}
                    onChange={(e) => setSortBy(e.target.value as SortOption)}
                    className="bg-white border border-slate-200 rounded-xl px-3 py-1.5 text-slate-900 font-bold focus:outline-none focus:border-cyan-500 cursor-pointer"
                  >
                    <option value="TRIVIO_SCORE">Trivio Score (Menor Atrito - Recomendado)</option>
                    <option value="PRICE_ASC">Menor Custo Efetivo Real</option>
                    <option value="DURATION_ASC">Mais Rápido (Porta a Porta)</option>
                  </select>
                </div>

              </div>
            )}
          </div>
        )}

        {/* Aviso das estimativas: ônibus pela ANTT e conexões ônibus + voo */}
        {quotes.some((q) => q.route_metadata?.is_simulated) && (
          <div className="mb-6 p-4 rounded-2xl bg-amber-50 border border-amber-200 text-amber-900 text-xs font-semibold">
            As opções de ônibus usam dados abertos da ANTT: empresa e horário da grade oficial das linhas
            interestaduais (quando a rota tem grade; senão, horário ilustrativo) e preço médio pago na
            classe, não a tarifa daquela partida. Elas não estão disponíveis para compra. Nas conexões, só
            o voo tem tarifa ao vivo.
          </div>
        )}

        {/* Mensagem de Erro se houver */}
        {error && (
          <div className="mb-6 p-4 rounded-2xl bg-red-50 border border-red-200 text-red-800 text-xs font-semibold">
            {error}
          </div>
        )}

        {/* Referência histórica rodoviária (ANTT): fica fora do ranking, nunca é uma oferta */}
        {roadReferences.length > 0 && (
          <div className="mb-6 p-4 rounded-2xl bg-slate-50 border border-slate-200">
            <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-700 mb-1">
              Referência histórica de ônibus (ANTT)
            </h3>
            <p className="text-[11px] text-slate-500 mb-3">
              Faixa do que foi vendido nesta rota ({roadReferences[0].reference_period}). Não é uma passagem:
              não tem horário, empresa nem compra. Não entra na comparação.
            </p>
            <div className="space-y-2">
              {roadReferences.map((ref) => (
                <div
                  key={ref.service_class}
                  className="flex flex-wrap items-baseline justify-between gap-2 text-xs"
                >
                  <span className="font-bold text-slate-800">
                    {ROAD_CLASS_LABELS[ref.service_class] ?? ref.service_class}
                  </span>
                  <span className="text-slate-600">
                    costuma ficar entre <strong>{brl(ref.p25_brl)}</strong> e <strong>{brl(ref.p75_brl)}</strong>
                    {" "}(mediana {brl(ref.median_brl)})
                  </span>
                  {ref.travel_month_mean_brl !== null && (
                    <span className="text-slate-500">
                      média em {ref.travel_month}: {brl(ref.travel_month_mean_brl)}
                    </span>
                  )}
                  {ref.projection && ref.projection.estimate_brl !== null && (
                    <span className="w-full text-[11px] text-slate-500">
                      Projeção para {ref.projection.trip_month}: <strong>{brl(ref.projection.estimate_brl)}</strong>
                      {ref.projection.low_brl !== null && ref.projection.high_brl !== null && (
                        <> (entre {brl(ref.projection.low_brl)} e {brl(ref.projection.high_brl)})</>
                      )}
                      {" "}· confiança {ref.projection.confidence} · {ref.projection.sample_size} bilhetes
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Avisos de provedores que não responderam (ex: aéreo ao vivo indisponível) */}
        {providerNotices.length > 0 && (
          <div className="mb-6 space-y-2">
            {providerNotices.map((notice) => (
              <div
                key={`${notice.provider}-${notice.reason ?? "erro"}`}
                role="status"
                className="p-4 rounded-2xl bg-amber-50 border border-amber-200 text-amber-900 text-xs font-semibold"
              >
                {providerNoticeText(notice)}
              </div>
            ))}
          </div>
        )}

        {/* 1. Destaque de Hacker Fares (Combinações Inteligentes) */}
        {hackerFares.length > 0 && (
          <div className="mb-8">
            <h3 className="text-sm font-extrabold uppercase tracking-wider text-amber-700 mb-3 flex items-center gap-1.5">
              <Sparkles className="w-4 h-4 text-amber-500" />
              Oportunidades Hacker Fares Identificadas
            </h3>
            {hackerFares.map((combo, idx) => (
              <HackerFareCard key={idx} combination={combo} />
            ))}
          </div>
        )}

        {/* 2. Lista de Cards de Cotação */}
        {filteredAndSortedQuotes.length > 0 ? (
          <div className="space-y-4">
            {filteredAndSortedQuotes.map((quote, idx) => (
              <QuoteCard
                key={quote.dedup_signature || idx}
                quote={quote}
                isBestScore={idx === 0}
                sortBy={sortBy}
              />
            ))}
          </div>
        ) : isSearching ? (
          /* Skeletons enquanto os primeiros lotes chegam */
          <div className="space-y-4">
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </div>
        ) : activeParams ? (
          /* Nenhuma opção encontrada */
          <div className="border border-slate-200 bg-slate-50 rounded-3xl p-12 text-center text-slate-600 text-sm">
            Nenhuma rota disponível encontrada para {displayCode(activeParams.origin)} ➔ {displayCode(activeParams.destination)} na data selecionada.
          </div>
        ) : (
          /* Estado Inicial */
          <div className="border-2 border-dashed border-slate-200 bg-slate-50/60 rounded-3xl p-12 text-center text-slate-500 text-sm">
            Selecione uma rota acima ou utilize os atalhos rápidos de São Paulo, Rio ou Curitiba para comparar tarifas.
          </div>
        )}

      </section>

      {/* 6. Footer Corporativo Clean */}
      <footer className="mt-auto border-t border-slate-200 bg-white py-10 px-4 sm:px-8 text-xs text-slate-500">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-3">
            <div className="relative w-8 h-8 rounded-lg overflow-hidden border border-slate-200 bg-slate-50 flex items-center justify-center p-1">
              <Image
                src="/logo_icon.png"
                alt="Trivio Icon"
                fill
                className="object-contain p-0.5"
              />
            </div>
            <span className="font-bold text-slate-900 text-sm">Trivio Intelligence</span>
            <span className="text-slate-300">|</span>
            <span className="text-slate-600">Transformando a mobilidade de longa distância no Brasil.</span>
          </div>

          <div className="flex flex-wrap items-center gap-6 text-slate-500 font-medium">
            <a href="#trivio-score" className="hover:text-cyan-600 transition-colors">Trivio Score</a>
            <a href="#hacker-fares" className="hover:text-orange-600 transition-colors">Hacker Fares</a>
            <a href="/docs" target="_blank" className="hover:text-cyan-600 transition-colors">API REST (FastAPI)</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
