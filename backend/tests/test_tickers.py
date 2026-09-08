"""Tests for ticker symbol normalization."""

import pytest

from app.tickers import normalize_ticker


class TestNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("AAPL", "AAPL"),
            ("aapl", "AAPL"),
            ("  msft  ", "MSFT"),
            ("BRK.B", "BRK.B"),
            ("RDS-A", "RDS-A"),
            ("a", "A"),
            ("ABCDEFGHIJ", "ABCDEFGHIJ"),  # 10 chars, the maximum
        ],
    )
    def test_accepts_valid_symbols(self, raw, expected):
        assert normalize_ticker(raw) == expected


class TestRejection:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "ABCDEFGHIJK",  # 11 chars
            "1AAPL",  # Must start with a letter
            "AA PL",  # No spaces
            "AA;PL",  # No punctuation beyond . and -
            "AA/PL",
            "'; DROP TABLE watchlist; --",
        ],
    )
    def test_rejects_invalid_symbols(self, raw):
        with pytest.raises(ValueError):
            normalize_ticker(raw)

    def test_rejects_none(self):
        with pytest.raises(ValueError):
            normalize_ticker(None)

    def test_error_names_the_offending_input(self):
        with pytest.raises(ValueError, match="123"):
            normalize_ticker("123")
