"""Watchlist management.

Watchlist changes drive what the market data feed tracks, so each mutation
touches both the database and the running MarketDataSource.
"""

from __future__ import annotations

import logging

from ..db import DEFAULT_USER_ID, get_connection, new_id, utc_now_iso
from ..market import MarketDataSource, PriceCache
from ..portfolio import holds_position
from .models import WatchlistItemOut

logger = logging.getLogger(__name__)


class DuplicateTickerError(Exception):
    """Ticker is already on the watchlist. Surfaced as a 409."""


class TickerNotFoundError(Exception):
    """Ticker isn't on the watchlist. Surfaced as a 404."""


def list_watchlist(
    price_cache: PriceCache, user_id: str = DEFAULT_USER_ID
) -> list[WatchlistItemOut]:
    """Watched tickers in display order, each with its latest cached price."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
            (user_id,),
        ).fetchall()

    items = []
    for row in rows:
        update = price_cache.get(row["ticker"])
        items.append(
            WatchlistItemOut(
                ticker=row["ticker"],
                added_at=row["added_at"],
                price=update.price if update else None,
                previous_price=update.previous_price if update else None,
                change=update.change if update else None,
                change_percent=update.change_percent if update else None,
                direction=update.direction if update else None,
            )
        )
    return items


def add_to_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> str:
    """Insert a ticker. Returns its added_at timestamp.

    Raises DuplicateTickerError if already watched. Database only — the caller
    starts tracking it on the feed.
    """
    added_at = utc_now_iso()
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        ).fetchone()
        if exists:
            raise DuplicateTickerError(f"{ticker} is already on the watchlist")

        conn.execute(
            "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (new_id(), user_id, ticker, added_at),
        )
    return added_at


def remove_from_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> None:
    """Delete a ticker. Raises TickerNotFoundError if it wasn't watched."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        )
        if cursor.rowcount == 0:
            raise TickerNotFoundError(f"{ticker} is not on the watchlist")


def should_stop_tracking(ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    """Whether the feed can drop a ticker after it leaves the watchlist.

    Holdings still need live prices to be valued, so a ticker the user owns
    keeps streaming even once it's unwatched.
    """
    return not holds_position(ticker, user_id)


async def track(source: MarketDataSource | None, ticker: str) -> None:
    """Start streaming a ticker, if the feed is running."""
    if source is not None:
        await source.add_ticker(ticker)


async def untrack(source: MarketDataSource | None, ticker: str) -> None:
    """Stop streaming a ticker, if the feed is running."""
    if source is not None:
        await source.remove_ticker(ticker)
