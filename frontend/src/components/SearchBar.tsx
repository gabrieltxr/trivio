"use client";

import { useState } from "react";
import {
  Search,
  ArrowRightLeft,
  Calendar,
  Clock,
  SlidersHorizontal,
  Sparkles,
  Compass,
} from "lucide-react";
import { SearchParams, TripType } from "@/types";
import LocationAutocomplete from "@/components/LocationAutocomplete";

interface SearchBarProps {
  onSearch: (params: SearchParams) => void;
  isLoading?: boolean;
}

// Cidade inteira usa o prefixo HUB: ("CWB" sozinho seria só o aeroporto de Curitiba).
const POPULAR_HUBS = [
  { code: "HUB:SAO", name: "São Paulo", state: "SP" },
  { code: "HUB:RIO", name: "Rio de Janeiro", state: "RJ" },
  { code: "HUB:CWB", name: "Curitiba", state: "PR" },
  { code: "HUB:POA", name: "Porto Alegre", state: "RS" },
  { code: "HUB:PET", name: "Pelotas", state: "RS" },
  { code: "HUB:VCP", name: "Campinas", state: "SP" },
];

export default function SearchBar({ onSearch, isLoading = false }: SearchBarProps) {
  // Datas padrão: Ida D+7, Volta D+9
  const today = new Date();
  const defaultDeparture = new Date(today);
  defaultDeparture.setDate(today.getDate() + 7);
  const defaultReturn = new Date(today);
  defaultReturn.setDate(today.getDate() + 9);

  // Data local (toISOString usaria UTC e, à noite no Brasil, já seria o dia seguinte)
  const formatDate = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const todayStr = formatDate(today);

  const [tripType, setTripType] = useState<TripType>("round_trip");
  const [origin, setOrigin] = useState("HUB:SAO");
  const [destination, setDestination] = useState("HUB:RIO");
  const [date, setDate] = useState(formatDate(defaultDeparture));
  const [returnDate, setReturnDate] = useState(formatDate(defaultReturn));
  const [radiusKm, setRadiusKm] = useState(120);
  const [hourlyValue, setHourlyValue] = useState(35.0);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const handleSwap = () => {
    const temp = origin;
    setOrigin(destination);
    setDestination(temp);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!origin.trim() || !destination.trim()) {
      setFormError("Selecione a origem e o destino na lista de sugestões.");
      return;
    }
    if (origin.trim().toUpperCase() === destination.trim().toUpperCase()) {
      setFormError("A origem e o destino precisam ser diferentes.");
      return;
    }
    setFormError(null);
    onSearch({
      origin,
      destination,
      date,
      return_date: tripType === "round_trip" ? returnDate : undefined,
      trip_type: tripType,
      hourly_value: hourlyValue,
      radius_km: radiusKm,
      // Conexão ônibus + avião só no modo "Conexão Híbrida": cada hub consulta um voo pago no Ignav.
      enable_virtual_interlining: tripType === "intermodal",
    });
  };

  return (
    <div className="w-full max-w-5xl mx-auto">
      {/* Card Flutuante White Clean com Sombra Elevada e Bordas Suaves */}
      <div className="bg-white border border-slate-200/90 rounded-3xl p-6 sm:p-8 shadow-2xl shadow-slate-200/70 relative z-20">
        
        {/* 1. Abas Segmentadas no Topo do Card */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6 pb-4 border-b border-slate-100">
          <div className="inline-flex p-1 rounded-2xl bg-slate-100/90 border border-slate-200/80">
            <button
              type="button"
              onClick={() => setTripType("round_trip")}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs sm:text-sm font-bold transition ${
                tripType === "round_trip"
                  ? "bg-gradient-to-r from-orange-500 to-amber-500 text-white shadow-md shadow-orange-500/25"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <Sparkles className="w-4 h-4" />
              <span>Ida e Volta (Hacker Fares)</span>
            </button>

            <button
              type="button"
              onClick={() => setTripType("one_way")}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs sm:text-sm font-bold transition ${
                tripType === "one_way"
                  ? "bg-cyan-600 text-white shadow-md shadow-cyan-600/25"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <span>Só Ida</span>
            </button>

            <button
              type="button"
              onClick={() => setTripType("intermodal")}
              className={`hidden sm:flex items-center gap-2 px-4 py-2 rounded-xl text-xs sm:text-sm font-bold transition ${
                tripType === "intermodal"
                  ? "bg-sky-600 text-white shadow-md shadow-sky-600/25"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <Compass className="w-4 h-4" />
              <span>Conexão Híbrida</span>
            </button>
          </div>

          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex items-center gap-1.5 text-xs text-cyan-700 hover:text-cyan-800 font-semibold transition-colors"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            <span>{showAdvanced ? "Ocultar Filtros" : "Ajustar Raio & Valor/Hora"}</span>
          </button>
        </div>

        {/* 2. Formulário Principal com Grid Segmentado */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 items-center">
            
            {/* Campo Origem com Autocomplete (col-span-3) */}
            <div className="lg:col-span-3">
              <LocationAutocomplete
                label="Origem (Hub, Cidade ou Aeroporto)"
                value={origin}
                onChange={(code) => setOrigin(code)}
                placeholder="Ex: São Paulo, Congonhas, CGH..."
                theme="cyan"
                required
              />
            </div>

            {/* Botão de Inversão Swap */}
            <div className="flex justify-center lg:col-span-1">
              <button
                type="button"
                onClick={handleSwap}
                aria-label="Inverter Origem e Destino"
                className="w-10 h-10 rounded-full bg-white border border-slate-200 hover:border-orange-300 hover:bg-orange-50 text-slate-600 hover:text-orange-600 shadow-sm flex items-center justify-center transition active:scale-90"
              >
                <ArrowRightLeft className="w-4 h-4" />
              </button>
            </div>

            {/* Campo Destino com Autocomplete (col-span-3) */}
            <div className="lg:col-span-3">
              <LocationAutocomplete
                label="Destino (Hub, Cidade ou Aeroporto)"
                value={destination}
                onChange={(code) => setDestination(code)}
                placeholder="Ex: Rio de Janeiro, Santos Dumont, SDU..."
                theme="orange"
                required
              />
            </div>

            {/* Campo Datas (col-span-3) */}
            <div className="lg:col-span-3 grid grid-cols-2 gap-2 bg-slate-50 border border-slate-200/90 rounded-2xl p-3">
              <div>
                <label className="block text-[11px] font-bold text-cyan-700 uppercase tracking-wider mb-1 flex items-center gap-1">
                  <Calendar className="w-3 h-3 text-cyan-600" />
                  Ida
                </label>
                <input
                  type="date"
                  value={date}
                  min={todayStr}
                  onChange={(e) => setDate(e.target.value)}
                  className="w-full bg-transparent text-slate-900 font-bold text-xs sm:text-sm focus:outline-none cursor-pointer"
                  required
                />
              </div>

              {tripType === "round_trip" ? (
                <div>
                  <label className="block text-[11px] font-bold text-orange-600 uppercase tracking-wider mb-1 flex items-center gap-1">
                    <Calendar className="w-3 h-3 text-orange-500" />
                    Volta
                  </label>
                  <input
                    type="date"
                    value={returnDate}
                    min={date}
                    onChange={(e) => setReturnDate(e.target.value)}
                    className="w-full bg-transparent text-slate-900 font-bold text-xs sm:text-sm focus:outline-none cursor-pointer"
                    required
                  />
                </div>
              ) : (
                <div className="flex items-center justify-center text-slate-400 text-xs italic">
                  Trecho único
                </div>
              )}
            </div>

            {/* Botão de Busca Sunrise Orange (col-span-2) */}
            <div className="lg:col-span-2">
              <button
                type="submit"
                disabled={isLoading}
                className="w-full h-14 bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 active:scale-[0.98] text-white font-black rounded-2xl shadow-xl shadow-orange-500/30 transition flex items-center justify-center gap-2 text-base disabled:opacity-50"
              >
                {isLoading ? (
                  <span className="inline-block w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <>
                    <Search className="w-5 h-5 stroke-[2.5]" />
                    <span>Comparar</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* 3. Atalhos de Hubs Rápidos */}
          <div className="flex flex-wrap items-center gap-2 pt-1 text-xs text-slate-500">
            <span className="font-semibold text-slate-500">Rotas populares:</span>
            {POPULAR_HUBS.map((hub) => (
              <button
                key={hub.code}
                type="button"
                onClick={() => {
                  if (origin === hub.code) return;
                  if (!destination || destination === hub.code) {
                    setDestination(hub.code);
                  } else {
                    setOrigin(hub.code);
                  }
                }}
                className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-cyan-50 text-slate-700 hover:text-cyan-700 border border-slate-200/80 transition-colors font-medium"
              >
                {hub.name} ({hub.state})
              </button>
            ))}
          </div>

          {/* 4. Painel de Filtros Avançados (Colapsável) */}
          {showAdvanced && (
            <div
              className={`pt-4 mt-4 border-t border-slate-200 grid grid-cols-1 ${
                tripType === "intermodal" ? "md:grid-cols-3" : "md:grid-cols-2"
              } gap-6 text-sm text-slate-700 animate-fade-in-up`}
            >
              
              {/* Valor da Hora */}
              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200">
                <div className="flex justify-between items-center mb-2">
                  <label className="text-xs font-bold uppercase tracking-wider text-cyan-800 flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5 text-cyan-600" />
                    Valor da sua Hora (V_hora)
                  </label>
                  <span className="font-extrabold text-slate-900">R$ {hourlyValue.toFixed(0)}/h</span>
                </div>
                <input
                  type="range"
                  min="15"
                  max="120"
                  step="5"
                  value={hourlyValue}
                  onChange={(e) => setHourlyValue(parseFloat(e.target.value))}
                  className="w-full accent-cyan-600 cursor-pointer"
                />
                <p className="text-[11px] text-slate-500 mt-1">
                  Calibra o Trivio Score valorizando noites de sono produtivo no ônibus Leito.
                </p>
              </div>

              {/* Raio Geodésico: só a Conexão Híbrida usa (hubs com aeroporto perto da origem) */}
              {tripType === "intermodal" && (
              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200">
                <div className="flex justify-between items-center mb-2">
                  <label className="text-xs font-bold uppercase tracking-wider text-cyan-800 flex items-center gap-1.5">
                    <Compass className="w-3.5 h-3.5 text-cyan-600" />
                    Raio de Abrangência
                  </label>
                  <span className="font-extrabold text-slate-900">{radiusKm} km</span>
                </div>
                <div className="flex gap-2">
                  {[50, 100, 120, 180].map((r) => (
                    <button
                      key={r}
                      type="button"
                      onClick={() => setRadiusKm(r)}
                      className={`flex-1 py-1 rounded-lg text-xs font-bold border transition ${
                        radiusKm === r
                          ? "bg-cyan-600 text-white border-cyan-600 shadow-sm"
                          : "bg-white text-slate-700 border-slate-200 hover:bg-slate-100"
                      }`}
                    >
                      {r} km
                    </button>
                  ))}
                </div>
                <p className="text-[11px] text-slate-500 mt-2">
                  Distância máxima da origem até o hub onde o ônibus encontra o voo.
                </p>
              </div>
              )}

              {/* Bagagem: sempre no custo efetivo (o card mostra a linha da franquia) */}
              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200">
                <p className="text-xs font-bold uppercase tracking-wider text-cyan-800 mb-1">
                  Bagagem
                </p>
                <p className="text-[11px] text-slate-500">
                  O custo efetivo do aéreo já inclui uma mala despachada de 23kg estimada. No ônibus,
                  a bagagem no bagageiro é gratuita.
                </p>
              </div>

            </div>
          )}

          {formError && (
            <p role="alert" className="text-xs font-semibold text-red-700">
              {formError}
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
