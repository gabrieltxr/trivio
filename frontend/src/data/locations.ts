import { LocationSuggestion } from "@/types";

/**
 * Localidades vêm só da API (`/api/v1/locations/autocomplete`), que lê os hubs e terminais
 * cadastrados no banco. Não há lista fixa no frontend: uma cidade oferecida aqui e ausente
 * no banco seria pesquisada como outra (ex.: "SSA" virava São Paulo).
 *
 * Códigos enviados na busca:
 * - `HUB:SAO`  -> cidade inteira (aeroportos e rodoviárias);
 * - `CGH`      -> só aquele terminal. "CWB" sozinho é o aeroporto, não Curitiba.
 */
export const HUB_CODE_PREFIX = "HUB:";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Toda sugestão já recebida, por código: permite mostrar o nome de um código escolhido. */
const knownLocations = new Map<string, LocationSuggestion>();

/**
 * Normaliza strings para busca insensível a acentuação e caixa.
 */
export function normalizeSearchString(text: string): string {
  if (!text) return "";
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
}

/** Código curto para exibição: `HUB:SAO` -> `SAO`. */
export function displayCode(code: string): string {
  if (!code) return "";
  return code.toUpperCase().startsWith(HUB_CODE_PREFIX)
    ? code.slice(HUB_CODE_PREFIX.length)
    : code;
}

/** Sugestão já conhecida para um código (exato). */
export function getKnownLocation(code: string): LocationSuggestion | undefined {
  if (!code) return undefined;
  return knownLocations.get(code.trim().toUpperCase());
}

/**
 * Consulta o autocomplete do backend. Sem `query`, devolve os hubs e terminais principais.
 * Em caso de falha de rede devolve lista vazia: sem API, a busca também não funcionaria.
 */
export async function fetchLocationSuggestions(
  query: string,
  limit: number = 12
): Promise<LocationSuggestion[]> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 3000);
  try {
    const params = new URLSearchParams({ limit: String(limit) });
    if (query.trim()) params.set("query", query.trim());
    const resp = await fetch(`${API_BASE}/api/v1/locations/autocomplete?${params}`, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) return [];
    const results: unknown = await resp.json();
    if (!Array.isArray(results)) return [];
    const suggestions = results as LocationSuggestion[];
    for (const item of suggestions) {
      knownLocations.set(item.code.toUpperCase(), item);
    }
    return suggestions;
  } catch {
    return [];
  } finally {
    clearTimeout(timeoutId);
  }
}

/** Resolve um código (ex.: `HUB:SAO`, `CGH`) para a sugestão correspondente. */
export async function resolveLocationCode(
  code: string
): Promise<LocationSuggestion | undefined> {
  const known = getKnownLocation(code);
  if (known) return known;
  const results = await fetchLocationSuggestions(code, 20);
  const upper = code.trim().toUpperCase();
  return results.find((item) => item.code.toUpperCase() === upper);
}
