"""FastAPI application entrypoint.

Serves the REST API, the SSE price stream, and (once built) the static Next.js
export — all on one port, single origin, so the frontend needs no CORS setup.

Run locally:
    uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from .db import ensure_initialized, get_db_path, get_watchlist_tickers
from .market import PriceCache, create_market_data_source, create_stream_router
from .portfolio import create_portfolio_router, get_position_tickers, record_snapshot
from .watchlist import create_watchlist_router

logger = logging.getLogger(__name__)

# Where the built frontend lands in the container image. Absent during backend
# development, in which case static serving is simply not mounted.
STATIC_DIR = Path(os.environ.get("FINTECH_STATIC_DIR", "static"))

# Seconds between automatic portfolio value snapshots, for the P&L chart.
SNAPSHOT_INTERVAL = float(os.environ.get("FINTECH_SNAPSHOT_INTERVAL", "30"))


def _startup_tickers() -> list[str]:
    """Tickers the feed should track: everything watched, plus everything held.

    A holding must keep streaming even once unwatched, or it can't be valued.
    """
    watched = get_watchlist_tickers()
    held = [t for t in get_position_tickers() if t not in watched]
    return watched + held


async def _snapshot_loop(app: FastAPI, interval: float) -> None:
    """Record portfolio value periodically so the P&L chart has history."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(record_snapshot, app.state.price_cache)
        except Exception:
            # A failed snapshot must never kill the loop.
            logger.exception("Portfolio snapshot failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize the database and run the market data feed for the app's lifetime."""
    ensure_initialized()

    tickers = _startup_tickers()
    cache: PriceCache = app.state.price_cache
    source = create_market_data_source(cache)
    await source.start(tickers)
    app.state.market_source = source
    logger.info("Market data started for %d ticker(s)", len(tickers))

    # An opening data point, so the chart isn't empty until the first interval.
    await asyncio.to_thread(record_snapshot, cache)
    snapshots = asyncio.create_task(_snapshot_loop(app, SNAPSHOT_INTERVAL), name="snapshot-loop")
    app.state.snapshot_task = snapshots

    try:
        yield
    finally:
        snapshots.cancel()
        try:
            await snapshots
        except asyncio.CancelledError:
            pass
        await source.stop()
        logger.info("Market data stopped")


def create_system_router(app: FastAPI) -> APIRouter:
    """Health and diagnostics endpoints."""
    router = APIRouter(prefix="/api", tags=["system"])

    @router.get("/health")
    async def health() -> dict[str, object]:
        """Health check for Docker and deployment probes."""
        cache: PriceCache = app.state.price_cache
        source = getattr(app.state, "market_source", None)
        return {
            "status": "ok",
            "database": str(get_db_path()),
            "market_source": type(source).__name__ if source else None,
            "tickers_tracked": len(source.get_tickers()) if source else 0,
            "prices_cached": len(cache),
        }

    return router


def create_app() -> FastAPI:
    """Build the FastAPI application.

    The PriceCache is created here rather than in the lifespan because routers
    capture it at mount time, which happens before startup runs.
    """
    app = FastAPI(
        title="FinTech",
        description="AI Trading Workstation",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.price_cache = PriceCache()
    app.state.market_source = None
    app.state.snapshot_task = None

    app.include_router(create_system_router(app))
    app.include_router(create_stream_router(app.state.price_cache))
    app.include_router(create_portfolio_router(app.state.price_cache))
    app.include_router(create_watchlist_router(app.state.price_cache))

    # Mounted last so /api routes always win. html=True serves index.html for
    # unknown paths, which client-side routing needs.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
        logger.info("Serving static frontend from %s", STATIC_DIR.resolve())
    else:
        logger.info("No static directory at %s — API only", STATIC_DIR)

    return app


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

app = create_app()
