"""Pytest configuration and fixtures."""

import pytest

from app.db import reset_initialization


@pytest.fixture
def event_loop_policy():
    """Use the default event loop policy for all async tests."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point every test at a throwaway database.

    Autouse so no test can accidentally read or write the real db/fintech.db.
    Yields the path for tests that need to inspect the file directly.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FINTECH_DB_PATH", str(db_path))
    reset_initialization()
    yield db_path
    reset_initialization()
