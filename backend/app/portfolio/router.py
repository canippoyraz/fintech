"""Portfolio REST endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..market import PriceCache
from . import service
from .models import PortfolioOut, SnapshotOut, TradeOut, TradeRequest


def create_portfolio_router(price_cache: PriceCache) -> APIRouter:
    """Build the portfolio router bound to a price cache.

    Handlers are sync so FastAPI runs them in its threadpool — SQLite calls
    block, and this keeps them off the event loop serving the SSE stream.
    """
    router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

    @router.get("", response_model=PortfolioOut)
    def get_portfolio() -> PortfolioOut:
        """Cash, positions valued at live prices, and aggregate P&L."""
        return service.get_portfolio(price_cache)

    @router.post("/trade", response_model=TradeOut)
    def execute_trade(request: TradeRequest) -> TradeOut:
        """Execute a market order. Instant fill at the current price."""
        try:
            return service.execute_trade(price_cache, request)
        except service.TradeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/history", response_model=list[SnapshotOut])
    def get_history(
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> list[SnapshotOut]:
        """Portfolio value snapshots over time, oldest first."""
        return service.get_history(limit=limit)

    return router
