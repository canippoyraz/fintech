"""Request and response schemas for the watchlist API."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from ..tickers import normalize_ticker


class WatchlistAddRequest(BaseModel):
    """Add a ticker to the watchlist."""

    ticker: str

    @field_validator("ticker")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_ticker(value)


class WatchlistItemOut(BaseModel):
    """A watched ticker with its latest price, or nulls if none seen yet."""

    ticker: str
    added_at: str
    price: float | None = None
    previous_price: float | None = None
    change: float | None = None
    change_percent: float | None = None
    direction: str | None = None
