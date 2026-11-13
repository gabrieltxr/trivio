"use client";

import { memo, useState } from "react";
import {
  Plane,
  Bus,
  Clock,
  Luggage,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  Flame,
  ShieldAlert,
  Sparkles,
  Layers,
} from "lucide-react";
import { FareQuote, PriceThermometerData } from "@/types";
import { formatDuration, formatTime } from "@/lib/format";

type SortCriterion = "TRIVIO_SCORE" | "PRICE_ASC" | "DURATION_ASC";

interface QuoteCardProps {
  quote: FareQuote;
  isBestScore?: boolean;
  /**
   * Critério da ordenação ativa, usado só no título da faixa do melhor card. O destaque do
   * valor que ordena a lista vem do `data-sort` do contêiner (grupo `results`), por CSS:
   * assim trocar a ordenação não redesenha todos os cards.
   */
  sortBy?: SortCriterion;
  /** Atraso da animação de entrada, para os cards de um mesmo lote entrarem em sequência. */
  enterDelayMs?: number;
}

const BEST_BANNER: Record<SortCriterion, { title: string; subtitle: string }> = {
  TRIVIO_SCORE: {
    title: "Melhor Escolha Trivio (Menor Índice de Agonia)",
    subtitle: "Custo Real + Tempo Otimizado",
  },
  PRICE_ASC: {
    title: "Menor Custo Efetivo da Lista",
    subtitle: "Ordenado por preço",
  },
  DURATION_ASC: {
    title: "Mais Rápido Porta a Porta",
    subtitle: "Ordenado por tempo",
  },
};

/** Nome exibido da fonte de cada cotação. */
const PROVIDER_LABELS: Record<string, string> = {
  antt_projection: "Dados ANTT",
  ignav: "Ignav",
  trivio_interliner: "Conexão Trivio",
};

// memo: um lote novo de cotações não redesenha os cards que já estavam na lista.
export default memo(function QuoteCard({
  quote,
  isBestScore = false,
  sortBy = "TRIVIO_SCORE",
  enterDelayMs = 0,
}: QuoteCardProps) {
  const [showDetails, setShowDetails] = useState(false);

  const isAir = quote.modal === "AIR";
  const isHybrid = quote.modal === "ROAD_AIR" || quote.modal === "HYBRID" || quote.route_metadata?.is_virtual_interline;

  const meta = quote.route_metadata || {};
  const pros = meta.pros || [];
  const cons = meta.cons || [];
  const setbacks = meta.potential_setbacks || [];
  const thermometer: PriceThermometerData | undefined = meta.price_thermometer;
  const isSimulated = meta.is_simulated === true;
  const bookingUrl = !isSimulated && typeof meta.booking_url === "string" ? meta.booking_url : null;

  // Cor e badge do Trivio Score
  const getScoreBadge = (score?: number | null) => {
    if (score === null || score === undefined) return null;
    let colorClass = "bg-emerald-50 text-emerald-700 border-emerald-200";
    let label = "Baixo Atrito";
    if (score > 600) {
      colorClass = "bg-orange-50 text-orange-700 border-orange-200";
      label = "Atrito Moderado";
    } else if (score > 400) {
      colorClass = "bg-sky-50 text-sky-700 border-sky-200";
      label = "Equilibrado";
    }

    return (
      <div className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-bold ${colorClass}`}>
        <Sparkles className="w-3.5 h-3.5" />
        <span>Trivio Score: {score.toFixed(0)} pts</span>
        <span className="opacity-70 font-normal">({label})</span>
      </div>
    );
  };

  // Badge do Termômetro de Preço
  const getThermometerBadge = (therm?: PriceThermometerData) => {
    if (!therm || therm.status === "INSUFFICIENT_DATA") return null;

    if (therm.status === "LOW") {
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-emerald-100 text-emerald-800 text-[11px] font-bold border border-emerald-300">
          <Flame className="w-3.5 h-3.5 text-emerald-600 fill-emerald-600" />
          Preço Baixo {therm.percentile ? `(P${therm.percentile})` : ""}
        </span>
      );
    }
    if (therm.status === "HIGH") {
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-amber-100 text-amber-900 text-[11px] font-bold border border-amber-300">
          <AlertTriangle className="w-3.5 h-3.5 text-amber-600" />
          Tarifa Elevada
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700 text-[11px] font-semibold border border-slate-200">
        Preço Típico
      </span>
    );
  };

  return (
    <div
      className={`w-full bg-white border rounded-2xl transition-[border-color,box-shadow] duration-200 animate-card-in ${
        isBestScore
          ? "border-emerald-400 shadow-lg shadow-emerald-500/10 ring-1 ring-emerald-400/40"
          : "border-slate-200/90 shadow-sm hover:shadow-md hover:border-slate-300"
      }`}
      style={enterDelayMs ? { animationDelay: `${enterDelayMs}ms` } : undefined}
    >
      {/* Faixa Destaque de Melhor Score */}
      {isBestScore && (
        <div className="bg-gradient-to-r from-emerald-500 to-teal-500 text-white text-[11px] font-black uppercase tracking-wider py-1 px-4 rounded-t-2xl flex items-center justify-between">
          <span className="flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5" />
            {BEST_BANNER[sortBy].title}
          </span>
          <span className="text-emerald-100 font-semibold text-[10px]">
            {BEST_BANNER[sortBy].subtitle}
          </span>
        </div>
      )}

      {/* Conteúdo Principal do Card */}
      <div className="p-5 sm:p-6">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6">
          
          {/* Lado Esquerdo: Identificação Modal, Linha do Tempo e Paradas */}
          <div className="flex-1 space-y-4">
            
            {/* Cabeçalho da Opção: Modal, Operadora e Badges */}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2.5">
                {/* Ícone Modal */}
                <div
                  className={`w-9 h-9 rounded-xl flex items-center justify-center font-bold ${
                    isHybrid
                      ? "bg-purple-50 text-purple-600 border border-purple-200"
                      : isAir
                      ? "bg-sky-50 text-sky-600 border border-sky-200"
                      : "bg-cyan-50 text-cyan-600 border border-cyan-200"
                  }`}
                >
                  {isHybrid ? (
                    <Layers className="w-5 h-5" />
                  ) : isAir ? (
                    <Plane className="w-5 h-5" />
                  ) : (
                    <Bus className="w-5 h-5" />
                  )}
                </div>

                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-extrabold text-slate-900 text-base">
                      {quote.carrier_name}
                    </span>
                    {quote.service_class && (
                      <span className="text-[11px] font-bold px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 uppercase">
                        {quote.service_class}
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-slate-400 capitalize">
                    {PROVIDER_LABELS[quote.provider_source] ?? (quote.provider_source.startsWith("gtfs_") ? "Horário GTFS" : quote.provider_source)}
                  </span>
                </div>
              </div>

              {/* Badges de Score e Termômetro */}
              <div className="flex flex-wrap items-center gap-2">
                {getScoreBadge(quote.trivio_score)}
                {getThermometerBadge(thermometer)}
                {isHybrid && (
                  <span className="px-2.5 py-0.5 rounded-full bg-purple-100 text-purple-800 text-[11px] font-bold border border-purple-200">
                    Conexão Híbrida
                  </span>
                )}
                {isSimulated && (
                  <span
                    title={meta.simulation_notice || "Estimativa, sem tarifa real de mercado."}
                    className="px-2.5 py-0.5 rounded-full bg-slate-200 text-slate-700 text-[11px] font-bold border border-slate-300"
                  >
                    {meta.price_basis === "antt_projection+ignav"
                      ? "Ônibus estimado (ANTT) + voo ao vivo"
                      : meta.price_basis === "antt_projection"
                      ? "Estimativa ANTT"
                      : meta.price_basis === "gtfs_fare_regulated"
                      ? "Horário GTFS · tarifa regulada"
                      : "Estimativa"}
                  </span>
                )}
              </div>
            </div>

            {/* Linha do Tempo Visual */}
            <div className="grid grid-cols-1 sm:grid-cols-12 gap-3 items-center pt-1">
              
              {/* Partida */}
              <div className="sm:col-span-3">
                <span className="text-2xl font-black text-slate-900">
                  {formatTime(quote.departure_time)}
                </span>
                <p className="text-xs font-bold text-slate-600 uppercase tracking-wider">
                  {quote.origin_external_id}
                </p>
                <p className="text-[11px] text-slate-400">
                  {quote.canonical_origin_id || "Origem"}
                </p>
              </div>

              {/* Indicador de Rota e Duração */}
              <div className="sm:col-span-6 flex flex-col items-center justify-center text-center px-2">
                <div
                  className="flex items-center gap-1.5 text-xs font-bold text-slate-600 mb-1 rounded-lg px-2 py-0.5 group-data-[sort=duration]/results:bg-cyan-50 group-data-[sort=duration]/results:ring-1 group-data-[sort=duration]/results:ring-cyan-300 group-data-[sort=duration]/results:text-cyan-900"
                >
                  <Clock className="w-3.5 h-3.5 text-cyan-600" />
                  <span>{formatDuration(quote.duration_minutes)}</span>
                  <span className="text-slate-400 font-normal">
                    ({formatDuration(quote.door_to_door_duration_minutes)} porta a porta)
                  </span>
                </div>

                <div className="w-full h-1 bg-slate-200 rounded-full relative my-1">
                  <div className="w-2.5 h-2.5 rounded-full bg-cyan-500 absolute left-0 top-1/2 -translate-y-1/2 shadow-sm" />
                  <div className="w-2.5 h-2.5 rounded-full bg-orange-500 absolute right-0 top-1/2 -translate-y-1/2 shadow-sm" />
                </div>

                <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-1.5">
                  {meta.is_direct ? (
                    <span className="text-emerald-700 font-semibold">Viagem Direta</span>
                  ) : (
                    <span className="text-amber-700 font-semibold">
                      {meta.stops_count || 1} parada/conexão
                      {meta.layover_airports?.length ? ` em ${meta.layover_airports.join(", ")}` : ""}
                    </span>
                  )}
                </div>
              </div>

              {/* Chegada */}
              <div className="sm:col-span-3 text-left sm:text-right">
                <span className="text-2xl font-black text-slate-900">
                  {formatTime(quote.arrival_time)}
                </span>
                <p className="text-xs font-bold text-slate-600 uppercase tracking-wider">
                  {quote.destination_external_id}
                </p>
                <p className="text-[11px] text-slate-400">
                  {quote.canonical_destination_id || "Destino"}
                </p>
              </div>

            </div>

          </div>

          {/* Lado Direito: Custo Efetivo Real e Ação */}
          <div className="lg:border-l lg:border-slate-100 lg:pl-6 flex flex-row lg:flex-col items-center lg:items-end justify-between gap-4 min-w-[200px]">
            
            <div
              className="text-left lg:text-right rounded-xl px-2 py-1 group-data-[sort=price]/results:bg-cyan-50 group-data-[sort=price]/results:ring-1 group-data-[sort=price]/results:ring-cyan-300"
            >
              <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider block">
                Custo Efetivo Real
              </span>
              <div className="flex items-baseline gap-1">
                <span className="text-xs font-bold text-slate-500">R$</span>
                <span className="text-3xl font-black text-slate-950">
                  {quote.total_effective_price.toFixed(2).replace(".", ",")}
                </span>
              </div>
              <p className="text-[11px] text-slate-400">
                Tarifa base: R$ {quote.base_price.toFixed(2).replace(".", ",")}
              </p>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setShowDetails(!showDetails)}
                className="px-3 py-2 rounded-xl border border-slate-200 hover:border-slate-300 text-xs font-semibold text-slate-600 hover:text-slate-900 transition-colors flex items-center gap-1"
                aria-label="Ver decomposição de custos"
              >
                <span>{showDetails ? "Ocultar" : "Detalhes"}</span>
                {showDetails ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              </button>

              {bookingUrl ? (
                <a
                  href={bookingUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 active:scale-95 text-white font-bold px-4 py-2 rounded-xl text-xs sm:text-sm shadow-md shadow-orange-500/20 transition flex items-center gap-1.5"
                >
                  <span>Ver Oferta</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              ) : (
                <span className="px-4 py-2 rounded-xl text-xs sm:text-sm font-bold text-slate-500 bg-slate-100 border border-slate-200">
                  {isSimulated ? "Indisponível para compra" : "Sem link de oferta"}
                </span>
              )}
            </div>

          </div>

        </div>

        {/* Alertas Imediatos de Contratempos (Setbacks Críticos) */}
        {setbacks.length > 0 && (
          <div className="mt-4 pt-3 border-t border-slate-100 flex flex-wrap gap-2">
            {setbacks.map((setback, i) => (
              <div
                key={i}
                className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-amber-50 border border-amber-200/80 text-amber-800 text-xs font-medium"
              >
                <ShieldAlert className="w-3.5 h-3.5 text-amber-600 shrink-0" />
                <span>{setback}</span>
              </div>
            ))}
          </div>
        )}

        {/* Gaveta Expansível de Detalhes, Prós & Contras e Decomposição Financeira */}
        {showDetails && (
          <div className="mt-5 pt-5 border-t border-slate-100 grid grid-cols-1 md:grid-cols-2 gap-6 animate-fade-in-up text-xs">
            
            {/* Coluna 1: Decomposição do Custo Real Porta a Porta */}
            <div className="bg-slate-50/80 p-4 rounded-xl border border-slate-200/80 space-y-2">
              <span className="font-bold text-slate-900 uppercase tracking-wider block mb-2 flex items-center gap-1.5">
                <Luggage className="w-3.5 h-3.5 text-cyan-600" />
                Decomposição Financeira Porta a Porta
              </span>

              <div className="flex justify-between py-1 border-b border-slate-200/60 text-slate-600">
                <span>Tarifa Base da Passagem:</span>
                <span className="font-semibold text-slate-900">
                  R$ {quote.base_price.toFixed(2).replace(".", ",")}
                </span>
              </div>

              <div className="flex justify-between py-1 border-b border-slate-200/60 text-slate-600">
                <span>Taxas de Embarque & Regulatórias:</span>
                <span className="font-semibold text-slate-900">
                  + R$ {quote.taxes_price.toFixed(2).replace(".", ",")}
                </span>
              </div>

              <div className="flex justify-between py-1 border-b border-slate-200/60 text-slate-600">
                <span>Franquia de Bagagem Despachada (23kg):</span>
                <span className="font-semibold text-slate-900">
                  {quote.baggage_fee_estimate > 0
                    ? `+ R$ ${quote.baggage_fee_estimate.toFixed(2).replace(".", ",")}`
                    : "Inclusa (R$ 0,00)"}
                </span>
              </div>

              <div className="flex justify-between py-1 border-b border-slate-200/60 text-slate-600">
                <span>Transfer Urbano até o Terminal/Aeroporto:</span>
                <span className="font-semibold text-slate-900">
                  {quote.transfer_cost_estimate > 0
                    ? `+ R$ ${quote.transfer_cost_estimate.toFixed(2).replace(".", ",")}`
                    : "Embarque Central (R$ 0,00)"}
                </span>
              </div>

              <div className="flex justify-between pt-2 text-sm font-extrabold text-slate-950">
                <span>Custo Efetivo Real Total:</span>
                <span className="text-orange-600">
                  R$ {quote.total_effective_price.toFixed(2).replace(".", ",")}
                </span>
              </div>

              {thermometer?.recommendation && (
                <div className="pt-1 space-y-1">
                  <p className="text-[11px] text-slate-500 italic">
                    💡 {thermometer.recommendation}
                  </p>
                  {thermometer.source === "antt_monitriip" && (
                    <p className="text-[10px] text-slate-400">
                      Fonte: bilhetes comercializados publicados pela ANTT
                      {thermometer.reference_period ? ` (${thermometer.reference_period})` : ""}
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* Coluna 2: Prós, Contras e Múltiplos Vendedores */}
            <div className="space-y-4">
              
              {/* Prós */}
              {pros.length > 0 && (
                <div>
                  <span className="font-bold text-slate-900 uppercase tracking-wider block mb-2 flex items-center gap-1.5">
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                    Pontos Positivos da Rota
                  </span>
                  <ul className="space-y-1">
                    {pros.map((pro, idx) => (
                      <li key={idx} className="flex items-start gap-1.5 text-slate-600">
                        <span className="text-emerald-500 font-bold">•</span>
                        <span>{pro}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Contras */}
              {cons.length > 0 && (
                <div>
                  <span className="font-bold text-slate-900 uppercase tracking-wider block mb-2 flex items-center gap-1.5">
                    <XCircle className="w-3.5 h-3.5 text-slate-400" />
                    Pontos de Atenção
                  </span>
                  <ul className="space-y-1">
                    {cons.map((con, idx) => (
                      <li key={idx} className="flex items-start gap-1.5 text-slate-500">
                        <span className="text-slate-400 font-bold">•</span>
                        <span>{con}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Múltiplos Vendedores Disponíveis */}
              {meta.available_sellers && meta.available_sellers.length > 1 && (
                <div className="bg-slate-50 p-2.5 rounded-lg border border-slate-200/70">
                  <span className="font-bold text-slate-700 block mb-1">
                    Comparado em múltiplos canais:
                  </span>
                  <div className="flex flex-wrap gap-2 text-[11px]">
                    {meta.available_sellers.map((seller, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 rounded bg-white border border-slate-200 text-slate-700 font-medium"
                      >
                        {seller.provider_source}: R$ {seller.total_effective_price.toFixed(2)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

            </div>

          </div>
        )}

      </div>
    </div>
  );
});
