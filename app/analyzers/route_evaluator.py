"""Motor de Avaliação de Rotas, Prós, Contras e Contratempos (Trivio Core).

Analisa heurísticas intermodais para gerar transparência ao viajante:
1. Prós: Vantagens de preço, conforto, bagagem inclusa, embarque central e comodidades.
2. Contras: Desvantagens como horário da madrugada, conexões, paradas excessivas e taxas extras.
3. Contratempos Potenciais: Riscos operacionais reais (conexões curtas, trechos de serra,
   troca de aeroporto e ausência de proteção em conexões virtuais).
4. Diagnóstico de Conexões Aéreas: Cálculo detalhado de tempo de solo (layover),
   tempo de caminhada, fechamento de portão e margem de segurança por hub.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("trivio.analyzers.route_evaluator")


# Grandes hubs brasileiros de alta complexidade e tráfego intenso
MAJOR_BRAZILIAN_HUBS = {"GRU", "GIG", "BSB", "CNF", "VCP", "CGH"}


@dataclass
class ConnectionDiagnostic:
    """Diagnóstico detalhado de uma conexão de voo específica."""

    airport: str
    layover_minutes: int
    is_major_hub: bool
    severity: str                       # 'CRITICAL', 'WARNING', 'SAFE', 'LONG'
    deplaning_est_min: int              # Tempo estimado de desembarque (remoto/finger)
    terminal_walk_est_min: int          # Tempo de caminhada até o portão de conexão
    gate_closing_buffer_min: int        # Fechamento do portão antes da decolagem (15 min padrão)
    safety_margin_min: int              # Margem líquida restante para imprevistos
    penalty_points: int                 # Penalidade para o Trivio Score
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "airport": self.airport,
            "layover_minutes": self.layover_minutes,
            "is_major_hub": self.is_major_hub,
            "severity": self.severity,
            "timeline": {
                "deplaning_est_min": self.deplaning_est_min,
                "terminal_walk_est_min": self.terminal_walk_est_min,
                "gate_closing_buffer_min": self.gate_closing_buffer_min,
                "safety_margin_min": self.safety_margin_min,
            },
            "penalty_points": self.penalty_points,
            "recommendation": self.recommendation,
        }


@dataclass
class RouteEvaluation:
    """Resultado estruturado da avaliação qualitativa da rota."""

    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    potential_setbacks: List[str] = field(default_factory=list)
    connection_diagnostics: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "pros": list(self.pros),
            "cons": list(self.cons),
            "potential_setbacks": list(self.potential_setbacks),
        }
        if self.connection_diagnostics:
            result["connection_diagnostics"] = self.connection_diagnostics
        return result


# ==============================================================================
# 1. Base de Conhecimento Geográfica e Rodovias Críticas do Brasil
# ==============================================================================

HIGHWAY_SETBACKS_KNOWLEDGE_BASE = [
    {
        "keywords": [("sao-paulo", "rio-de-janeiro"), ("tiete", "novo-rio"), ("barra-funda", "novo-rio")],
        "modal": "ROAD",
        "setback": "Trecho da Rodovia Presidente Dutra (BR-116) sujeito a congestionamentos na saída de SP e chegada ao RJ.",
    },
    {
        "keywords": [("sao-paulo", "curitiba"), ("curitiba", "sao-paulo")],
        "modal": "ROAD",
        "setback": "Trecho de serra (Serra do Cafezal / BR-116) com histórico de lentidão em dias chuvosos ou sob neblina.",
    },
    {
        "keywords": [("porto-alegre", "pelotas"), ("pelotas", "porto-alegre"), ("porto-alegre", "rio-grande"), ("rio-grande", "porto-alegre")],
        "modal": "ROAD",
        "setback": "BR-116 Sul sujeita a trechos com tráfego intenso de cargas pesadas em direção ao Porto de Rio Grande.",
    },
    {
        "keywords": [("campinas", "sao-paulo"), ("sao-paulo", "campinas")],
        "modal": "ROAD",
        "setback": "Tráfego intenso no Sistema Anhanguera-Bandeirantes nos horários de pico comercial (07h-09h e 17h-19h).",
    },
]


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Extrai campo de dataclass, Pydantic, ORM ou dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_brazil_time(dt: Optional[datetime]) -> Optional[datetime]:
    """Converte datetimes com fuso horário (ex: UTC) para o fuso padrão brasileiro (America/Sao_Paulo)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo
            return dt.astimezone(ZoneInfo("America/Sao_Paulo"))
        except Exception:
            from datetime import timedelta, timezone
            return dt.astimezone(timezone(timedelta(hours=-3)))
    return dt


def _is_madrugada(dt: Optional[datetime]) -> bool:
    """Verifica se o horário está na janela crítica da madrugada (01:00 às 04:59) no fuso brasileiro."""
    local_dt = _to_brazil_time(dt)
    if not local_dt:
        return False
    return time(1, 0) <= local_dt.time() <= time(4, 59)


def _is_noturno(dt: Optional[datetime]) -> bool:
    """Verifica se o horário é noturno adequado para pernoite/dormir (partida entre 20h e 23h59) no fuso brasileiro."""
    local_dt = _to_brazil_time(dt)
    if not local_dt:
        return False
    return local_dt.hour >= 20


# ==============================================================================
# 2. Diagnóstico Especializado de Conexões de Voo (MCT Engine)
# ==============================================================================

def diagnose_connections(
    layovers: List[Dict[str, Any]],
) -> Tuple[List[ConnectionDiagnostic], Optional[Dict[str, Any]]]:
    """Analisa detalhadamente cada conexão de voo e calcula atritos e margens."""
    if not layovers:
        return [], None

    diagnostics: List[ConnectionDiagnostic] = []
    worst_severity = "SAFE"
    total_penalty = 0
    tightest_min = None

    severity_rank = {"SAFE": 0, "LONG": 1, "WARNING": 2, "CRITICAL": 3}

    for layover in layovers:
        airport = str(layover.get("airport", "")).strip().upper()
        duration = int(layover.get("duration_minutes", 60) or 60)
        is_major = airport in MAJOR_BRAZILIAN_HUBS

        if tightest_min is None or duration < tightest_min:
            tightest_min = duration

        # Parâmetros operacionais realistas de aeroportos brasileiros
        deplaning_est = 15 if is_major else 10
        walk_est = 15 if is_major else 5
        gate_closing_buffer = 15
        consumed = deplaning_est + walk_est + gate_closing_buffer
        safety_margin = duration - consumed

        # Critérios de Risco
        if (is_major and duration < 50) or (not is_major and duration < 40):
            severity = "CRITICAL"
            penalty = 100
            recommendation = (
                f"Conexão crítica de apenas {duration} min em {airport}: margem de segurança de "
                f"{safety_margin} min. Alto risco de perda do próximo voo e extravio de bagagem despachada."
            )
        elif (is_major and 50 <= duration < 65) or (not is_major and 40 <= duration < 50):
            severity = "WARNING"
            penalty = 50
            recommendation = (
                f"Conexão curta ({duration} min) em {airport}: tempo reduzido para deslocamento. "
                "Recomenda-se desembarque ágil e evitar assentos no fundo do avião."
            )
        elif duration > 300:
            severity = "LONG"
            penalty = 20
            recommendation = (
                f"Conexão excessivamente longa ({duration // 60}h{duration % 60:02d}m) em {airport}: "
                "tempo ocioso no terminal sem benefício de hotel."
            )
        else:
            severity = "SAFE"
            penalty = 0
            recommendation = f"Conexão confortável ({duration} min) em {airport} com margem segura."

        total_penalty += penalty
        if severity_rank.get(severity, 0) > severity_rank.get(worst_severity, 0):
            worst_severity = severity

        diag = ConnectionDiagnostic(
            airport=airport,
            layover_minutes=duration,
            is_major_hub=is_major,
            severity=severity,
            deplaning_est_min=deplaning_est,
            terminal_walk_est_min=walk_est,
            gate_closing_buffer_min=gate_closing_buffer,
            safety_margin_min=safety_margin,
            penalty_points=penalty,
            recommendation=recommendation,
        )
        diagnostics.append(diag)

    summary = {
        "has_connection": True,
        "connections_count": len(diagnostics),
        "tightest_layover_minutes": tightest_min,
        "worst_severity": worst_severity,
        "total_connection_penalty_points": total_penalty,
        "is_tight": worst_severity in ("CRITICAL", "WARNING"),
        "connections": [d.to_dict() for d in diagnostics],
    }

    return diagnostics, summary


# ==============================================================================
# 3. Classe Principal RouteEvaluator
# ==============================================================================

class RouteEvaluator:
    """Avaliador de conveniência, atrito e riscos para cotações intermodais."""

    @classmethod
    def evaluate(cls, quote: Any) -> RouteEvaluation:
        """Gera prós, contras e contratempos para qualquer cotação."""
        pros: List[str] = []
        cons: List[str] = []
        setbacks: List[str] = []

        # Extração de campos normalizados
        modal = str(_get_val(quote, "modal", "")).strip().upper()
        carrier_name = str(_get_val(quote, "carrier_name", ""))
        service_class = str(_get_val(quote, "service_class", "")).strip().upper()
        dep_time: Optional[datetime] = _get_val(quote, "departure_time")
        arr_time: Optional[datetime] = _get_val(quote, "arrival_time")
        duration_min = int(_get_val(quote, "duration_minutes", 0) or 0)
        base_price = float(_get_val(quote, "base_price", 0.0) or 0.0)
        taxes_price = float(_get_val(quote, "taxes_price", 0.0) or 0.0)
        baggage_fee = float(_get_val(quote, "baggage_fee_estimate", 0.0) or 0.0)
        transfer_cost = float(_get_val(quote, "transfer_cost_estimate", 0.0) or 0.0)
        origin_id = str(_get_val(quote, "origin_external_id", "") or _get_val(quote, "origin_terminal_id", "")).lower()
        dest_id = str(_get_val(quote, "destination_external_id", "") or _get_val(quote, "destination_terminal_id", "")).lower()

        meta: Dict[str, Any] = _get_val(quote, "route_metadata", {}) or {}
        if not isinstance(meta, dict):
            meta = {}

        is_direct = meta.get("is_direct", True)
        stops_count = int(meta.get("stops_count", 0) or 0)
        layovers = meta.get("layover_airports") or meta.get("intermediate_stops") or []
        layover_details = meta.get("layover_details", [])
        is_virtual = meta.get("is_virtual_interline", False)
        amenities = [str(a).lower() for a in meta.get("amenities", [])]
        is_code_share = meta.get("is_code_share", False)
        savings_brl = float(meta.get("savings_brl", 0.0) or 0.0)
        cheapest_provider = meta.get("cheapest_provider", "")
        baggage_included = meta.get("baggage_included", False)

        # ----------------------------------------------------------------------
        # Avaliação Especializada de Conexões Aéreas (MCT)
        # ----------------------------------------------------------------------
        connection_diagnostics_summary = None
        if modal == "AIR" and (layover_details or stops_count > 0):
            # Se layover_details não estiver detalhado, cria mock a partir dos aeroportos
            if not layover_details and layovers:
                layover_details = [{"airport": str(apt), "duration_minutes": 60} for apt in layovers]

            conn_diags, connection_diagnostics_summary = diagnose_connections(layover_details)
            for diag in conn_diags:
                if diag.severity == "CRITICAL":
                    setbacks.append(diag.recommendation)
                elif diag.severity in ("WARNING", "LONG"):
                    cons.append(diag.recommendation)

        # ----------------------------------------------------------------------
        # Avaliação de PRÓS
        # ----------------------------------------------------------------------

        # 1. Bagagem Despachada
        if modal == "ROAD" or baggage_fee == 0.0 or baggage_included:
            if modal == "ROAD":
                pros.append("Bagagem despachada de até 30kg inclusa gratuitamente no bagageiro (ANTT)")
            else:
                pros.append("Bagagem despachada de 23kg inclusa gratuitamente na tarifa")

        # 2. Conforto & Pernoite
        if "LEITO" in service_class:
            if _is_noturno(dep_time) and arr_time and arr_time.hour in (5, 6, 7, 8):
                pros.append("Poltrona Leito com pernoite produtivo (economia de 1 diária de hospedagem)")
            else:
                pros.append(f"Poltrona {service_class.replace('_', ' ').title()} com alto nível de reclinação e conforto")
        elif service_class == "BUSINESS":
            pros.append("Classe Executiva com assento prioritário, serviço de bordo premium e sala VIP")

        # 3. Viagem Direta
        if is_direct and stops_count == 0:
            if modal == "AIR":
                pros.append("Voo direto sem escalas ou conexões (tempo mínimo de voo)")
            else:
                pros.append("Viagem expressa direta sem paradas intermediárias")

        # 4. Embarque Central / Acesso Urbano Econômico
        if transfer_cost > 0 and transfer_cost <= 30.0:
            pros.append(f"Embarque em terminal de fácil acesso central (transfer estimado em R$ {transfer_cost:.2f})")

        # 5. Economia Code-Share
        if is_code_share and savings_brl > 0:
            pros.append(
                f"Economia de R$ {savings_brl:.2f} identificada comprando via {cheapest_provider.title()}"
            )

        # 6. Comodidades (Amenities)
        if any("wifi" in a for a in amenities):
            pros.append("Conexão Wi-Fi a bordo disponível")
        if any("usb" in a or "tomada" in a for a in amenities):
            pros.append("Tomadas de energia ou portas USB individuais nos assentos")

        # ----------------------------------------------------------------------
        # Avaliação de CONTRAS
        # ----------------------------------------------------------------------

        # 1. Horário da Madrugada (01:00 às 04:59)
        dep_local = _to_brazil_time(dep_time)
        arr_local = _to_brazil_time(arr_time)
        if _is_madrugada(dep_local):
            dep_time_str = dep_local.strftime("%H:%M") if dep_local else ""
            cons.append(f"Embarque em janela crítica da madrugada ({dep_time_str}) - requer atenção ao deslocamento noturno")
        if _is_madrugada(arr_local):
            arr_time_str = arr_local.strftime("%H:%M") if arr_local else ""
            cons.append(f"Chegada de madrugada ({arr_time_str}) - opções reduzidas de transporte coletivo e terminal com baixo movimento")

        # 2. Taxa de Bagagem Extra no Aéreo
        if modal == "AIR" and not baggage_included and baggage_fee > 0:
            cons.append(
                f"Tarifa básica sem mala despachada (adiciona R$ {baggage_fee:.2f} caso precise despachar bagagem de 23kg)"
            )

        # 3. Conexões / Layovers no Rodoviário
        if modal == "ROAD" and stops_count > 0:
            cons.append(f"Linha paradora com {stops_count} paradas intermediárias ao longo da viagem")

        # 4. Viagem Rodoviária Longa em Classe Básica
        if modal == "ROAD" and duration_min >= 420 and "CONVENCIONAL" in service_class:
            cons.append(f"Viagem longa ({duration_min // 60}h) em poltrona convencional (maior atrito e desgaste físico)")

        # 5. Terminal Afastado / Transfer Elevado
        if transfer_cost >= 50.0:
            cons.append(
                f"Terminal distante do centro da cidade (custo elevado de transfer estimado em R$ {transfer_cost:.2f})"
            )

        # 6. Restrição de Bagageiro Superior em Aeronaves Comerciais Turboélice (ex: ATR-72)
        aircraft_name = str(meta.get("aircraft", "")).strip()
        aircraft_upper = aircraft_name.upper()
        is_turboprop = any(k in aircraft_upper for k in ("ATR", "AT7", "AT4", "CARAVAN", "C208", "EMB-120"))
        if modal == "AIR" and is_turboprop:
            display_aircraft = aircraft_name or "ATR-72"
            cons.append(
                f"Aeronave comercial turboélice ({display_aircraft}): compartimento de bagagem de mão reduzido. "
                "Malas de bordo de 10kg padrão não cabem no bagageiro de teto e são despachadas na escada do avião."
            )

        # ----------------------------------------------------------------------
        # Avaliação de CONTRATEMPOS POTENCIAIS (Riscos de Rota)
        # ----------------------------------------------------------------------

        # 1. Conexão Virtual Intermodal Independente
        if is_virtual:
            setbacks.append(
                "Conexão intermodal independente (ônibus + voo): bilhetes emitidos separadamente "
                "sem garantia de reacomodação em caso de atraso na primeira perna."
            )

        # 2. Troca de Aeroporto na Conexão (ex: CGH -> GRU ou SDU -> GIG)
        if modal == "AIR" and layovers:
            has_sp_swap = any("CGH" in str(s) for s in layovers) and any("GRU" in str(s) for s in layovers)
            has_rj_swap = any("SDU" in str(s) for s in layovers) and any("GIG" in str(s) for s in layovers)
            if has_sp_swap:
                setbacks.append(
                    "Atenção crítica: Troca de aeroporto na conexão em São Paulo (CGH ↔ GRU). "
                    "Exige deslocamento rodoviário sujeito a trânsito pesado na Marginal Tietê."
                )
            if has_rj_swap:
                setbacks.append(
                    "Atenção crítica: Troca de aeroporto na conexão no Rio de Janeiro (SDU ↔ GIG) pela Linha Vermelha."
                )

        # 3. Sensibilidade Climática e Teto Operacional dos Aeroportos (Aéreo)
        if modal == "AIR":
            origin_upper = origin_id.upper()
            dest_upper = dest_id.upper()
            all_airports = {origin_upper, dest_upper}
            for lay in layovers:
                if isinstance(lay, str):
                    all_airports.add(lay.upper())
                elif isinstance(lay, dict) and "airport" in lay:
                    all_airports.add(str(lay["airport"]).upper())

            # A. Curitiba (CWB) - Nevoeiro matinal (05h00 às 09h00)
            if "CWB" in all_airports:
                dep_local = _to_brazil_time(dep_time)
                arr_local = _to_brazil_time(arr_time)
                dep_hour = dep_local.hour if dep_local else 12
                arr_hour = arr_local.hour if arr_local else 12
                cwb_is_morning = (
                    ("CWB" in origin_upper and 5 <= dep_hour <= 9)
                    or ("CWB" in dest_upper and 5 <= arr_hour <= 9)
                )
                if cwb_is_morning:
                    setbacks.append(
                        "Aeroporto de Curitiba (CWB) com histórico recorrente de restrição operacional "
                        "por nevoeiro matinal (05h-09h); voos sujeitos a espera em voo ou atrasos em dias de baixa visibilidade."
                    )

            # B. Santos Dumont (SDU) - Pista curta e teto visual rigoroso (desvio para Galeão GIG)
            if "SDU" in all_airports:
                setbacks.append(
                    "Santos Dumont (SDU) opera com teto visual rigoroso e pista curta; voos sujeitos a "
                    "arremetidas ou desvios não planejados para o Galeão (GIG) em dias de chuva ou baixa nebulosidade."
                )

            # C. Devolução de Bagagem de Mão na Pista em Conexões de Turboélices
            if is_turboprop and stops_count > 0:
                display_aircraft = aircraft_name or "ATR-72"
                setbacks.append(
                    f"Aeronave comercial turboélice ({display_aircraft}) em voo com conexão: a devolução da mala de mão "
                    "na escada da pista adiciona 10 a 15 min de espera antes do acesso ao terminal, comprometendo a conexão."
                )

        # 4. Rodovias com Histórico de Retenção
        if modal == "ROAD":
            for rule in HIGHWAY_SETBACKS_KNOWLEDGE_BASE:
                for orig_kw, dest_kw in rule["keywords"]:
                    if (orig_kw in origin_id and dest_kw in dest_id) or (orig_kw in dest_id and dest_kw in origin_id):
                        setbacks.append(rule["setback"])
                        break

        # 5. Preço suspeito: a fonte devolveu o mesmo valor para vários voos
        if meta.get("price_suspect"):
            n_flights = int(meta.get("price_suspect_flights") or 0)
            setbacks.append(
                f"Preço suspeito: a fonte devolveu o mesmo valor para {n_flights} voos desta data. "
                "Confirme o preço no site da companhia antes de decidir."
            )

        # Remove duplicatas preservando a ordem
        unique_pros = list(dict.fromkeys(pros))
        unique_cons = list(dict.fromkeys(cons))
        unique_setbacks = list(dict.fromkeys(setbacks))

        return RouteEvaluation(
            pros=unique_pros,
            cons=unique_cons,
            potential_setbacks=unique_setbacks,
            connection_diagnostics=connection_diagnostics_summary,
        )

    @classmethod
    def enrich_quote(cls, quote: Any) -> Any:
        """Avalia a rota e injeta pros, cons, potential_setbacks e connection_diagnostics no route_metadata."""
        eval_result = cls.evaluate(quote)

        update_dict = eval_result.to_dict()

        if isinstance(quote, dict):
            if "route_metadata" not in quote or not isinstance(quote["route_metadata"], dict):
                quote["route_metadata"] = {}
            quote["route_metadata"].update(update_dict)
        elif hasattr(quote, "route_metadata"):
            meta = getattr(quote, "route_metadata")
            if meta is None or not isinstance(meta, dict):
                setattr(quote, "route_metadata", {})
                meta = getattr(quote, "route_metadata")
            meta.update(update_dict)

        return quote


def evaluate_route(quote: Any) -> RouteEvaluation:
    """Função utilitária para avaliação direta de cotação."""
    return RouteEvaluator.evaluate(quote)


def enrich_quote_with_evaluation(quote: Any) -> Any:
    """Função utilitária para enriquecer cotação com prós, contras e contratempos."""
    return RouteEvaluator.enrich_quote(quote)

