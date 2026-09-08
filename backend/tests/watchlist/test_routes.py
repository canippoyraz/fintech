"""Tests for the /api/watchlist endpoints."""

import pytest

from app.db import DEFAULT_WATCHLIST


class TestGetWatchlist:
    def test_returns_the_seeded_tickers_in_order(self, api):
        body = api.get("/api/watchlist").json()

        assert [item["ticker"] for item in body] == list(DEFAULT_WATCHLIST)

    def test_includes_live_prices(self, api):
        body = api.get("/api/watchlist").json()
        aapl = next(item for item in body if item["ticker"] == "AAPL")

        assert aapl["price"] == 190.0
        assert aapl["direction"] in {"up", "down", "flat"}

    def test_is_empty_when_every_ticker_is_removed(self, api):
        for ticker in DEFAULT_WATCHLIST:
            api.delete(f"/api/watchlist/{ticker}")

        assert api.get("/api/watchlist").json() == []


class TestAddTicker:
    def test_adds_a_ticker(self, api):
        response = api.post("/api/watchlist", json={"ticker": "PYPL"})

        assert response.status_code == 201
        assert response.json()["ticker"] == "PYPL"

    def test_appears_in_the_listing(self, api):
        api.post("/api/watchlist", json={"ticker": "PYPL"})

        tickers = [item["ticker"] for item in api.get("/api/watchlist").json()]
        assert "PYPL" in tickers

    def test_starts_streaming_the_ticker(self, api):
        api.post("/api/watchlist", json={"ticker": "PYPL"})

        assert "PYPL" in api.app.state.market_source.get_tickers()

    def test_has_a_price_immediately(self, api):
        assert api.post("/api/watchlist", json={"ticker": "PYPL"}).json()["price"] is not None

    def test_normalizes_case(self, api):
        response = api.post("/api/watchlist", json={"ticker": "pypl"})

        assert response.status_code == 201
        assert response.json()["ticker"] == "PYPL"

    def test_rejects_a_duplicate_as_409(self, api):
        response = api.post("/api/watchlist", json={"ticker": "AAPL"})

        assert response.status_code == 409
        assert "already on the watchlist" in response.json()["detail"]

    def test_a_duplicate_does_not_grow_the_list(self, api):
        before = len(api.get("/api/watchlist").json())
        api.post("/api/watchlist", json={"ticker": "AAPL"})

        assert len(api.get("/api/watchlist").json()) == before

    @pytest.mark.parametrize(
        "payload",
        [{"ticker": ""}, {"ticker": "  "}, {"ticker": "1BAD"}, {"ticker": "WAY-TOO-LONG"}, {}],
    )
    def test_rejects_malformed_requests_as_422(self, api, payload):
        assert api.post("/api/watchlist", json=payload).status_code == 422


class TestRemoveTicker:
    def test_removes_a_ticker(self, api):
        assert api.delete("/api/watchlist/AAPL").status_code == 204

        tickers = [item["ticker"] for item in api.get("/api/watchlist").json()]
        assert "AAPL" not in tickers

    def test_stops_streaming_it(self, api):
        api.delete("/api/watchlist/AAPL")

        assert "AAPL" not in api.app.state.market_source.get_tickers()

    def test_accepts_a_lowercase_ticker(self, api):
        assert api.delete("/api/watchlist/aapl").status_code == 204

    def test_unknown_ticker_is_404(self, api):
        response = api.delete("/api/watchlist/PYPL")

        assert response.status_code == 404
        assert "not on the watchlist" in response.json()["detail"]

    def test_invalid_ticker_is_400(self, api):
        assert api.delete("/api/watchlist/1BAD").status_code == 400

    def test_keeps_streaming_a_ticker_the_user_holds(self, api):
        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
        api.delete("/api/watchlist/AAPL")

        # Unwatched but still held, so it must keep a live price to be valued.
        assert "AAPL" in api.app.state.market_source.get_tickers()

    def test_a_held_position_stays_valued_after_unwatching(self, api):
        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
        api.delete("/api/watchlist/AAPL")

        position = api.get("/api/portfolio").json()["positions"][0]
        assert position["current_price"] == 190.0

    def test_stops_streaming_once_the_position_is_closed(self, api):
        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "sell"})
        api.delete("/api/watchlist/AAPL")

        assert "AAPL" not in api.app.state.market_source.get_tickers()


class TestStartupTracking:
    def test_tracks_held_tickers_that_are_not_watched(self, api, monkeypatch):
        from fastapi.testclient import TestClient

        from app.main import create_app

        api.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
        api.delete("/api/watchlist/AAPL")

        # Restart: AAPL is held but unwatched, so startup must still track it.
        with TestClient(create_app()) as restarted:
            assert "AAPL" in restarted.app.state.market_source.get_tickers()
            assert "AAPL" not in [i["ticker"] for i in restarted.get("/api/watchlist").json()]
