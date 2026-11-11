"""Proteções da busca: datas, parâmetros numéricos, origem do WebSocket e limite por cliente.

Toda request ao Ignav é paga: entrada inválida não pode virar consulta, e um único cliente
(ou um site de terceiros) não pode esgotar o saldo.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.main import app
from app.api.rate_limit import RateLimiter
from app.config import settings

SP_RJ = {"origin": "HUB:SAO", "destination": "HUB:RIO"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _search(client, payload, headers=None):
    events = []
    with client.websocket_connect("/ws/search", headers=headers or {}) as ws:
        ws.send_text(json.dumps({"action": "start_search", **payload}))
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["event"] in ("SEARCH_COMPLETED", "SEARCH_ERROR"):
                return events


@pytest.mark.parametrize(
    "extra, expected",
    [
        ({"date": "2026-10-31"}, "já passou"),                                   # ontem (hoje = 2026-11-01)
        ({"date": "2027-12-01"}, "no máximo"),                                   # além do horizonte
        ({"date": "2026-12-20", "return_date": "2026-12-01"}, "anterior à de ida"),
        ({"date": "2026-12-20", "hourly_value": "abc"}, "Valor da hora inválido"),
        ({"date": "2026-12-20", "hourly_value": "nan"}, "fora da faixa"),
        ({"date": "2026-12-20", "radius_km": -5}, "fora da faixa"),
    ],
)
@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_invalid_search_never_pays_ignav(mock_air, client, extra, expected):
    events = _search(client, {**SP_RJ, **extra})
    assert events[-1]["event"] == "SEARCH_ERROR"
    assert events[-1]["reason"] == "invalid_params"
    assert expected in events[-1]["message"]
    mock_air.assert_not_called()


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_today_is_a_valid_departure(mock_air, client):
    mock_air.return_value = []
    events = _search(client, {**SP_RJ, "date": "2026-11-01"})
    assert events[-1]["event"] == "SEARCH_COMPLETED"


def test_websocket_rejects_foreign_origin(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/search", headers={"origin": "https://site-de-terceiros.example"}) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_websocket_accepts_configured_frontend_origin(mock_air, client):
    mock_air.return_value = []
    events = _search(client, {**SP_RJ, "date": "2026-12-20"}, headers={"origin": "http://localhost:3000"})
    assert events[-1]["event"] == "SEARCH_COMPLETED"


@patch("app.services.air_quotes.AirQuoteService.get_quotes")
def test_search_rate_limit_per_client(mock_air, client, monkeypatch):
    mock_air.return_value = []
    monkeypatch.setattr(settings, "SEARCH_RATE_LIMIT_PER_HOUR", 2)
    payload = {**SP_RJ, "date": "2026-12-20"}
    assert _search(client, payload)[-1]["event"] == "SEARCH_COMPLETED"
    assert _search(client, payload)[-1]["event"] == "SEARCH_COMPLETED"
    blocked = _search(client, payload)[-1]
    assert blocked["event"] == "SEARCH_ERROR" and blocked["reason"] == "rate_limited"
    assert mock_air.call_count == 2


@patch("app.services.air_quotes.AirQuoteService.get_booking_url")
def test_booking_link_rate_limit_per_client(mock_link, client, monkeypatch):
    mock_link.return_value = "https://companhia.example/compra"
    monkeypatch.setattr(settings, "BOOKING_LINK_RATE_LIMIT_PER_HOUR", 1)
    url = "/api/v1/air/booking-link?ignav_id=abcdefgh12&origin=CGH&destination=SDU&departure_date=2026-12-20"
    assert client.get(url, follow_redirects=False).status_code == 302
    assert client.get(url, follow_redirects=False).status_code == 429
    assert mock_link.call_count == 1


def test_rate_limiter_window_slides_and_zero_disables():
    now = [0.0]
    limiter = RateLimiter(lambda: 2, clock=lambda: now[0])
    assert limiter.allow("ip") and limiter.allow("ip")
    assert not limiter.allow("ip")
    assert limiter.allow("outro-ip")
    now[0] = 3600.0
    assert limiter.allow("ip")
    unlimited = RateLimiter(lambda: 0)
    assert all(unlimited.allow("ip") for _ in range(100))


def test_robots_txt_cannot_be_disabled_by_config():
    assert not hasattr(settings, "RESPECT_ROBOTS_TXT")


def test_debug_is_off_by_default():
    from app.config import Settings

    assert Settings(_env_file=None).DEBUG is False


def test_local_frontend_origin_only_in_development():
    from app.config import Settings

    dev = Settings(_env_file=None, ENVIRONMENT="development", CORS_ORIGINS=["https://trivio.com.br"])
    prod = Settings(_env_file=None, ENVIRONMENT="production", CORS_ORIGINS=["https://trivio.com.br"])
    assert "http://localhost:3000" in dev.allowed_origins()
    assert prod.allowed_origins() == ["https://trivio.com.br"]
