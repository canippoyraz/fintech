"""Tests for lazy initialization, seeding, and connection handling."""

import sqlite3

import pytest

from app.db import (
    DEFAULT_CASH_BALANCE,
    DEFAULT_USER_ID,
    DEFAULT_WATCHLIST,
    ensure_initialized,
    get_cash_balance,
    get_connection,
    get_db_path,
    get_watchlist_tickers,
    init_db,
    new_id,
    reset_initialization,
    utc_now_iso,
)
from app.db.schema import EXPECTED_TABLES


class TestPathResolution:
    def test_reads_env_var(self, temp_db):
        assert get_db_path() == temp_db

    def test_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("FINTECH_DB_PATH", raising=False)
        assert get_db_path().as_posix() == "db/fintech.db"

    def test_expands_user_home(self, monkeypatch):
        monkeypatch.setenv("FINTECH_DB_PATH", "~/fintech.db")
        assert "~" not in str(get_db_path())


class TestInitialization:
    def test_creates_file_on_first_use(self, temp_db):
        assert not temp_db.exists()
        ensure_initialized()
        assert temp_db.exists()

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "fintech.db"
        monkeypatch.setenv("FINTECH_DB_PATH", str(nested))
        reset_initialization()
        ensure_initialized()
        assert nested.exists()

    def test_creates_all_expected_tables(self):
        with get_connection() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        assert EXPECTED_TABLES.issubset({row["name"] for row in rows})

    def test_is_idempotent(self):
        init_db()
        init_db()
        init_db()
        assert get_watchlist_tickers() == list(DEFAULT_WATCHLIST)
        assert get_cash_balance() == DEFAULT_CASH_BALANCE

    def test_repairs_a_dropped_table(self):
        with get_connection() as conn:
            conn.execute("DROP TABLE trades")
        init_db()
        with get_connection() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        assert "trades" in {row["name"] for row in rows}

    def test_ensure_initialized_is_a_noop_after_first_call(self, temp_db):
        ensure_initialized()
        with get_connection() as conn:
            conn.execute("UPDATE users_profile SET cash_balance = 42.0")
        ensure_initialized()  # Must not re-seed over live data
        assert get_cash_balance() == 42.0

    def test_repointing_the_path_reinitializes(self, tmp_path, monkeypatch):
        ensure_initialized()
        other = tmp_path / "other.db"
        monkeypatch.setenv("FINTECH_DB_PATH", str(other))
        reset_initialization()
        ensure_initialized()
        assert other.exists()


class TestSeedData:
    def test_seeds_default_profile(self):
        assert get_cash_balance() == DEFAULT_CASH_BALANCE

    def test_seeds_default_watchlist(self):
        assert get_watchlist_tickers() == list(DEFAULT_WATCHLIST)

    def test_seeds_exactly_ten_tickers(self):
        assert len(get_watchlist_tickers()) == 10

    def test_does_not_duplicate_seed_rows(self):
        init_db()
        with get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM watchlist").fetchone()["n"]
        assert count == len(DEFAULT_WATCHLIST)

    def test_profile_uses_default_user_id(self):
        with get_connection() as conn:
            row = conn.execute("SELECT id FROM users_profile").fetchone()
        assert row["id"] == DEFAULT_USER_ID

    def test_does_not_restore_a_watchlist_the_user_emptied(self):
        ensure_initialized()
        with get_connection() as conn:
            conn.execute("DELETE FROM watchlist")

        reset_initialization()  # Simulate a process restart
        ensure_initialized()

        assert get_watchlist_tickers() == []

    def test_does_not_restore_individually_removed_tickers(self):
        with get_connection() as conn:
            conn.execute("DELETE FROM watchlist WHERE ticker IN ('TSLA', 'NFLX')")

        reset_initialization()
        ensure_initialized()

        remaining = get_watchlist_tickers()
        assert "TSLA" not in remaining and "NFLX" not in remaining
        assert len(remaining) == 8

    def test_seeds_a_watchlist_when_the_profile_is_recreated(self):
        # A wiped database seeds fully, even if the file itself already exists.
        with get_connection() as conn:
            conn.execute("DELETE FROM watchlist")
            conn.execute("DELETE FROM users_profile")

        reset_initialization()
        ensure_initialized()

        assert get_watchlist_tickers() == list(DEFAULT_WATCHLIST)
        assert get_cash_balance() == DEFAULT_CASH_BALANCE


class TestConstraints:
    def test_watchlist_rejects_duplicate_ticker_for_same_user(self):
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                    (new_id(), DEFAULT_USER_ID, "AAPL", utc_now_iso()),
                )

    def test_watchlist_allows_same_ticker_for_another_user(self):
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                (new_id(), "someone-else", "AAPL", utc_now_iso()),
            )
        assert get_watchlist_tickers("someone-else") == ["AAPL"]

    def test_trades_reject_an_invalid_side(self):
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), DEFAULT_USER_ID, "AAPL", "hodl", 1.0, 190.0, utc_now_iso()),
                )

    def test_chat_messages_reject_an_invalid_role(self):
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO chat_messages (id, user_id, role, content, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (new_id(), DEFAULT_USER_ID, "system", "hi", utc_now_iso()),
                )

    def test_positions_support_fractional_quantities(self):
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), DEFAULT_USER_ID, "AAPL", 2.5, 190.0, utc_now_iso()),
            )
            row = conn.execute("SELECT quantity FROM positions WHERE ticker = 'AAPL'").fetchone()
        assert row["quantity"] == 2.5


class TestTransactions:
    def test_commits_on_success(self):
        with get_connection() as conn:
            conn.execute("UPDATE users_profile SET cash_balance = 123.45")
        assert get_cash_balance() == 123.45

    def test_rolls_back_on_error(self):
        with pytest.raises(RuntimeError):
            with get_connection() as conn:
                conn.execute("UPDATE users_profile SET cash_balance = 999.0")
                raise RuntimeError("boom")
        assert get_cash_balance() == DEFAULT_CASH_BALANCE

    def test_rows_are_accessible_by_column_name(self):
        with get_connection() as conn:
            row = conn.execute("SELECT cash_balance FROM users_profile").fetchone()
        assert row["cash_balance"] == DEFAULT_CASH_BALANCE


class TestHelpers:
    def test_new_id_is_unique(self):
        assert len({new_id() for _ in range(100)}) == 100

    def test_utc_now_iso_round_trips(self):
        from datetime import datetime

        parsed = datetime.fromisoformat(utc_now_iso())
        assert parsed.tzinfo is not None

    def test_get_cash_balance_raises_for_unknown_user(self):
        with pytest.raises(KeyError):
            get_cash_balance("nobody")

    def test_get_watchlist_tickers_is_empty_for_unknown_user(self):
        assert get_watchlist_tickers("nobody") == []
