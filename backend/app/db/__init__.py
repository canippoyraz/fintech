"""SQLite persistence layer for FinTech.

Public API:
    get_connection      - Context manager yielding a committed/rolled-back connection
    ensure_initialized  - Lazily create and seed the database on first use
    init_db             - Create missing tables and seed defaults (idempotent)
    get_db_path         - Resolved SQLite file location
    utc_now_iso/new_id  - Timestamp and primary key helpers
    get_watchlist_tickers / get_cash_balance - Read helpers
"""

from .connection import (
    ensure_initialized,
    get_connection,
    get_db_path,
    init_db,
    new_id,
    reset_initialization,
    utc_now_iso,
)
from .queries import get_cash_balance, get_watchlist_tickers
from .schema import DEFAULT_CASH_BALANCE, DEFAULT_USER_ID, DEFAULT_WATCHLIST

__all__ = [
    "DEFAULT_CASH_BALANCE",
    "DEFAULT_USER_ID",
    "DEFAULT_WATCHLIST",
    "ensure_initialized",
    "get_cash_balance",
    "get_connection",
    "get_db_path",
    "get_watchlist_tickers",
    "init_db",
    "new_id",
    "reset_initialization",
    "utc_now_iso",
]
