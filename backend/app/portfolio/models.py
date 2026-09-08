"""Request and response schemas for the portfolio API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..tickers import normalize_ticker


class TradeRequest(BaseModel):
    """A market order. Instant fill at the current price, no fees."""

    ticker: str
    quantity: float = Field(gt=0, description="Shares to trade; fractional allowed")
    side: Literal["buy", "sell"]

    @field_validator("ticker")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_ticker(value)


class PositionOut(BaseModel):
    """A holding valued at the latest known price."""

    ticker: str
    quantity: float
    avg_cost: float
    current_price: float | None = Field(
        default=None, description="None when no price has been seen for this ticker yet"
    )
    cost_basis: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_percent: float


class PortfolioOut(BaseModel):
    """Full portfolio state: cash, holdings, and aggregate P&L."""

    cash_balance: float
    positions: list[PositionOut]
    positions_value: float
    total_value: float
    total_cost_basis: float
    total_unrealized_pnl: float
    total_unrealized_pnl_percent: float


class TradeOut(BaseModel):
    """Result of an executed trade."""

    id: str
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    total: float
    executed_at: str
    cash_balance: float
    position: PositionOut | None = Field(
        default=None, description="Resulting holding; None when fully sold out"
    )


class SnapshotOut(BaseModel):
    """One point on the portfolio value chart."""

    total_value: float
    recorded_at: str
