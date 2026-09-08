"""Portfolio feature: valuation, trade execution, and value history.

Public API:
    create_portfolio_router - FastAPI router factory for /api/portfolio
    get_portfolio / execute_trade / get_history - Service functions
    record_snapshot         - Store current total value (background task + trades)
    get_position_tickers    - Held tickers, so startup tracks them
    holds_position          - Whether a ticker is held (watchlist removal check)
    TradeError              - Validation failure, surfaced as HTTP 400
"""

from .models import PortfolioOut, PositionOut, SnapshotOut, TradeOut, TradeRequest
from .router import create_portfolio_router
from .service import (
    TradeError,
    execute_trade,
    get_history,
    get_portfolio,
    get_position_tickers,
    holds_position,
    record_snapshot,
)

__all__ = [
    "PortfolioOut",
    "PositionOut",
    "SnapshotOut",
    "TradeError",
    "TradeOut",
    "TradeRequest",
    "create_portfolio_router",
    "execute_trade",
    "get_history",
    "get_portfolio",
    "get_position_tickers",
    "holds_position",
    "record_snapshot",
]
