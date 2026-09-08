"""Watchlist REST endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status

from ..market import PriceCache
from ..tickers import normalize_ticker
from . import service
from .models import WatchlistAddRequest, WatchlistItemOut


def create_watchlist_router(price_cache: PriceCache) -> APIRouter:
    """Build the watchlist router bound to a price cache.

    Mutations are async because they await the market data source, which is only
    available on app.state once the lifespan has started it. Blocking SQLite work
    is pushed to a thread so the event loop keeps serving the SSE stream.
    """
    router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

    @router.get("", response_model=list[WatchlistItemOut])
    def get_watchlist() -> list[WatchlistItemOut]:
        """Watched tickers with their latest prices."""
        return service.list_watchlist(price_cache)

    @router.post("", response_model=WatchlistItemOut, status_code=status.HTTP_201_CREATED)
    async def add_ticker(body: WatchlistAddRequest, request: Request) -> WatchlistItemOut:
        """Add a ticker and start streaming it."""
        ticker = body.ticker
        try:
            added_at = await asyncio.to_thread(service.add_to_watchlist, ticker)
        except service.DuplicateTickerError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        await service.track(request.app.state.market_source, ticker)

        update = price_cache.get(ticker)
        return WatchlistItemOut(
            ticker=ticker,
            added_at=added_at,
            price=update.price if update else None,
            previous_price=update.previous_price if update else None,
            change=update.change if update else None,
            change_percent=update.change_percent if update else None,
            direction=update.direction if update else None,
        )

    @router.delete("/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_ticker(ticker: str, request: Request) -> None:
        """Remove a ticker. Held positions keep streaming so they stay valued."""
        try:
            symbol = normalize_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            await asyncio.to_thread(service.remove_from_watchlist, symbol)
        except service.TickerNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

        if await asyncio.to_thread(service.should_stop_tracking, symbol):
            await service.untrack(request.app.state.market_source, symbol)

    return router
