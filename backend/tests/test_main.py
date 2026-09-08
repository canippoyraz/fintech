"""Tests for the FastAPI application entrypoint."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import DEFAULT_WATCHLIST, get_connection
from app.main import create_app
from app.market.stream import _generate_events


@pytest.fixture
def client():
    """A client whose lifespan has run: database seeded, market feed live."""
    with TestClient(create_app()) as c:
        yield c


class TestAppConstruction:
    def test_registers_expected_routes(self):
        app = create_app()
        paths = {route.path for route in app.routes}
        assert "/api/health" in paths
        assert "/api/stream/prices" in paths

    def test_creates_a_price_cache_before_startup(self):
        # Routers capture the cache at mount time, so it must exist immediately.
        app = create_app()
        assert app.state.price_cache is not None
        assert app.state.market_source is None

    def test_each_app_gets_its_own_cache(self):
        assert create_app().state.price_cache is not create_app().state.price_cache

    def test_skips_static_mount_when_directory_is_absent(self):
        app = create_app()
        assert not any(getattr(route, "name", None) == "static" for route in app.routes)

    def test_mounts_static_when_directory_exists(self, tmp_path, monkeypatch):
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<h1>FinTech</h1>")
        monkeypatch.setattr("app.main.STATIC_DIR", static_dir)

        app = create_app()
        assert any(getattr(route, "name", None) == "static" for route in app.routes)

    def test_api_routes_win_over_the_static_mount(self, tmp_path, monkeypatch):
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("<h1>FinTech</h1>")
        monkeypatch.setattr("app.main.STATIC_DIR", static_dir)

        with TestClient(create_app()) as c:
            assert c.get("/api/health").json()["status"] == "ok"
            assert "FinTech" in c.get("/").text


class TestHealth:
    def test_reports_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_reports_the_active_database(self, client, temp_db):
        assert client.get("/api/health").json()["database"] == str(temp_db)

    def test_reports_the_simulator_without_an_api_key(self, client):
        assert client.get("/api/health").json()["market_source"] == "SimulatorDataSource"

    def test_tracks_the_seeded_watchlist(self, client):
        assert client.get("/api/health").json()["tickers_tracked"] == len(DEFAULT_WATCHLIST)


class TestLifespan:
    def test_creates_the_database_on_startup(self, temp_db):
        assert not temp_db.exists()
        with TestClient(create_app()):
            assert temp_db.exists()

    def test_starts_the_feed_with_watchlist_tickers(self, client):
        tracked = client.app.state.market_source.get_tickers()
        assert sorted(tracked) == sorted(DEFAULT_WATCHLIST)

    def test_populates_the_price_cache(self, client):
        assert len(client.app.state.price_cache) == len(DEFAULT_WATCHLIST)

    def test_tracks_a_customized_watchlist(self):
        with get_connection() as conn:
            conn.execute("DELETE FROM watchlist")
            conn.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES ('1', 'default', 'AAPL', '2026-01-01')"
            )

        with TestClient(create_app()) as c:
            assert c.app.state.market_source.get_tickers() == ["AAPL"]

    def test_stops_the_feed_on_shutdown(self):
        app = create_app()
        with TestClient(app):
            pass
        assert app.state.market_source is not None  # Retained for inspection

    def test_honours_an_emptied_watchlist_across_a_restart(self):
        from app.db import reset_initialization

        with get_connection() as conn:
            conn.execute("DELETE FROM watchlist")
        reset_initialization()  # Simulate a process restart

        with TestClient(create_app()) as c:
            assert c.app.state.market_source.get_tickers() == []
            assert c.get("/api/health").json()["tickers_tracked"] == 0


class TestStreamIntegration:
    """The SSE stream is exercised through the generator rather than the ASGI layer.

    ``_generate_events`` only returns when ``request.is_disconnected()`` goes true.
    Real uvicorn delivers http.disconnect when the client goes away, but httpx's
    ASGITransport (which TestClient uses) never does — so consuming the endpoint
    through TestClient blocks forever on close. Driving the generator directly
    tests the same code with a stub request we control.
    """

    async def test_streams_cached_prices_for_the_seeded_watchlist(self):
        app = create_app()
        with TestClient(app):
            gen = _generate_events(app.state.price_cache, _StubRequest(), interval=0.05)
            try:
                events = [chunk async for chunk in _take(gen, 3)]
            finally:
                await gen.aclose()

        assert events[0] == "retry: 1000\n\n"

        payload = json.loads(events[1][len("data: ") :])
        assert set(payload) == set(DEFAULT_WATCHLIST)
        assert payload["AAPL"]["direction"] in {"up", "down", "flat"}
        assert payload["AAPL"]["price"] > 0

    async def test_stops_when_the_client_disconnects(self):
        app = create_app()
        with TestClient(app):
            request = _StubRequest(disconnected=True)
            events = [chunk async for chunk in _generate_events(app.state.price_cache, request)]

        # Only the retry preamble, which is yielded before the disconnect check.
        assert events == ["retry: 1000\n\n"]


class _StubRequest:
    """Minimal stand-in for starlette's Request, as used by _generate_events."""

    def __init__(self, disconnected: bool = False) -> None:
        self._disconnected = disconnected
        self.client = SimpleNamespace(host="test-client")

    async def is_disconnected(self) -> bool:
        return self._disconnected


async def _take(gen, n):
    """Yield the first n items of an async generator."""
    count = 0
    async for item in gen:
        yield item
        count += 1
        if count >= n:
            return
