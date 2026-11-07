import { LocationSuggestion } from "@/types";

/**
 * Normaliza strings para busca insensível a acentuação e caixa.
 */
export function normalizeSearchString(text: string): string {
  if (!text) return "";
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

/**
 * Base canônica local de cidades, hubs, aeroportos e rodoviárias brasileiras.
 * Garante resposta instantânea (0ms) mesmo antes ou durante a requisição de rede.
 */
export const CANONICAL_LOCATIONS: LocationSuggestion[] = [
  // --- HUBS (Municípios Âncora) ---
  {
    id: "hub-sao",
    type: "hub",
    name: "São Paulo",
    city: "São Paulo",
    state: "SP",
    code: "SAO",
    modal: "ALL",
    title: "São Paulo, SP",
    subtitle: "Todos os aeroportos e rodoviárias (Hub)",
    highlight: "SAO",
  },
  {
    id: "hub-rio",
    type: "hub",
    name: "Rio de Janeiro",
    city: "Rio de Janeiro",
    state: "RJ",
    code: "RIO",
    modal: "ALL",
    title: "Rio de Janeiro, RJ",
    subtitle: "Todos os aeroportos e rodoviárias (Hub)",
    highlight: "RIO",
  },
  {
    id: "hub-cwb",
    type: "hub",
    name: "Curitiba",
    city: "Curitiba",
    state: "PR",
    code: "CWB",
    modal: "ALL",
    title: "Curitiba, PR",
    subtitle: "Todos os aeroportos e rodoviárias (Hub)",
    highlight: "CWB",
  },
  {
    id: "hub-poa",
    type: "hub",
    name: "Porto Alegre",
    city: "Porto Alegre",
    state: "RS",
    code: "POA",
    modal: "ALL",
    title: "Porto Alegre, RS",
    subtitle: "Todos os aeroportos e rodoviárias (Hub)",
    highlight: "POA",
  },
  {
    id: "hub-vcp",
    type: "hub",
    name: "Campinas",
    city: "Campinas",
    state: "SP",
    code: "VCP",
    modal: "ALL",
    title: "Campinas, SP",
    subtitle: "Viracopos e Rodoviária Ramos de Azevedo",
    highlight: "VCP",
  },
  {
    id: "hub-pet",
    type: "hub",
    name: "Pelotas",
    city: "Pelotas",
    state: "RS",
    code: "PET",
    modal: "ALL",
    title: "Pelotas, RS",
    subtitle: "Aeroporto e Rodoviária de Pelotas",
    highlight: "PET",
  },
  {
    id: "hub-rig",
    type: "hub",
    name: "Rio Grande",
    city: "Rio Grande",
    state: "RS",
    code: "RIG",
    modal: "ALL",
    title: "Rio Grande, RS",
    subtitle: "Aeroporto Regional e Rodoviária",
    highlight: "RIG",
  },
  {
    id: "hub-bsb",
    type: "hub",
    name: "Brasília",
    city: "Brasília",
    state: "DF",
    code: "BSB",
    modal: "ALL",
    title: "Brasília, DF",
    subtitle: "Aeroporto Internacional e Terminal Interestadual",
    highlight: "BSB",
  },
  {
    id: "hub-bhz",
    type: "hub",
    name: "Belo Horizonte",
    city: "Belo Horizonte",
    state: "MG",
    code: "BHZ",
    modal: "ALL",
    title: "Belo Horizonte, MG",
    subtitle: "Confins, Pampulha e Rodoviária",
    highlight: "BHZ",
  },
  {
    id: "hub-fln",
    type: "hub",
    name: "Florianópolis",
    city: "Florianópolis",
    state: "SC",
    code: "FLN",
    modal: "ALL",
    title: "Florianópolis, SC",
    subtitle: "Hercílio Luz e Terminal Rita Maria",
    highlight: "FLN",
  },
  {
    id: "hub-ssa",
    type: "hub",
    name: "Salvador",
    city: "Salvador",
    state: "BA",
    code: "SSA",
    modal: "ALL",
    title: "Salvador, BA",
    subtitle: "Aeroporto de Salvador e Rodoviária",
    highlight: "SSA",
  },
  {
    id: "hub-for",
    type: "hub",
    name: "Fortaleza",
    city: "Fortaleza",
    state: "CE",
    code: "FOR",
    modal: "ALL",
    title: "Fortaleza, CE",
    subtitle: "Pinto Martins e Rodoviária João Thomé",
    highlight: "FOR",
  },
  {
    id: "hub-rec",
    type: "hub",
    name: "Recife",
    city: "Recife",
    state: "PE",
    code: "REC",
    modal: "ALL",
    title: "Recife, PE",
    subtitle: "Guararapes e Terminal Integrado (TIP)",
    highlight: "REC",
  },

  // --- AEROPORTOS (AIR) ---
  {
    id: "air-cgh",
    type: "airport",
    name: "Aeroporto de Congonhas",
    city: "São Paulo",
    state: "SP",
    code: "CGH",
    modal: "AIR",
    title: "Aeroporto de Congonhas (CGH)",
    subtitle: "São Paulo, SP • Aeroporto • IATA: CGH",
    highlight: "CGH",
  },
  {
    id: "air-gru",
    type: "airport",
    name: "Aeroporto Internacional de Guarulhos",
    city: "São Paulo",
    state: "SP",
    code: "GRU",
    modal: "AIR",
    title: "Aeroporto Int. de Guarulhos (GRU)",
    subtitle: "São Paulo, SP • Aeroporto • IATA: GRU",
    highlight: "GRU",
  },
  {
    id: "air-sdu",
    type: "airport",
    name: "Aeroporto Santos Dumont",
    city: "Rio de Janeiro",
    state: "RJ",
    code: "SDU",
    modal: "AIR",
    title: "Aeroporto Santos Dumont (SDU)",
    subtitle: "Rio de Janeiro, RJ • Aeroporto • IATA: SDU",
    highlight: "SDU",
  },
  {
    id: "air-gig",
    type: "airport",
    name: "Aeroporto Internacional do Galeão",
    city: "Rio de Janeiro",
    state: "RJ",
    code: "GIG",
    modal: "AIR",
    title: "Aeroporto do Galeão (GIG)",
    subtitle: "Rio de Janeiro, RJ • Aeroporto • IATA: GIG",
    highlight: "GIG",
  },
  {
    id: "air-vcp",
    type: "airport",
    name: "Aeroporto Internacional de Viracopos",
    city: "Campinas",
    state: "SP",
    code: "VCP",
    modal: "AIR",
    title: "Aeroporto de Viracopos (VCP)",
    subtitle: "Campinas, SP • Aeroporto • IATA: VCP",
    highlight: "VCP",
  },
  {
    id: "air-cwb",
    type: "airport",
    name: "Aeroporto Internacional Afonso Pena",
    city: "Curitiba",
    state: "PR",
    code: "CWB",
    modal: "AIR",
    title: "Aeroporto Afonso Pena (CWB)",
    subtitle: "Curitiba, PR • Aeroporto • IATA: CWB",
    highlight: "CWB",
  },
  {
    id: "air-poa",
    type: "airport",
    name: "Aeroporto Internacional Salgado Filho",
    city: "Porto Alegre",
    state: "RS",
    code: "POA",
    modal: "AIR",
    title: "Aeroporto Salgado Filho (POA)",
    subtitle: "Porto Alegre, RS • Aeroporto • IATA: POA",
    highlight: "POA",
  },
  {
    id: "air-cnf",
    type: "airport",
    name: "Aeroporto Internacional de Confins",
    city: "Belo Horizonte",
    state: "MG",
    code: "CNF",
    modal: "AIR",
    title: "Aeroporto de Confins (CNF)",
    subtitle: "Belo Horizonte, MG • Aeroporto • IATA: CNF",
    highlight: "CNF",
  },
  {
    id: "air-bsb",
    type: "airport",
    name: "Aeroporto Internacional de Brasília",
    city: "Brasília",
    state: "DF",
    code: "BSB",
    modal: "AIR",
    title: "Aeroporto de Brasília (BSB)",
    subtitle: "Brasília, DF • Aeroporto • IATA: BSB",
    highlight: "BSB",
  },
  {
    id: "air-fln",
    type: "airport",
    name: "Aeroporto Internacional Hercílio Luz",
    city: "Florianópolis",
    state: "SC",
    code: "FLN",
    modal: "AIR",
    title: "Aeroporto Hercílio Luz (FLN)",
    subtitle: "Florianópolis, SC • Aeroporto • IATA: FLN",
    highlight: "FLN",
  },
  {
    id: "air-pet",
    type: "airport",
    name: "Aeroporto Internacional de Pelotas",
    city: "Pelotas",
    state: "RS",
    code: "PET",
    modal: "AIR",
    title: "Aeroporto de Pelotas (PET)",
    subtitle: "Pelotas, RS • Aeroporto • IATA: PET",
    highlight: "PET",
  },
  {
    id: "air-rig",
    type: "airport",
    name: "Aeroporto Regional de Rio Grande",
    city: "Rio Grande",
    state: "RS",
    code: "RIG",
    modal: "AIR",
    title: "Aeroporto de Rio Grande (RIG)",
    subtitle: "Rio Grande, RS • Aeroporto • IATA: RIG",
    highlight: "RIG",
  },

  // --- RODOVIÁRIAS (ROAD) ---
  {
    id: "rod-tiete",
    type: "bus_station",
    name: "Terminal Rodoviário do Tietê",
    city: "São Paulo",
    state: "SP",
    code: "TIETE",
    modal: "ROAD",
    title: "Terminal Rodoviário do Tietê (TIETE)",
    subtitle: "São Paulo, SP • Terminal Rodoviário",
    highlight: "TIETE",
  },
  {
    id: "rod-barrafunda",
    type: "bus_station",
    name: "Terminal Rodoviário Barra Funda",
    city: "São Paulo",
    state: "SP",
    code: "BARRA_FUNDA",
    modal: "ROAD",
    title: "Terminal Barra Funda (BARRA_FUNDA)",
    subtitle: "São Paulo, SP • Terminal Rodoviário",
    highlight: "BARRA_FUNDA",
  },
  {
    id: "rod-novorio",
    type: "bus_station",
    name: "Rodoviária Novo Rio",
    city: "Rio de Janeiro",
    state: "RJ",
    code: "NOVO_RIO",
    modal: "ROAD",
    title: "Rodoviária Novo Rio (NOVO_RIO)",
    subtitle: "Rio de Janeiro, RJ • Terminal Rodoviário",
    highlight: "NOVO_RIO",
  },
  {
    id: "rod-cwb",
    type: "bus_station",
    name: "Rodoferroviária de Curitiba",
    city: "Curitiba",
    state: "PR",
    code: "RODO_CWB",
    modal: "ROAD",
    title: "Rodoferroviária de Curitiba (RODO_CWB)",
    subtitle: "Curitiba, PR • Terminal Rodoviário",
    highlight: "RODO_CWB",
  },
  {
    id: "rod-campinas",
    type: "bus_station",
    name: "Terminal Multimodal Ramos de Azevedo",
    city: "Campinas",
    state: "SP",
    code: "ROD_CAMPINAS",
    modal: "ROAD",
    title: "Rodoviária de Campinas (ROD_CAMPINAS)",
    subtitle: "Campinas, SP • Terminal Rodoviário",
    highlight: "ROD_CAMPINAS",
  },
  {
    id: "rod-poa",
    type: "bus_station",
    name: "Estação Rodoviária de Porto Alegre",
    city: "Porto Alegre",
    state: "RS",
    code: "ROD_POA",
    modal: "ROAD",
    title: "Rodoviária de Porto Alegre (ROD_POA)",
    subtitle: "Porto Alegre, RS • Terminal Rodoviário",
    highlight: "ROD_POA",
  },
  {
    id: "rod-pelotas",
    type: "bus_station",
    name: "Terminal Rodoviário de Pelotas",
    city: "Pelotas",
    state: "RS",
    code: "ROD_PELOTAS",
    modal: "ROAD",
    title: "Rodoviária de Pelotas (ROD_PELOTAS)",
    subtitle: "Pelotas, RS • Terminal Rodoviário",
    highlight: "ROD_PELOTAS",
  },
  {
    id: "rod-riogrande",
    type: "bus_station",
    name: "Estação Rodoviária de Rio Grande",
    city: "Rio Grande",
    state: "RS",
    code: "ROD_RIO_GRANDE",
    modal: "ROAD",
    title: "Rodoviária de Rio Grande (ROD_RIO_GRANDE)",
    subtitle: "Rio Grande, RS • Terminal Rodoviário",
    highlight: "ROD_RIO_GRANDE",
  },
  {
    id: "rod-bh",
    type: "bus_station",
    name: "Terminal Rodoviário de Belo Horizonte",
    city: "Belo Horizonte",
    state: "MG",
    code: "ROD_BH",
    modal: "ROAD",
    title: "Rodoviária de Belo Horizonte (ROD_BH)",
    subtitle: "Belo Horizonte, MG • Terminal Rodoviário",
    highlight: "ROD_BH",
  },
];

/**
 * Retorna os principais Hubs para exibição inicial no foco do campo.
 */
export function getPopularLocations(): LocationSuggestion[] {
  return CANONICAL_LOCATIONS.filter((l) => l.type === "hub").slice(0, 7);
}

/**
 * Busca local síncrona com ranking por precisão.
 */
export function searchLocalLocations(
  query: string,
  limit: number = 10
): LocationSuggestion[] {
  const cleanQ = normalizeSearchString(query);
  const upperQ = query.trim().toUpperCase();

  if (!cleanQ) {
    return getPopularLocations();
  }

  const scored: Array<{ score: number; item: LocationSuggestion }> = [];

  for (const item of CANONICAL_LOCATIONS) {
    const normName = normalizeSearchString(item.name);
    const normCity = normalizeSearchString(item.city);
    const normState = item.state.toLowerCase();
    const itemCode = item.code.toUpperCase();

    let score = 99;

    if (upperQ === itemCode) {
      score = 0; // Código IATA ou sigla exata
    } else if (cleanQ === normName || cleanQ === normCity) {
      score = 1; // Nome exato
    } else if (itemCode.startsWith(upperQ) && upperQ.length >= 2) {
      score = 2; // Prefixo de código
    } else if (normName.startsWith(cleanQ) || normCity.startsWith(cleanQ)) {
      score = 3; // Prefixo do nome/cidade
    } else if (normName.includes(cleanQ) || normCity.includes(cleanQ)) {
      score = 4; // Substring do nome/cidade
    } else if (cleanQ === normState) {
      score = 5; // Estado (UF)
    }

    if (score < 90) {
      scored.push({ score, item });
    }
  }

  // Ordena por score e tipo (hubs primeiro, depois aeroportos, depois rodoviárias)
  scored.sort((a, b) => {
    if (a.score !== b.score) return a.score - b.score;
    const typeOrder = { hub: 1, airport: 2, bus_station: 3 };
    return typeOrder[a.item.type] - typeOrder[b.item.type];
  });

  return scored.slice(0, limit).map((s) => s.item);
}

/**
 * Busca por código exato (ex: SAO, CGH, RIO, SDU).
 */
export function getLocationByCode(code: string): LocationSuggestion | undefined {
  if (!code) return undefined;
  const upper = code.trim().toUpperCase();
  return CANONICAL_LOCATIONS.find((l) => l.code.toUpperCase() === upper);
}

/**
 * Busca híbrida assíncrona: consulta API do FastAPI com fallback local transparente.
 */
export async function fetchLocationSuggestions(
  query: string,
  limit: number = 12
): Promise<LocationSuggestion[]> {
  const localResults = searchLocalLocations(query, limit);

  try {
    const apiBase =
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 1200);

    const resp = await fetch(
      `${apiBase}/api/v1/locations/autocomplete?query=${encodeURIComponent(
        query.trim()
      )}&limit=${limit}`,
      {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      }
    );

    clearTimeout(timeoutId);

    if (resp.ok) {
      const serverResults: LocationSuggestion[] = await resp.json();
      if (Array.isArray(serverResults) && serverResults.length > 0) {
        // Mescla garantindo deduplicação por código ou id
        const seen = new Set<string>();
        const merged: LocationSuggestion[] = [];

        for (const item of serverResults) {
          const key = `${item.type}-${item.code}`;
          if (!seen.has(key)) {
            seen.add(key);
            merged.push(item);
          }
        }

        for (const item of localResults) {
          const key = `${item.type}-${item.code}`;
          if (!seen.has(key)) {
            seen.add(key);
            merged.push(item);
          }
        }

        return merged.slice(0, limit);
      }
    }
  } catch {
    // Em caso de offline ou timeout, retorna os resultados locais imediatamente
  }

  return localResults;
}
