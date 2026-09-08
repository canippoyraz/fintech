"""Tests for the /api/portfolio endpoints."""

import pytest

from app.db import DEFAULT_CASH_BALANCE


class TestGetPortfolio:
    def test_returns_the_seeded_state(self, api):
        body = api.get("/api/portfolio").json()

        assert body["cash_balance"] == DEFAULT_CASH_BALANCE
        assert body["total_value"] == DEFAULT_CASH_BALANCE
        assert body["positions"] == []

    def test_includes_a_position_after_buying(self, api):
        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"})

        body = api.get("/api/portfolio").json()
        assert len(body["positions"]) == 1
        assert body["positions"][0]["ticker"] == "AAPL"
        assert body["positions"][0]["current_price"] == 190.0


class TestTrade:
    def test_buys(self, api):
        response = api.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1900.0
        assert body["cash_balance"] == DEFAULT_CASH_BALANCE - 1900.0
        assert body["position"]["quantity"] == 10

    def test_sells(self, api):
        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"})
        response = api.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "sell"}
        )

        assert response.status_code == 200
        assert response.json()["position"] is None
        assert response.json()["cash_balance"] == DEFAULT_CASH_BALANCE

    def test_accepts_a_lowercase_ticker(self, api):
        response = api.post(
            "/api/portfolio/trade", json={"ticker": "aapl", "quantity": 1, "side": "buy"}
        )

        assert response.status_code == 200
        assert response.json()["ticker"] == "AAPL"

    def test_rejects_insufficient_cash_as_400(self, api):
        response = api.post(
            "/api/portfolio/trade", json={"ticker": "NVDA", "quantity": 100, "side": "buy"}
        )

        assert response.status_code == 400
        assert "Insufficient cash" in response.json()["detail"]

    def test_rejects_overselling_as_400(self, api):
        response = api.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "sell"}
        )

        assert response.status_code == 400
        assert "Insufficient shares" in response.json()["detail"]

    def test_rejects_an_untracked_ticker_as_400(self, api):
        response = api.post(
            "/api/portfolio/trade", json={"ticker": "ZZZZ", "quantity": 1, "side": "buy"}
        )

        assert response.status_code == 400
        assert "No market price" in response.json()["detail"]

    @pytest.mark.parametrize(
        "payload",
        [
            {"ticker": "AAPL", "quantity": 0, "side": "buy"},
            {"ticker": "AAPL", "quantity": -5, "side": "buy"},
            {"ticker": "AAPL", "quantity": 1, "side": "hold"},
            {"ticker": "", "quantity": 1, "side": "buy"},
            {"ticker": "1BAD", "quantity": 1, "side": "buy"},
            {"quantity": 1, "side": "buy"},
            {"ticker": "AAPL", "side": "buy"},
        ],
    )
    def test_rejects_malformed_requests_as_422(self, api, payload):
        assert api.post("/api/portfolio/trade", json=payload).status_code == 422

    def test_a_failed_trade_leaves_the_balance_untouched(self, api):
        api.post("/api/portfolio/trade", json={"ticker": "NVDA", "quantity": 100, "side": "buy"})

        assert api.get("/api/portfolio").json()["cash_balance"] == DEFAULT_CASH_BALANCE


class TestHistory:
    def test_startup_records_an_opening_snapshot(self, api):
        history = api.get("/api/portfolio/history").json()

        assert len(history) == 1
        assert history[0]["total_value"] == DEFAULT_CASH_BALANCE

    def test_each_trade_appends_a_snapshot(self, api):
        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})

        assert len(api.get("/api/portfolio/history").json()) == 3  # Startup + two trades

    def test_respects_the_limit(self, api):
        for _ in range(4):
            api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})

        assert len(api.get("/api/portfolio/history?limit=2").json()) == 2

    @pytest.mark.parametrize("limit", [0, -1, 5001, "abc"])
    def test_rejects_an_invalid_limit(self, api, limit):
        assert api.get(f"/api/portfolio/history?limit={limit}").status_code == 422
