"""Portfolio valuation and trade execution.

Money is rounded to cents on every write so repeated trades can't accumulate
float drift in the cash balance. Quantities stay unrounded — fractional shares
are supported.
"""

from __future__ import annotations

import logging
import sqlite3

from ..db import DEFAULT_USER_ID, get_connection, new_id, utc_now_iso
from ..market import PriceCache
from .models import PortfolioOut, PositionOut, SnapshotOut, TradeOut, TradeRequest

logger = logging.getLogger(__name__)

# Tolerance for float comparisons on quantities and cash. Below this, a holding
# is considered fully sold rather than leaving a dust position behind.
EPSILON = 1e-9


class TradeError(Exception):
    """A trade failed validation. Surfaced to the caller as a 400."""


def get_portfolio(price_cache: PriceCache, user_id: str = DEFAULT_USER_ID) -> PortfolioOut:
    """Current cash, holdings, and aggregate P&L at latest known prices."""
    with get_connection() as conn:
        cash = _fetch_cash(conn, user_id)
        rows = _fetch_positions(conn, user_id)

    positions = [_value_position(row, price_cache) for row in rows]
    return _summarize(cash, positions)


def execute_trade(
    price_cache: PriceCache,
    request: TradeRequest,
    user_id: str = DEFAULT_USER_ID,
) -> TradeOut:
    """Execute a market order and record the resulting portfolio snapshot.

    The balance change, position change, trade log entry, and snapshot all share
    one transaction, so a failure part-way leaves nothing behind.

    Raises TradeError if no price is known, cash is insufficient for a buy, or
    the user holds too few shares for a sell.
    """
    price = price_cache.get_price(request.ticker)
    if price is None:
        raise TradeError(
            f"No market price available for {request.ticker}. Add it to your watchlist first."
        )

    with get_connection() as conn:
        cash = _fetch_cash(conn, user_id)
        existing = _fetch_position(conn, user_id, request.ticker)
        held = float(existing["quantity"]) if existing else 0.0
        avg_cost = float(existing["avg_cost"]) if existing else 0.0

        gross = round(request.quantity * price, 2)

        if request.side == "buy":
            if gross > cash + EPSILON:
                raise TradeError(
                    f"Insufficient cash: {request.ticker} costs ${gross:,.2f}, "
                    f"balance is ${cash:,.2f}"
                )
            new_cash = round(cash - gross, 2)
            new_quantity = held + request.quantity
            # Weighted average cost; only buys move it.
            new_avg_cost = round((held * avg_cost + request.quantity * price) / new_quantity, 6)
        else:
            if request.quantity > held + EPSILON:
                raise TradeError(
                    f"Insufficient shares: tried to sell {request.quantity:g} "
                    f"{request.ticker}, holding {held:g}"
                )
            new_cash = round(cash + gross, 2)
            new_quantity = held - request.quantity
            new_avg_cost = avg_cost  # Sells realize P&L; they don't change basis

        _write_cash(conn, user_id, new_cash)

        if new_quantity <= EPSILON:
            _delete_position(conn, user_id, request.ticker)
            resulting = None
        else:
            _upsert_position(conn, user_id, request.ticker, new_quantity, new_avg_cost)
            resulting = _value_position(
                {"ticker": request.ticker, "quantity": new_quantity, "avg_cost": new_avg_cost},
                price_cache,
            )

        trade_id = new_id()
        executed_at = utc_now_iso()
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                trade_id,
                user_id,
                request.ticker,
                request.side,
                request.quantity,
                price,
                executed_at,
            ),
        )

        # PLAN: snapshot immediately after each trade, in the same transaction.
        _insert_snapshot(conn, user_id, _total_value(conn, price_cache, user_id, new_cash))

    logger.info(
        "Trade: %s %g %s @ %.2f (cash now %.2f)",
        request.side,
        request.quantity,
        request.ticker,
        price,
        new_cash,
    )

    return TradeOut(
        id=trade_id,
        ticker=request.ticker,
        side=request.side,
        quantity=request.quantity,
        price=price,
        total=gross,
        executed_at=executed_at,
        cash_balance=new_cash,
        position=resulting,
    )


def get_history(user_id: str = DEFAULT_USER_ID, limit: int = 500) -> list[SnapshotOut]:
    """Portfolio value over time, oldest first.

    The most recent `limit` snapshots are returned, then re-ordered ascending so
    the frontend can plot them directly.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT total_value, recorded_at FROM portfolio_snapshots"
            " WHERE user_id = ? ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()

    snapshots = [
        SnapshotOut(total_value=row["total_value"], recorded_at=row["recorded_at"]) for row in rows
    ]
    snapshots.reverse()
    return snapshots


def record_snapshot(price_cache: PriceCache, user_id: str = DEFAULT_USER_ID) -> float:
    """Store the current total portfolio value. Returns the value recorded."""
    with get_connection() as conn:
        cash = _fetch_cash(conn, user_id)
        total = _total_value(conn, price_cache, user_id, cash)
        _insert_snapshot(conn, user_id, total)
    return total


def get_position_tickers(user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Tickers the user holds. Startup tracks these even if unwatched."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT ticker FROM positions WHERE user_id = ? AND quantity > 0 ORDER BY ticker",
            (user_id,),
        ).fetchall()
    return [row["ticker"] for row in rows]


def holds_position(ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    """Whether the user holds any shares of a ticker."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM positions WHERE user_id = ? AND ticker = ? AND quantity > 0",
            (user_id, ticker),
        ).fetchone()
    return row is not None


# --- internals ---------------------------------------------------------------


def _fetch_cash(conn: sqlite3.Connection, user_id: str) -> float:
    row = conn.execute("SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise TradeError(f"No user profile for {user_id!r}")
    return float(row["cash_balance"])


def _write_cash(conn: sqlite3.Connection, user_id: str, amount: float) -> None:
    conn.execute("UPDATE users_profile SET cash_balance = ? WHERE id = ?", (amount, user_id))


def _fetch_positions(conn: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT ticker, quantity, avg_cost FROM positions"
        " WHERE user_id = ? AND quantity > 0 ORDER BY ticker",
        (user_id,),
    ).fetchall()


def _fetch_position(conn: sqlite3.Connection, user_id: str, ticker: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
        (user_id, ticker),
    ).fetchone()


def _upsert_position(
    conn: sqlite3.Connection, user_id: str, ticker: str, quantity: float, avg_cost: float
) -> None:
    conn.execute(
        "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (user_id, ticker) DO UPDATE SET"
        " quantity = excluded.quantity, avg_cost = excluded.avg_cost,"
        " updated_at = excluded.updated_at",
        (new_id(), user_id, ticker, quantity, avg_cost, utc_now_iso()),
    )


def _delete_position(conn: sqlite3.Connection, user_id: str, ticker: str) -> None:
    conn.execute("DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker))


def _insert_snapshot(conn: sqlite3.Connection, user_id: str, total_value: float) -> None:
    conn.execute(
        "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
        " VALUES (?, ?, ?, ?)",
        (new_id(), user_id, total_value, utc_now_iso()),
    )


def _total_value(
    conn: sqlite3.Connection, price_cache: PriceCache, user_id: str, cash: float
) -> float:
    positions = [_value_position(row, price_cache) for row in _fetch_positions(conn, user_id)]
    return round(cash + sum(p.market_value for p in positions), 2)


def _value_position(row, price_cache: PriceCache) -> PositionOut:
    """Value one holding at the latest known price.

    With no cached price (a ticker the feed has never covered) the position is
    held at cost, so an unknown price reads as zero P&L rather than distorting
    the portfolio total.
    """
    ticker = row["ticker"]
    quantity = float(row["quantity"])
    avg_cost = float(row["avg_cost"])
    cost_basis = round(quantity * avg_cost, 2)

    price = price_cache.get_price(ticker)
    if price is None:
        market_value = cost_basis
        pnl = 0.0
    else:
        market_value = round(quantity * price, 2)
        pnl = round(market_value - cost_basis, 2)

    return PositionOut(
        ticker=ticker,
        quantity=quantity,
        avg_cost=avg_cost,
        current_price=price,
        cost_basis=cost_basis,
        market_value=market_value,
        unrealized_pnl=pnl,
        unrealized_pnl_percent=round(pnl / cost_basis * 100, 4) if cost_basis else 0.0,
    )


def _summarize(cash: float, positions: list[PositionOut]) -> PortfolioOut:
    positions_value = round(sum(p.market_value for p in positions), 2)
    cost_basis = round(sum(p.cost_basis for p in positions), 2)
    pnl = round(positions_value - cost_basis, 2)

    return PortfolioOut(
        cash_balance=round(cash, 2),
        positions=positions,
        positions_value=positions_value,
        total_value=round(cash + positions_value, 2),
        total_cost_basis=cost_basis,
        total_unrealized_pnl=pnl,
        total_unrealized_pnl_percent=round(pnl / cost_basis * 100, 4) if cost_basis else 0.0,
    )
