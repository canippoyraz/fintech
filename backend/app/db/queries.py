"""Shared read helpers over the SQLite store.

Portfolio, trade and chat operations get their own modules as those features
land; this holds the queries the app entrypoint needs today.
"""

from __future__ import annotations

from .connection import get_connection
from .schema import DEFAULT_USER_ID


def get_watchlist_tickers(user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Tickers on a user's watchlist, oldest first.

    Ties on added_at break by insertion order (rowid) rather than alphabetically,
    so the seeded default watchlist keeps its curated order — every seed row
    shares one timestamp.

    Used at startup to tell the market data source what to track.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
            (user_id,),
        ).fetchall()
    return [row["ticker"] for row in rows]


def get_cash_balance(user_id: str = DEFAULT_USER_ID) -> float:
    """Current cash balance. Raises KeyError if the profile is missing."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        ).fetchone()

    if row is None:
        raise KeyError(f"No user profile for {user_id!r}")
    return float(row["cash_balance"])
