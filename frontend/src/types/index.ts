/**
 * TypeScript Types for Trivio Intermodal Travel Intelligence Platform.
 */

export type TravelMode = "AIR" | "ROAD" | "ROAD_AIR" | "HYBRID";

export type TripType = "round_trip" | "one_way" | "intermodal";

export type LocationType = "hub" | "airport" | "bus_station";

export interface LocationSuggestion {
  id: string;
  type: LocationType;
  name: string;
  city: string;
  state: string;
  code: string;
  modal: "ALL" | "AIR" | "ROAD";
  title: string;
  subtitle: string;
  highlight?: string;
}

export interface Hub {
  id: number;
  ibge_code: string;
  name: string;
  state: string;
  latitude: number;
  longitude: number;
}

export interface Terminal {
  id: number;
  hub_id: number;
  name: string;
  modal: "AIR" | "ROAD";
  official_code: string | null;
  latitude: number;
  longitude: number;
  avg_city_center_distance_km: number;
  estimated_transfer_cost_brl: number;
  estimated_transfer_minutes: number;
}

export interface SearchParams {
  origin: string;
  destination: string;
  date: string;
  return_date?: string;
  trip_type: TripType;
  hourly_value: number;
  radius_km: number;
  enable_virtual_interlining: boolean;
}

export interface PriceThermometerData {
  status: "LOW" | "MEDIUM" | "HIGH" | "INSUFFICIENT_DATA";
  percentile: number | null;
  historical_median_brl: number | null;
  p25_brl?: number | null;
  p75_brl?: number | null;
  sample_size?: number;
  difference_percentage?: number | null;
  recommendation: string;
  /** Origem da série: "antt_monitriip" (bilhetes oficiais) ou "coletas_trivio". */
  source?: string;
  /** Janela de referência da série oficial (ex: "2024-09"). */
  reference_period?: string | null;
}

export interface FareQuote {
  id?: number;
  dedup_signature: string;
  origin_external_id: string;
  destination_external_id: string;
  canonical_origin_id?: string | null;
  canonical_destination_id?: string | null;
  modal: TravelMode | string;
  provider_source: string;
  carrier_name: string;
  service_class?: string | null;
  departure_time: string;
  arrival_time: string;
  duration_minutes: number;
  door_to_door_duration_minutes: number;
  base_price: number;
  taxes_price: number;
  baggage_fee_estimate: number;
  transfer_cost_estimate: number;
  total_effective_price: number;
  trivio_score?: number | null;
  route_metadata: {
    is_direct?: boolean;
    stops_count?: number;
    layover_airports?: string[];
    aircraft?: string;
    pros?: string[];
    cons?: string[];
    potential_setbacks?: string[];
    price_thermometer?: PriceThermometerData;
    score_breakdown?: Record<string, unknown>;
    available_sellers?: Array<{
      provider_source: string;
      base_price: number;
      total_effective_price: number;
    }>;
    is_virtual_interline?: boolean;
    transfer_details?: Record<string, unknown>;
    /** Estimativa gerada pelo Trivio sem preço real de mercado (não disponível para compra). */
    is_simulated?: boolean;
    simulation_notice?: string;
    /** Origem do preço: "antt_projection" (ônibus) ou "antt_projection+ignav" (conexão). */
    price_basis?: string;
    booking_url?: string;
    [key: string]: unknown;
  };
}

export interface HackerFareCombination {
  strategy_type: "HYBRID_MODAL" | "SPLIT_CARRIER" | "SAME_CARRIER";
  combined_effective_price: number;
  combined_trivio_score: number;
  combined_door_to_door_minutes: number;
  savings_vs_single_modal_brl: number;
  hack_title: string;
  hack_description: string;
  outbound: FareQuote;
  inbound: FareQuote;
}
