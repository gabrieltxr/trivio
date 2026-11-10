"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  MapPin,
  Plane,
  Bus,
  Building2,
  Search,
  X,
  Check,
  ChevronDown,
} from "lucide-react";
import { LocationSuggestion, LocationType } from "@/types";
import {
  fetchLocationSuggestions,
  getLocationByCode,
  getPopularLocations,
  normalizeSearchString,
  searchLocalLocations,
} from "@/data/locations";

interface LocationAutocompleteProps {
  label: string;
  value: string; // Código canônico (ex: "SAO", "CGH", "RIO")
  onChange: (code: string, item?: LocationSuggestion) => void;
  placeholder?: string;
  theme?: "cyan" | "orange";
  required?: boolean;
}

// Ordem e títulos das seções: hubs, depois aeroportos, depois rodoviárias.
const TYPE_ORDER: Record<LocationType, number> = { hub: 0, airport: 1, bus_station: 2 };
const SECTION_TITLES: Record<LocationType, string> = {
  hub: "Hubs & Cidades",
  airport: "Aeroportos",
  bus_station: "Rodoviárias",
};

export default function LocationAutocomplete({
  label,
  value,
  onChange,
  placeholder = "Digite cidade, aeroporto ou sigla...",
  theme = "cyan",
  required = true,
}: LocationAutocompleteProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [suggestions, setSuggestions] = useState<LocationSuggestion[]>([]);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const [isLoading, setIsLoading] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Sugestões na ordem exibida (agrupadas por tipo; a relevância se mantém dentro de cada grupo).
  // Navegação por teclado e renderização usam esta mesma lista, então os índices batem.
  const ordered = [...suggestions].sort(
    (a, b) => TYPE_ORDER[a.type] - TYPE_ORDER[b.type]
  );

  // Sincroniza a exibição amigável do campo a partir do código externo
  useEffect(() => {
    if (value) {
      const match = getLocationByCode(value);
      if (match) {
        setInputValue(match.title);
      } else {
        setInputValue(value);
      }
    } else {
      setInputValue("");
    }
  }, [value]);

  // Carrega sugestões populares ao inicializar
  useEffect(() => {
    setSuggestions(getPopularLocations());
  }, []);

  // Resolve o texto digitado para uma localidade: código exato, nome/título exato
  // ou, por fim, a melhor sugestão exibida (mesmo critério do Enter).
  const resolveTypedText = (text: string): LocationSuggestion | undefined => {
    const typed = text.trim();
    if (!typed) return undefined;
    const byCode = getLocationByCode(typed);
    if (byCode) return byCode;
    const norm = normalizeSearchString(typed);
    const exact = suggestions.find(
      (s) =>
        s.code.toUpperCase() === typed.toUpperCase() ||
        normalizeSearchString(s.title) === norm ||
        normalizeSearchString(s.name) === norm
    );
    if (exact) return exact;
    return suggestions[0] ?? searchLocalLocations(typed, 1)[0];
  };

  // Confirma o que foi digitado sem clicar numa sugestão. Antes, o texto ficava só
  // visível no campo e a busca era enviada com o código vazio ou o anterior.
  const commitTypedText = () => {
    setIsOpen(false);
    setHighlightedIndex(-1);

    const current = value ? getLocationByCode(value) : undefined;
    const currentLabel = current ? current.title : value;
    if (value && inputValue === currentLabel) return; // nada mudou

    if (!inputValue.trim()) {
      if (value) onChange("");
      return;
    }

    const match = resolveTypedText(inputValue);
    if (match) {
      setInputValue(match.title);
      if (match.code !== value) onChange(match.code, match);
    } else {
      // Texto não reconhecido: restaura a seleção anterior
      setInputValue(currentLabel || "");
    }
  };

  // Ao clicar fora do componente, confirma o texto digitado
  const commitRef = useRef(commitTypedText);
  commitRef.current = commitTypedText;
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        commitRef.current();
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  // Executa a busca local instantânea e agenda busca na API
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const text = e.target.value;
    setInputValue(text);
    setIsOpen(true);
    setHighlightedIndex(-1);

    // 1. Resposta instantânea (0ms) via base canônica local
    const instantResults = text.trim()
      ? searchLocalLocations(text, 10)
      : getPopularLocations();
    setSuggestions(instantResults);

    // 2. Busca debounced assíncrona para enriquecer com o backend FastAPI
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    if (text.trim().length >= 1) {
      setIsLoading(true);
      debounceTimerRef.current = setTimeout(async () => {
        try {
          const apiResults = await fetchLocationSuggestions(text, 12);
          if (apiResults && apiResults.length > 0) {
            setSuggestions(apiResults);
          }
        } catch {
          // Mantém as sugestões locais em caso de erro de rede
        } finally {
          setIsLoading(false);
        }
      }, 150);
    } else {
      setIsLoading(false);
    }
  };

  const handleSelect = (item: LocationSuggestion) => {
    setInputValue(item.title);
    onChange(item.code, item);
    setIsOpen(false);
    setHighlightedIndex(-1);
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    setInputValue("");
    onChange("");
    setSuggestions(getPopularLocations());
    setIsOpen(true);
    inputRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!isOpen) {
      if (e.key === "ArrowDown" || e.key === "Enter") {
        setIsOpen(true);
        e.preventDefault();
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightedIndex((prev) =>
        prev < ordered.length - 1 ? prev + 1 : 0
      );
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightedIndex((prev) =>
        prev > 0 ? prev - 1 : ordered.length - 1
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlightedIndex >= 0 && highlightedIndex < ordered.length) {
        handleSelect(ordered[highlightedIndex]);
      } else if (ordered.length > 0) {
        handleSelect(ordered[0]);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      commitTypedText();
    } else if (e.key === "Tab") {
      commitTypedText();
    }
  };

  const isCyan = theme === "cyan";

  const renderBadge = (type: LocationType, code: string) => {
    if (type === "hub") {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold bg-cyan-100/80 text-cyan-800 border border-cyan-200">
          <Building2 className="w-3 h-3" />
          <span>HUB</span>
        </span>
      );
    }
    if (type === "airport") {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold bg-sky-100/80 text-sky-800 border border-sky-200">
          <Plane className="w-3 h-3" />
          <span>{code || "AÉREO"}</span>
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold bg-amber-100/80 text-amber-800 border border-amber-200">
        <Bus className="w-3 h-3" />
        <span>RODO</span>
      </span>
    );
  };

  return (
    <div ref={containerRef} className="relative w-full">
      {/* Box do Campo com Destaque de Foco por Tema */}
      <div
        className={`bg-slate-50 hover:bg-white focus-within:bg-white border rounded-2xl p-3 transition-all relative ${
          isOpen
            ? isCyan
              ? "border-cyan-500 ring-2 ring-cyan-500/20 bg-white"
              : "border-orange-500 ring-2 ring-orange-500/20 bg-white"
            : isCyan
            ? "border-slate-200/90 hover:border-slate-300 focus-within:border-cyan-500 focus-within:ring-2 focus-within:ring-cyan-500/20"
            : "border-slate-200/90 hover:border-slate-300 focus-within:border-orange-500 focus-within:ring-2 focus-within:ring-orange-500/20"
        }`}
      >
        <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1 flex items-center justify-between">
          <span className="flex items-center gap-1.5">
            <MapPin
              className={`w-3.5 h-3.5 ${
                isCyan ? "text-cyan-600" : "text-orange-500"
              }`}
            />
            {label}
          </span>
          {value && (
            <span
              className={`text-[10px] font-extrabold px-1.5 py-0.2 rounded ${
                isCyan
                  ? "bg-cyan-50 text-cyan-700 border border-cyan-200/60"
                  : "bg-orange-50 text-orange-700 border border-orange-200/60"
              }`}
            >
              {value}
            </span>
          )}
        </label>

        <div className="flex items-center gap-1">
          <input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={handleInputChange}
            onFocus={() => {
              setIsOpen(true);
              // Seleciona o texto atual: digitar substitui em vez de acrescentar ao valor anterior
              inputRef.current?.select();
              if (!inputValue.trim()) {
                setSuggestions(getPopularLocations());
              }
            }}
            onClick={(e) => e.currentTarget.select()}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            required={required}
            autoComplete="off"
            className="w-full bg-transparent text-slate-900 font-bold text-base focus:outline-none placeholder:text-slate-400 truncate"
          />

          {inputValue ? (
            <button
              type="button"
              onClick={handleClear}
              aria-label="Limpar campo"
              className="p-1 rounded-full text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          ) : (
            <ChevronDown
              className={`w-4 h-4 text-slate-400 transition-transform cursor-pointer ${
                isOpen ? "rotate-180" : ""
              }`}
              onClick={() => {
                setIsOpen(!isOpen);
                inputRef.current?.focus();
              }}
            />
          )}
        </div>
      </div>

      {/* Dropdown Flutuante de Preenchimento Automático */}
      {isOpen && (
        <div className="absolute top-[calc(100%+6px)] left-0 right-0 bg-white border border-slate-200 rounded-2xl shadow-2xl p-2 z-50 max-h-80 overflow-y-auto animate-in fade-in slide-in-from-top-1 duration-150">
          <div className="px-3 py-1.5 text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center justify-between border-b border-slate-100 mb-1">
            <span>
              {inputValue.trim()
                ? `Resultados para "${inputValue}"`
                : "Principais Hubs & Cidades"}
            </span>
            {isLoading && (
              <span className="flex items-center gap-1 text-[10px] text-cyan-600">
                <span className="w-2.5 h-2.5 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin" />
                Buscando...
              </span>
            )}
          </div>

          {ordered.length === 0 ? (
            <div className="py-6 px-4 text-center text-slate-400 text-sm">
              <Search className="w-6 h-6 mx-auto mb-2 text-slate-300 stroke-[1.5]" />
              <p className="font-semibold text-slate-600">
                Nenhuma localidade encontrada
              </p>
              <p className="text-xs text-slate-400 mt-0.5">
                Tente o nome da cidade (ex: São Paulo, Campinas) ou código IATA (ex: CGH, GRU, SDU).
              </p>
            </div>
          ) : (
            <ul className="space-y-1">
              {ordered.map((item, index) => {
                const isSelected = value === item.code;
                const isHighlighted = highlightedIndex === index;
                const showHeader =
                  index === 0 || ordered[index - 1].type !== item.type;

                return [
                  showHeader ? (
                    <li
                      key={`header-${item.type}`}
                      role="presentation"
                      className="px-3 pt-2 pb-1 text-[10px] font-bold text-slate-400 uppercase tracking-wider"
                    >
                      {SECTION_TITLES[item.type]}
                    </li>
                  ) : null,
                  <li
                    key={item.id}
                    onMouseEnter={() => setHighlightedIndex(index)}
                    onClick={() => handleSelect(item)}
                    className={`flex items-center justify-between p-2.5 rounded-xl cursor-pointer transition-all ${
                      isHighlighted
                        ? isCyan
                          ? "bg-cyan-50/80 text-cyan-950"
                          : "bg-orange-50/80 text-orange-950"
                        : isSelected
                        ? "bg-slate-100 text-slate-900 font-semibold"
                        : "hover:bg-slate-50 text-slate-700"
                    }`}
                  >
                    <div className="flex items-center gap-3 min-w-0 pr-2">
                      <div
                        className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                          item.type === "hub"
                            ? "bg-cyan-100/70 text-cyan-700"
                            : item.type === "airport"
                            ? "bg-sky-100/70 text-sky-700"
                            : "bg-amber-100/70 text-amber-700"
                        }`}
                      >
                        {item.type === "hub" ? (
                          <Building2 className="w-4 h-4" />
                        ) : item.type === "airport" ? (
                          <Plane className="w-4 h-4" />
                        ) : (
                          <Bus className="w-4 h-4" />
                        )}
                      </div>

                      <div className="min-w-0">
                        <div className="font-bold text-sm text-slate-900 truncate flex items-center gap-1.5">
                          <span>{item.name}</span>
                          {item.type === "hub" && (
                            <span className="text-[11px] font-medium text-slate-400">
                              ({item.state})
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-slate-500 truncate">
                          {item.subtitle}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      {renderBadge(item.type, item.code)}
                      {isSelected && (
                        <Check className="w-4 h-4 text-emerald-600 stroke-[2.5]" />
                      )}
                    </div>
                  </li>,
                ];
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
