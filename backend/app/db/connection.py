"""SQLite connection management and lazy schema initialization.

The database is created and seeded on first use — no migration step, no manual
setup. A fresh Docker volume therefore starts with a clean, seeded database.

Path resolution: ``FINTECH_DB_PATH`` if set, otherwise ``db/fintech.db``
relative to the working directory. The container sets WORKDIR=/app and mounts
the volume at /app/db, so the default resolves onto the volume unchanged.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from .schema import (
    DEFAULT_CASH_BALANCE,
    DEFAULT_USER_ID,
    DEFAULT_WATCHLIST,
    EXPECTED_TABLES,
    SCHEMA_SQL,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "db/fintech.db"

# Guards one-time initialization. Keyed by path so a test that repoints
# FINTECH_DB_PATH re-initializes rather than silently reusing the flag.
_init_lock = Lock()
_initialized_path: Path | None = None


def get_db_path() -> Path:
    """Resolve the SQLite file location from the environment."""
    return Path(os.environ.get("FINTECH_DB_PATH", DEFAULT_DB_PATH)).expanduser()


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string — the format every table stores."""
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    """Generate a primary key."""
    return str(uuid.uuid4())


@contextmanager
def get_connection(*, initialize: bool = True) -> Iterator[sqlite3.Connection]:
    """Open a connection, committing on success and rolling back on error.

    A connection per operation keeps this safe across FastAPI's threadpool
    without a shared-connection lock; at single-user volumes the open cost is
    irrelevant.

    Pass ``initialize=False`` only from within initialization itself, to avoid
    recursing back into :func:`ensure_initialized`.
    """
    if initialize:
        ensure_initialized()

    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_initialized() -> None:
    """Create and seed the database if it hasn't been set up yet.

    Cheap no-op after the first call for a given path.
    """
    global _initialized_path

    path = get_db_path()
    if _initialized_path == path:
        return

    with _init_lock:
        if _initialized_path == path:  # Another thread won the race
            return
        init_db()
        _initialized_path = path


def init_db() -> None:
    """Create any missing tables and seed default data. Idempotent."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with get_connection(initialize=False) as conn:
        existing = _existing_tables(conn)
        if not EXPECTED_TABLES.issubset(existing):
            logger.info("Initializing database schema at %s", path)
        conn.executescript(SCHEMA_SQL)
        _seed(conn)

    logger.info("Database ready at %s", path)


def reset_initialization() -> None:
    """Forget that initialization happened. For tests that swap the DB path."""
    global _initialized_path
    with _init_lock:
        _initialized_path = None


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _seed(conn: sqlite3.Connection) -> None:
    """Insert the default user profile and watchlist for a brand-new database.

    The default watchlist is seeded only alongside a newly created profile — never
    just because the watchlist is empty. Otherwise a user who removed every ticker
    would find all ten restored on the next restart.
    """
    profile_missing = (
        conn.execute(
            "SELECT COUNT(*) AS n FROM users_profile WHERE id = ?", (DEFAULT_USER_ID,)
        ).fetchone()["n"]
        == 0
    )

    if profile_missing:
        conn.execute(
            "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
            (DEFAULT_USER_ID, DEFAULT_CASH_BALANCE, utc_now_iso()),
        )
        logger.info("Seeded default user profile with $%.2f cash", DEFAULT_CASH_BALANCE)

    watchlist_empty = (
        conn.execute(
            "SELECT COUNT(*) AS n FROM watchlist WHERE user_id = ?", (DEFAULT_USER_ID,)
        ).fetchone()["n"]
        == 0
    )

    # The emptiness check also guards the UNIQUE(user_id, ticker) constraint if a
    # profile row is ever removed while its watchlist survives.
    if profile_missing and watchlist_empty:
        now = utc_now_iso()
        conn.executemany(
            "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            [(new_id(), DEFAULT_USER_ID, ticker, now) for ticker in DEFAULT_WATCHLIST],
        )
        logger.info("Seeded default watchlist: %s", ", ".join(DEFAULT_WATCHLIST))
