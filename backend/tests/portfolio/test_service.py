"""Tests for portfolio valuation and trade execution."""

import pytest

from app.db import DEFAULT_CASH_BALANCE, get_connection
from app.portfolio import (
    TradeError,
    TradeRequest,
    execute_trade,
    get_history,
    get_portfolio,
    get_position_tickers,
    holds_position,
    record_snapshot,
)


def buy(cache, ticker="AAPL", quantity=10):
    return execute_trade(cache, TradeRequest(ticker=ticker, quantity=quantity, side="buy"))


def sell(cache, ticker="AAPL", quantity=10):
    return execute_trade(cache, TradeRequest(ticker=ticker, quantity=quantity, side="sell"))


class TestBuying:
    def test_deducts_cash_and_opens_a_position(self, static_cache):
        result = buy(static_cache, "AAPL", 10)  # 10 x 190.00 = 1900.00

        assert result.total == 1900.0
        assert result.cash_balance == DEFAULT_CASH_BALANCE - 1900.0
        assert result.position.quantity == 10
        assert result.position.avg_cost == 190.0

    def test_records_the_trade(self, static_cache):
        result = buy(static_cache, "AAPL", 10)

        with get_connection() as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (result.id,)).fetchone()

        assert row["side"] == "buy"
        assert row["ticker"] == "AAPL"
        assert row["quantity"] == 10
        assert row["price"] == 190.0

    def test_second_buy_weights_the_average_cost(self, static_cache):
        buy(static_cache, "AAPL", 10)  # 10 @ 190
        static_cache.update(ticker="AAPL", price=210.0)
        result = buy(static_cache, "AAPL", 10)  # 10 @ 210

        assert result.position.quantity == 20
        assert result.position.avg_cost == 200.0

    def test_supports_fractional_shares(self, static_cache):
        result = buy(static_cache, "AAPL", 0.5)

        assert result.position.quantity == 0.5
        assert result.total == 95.0

    def test_rejects_a_purchase_beyond_the_cash_balance(self, static_cache):
        with pytest.raises(TradeError, match="Insufficient cash"):
            buy(static_cache, "NVDA", 100)  # 100 x 800 = 80,000

    def test_allows_spending_the_entire_balance(self, static_cache):
        static_cache.update(ticker="AAPL", price=100.0)
        result = buy(static_cache, "AAPL", 100)  # Exactly 10,000

        assert result.cash_balance == 0.0

    def test_rejects_a_ticker_with_no_price(self, static_cache):
        with pytest.raises(TradeError, match="No market price"):
            buy(static_cache, "ZZZZ", 1)

    def test_a_rejected_trade_changes_nothing(self, static_cache):
        with pytest.raises(TradeError):
            buy(static_cache, "NVDA", 100)

        portfolio = get_portfolio(static_cache)
        assert portfolio.cash_balance == DEFAULT_CASH_BALANCE
        assert portfolio.positions == []
        with get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"] == 0


class TestSelling:
    def test_credits_cash_and_reduces_the_position(self, static_cache):
        buy(static_cache, "AAPL", 10)
        result = sell(static_cache, "AAPL", 4)

        assert result.total == 760.0
        assert result.position.quantity == 6
        assert result.cash_balance == DEFAULT_CASH_BALANCE - 1900.0 + 760.0

    def test_selling_everything_closes_the_position(self, static_cache):
        buy(static_cache, "AAPL", 10)
        result = sell(static_cache, "AAPL", 10)

        assert result.position is None
        assert get_portfolio(static_cache).positions == []

    def test_returns_to_the_starting_balance_on_a_flat_round_trip(self, static_cache):
        buy(static_cache, "AAPL", 10)
        result = sell(static_cache, "AAPL", 10)

        assert result.cash_balance == DEFAULT_CASH_BALANCE

    def test_realizes_a_profit_when_the_price_rose(self, static_cache):
        buy(static_cache, "AAPL", 10)
        static_cache.update(ticker="AAPL", price=200.0)
        result = sell(static_cache, "AAPL", 10)

        assert result.cash_balance == DEFAULT_CASH_BALANCE + 100.0

    def test_does_not_move_average_cost(self, static_cache):
        buy(static_cache, "AAPL", 10)
        static_cache.update(ticker="AAPL", price=300.0)
        result = sell(static_cache, "AAPL", 5)

        assert result.position.avg_cost == 190.0

    def test_rejects_selling_more_than_held(self, static_cache):
        buy(static_cache, "AAPL", 5)
        with pytest.raises(TradeError, match="Insufficient shares"):
            sell(static_cache, "AAPL", 6)

    def test_rejects_selling_something_not_held(self, static_cache):
        with pytest.raises(TradeError, match="Insufficient shares"):
            sell(static_cache, "AAPL", 1)

    def test_leaves_no_dust_position_after_fractional_round_trip(self, static_cache):
        buy(static_cache, "AAPL", 0.1)
        buy(static_cache, "AAPL", 0.2)
        result = sell(static_cache, "AAPL", 0.3)

        assert result.position is None
        assert get_position_tickers() == []


class TestValuation:
    def test_reports_the_seeded_balance_when_empty(self, static_cache):
        portfolio = get_portfolio(static_cache)

        assert portfolio.cash_balance == DEFAULT_CASH_BALANCE
        assert portfolio.total_value == DEFAULT_CASH_BALANCE
        assert portfolio.positions_value == 0.0
        assert portfolio.total_unrealized_pnl_percent == 0.0

    def test_values_holdings_at_the_live_price(self, static_cache):
        buy(static_cache, "AAPL", 10)
        static_cache.update(ticker="AAPL", price=200.0)

        portfolio = get_portfolio(static_cache)
        position = portfolio.positions[0]

        assert position.current_price == 200.0
        assert position.market_value == 2000.0
        assert position.cost_basis == 1900.0
        assert position.unrealized_pnl == 100.0
        assert round(position.unrealized_pnl_percent, 2) == 5.26

    def test_total_value_is_cash_plus_holdings(self, static_cache):
        buy(static_cache, "AAPL", 10)
        static_cache.update(ticker="AAPL", price=200.0)

        portfolio = get_portfolio(static_cache)
        assert portfolio.total_value == round(portfolio.cash_balance + 2000.0, 2)

    def test_reports_a_loss_when_the_price_fell(self, static_cache):
        buy(static_cache, "AAPL", 10)
        static_cache.update(ticker="AAPL", price=180.0)

        assert get_portfolio(static_cache).total_unrealized_pnl == -100.0

    def test_holds_a_position_at_cost_when_no_price_is_known(self, static_cache):
        buy(static_cache, "AAPL", 10)
        static_cache.remove("AAPL")

        position = get_portfolio(static_cache).positions[0]
        assert position.current_price is None
        assert position.market_value == 1900.0
        assert position.unrealized_pnl == 0.0

    def test_aggregates_across_several_positions(self, static_cache):
        buy(static_cache, "AAPL", 10)  # 1900
        buy(static_cache, "MSFT", 5)  # 2100

        portfolio = get_portfolio(static_cache)
        assert len(portfolio.positions) == 2
        assert portfolio.total_cost_basis == 4000.0
        assert portfolio.positions_value == 4000.0

    def test_lists_positions_alphabetically(self, static_cache):
        buy(static_cache, "MSFT", 1)
        buy(static_cache, "AAPL", 1)

        assert [p.ticker for p in get_portfolio(static_cache).positions] == ["AAPL", "MSFT"]


class TestSnapshots:
    def test_a_trade_records_a_snapshot(self, static_cache):
        buy(static_cache, "AAPL", 10)

        history = get_history()
        assert len(history) == 1
        assert history[0].total_value == DEFAULT_CASH_BALANCE

    def test_snapshot_reflects_post_trade_value(self, static_cache):
        buy(static_cache, "AAPL", 10)
        static_cache.update(ticker="AAPL", price=200.0)
        record_snapshot(static_cache)

        assert get_history()[-1].total_value == DEFAULT_CASH_BALANCE + 100.0

    def test_history_is_oldest_first(self, static_cache):
        for _ in range(3):
            buy(static_cache, "AAPL", 1)

        history = get_history()
        assert len(history) == 3
        assert [s.recorded_at for s in history] == sorted(s.recorded_at for s in history)

    def test_history_limit_keeps_the_most_recent(self, static_cache):
        for _ in range(5):
            buy(static_cache, "AAPL", 1)

        limited = get_history(limit=2)
        assert len(limited) == 2
        assert limited[-1].recorded_at == get_history()[-1].recorded_at

    def test_history_is_empty_before_any_activity(self):
        assert get_history() == []


class TestPositionQueries:
    def test_lists_held_tickers(self, static_cache):
        buy(static_cache, "AAPL", 1)
        buy(static_cache, "MSFT", 1)

        assert get_position_tickers() == ["AAPL", "MSFT"]

    def test_holds_position_is_true_only_for_holdings(self, static_cache):
        buy(static_cache, "AAPL", 1)

        assert holds_position("AAPL") is True
        assert holds_position("MSFT") is False

    def test_holds_position_is_false_after_selling_out(self, static_cache):
        buy(static_cache, "AAPL", 1)
        sell(static_cache, "AAPL", 1)

        assert holds_position("AAPL") is False
