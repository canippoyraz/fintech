"""Pytest configuration and fixtures."""

import pytest
from fastapi.testclient import TestClient

from app.db import reset_initialization
from app.main import create_app
from app.market import MarketDataSource, PriceCache
from app.market.seed_prices import SEED_PRICES

# Price used for tickers outside SEED_PRICES, so tests can add arbitrary symbols.
FALLBACK_PRICE = 100.0


@pytest.fixture
def event_loop_policy():
    """Use the default event loop policy for all async tests."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point every test at a throwaway database.

    Autouse so no test can accidentally read or write the real db/fintech.db.
    Yields the path for tests that need to inspect the file directly.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FINTECH_DB_PATH", str(db_path))
    reset_initialization()
    yield db_path
    reset_initialization()


class StaticDataSource(MarketDataSource):
    """A market feed whose prices never move.

    The GBM simulator re-prices every 500ms, which makes assertions on trade
    proceeds and P&L racy. This holds each ticker at a fixed price so tests can
    assert exact figures.
    """

    def __init__(self, price_cache: PriceCache, prices: dict[str, float] | None = None) -> None:
        self._cache = price_cache
        self._prices = dict(prices or SEED_PRICES)
        self._tickers: list[str] = []

    def price_for(self, ticker: str) -> float:
        return self._prices.get(ticker, FALLBACK_PRICE)

    async def start(self, tickers: list[str]) -> None:
        for ticker in tickers:
            await self.add_ticker(ticker)

    async def stop(self) -> None:
        pass

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self._tickers:
            self._tickers.append(ticker)
        self._cache.update(ticker=ticker, price=self.price_for(ticker))

    async def remove_ticker(self, ticker: str) -> None:
        if ticker in self._tickers:
            self._tickers.remove(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)


@pytest.fixture
def static_cache():
    """A price cache pre-loaded with fixed seed prices, for service-level tests."""
    cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        cache.update(ticker=ticker, price=price)
    return cache


@pytest.fixture
def api(monkeypatch):
    """TestClient backed by a non-moving feed, with periodic snapshots disabled.

    Snapshots are pushed out of the way so only the ones a test triggers
    (startup and trades) appear in history.
    """
    monkeypatch.setattr("app.main.create_market_data_source", StaticDataSource)
    monkeypatch.setattr("app.main.SNAPSHOT_INTERVAL", 3600.0)

    with TestClient(create_app()) as client:
        yield client
