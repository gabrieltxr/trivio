"use client";

import { Sparkles, ArrowRight, Plane, Bus, Clock, ShieldCheck, ExternalLink } from "lucide-react";
import { HackerFareCombination } from "@/types";

interface HackerFareCardProps {
  combination: HackerFareCombination;
}

export default function HackerFareCard({ combination }: HackerFareCardProps) {
  const { outbound, inbound } = combination;

  const formatTime = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    } catch {
      return isoString;
    }
  };

  const formatDuration = (minutes: number) => {
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (h === 0) return `${m}m`;
    if (m === 0) return `${h}h`;
    return `${h}h ${m}m`;
  };

  const getModalIcon = (modal: string) => {
    if (modal === "AIR") {
      return <Plane className="w-4 h-4 text-sky-600" />;
    }
    return <Bus className="w-4 h-4 text-cyan-600" />;
  };

  return (
    <div className="w-full bg-gradient-to-r from-amber-500/10 via-orange-500/5 to-cyan-500/10 p-[2px] rounded-3xl shadow-xl shadow-amber-500/10 mb-8 animate-fadeIn">
      <div className="bg-white rounded-[22px] p-6 sm:p-7 border border-amber-200/60">
        
        {/* Cabeçalho da Hacker Fare */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-5 border-b border-slate-100">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center text-white shadow-md shadow-orange-500/30">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-100 text-amber-900 text-[11px] font-black uppercase tracking-wider mb-1">
                Economia Hacker Fare Desbloqueada
              </div>
              <h3 className="text-lg sm:text-xl font-black text-slate-950">
                {combination.hack_title}
              </h3>
            </div>
          </div>

          <div className="text-left sm:text-right">
            <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider block">
              Economia Estimada
            </span>
            <div className="inline-flex items-center gap-1.5 text-emerald-700 font-black text-xl sm:text-2xl">
              <span>- R$ {combination.savings_vs_single_modal_brl.toFixed(2).replace(".", ",")}</span>
            </div>
            <p className="text-[11px] text-slate-400">
              vs. compra convencional ida e volta
            </p>
          </div>
        </div>

        {/* Descrição da Estratégia */}
        <p className="text-slate-600 text-xs sm:text-sm my-4 leading-relaxed">
          {combination.hack_description}
        </p>

        {/* Grid de Trechos: Ida vs. Volta */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 my-5">
          
          {/* Trecho de Ida */}
          <div className="bg-slate-50/90 rounded-2xl p-4 border border-slate-200/80">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                {getModalIcon(outbound.modal)}
                Trecho de Ida ({outbound.modal === "AIR" ? "Voo Rápido" : "Ônibus"})
              </span>
              <span className="text-xs font-extrabold text-slate-900">
                R$ {outbound.total_effective_price.toFixed(2).replace(".", ",")}
              </span>
            </div>

            <div className="flex items-center justify-between text-slate-900 font-extrabold text-base">
              <span>{formatTime(outbound.departure_time)} ({outbound.origin_external_id})</span>
              <ArrowRight className="w-4 h-4 text-slate-400" />
              <span>{formatTime(outbound.arrival_time)} ({outbound.destination_external_id})</span>
            </div>

            <div className="flex items-center justify-between text-[11px] text-slate-500 mt-2 pt-2 border-t border-slate-200/60">
              <span>{outbound.carrier_name} • {outbound.service_class || "Padrão"}</span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3 text-cyan-600" />
                {formatDuration(outbound.duration_minutes)}
              </span>
            </div>
          </div>

          {/* Trecho de Volta */}
          <div className="bg-slate-50/90 rounded-2xl p-4 border border-slate-200/80">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                {getModalIcon(inbound.modal)}
                Trecho de Volta ({inbound.modal === "ROAD" ? "Leito Conforto" : "Aéreo"})
              </span>
              <span className="text-xs font-extrabold text-slate-900">
                R$ {inbound.total_effective_price.toFixed(2).replace(".", ",")}
              </span>
            </div>

            <div className="flex items-center justify-between text-slate-900 font-extrabold text-base">
              <span>{formatTime(inbound.departure_time)} ({inbound.origin_external_id})</span>
              <ArrowRight className="w-4 h-4 text-slate-400" />
              <span>{formatTime(inbound.arrival_time)} ({inbound.destination_external_id})</span>
            </div>

            <div className="flex items-center justify-between text-[11px] text-slate-500 mt-2 pt-2 border-t border-slate-200/60">
              <span>{inbound.carrier_name} • {inbound.service_class || "Padrão"}</span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3 text-cyan-600" />
                {formatDuration(inbound.duration_minutes)}
              </span>
            </div>
          </div>

        </div>

        {/* Rodapé do Card com Preço Total e Ação de Compra Desdobrada */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pt-4 border-t border-slate-100">
          <div className="flex items-center gap-3">
            <ShieldCheck className="w-5 h-5 text-emerald-600 shrink-0" />
            <p className="text-xs text-slate-500">
              Compradas separadamente em cada operadora com garantia total de assento.
            </p>
          </div>

          <div className="flex items-center gap-4">
            <div className="text-right">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">
                Total Combinado
              </span>
              <span className="text-2xl font-black text-slate-950">
                R$ {combination.combined_effective_price.toFixed(2).replace(".", ",")}
              </span>
            </div>

            <button
              type="button"
              onClick={() => {
                const urlOut = outbound.route_metadata?.booking_url as string;
                if (urlOut) window.open(urlOut, "_blank");
              }}
              className="bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 active:scale-95 text-white font-black px-5 py-3 rounded-xl text-sm shadow-md shadow-orange-500/25 transition-all flex items-center gap-2"
            >
              <span>Reservar Hacker Fare</span>
              <ExternalLink className="w-4 h-4 stroke-[2.5]" />
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
