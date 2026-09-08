"""Watchlist feature: which tickers the user tracks.

Public API:
    create_watchlist_router - FastAPI router factory for /api/watchlist
    list_watchlist / add_to_watchlist / remove_from_watchlist - Service functions
    DuplicateTickerError    - Already watched, surfaced as HTTP 409
    TickerNotFoundError     - Not watched, surfaced as HTTP 404
"""

from .models import WatchlistAddRequest, WatchlistItemOut
from .router import create_watchlist_router
from .service import (
    DuplicateTickerError,
    TickerNotFoundError,
    add_to_watchlist,
    list_watchlist,
    remove_from_watchlist,
    should_stop_tracking,
)

__all__ = [
    "DuplicateTickerError",
    "TickerNotFoundError",
    "WatchlistAddRequest",
    "WatchlistItemOut",
    "add_to_watchlist",
    "create_watchlist_router",
    "list_watchlist",
    "remove_from_watchlist",
    "should_stop_tracking",
]
