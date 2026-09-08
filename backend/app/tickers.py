"""Ticker symbol normalization, shared by the portfolio and watchlist features."""

from __future__ import annotations

import re

# Uppercase symbol, optionally with dots or dashes for share classes (BRK.B, RDS-A).
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def normalize_ticker(raw: str) -> str:
    """Trim and upper-case a user-supplied symbol.

    Raises ValueError if the result isn't a plausible ticker. Callers surface
    that as a 400 — this is the only place symbol shape is decided.
    """
    ticker = (raw or "").strip().upper()

    if not ticker:
        raise ValueError("Ticker is required")
    if not TICKER_PATTERN.match(ticker):
        raise ValueError(
            f"{raw.strip()!r} is not a valid ticker symbol "
            "(1-10 characters, starting with a letter)"
        )
    return ticker
