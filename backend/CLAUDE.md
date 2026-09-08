# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Running the App

```bash
uv run uvicorn app.main:app --reload   # http://localhost:8000
```

`app/main.py` builds the FastAPI app in `create_app()`. Startup (the lifespan)
initializes the database, reads the watchlist, and starts the market data feed;
shutdown stops it. Live objects hang off `app.state`:

- `app.state.price_cache` — the `PriceCache`, created at app-build time because
  routers capture it when they mount, which happens before startup runs
- `app.state.market_source` — the running `MarketDataSource` (None before startup)

Endpoints so far: `GET /api/health`, `GET /api/stream/prices`. If a directory
exists at `FINTECH_STATIC_DIR` (default `static`), it is mounted at `/` to serve
the built frontend; API routes are registered first so they always take priority.

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `FINTECH_DB_PATH` | `db/fintech.db` | SQLite file location (relative to CWD) |
| `FINTECH_STATIC_DIR` | `static` | Built frontend; skipped if absent |
| `MASSIVE_API_KEY` | — | Real market data; simulator used when unset |
| `LOG_LEVEL` | `INFO` | Root log level |

## Database API

```python
from app.db import get_connection, ensure_initialized, get_watchlist_tickers
```

Schema and defaults live in `app/db/schema.py`; connection handling and lazy
initialization in `app/db/connection.py`; read helpers in `app/db/queries.py`.

- **`get_connection()`** — context manager yielding a `sqlite3.Connection` with
  `Row` factory. Commits on clean exit, rolls back on exception, always closes.
  Calls `ensure_initialized()` first, so callers never worry about setup.
- **`ensure_initialized()`** — creates and seeds the database on first use;
  a cheap no-op afterwards. Keyed by path, so tests can repoint it.
- **`utc_now_iso()` / `new_id()`** — the timestamp format and UUID primary keys
  every table expects.

Tables: `users_profile`, `watchlist`, `positions`, `trades`,
`portfolio_snapshots`, `chat_messages`. All carry `user_id` (currently always
`"default"`) so multi-user needs no migration.

**Seeding rule:** the default $10k profile and 10-ticker watchlist are seeded
only when the profile row is absent — i.e. a brand-new database. An emptied
watchlist is a legitimate state and is never silently repopulated.

Tests get an isolated temp database automatically via the autouse `temp_db`
fixture in `tests/conftest.py`; no test can touch the real `db/fintech.db`.

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp`, plus properties `change`, `change_percent`, `direction` ("up"/"down"/"flat"), and `to_dict()` for JSON serialization.

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None) -> PriceUpdate`
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)`
  - `version` property — monotonic counter, increments on every update (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set, otherwise `SimulatorDataSource`.

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache)  # Returns FastAPI APIRouter
# Endpoint: GET /api/stream/prices (text/event-stream)
```

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
