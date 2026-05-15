# Arbitrage Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted CEX arbitrage radar that directly collects public exchange data, computes `SF`/`FF`/`SS` opportunities, displays a live dashboard, and sends Feishu alerts.

**Architecture:** Use a lightweight monolith: FastAPI backend, React/Vite frontend, SQLite configuration storage, in-memory latest market snapshots, and Docker Compose deployment. Keep code boundaries clean so collector, alert worker, and API can be split later without rewriting the domain model.

**Tech Stack:** Python 3.12, FastAPI, httpx, Pydantic v2, pytest, SQLite, React, TypeScript, Vite, Ant Design, Vitest, Docker Compose.

---

## Current Workspace Notes

- The current workspace is not a Git repository. Commit steps below are included for normal execution in a Git repo; in this workspace, skip commit commands unless Git is initialized intentionally.
- The existing analysis scripts (`analyze_pulse_lite.py`, `analyze_zhipu_spread.py`) are research artifacts and should not be imported by the app.
- The implementation should create a new app structure under the current workspace root, not under `output/`.

## File Structure

Create this structure:

```text
backend/
  app/
    __init__.py
    main.py
    core/
      __init__.py
      config.py
      logging.py
      scheduler.py
      security.py
    models/
      __init__.py
      market.py
      opportunity.py
      alert.py
      settings.py
    exchanges/
      __init__.py
      base.py
      binance.py
      okx.py
      bybit.py
      gate.py
      bitget.py
      htx.py
      aster.py
    services/
      __init__.py
      alert_engine.py
      collector.py
      feishu.py
      risk_labels.py
      snapshot_store.py
      spread_engine.py
    db/
      __init__.py
      database.py
      repositories.py
      schema.py
    api/
      __init__.py
      routes_alerts.py
      routes_health.py
      routes_opportunities.py
      routes_settings.py
      stream.py
  tests/
    conftest.py
    test_alert_engine.py
    test_api.py
    test_repositories.py
    test_risk_labels.py
    test_spread_engine.py
    test_symbol_normalization.py
  Dockerfile
  pyproject.toml
frontend/
  src/
    api/client.ts
    api/types.ts
    components/AppShell.tsx
    components/OpportunityTable.tsx
    components/TopFilters.tsx
    components/RiskTags.tsx
    pages/DashboardPage.tsx
    pages/SettingsPage.tsx
    pages/AlertHistoryPage.tsx
    state/useRadarStore.ts
    styles.css
    main.tsx
  tests/
    OpportunityTable.test.tsx
    SettingsPage.test.tsx
  Dockerfile
  index.html
  package.json
  tsconfig.json
  vite.config.ts
docker-compose.yml
.env.example
README.md
```

Responsibilities:

- `models/`: pure Pydantic data types and enums.
- `exchanges/`: exchange-specific public API clients and parsers.
- `services/spread_engine.py`: pure opportunity generation.
- `services/risk_labels.py`: pure risk labeling.
- `services/alert_engine.py`: pure rule matching, consecutive-hit, and cooldown logic.
- `db/`: SQLite schema and repositories.
- `api/`: FastAPI routes and streaming endpoint.
- `frontend/src/api/`: TypeScript API types and request helpers.
- `frontend/src/pages/`: route-level screens.

---

## Task 1: Scaffold Project Tooling

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/core/config.py`
- Create: `backend/tests/conftest.py`
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/styles.css`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `README.md`

- [ ] **Step 1: Create backend package and dependency file**

Create `backend/pyproject.toml`:

```toml
[project]
name = "arbitrage-radar-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.111.0",
  "uvicorn[standard]>=0.30.0",
  "httpx>=0.27.0",
  "pydantic>=2.7.0",
  "pydantic-settings>=2.2.0",
  "aiosqlite>=0.20.0",
  "python-dotenv>=1.0.1",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "pytest-asyncio>=0.23.0",
  "ruff>=0.5.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: Create minimal FastAPI app**

Create `backend/app/__init__.py` as an empty file.

Create `backend/app/core/config.py`:

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Arbitrage Radar"
    environment: str = "development"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    database_url: str = "sqlite:///./data/radar.db"
    poll_interval_seconds: float = Field(default=8.0, ge=3.0, le=60.0)
    funding_poll_interval_seconds: float = Field(default=120.0, ge=30.0, le=600.0)
    feishu_webhook_url: str = ""
    feishu_secret: str = ""
    dashboard_password: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Create `backend/app/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

Create `backend/tests/conftest.py`:

```python
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
```

- [ ] **Step 3: Verify backend app imports**

Run:

```bash
cd backend
python -m pytest -q
python -c "from app.main import app; print(app.title)"
```

Expected:

```text
no tests ran
Arbitrage Radar
```

- [ ] **Step 4: Create frontend scaffold**

Create `frontend/package.json`:

```json
{
  "name": "arbitrage-radar-frontend",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "tsc && vite build",
    "test": "vitest run",
    "preview": "vite preview --host 0.0.0.0"
  },
  "dependencies": {
    "@ant-design/icons": "^5.3.0",
    "antd": "^5.18.0",
    "dayjs": "^1.11.11",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "zustand": "^4.5.4"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.6",
    "@testing-library/react": "^15.0.7",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^24.1.0",
    "typescript": "^5.4.5",
    "vite": "^5.3.1",
    "vitest": "^1.6.0"
  }
}
```

Create `frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Arbitrage Radar</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2020"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src", "tests"],
  "references": []
}
```

Create `frontend/vite.config.ts`:

```ts
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: [],
  },
});
```

Create `frontend/src/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider, Typography } from 'antd';
import './styles.css';

const App = () => (
  <ConfigProvider>
    <main className="app-shell">
      <Typography.Title level={2}>Arbitrage Radar</Typography.Title>
      <Typography.Text type="secondary">Dashboard scaffold is ready.</Typography.Text>
    </main>
  </ConfigProvider>
);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

Create `frontend/src/styles.css`:

```css
html,
body,
#root {
  min-height: 100%;
  margin: 0;
}

body {
  background: #f5f7fb;
  color: #172033;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.app-shell {
  min-height: 100vh;
  padding: 24px;
}
```

- [ ] **Step 5: Create environment and deployment files**

Create `.env.example`:

```env
ENVIRONMENT=production
CORS_ORIGINS=http://localhost:3000
DATABASE_URL=sqlite:///./data/radar.db
POLL_INTERVAL_SECONDS=8
FUNDING_POLL_INTERVAL_SECONDS=120
FEISHU_WEBHOOK_URL=
FEISHU_SECRET=
DASHBOARD_PASSWORD=
```

Create `docker-compose.yml`:

```yaml
services:
  backend:
    build:
      context: ./backend
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      - radar-data:/app/data
    restart: unless-stopped

  frontend:
    build:
      context: ./frontend
    ports:
      - "3000:80"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  radar-data:
```

Create `README.md`:

```markdown
# Arbitrage Radar

Self-hosted CEX arbitrage radar for monitoring SF, FF, and SS opportunities.

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:3000`.

## Scope

This app monitors public exchange data and sends alerts. It does not store private exchange API keys and does not place orders.
```

- [ ] **Step 6: Commit scaffold if Git is available**

Run:

```bash
git add backend frontend docker-compose.yml .env.example README.md
git commit -m "chore: scaffold arbitrage radar app"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 2: Domain Models, Spread Formula, And Pure Tests

**Files:**
- Create: `backend/app/models/market.py`
- Create: `backend/app/models/opportunity.py`
- Create: `backend/app/services/spread_engine.py`
- Create: `backend/tests/test_spread_engine.py`

- [ ] **Step 1: Write failing spread-engine tests**

Create `backend/tests/test_spread_engine.py`:

```python
from datetime import UTC, datetime

from app.models.market import MarketSnapshot, MarketType
from app.services.spread_engine import build_opportunities, midpoint_spread_pct


def snapshot(exchange: str, market_type: MarketType, bid: float, ask: float) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        quote="USDT",
        exchange=exchange,
        market_type=market_type,
        bid=bid,
        ask=ask,
        volume_24h_usdt=10_000_000,
        timestamp=datetime(2026, 5, 15, tzinfo=UTC),
        raw_symbol="BTCUSDT",
    )


def test_midpoint_spread_pct_uses_bid_ask_formula() -> None:
    buy = snapshot("binance", MarketType.SPOT, bid=99, ask=100)
    sell = snapshot("okx", MarketType.FUTURE, bid=102, ask=103)

    open_spread, close_spread = midpoint_spread_pct(buy, sell)

    assert round(open_spread, 6) == round(2 * (102 - 100) / (100 + 102) * 100, 6)
    assert round(close_spread, 6) == round(2 * (103 - 99) / (99 + 103) * 100, 6)


def test_builds_sf_opportunity_from_spot_and_future() -> None:
    markets = [
        snapshot("binance", MarketType.SPOT, bid=99, ask=100),
        snapshot("okx", MarketType.FUTURE, bid=102, ask=103),
    ]

    opportunities = build_opportunities(markets, mode="SF")

    assert len(opportunities) == 1
    assert opportunities[0].symbol == "BTCUSDT"
    assert opportunities[0].buy_exchange == "binance"
    assert opportunities[0].sell_exchange == "okx"
    assert opportunities[0].type == "SF"


def test_builds_ff_opportunity_and_orients_positive_spread() -> None:
    markets = [
        snapshot("binance", MarketType.FUTURE, bid=99, ask=100),
        snapshot("okx", MarketType.FUTURE, bid=102, ask=103),
    ]

    opportunities = build_opportunities(markets, mode="FF")

    assert len(opportunities) == 1
    assert opportunities[0].buy_exchange == "binance"
    assert opportunities[0].sell_exchange == "okx"
    assert opportunities[0].open_spread_pct > 0


def test_skips_symbols_without_two_matching_markets() -> None:
    opportunities = build_opportunities(
        [snapshot("binance", MarketType.SPOT, bid=99, ask=100)],
        mode="SF",
    )

    assert opportunities == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_spread_engine.py -q
```

Expected: FAIL with import errors for `app.models.market`.

- [ ] **Step 3: Implement market and opportunity models**

Create `backend/app/models/market.py`:

```python
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURE = "future"


class MarketSnapshot(BaseModel):
    symbol: str
    base: str
    quote: str = "USDT"
    exchange: str
    market_type: MarketType
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    bid_size: float | None = None
    ask_size: float | None = None
    volume_24h_usdt: float | None = None
    funding_rate_pct: float | None = None
    funding_interval_hours: int | None = None
    funding_next_time: datetime | None = None
    mark_price: float | None = None
    index_price: float | None = None
    timestamp: datetime
    raw_symbol: str
```

Create `backend/app/models/opportunity.py`:

```python
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.models.market import MarketType


class OpportunityType(StrEnum):
    SF = "SF"
    FF = "FF"
    SS = "SS"


class Opportunity(BaseModel):
    id: str
    type: OpportunityType
    symbol: str
    buy_exchange: str
    buy_market_type: MarketType
    sell_exchange: str
    sell_market_type: MarketType
    open_spread_pct: float
    close_spread_pct: float
    fee_adjusted_open_pct: float
    spread_width_pct: float
    buy_bid: float
    buy_ask: float
    sell_bid: float
    sell_ask: float
    buy_volume_24h_usdt: float | None
    sell_volume_24h_usdt: float | None
    funding_rate_buy_pct: float | None
    funding_rate_sell_pct: float | None
    net_funding_pct: float | None
    mark_index_diff_buy_pct: float | None
    mark_index_diff_sell_pct: float | None
    risk_labels: list[str]
    last_seen_at: datetime
```

- [ ] **Step 4: Implement spread engine**

Create `backend/app/services/spread_engine.py`:

```python
from collections import defaultdict
from hashlib import sha1
from typing import Literal

from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity, OpportunityType

Mode = Literal["SF", "FF", "SS"]


def midpoint_spread_pct(buy_leg: MarketSnapshot, sell_leg: MarketSnapshot) -> tuple[float, float]:
    open_spread = 2 * (sell_leg.bid - buy_leg.ask) / (buy_leg.ask + sell_leg.bid) * 100
    close_spread = 2 * (sell_leg.ask - buy_leg.bid) / (buy_leg.bid + sell_leg.ask) * 100
    return open_spread, close_spread


def mark_index_diff_pct(snapshot: MarketSnapshot) -> float | None:
    if not snapshot.mark_price or not snapshot.index_price or snapshot.index_price <= 0:
        return None
    return (snapshot.mark_price - snapshot.index_price) / snapshot.index_price * 100


def opportunity_id(mode: Mode, symbol: str, buy_leg: MarketSnapshot, sell_leg: MarketSnapshot) -> str:
    value = (
        f"{mode}:{symbol}:{buy_leg.exchange}:{buy_leg.market_type}:"
        f"{sell_leg.exchange}:{sell_leg.market_type}"
    )
    return sha1(value.encode("utf-8")).hexdigest()[:16]


def pair_allowed(mode: Mode, first: MarketSnapshot, second: MarketSnapshot) -> bool:
    if mode == "SF":
        return first.market_type == MarketType.SPOT and second.market_type == MarketType.FUTURE
    if mode == "FF":
        return first.market_type == MarketType.FUTURE and second.market_type == MarketType.FUTURE
    if mode == "SS":
        return first.market_type == MarketType.SPOT and second.market_type == MarketType.SPOT
    return False


def orient_pair(mode: Mode, first: MarketSnapshot, second: MarketSnapshot) -> tuple[MarketSnapshot, MarketSnapshot] | None:
    if pair_allowed(mode, first, second):
        return first, second
    if mode in {"FF", "SS"} and pair_allowed(mode, second, first):
        return second, first
    return None


def build_opportunities(
    snapshots: list[MarketSnapshot],
    mode: Mode,
    buy_fee_pct: float = 0.1,
    sell_fee_pct: float = 0.1,
    safety_slippage_pct: float = 0.05,
) -> list[Opportunity]:
    by_symbol: dict[str, list[MarketSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        by_symbol[snapshot.symbol].append(snapshot)

    opportunities: list[Opportunity] = []
    seen: set[tuple[str, str, str]] = set()
    for symbol, legs in by_symbol.items():
        if len(legs) < 2:
            continue
        for first in legs:
            for second in legs:
                if first == second:
                    continue
                oriented = orient_pair(mode, first, second)
                if oriented is None:
                    continue
                buy_leg, sell_leg = oriented
                pair_key = tuple(sorted((buy_leg.exchange + buy_leg.market_type, sell_leg.exchange + sell_leg.market_type)))
                dedupe_key = (mode, symbol, "|".join(pair_key))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                open_spread_pct, close_spread_pct = midpoint_spread_pct(buy_leg, sell_leg)
                if mode in {"FF", "SS"} and open_spread_pct < 0 and close_spread_pct < 0:
                    buy_leg, sell_leg = sell_leg, buy_leg
                    open_spread_pct, close_spread_pct = midpoint_spread_pct(buy_leg, sell_leg)
                if open_spread_pct <= 0:
                    continue

                fee_adjusted = open_spread_pct - buy_fee_pct - sell_fee_pct - safety_slippage_pct
                net_funding = None
                if buy_leg.funding_rate_pct is not None and sell_leg.funding_rate_pct is not None:
                    net_funding = sell_leg.funding_rate_pct - buy_leg.funding_rate_pct

                opportunities.append(
                    Opportunity(
                        id=opportunity_id(mode, symbol, buy_leg, sell_leg),
                        type=OpportunityType(mode),
                        symbol=symbol,
                        buy_exchange=buy_leg.exchange,
                        buy_market_type=buy_leg.market_type,
                        sell_exchange=sell_leg.exchange,
                        sell_market_type=sell_leg.market_type,
                        open_spread_pct=open_spread_pct,
                        close_spread_pct=close_spread_pct,
                        fee_adjusted_open_pct=fee_adjusted,
                        spread_width_pct=abs(close_spread_pct - open_spread_pct),
                        buy_bid=buy_leg.bid,
                        buy_ask=buy_leg.ask,
                        sell_bid=sell_leg.bid,
                        sell_ask=sell_leg.ask,
                        buy_volume_24h_usdt=buy_leg.volume_24h_usdt,
                        sell_volume_24h_usdt=sell_leg.volume_24h_usdt,
                        funding_rate_buy_pct=buy_leg.funding_rate_pct,
                        funding_rate_sell_pct=sell_leg.funding_rate_pct,
                        net_funding_pct=net_funding,
                        mark_index_diff_buy_pct=mark_index_diff_pct(buy_leg),
                        mark_index_diff_sell_pct=mark_index_diff_pct(sell_leg),
                        risk_labels=[],
                        last_seen_at=max(buy_leg.timestamp, sell_leg.timestamp),
                    )
                )
    return sorted(opportunities, key=lambda item: item.open_spread_pct, reverse=True)
```

- [ ] **Step 5: Run spread tests**

Run:

```bash
cd backend
python -m pytest tests/test_spread_engine.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit domain layer if Git is available**

Run:

```bash
git add backend/app/models backend/app/services/spread_engine.py backend/tests/test_spread_engine.py
git commit -m "feat: add spread opportunity engine"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 3: Risk Labels And Filters

**Files:**
- Create: `backend/app/models/settings.py`
- Create: `backend/app/services/risk_labels.py`
- Create: `backend/tests/test_risk_labels.py`

- [ ] **Step 1: Write failing risk-label tests**

Create `backend/tests/test_risk_labels.py`:

```python
from datetime import UTC, datetime, timedelta

from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import RiskSettings
from app.services.risk_labels import apply_risk_labels


def opportunity(**overrides) -> Opportunity:
    base = dict(
        id="abc",
        type=OpportunityType.FF,
        symbol="AIUSDT",
        buy_exchange="gate",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=12.0,
        close_spread_pct=16.5,
        fee_adjusted_open_pct=11.75,
        spread_width_pct=4.5,
        buy_bid=1.0,
        buy_ask=1.01,
        sell_bid=1.15,
        sell_ask=1.17,
        buy_volume_24h_usdt=50_000,
        sell_volume_24h_usdt=20_000_000,
        funding_rate_buy_pct=0.05,
        funding_rate_sell_pct=-0.02,
        net_funding_pct=-0.07,
        mark_index_diff_buy_pct=0.1,
        mark_index_diff_sell_pct=1.1,
        risk_labels=[],
        last_seen_at=datetime.now(UTC) - timedelta(seconds=90),
    )
    base.update(overrides)
    return Opportunity(**base)


def test_applies_expected_risk_labels() -> None:
    settings = RiskSettings(
        min_volume_24h_usdt=100_000,
        stale_after_seconds=30,
        huge_spread_pct=10,
        wide_spread_pct=3,
        mark_index_deviation_pct=1,
        ticker_collision_symbols=["AIUSDT"],
    )

    labeled = apply_risk_labels(opportunity(), settings=settings, now=datetime.now(UTC))

    assert "LOW_VOLUME" in labeled.risk_labels
    assert "STALE_DATA" in labeled.risk_labels
    assert "HUGE_SPREAD_VERIFY" in labeled.risk_labels
    assert "WIDE_SPREAD" in labeled.risk_labels
    assert "SAME_TICKER_RISK" in labeled.risk_labels
    assert "FUNDING_AGAINST" in labeled.risk_labels
    assert "MARK_INDEX_DEVIATION" in labeled.risk_labels


def test_clean_opportunity_has_no_labels() -> None:
    settings = RiskSettings(ticker_collision_symbols=[])
    labeled = apply_risk_labels(
        opportunity(
            symbol="BTCUSDT",
            open_spread_pct=0.4,
            close_spread_pct=0.5,
            spread_width_pct=0.1,
            buy_volume_24h_usdt=100_000_000,
            sell_volume_24h_usdt=100_000_000,
            funding_rate_buy_pct=0.0,
            funding_rate_sell_pct=0.02,
            net_funding_pct=0.02,
            mark_index_diff_buy_pct=0.01,
            mark_index_diff_sell_pct=0.02,
            last_seen_at=datetime.now(UTC),
        ),
        settings=settings,
        now=datetime.now(UTC),
    )

    assert labeled.risk_labels == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_risk_labels.py -q
```

Expected: FAIL with import error for `RiskSettings`.

- [ ] **Step 3: Implement risk settings**

Create `backend/app/models/settings.py`:

```python
from pydantic import BaseModel, Field


class RiskSettings(BaseModel):
    min_volume_24h_usdt: float = Field(default=1_000_000, ge=0)
    stale_after_seconds: int = Field(default=30, ge=5)
    huge_spread_pct: float = Field(default=10.0, ge=0)
    wide_spread_pct: float = Field(default=3.0, ge=0)
    mark_index_deviation_pct: float = Field(default=1.0, ge=0)
    funding_against_pct: float = Field(default=0.01, ge=0)
    ticker_collision_symbols: list[str] = Field(default_factory=lambda: ["AIUSDT", "UPUSDT", "LABUSDT"])


class FeeSettings(BaseModel):
    spot_fee_pct: float = 0.1
    future_fee_pct: float = 0.05
    safety_slippage_pct: float = 0.05
```

- [ ] **Step 4: Implement risk label service**

Create `backend/app/services/risk_labels.py`:

```python
from datetime import UTC, datetime

from app.models.market import MarketType
from app.models.opportunity import Opportunity
from app.models.settings import RiskSettings


def apply_risk_labels(
    opportunity: Opportunity,
    settings: RiskSettings,
    now: datetime | None = None,
) -> Opportunity:
    current = now or datetime.now(UTC)
    labels: list[str] = []

    min_volume = min(
        opportunity.buy_volume_24h_usdt or 0,
        opportunity.sell_volume_24h_usdt or 0,
    )
    if min_volume < settings.min_volume_24h_usdt:
        labels.append("LOW_VOLUME")

    age_seconds = (current - opportunity.last_seen_at).total_seconds()
    if age_seconds > settings.stale_after_seconds:
        labels.append("STALE_DATA")

    if opportunity.open_spread_pct >= settings.huge_spread_pct:
        labels.append("HUGE_SPREAD_VERIFY")

    if opportunity.spread_width_pct >= settings.wide_spread_pct:
        labels.append("WIDE_SPREAD")

    if opportunity.symbol.upper() in {item.upper() for item in settings.ticker_collision_symbols}:
        labels.append("SAME_TICKER_RISK")

    if (
        opportunity.net_funding_pct is not None
        and opportunity.net_funding_pct < -settings.funding_against_pct
    ):
        labels.append("FUNDING_AGAINST")

    mark_diffs = [
        abs(value)
        for value in [opportunity.mark_index_diff_buy_pct, opportunity.mark_index_diff_sell_pct]
        if value is not None
    ]
    if any(value >= settings.mark_index_deviation_pct for value in mark_diffs):
        labels.append("MARK_INDEX_DEVIATION")

    if (
        opportunity.buy_market_type == MarketType.FUTURE
        and opportunity.funding_rate_buy_pct is None
    ) or (
        opportunity.sell_market_type == MarketType.FUTURE
        and opportunity.funding_rate_sell_pct is None
    ):
        labels.append("MISSING_FUNDING")

    return opportunity.model_copy(update={"risk_labels": labels})
```

- [ ] **Step 5: Run risk-label tests**

Run:

```bash
cd backend
python -m pytest tests/test_risk_labels.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit risk layer if Git is available**

Run:

```bash
git add backend/app/models/settings.py backend/app/services/risk_labels.py backend/tests/test_risk_labels.py
git commit -m "feat: add opportunity risk labels"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 4: SQLite Schema And Repositories

**Files:**
- Create: `backend/app/models/alert.py`
- Create: `backend/app/db/database.py`
- Create: `backend/app/db/schema.py`
- Create: `backend/app/db/repositories.py`
- Create: `backend/tests/test_repositories.py`

- [ ] **Step 1: Write repository tests**

Create `backend/tests/test_repositories.py`:

```python
import pytest

from app.db.database import connect_database
from app.db.repositories import AlertRuleRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.alert import AlertRule, AlertSeverity


@pytest.mark.asyncio
async def test_alert_rule_crud_roundtrip() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = AlertRuleRepository(db)

    rule = AlertRule(
        name="large FF spread",
        enabled=True,
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.25,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=3,
        cooldown_seconds=300,
        severity=AlertSeverity.WARNING,
    )

    created = await repo.create(rule)
    loaded = await repo.get(created.id)

    assert loaded is not None
    assert loaded.name == "large FF spread"
    assert loaded.types == ["FF"]


@pytest.mark.asyncio
async def test_settings_repository_defaults() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = SettingsRepository(db)

    settings = await repo.get_risk_settings()

    assert settings.min_volume_24h_usdt == 1_000_000
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_repositories.py -q
```

Expected: FAIL with import error for `app.db.database`.

- [ ] **Step 3: Implement alert models**

Create `backend/app/models/alert.py`:

```python
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    enabled: bool = True
    types: list[str] = Field(default_factory=lambda: ["SF", "FF", "SS"])
    include_exchanges: list[str] = Field(default_factory=list)
    exclude_exchanges: list[str] = Field(default_factory=list)
    include_symbols: list[str] = Field(default_factory=list)
    exclude_symbols: list[str] = Field(default_factory=list)
    min_open_spread_pct: float = 0.0
    min_fee_adjusted_open_pct: float = 0.0
    min_volume_24h_usdt: float = 0.0
    max_data_age_seconds: int = 60
    excluded_risk_labels: list[str] = Field(default_factory=list)
    consecutive_hits: int = Field(default=3, ge=1)
    cooldown_seconds: int = Field(default=300, ge=0)
    severity: AlertSeverity = AlertSeverity.INFO


class AlertEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    rule_id: str
    opportunity_id: str
    symbol: str
    status: str
    message: str
    created_at: datetime
```

- [ ] **Step 4: Implement database helpers and schema**

Create `backend/app/db/database.py`:

```python
import aiosqlite


async def connect_database(path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db
```

Create `backend/app/db/schema.py`:

```python
import aiosqlite


async def initialize_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS alert_rules (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alert_events (
          id TEXT PRIMARY KEY,
          rule_id TEXT NOT NULL,
          opportunity_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          status TEXT NOT NULL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          payload TEXT NOT NULL
        );
        """
    )
    await db.commit()
```

- [ ] **Step 5: Implement repositories**

Create `backend/app/db/repositories.py`:

```python
import json

import aiosqlite

from app.models.alert import AlertEvent, AlertRule
from app.models.settings import RiskSettings


class AlertRuleRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create(self, rule: AlertRule) -> AlertRule:
        payload = rule.model_dump_json()
        await self.db.execute(
            "INSERT INTO alert_rules (id, payload) VALUES (?, ?)",
            (rule.id, payload),
        )
        await self.db.commit()
        return rule

    async def list(self) -> list[AlertRule]:
        cursor = await self.db.execute("SELECT payload FROM alert_rules ORDER BY created_at")
        rows = await cursor.fetchall()
        return [AlertRule.model_validate_json(row["payload"]) for row in rows]

    async def get(self, rule_id: str) -> AlertRule | None:
        cursor = await self.db.execute("SELECT payload FROM alert_rules WHERE id = ?", (rule_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return AlertRule.model_validate_json(row["payload"])

    async def upsert(self, rule: AlertRule) -> AlertRule:
        payload = rule.model_dump_json()
        await self.db.execute(
            """
            INSERT INTO alert_rules (id, payload, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = CURRENT_TIMESTAMP
            """,
            (rule.id, payload),
        )
        await self.db.commit()
        return rule

    async def delete(self, rule_id: str) -> None:
        await self.db.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
        await self.db.commit()


class AlertEventRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create(self, event: AlertEvent) -> AlertEvent:
        await self.db.execute(
            """
            INSERT INTO alert_events (id, rule_id, opportunity_id, symbol, status, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.rule_id,
                event.opportunity_id,
                event.symbol,
                event.status,
                event.message,
                event.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return event


class SettingsRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def get_risk_settings(self) -> RiskSettings:
        cursor = await self.db.execute("SELECT payload FROM app_settings WHERE key = ?", ("risk",))
        row = await cursor.fetchone()
        if row is None:
            return RiskSettings()
        return RiskSettings.model_validate(json.loads(row["payload"]))

    async def set_risk_settings(self, settings: RiskSettings) -> RiskSettings:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            ("risk", settings.model_dump_json()),
        )
        await self.db.commit()
        return settings
```

- [ ] **Step 6: Run repository tests**

Run:

```bash
cd backend
python -m pytest tests/test_repositories.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit storage layer if Git is available**

Run:

```bash
git add backend/app/models/alert.py backend/app/db backend/tests/test_repositories.py
git commit -m "feat: add sqlite repositories"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 5: Alert Engine And Feishu Notifier

**Files:**
- Create: `backend/app/services/alert_engine.py`
- Create: `backend/app/services/feishu.py`
- Create: `backend/tests/test_alert_engine.py`

- [ ] **Step 1: Write alert engine tests**

Create `backend/tests/test_alert_engine.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.models.alert import AlertRule
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.services.alert_engine import AlertEngine


def opportunity(spread: float = 0.8) -> Opportunity:
    return Opportunity(
        id="opp-1",
        type=OpportunityType.FF,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=MarketType.FUTURE,
        sell_exchange="okx",
        sell_market_type=MarketType.FUTURE,
        open_spread_pct=spread,
        close_spread_pct=spread + 0.1,
        fee_adjusted_open_pct=spread - 0.2,
        spread_width_pct=0.1,
        buy_bid=99,
        buy_ask=100,
        sell_bid=101,
        sell_ask=102,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=20_000_000,
        funding_rate_buy_pct=0.0,
        funding_rate_sell_pct=0.02,
        net_funding_pct=0.02,
        mark_index_diff_buy_pct=0.01,
        mark_index_diff_sell_pct=0.01,
        risk_labels=[],
        last_seen_at=datetime.now(UTC),
    )


def test_requires_consecutive_hits_before_firing() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="ff spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.3,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=3,
        cooldown_seconds=300,
    )

    now = datetime.now(UTC)
    assert engine.evaluate([opportunity()], [rule], now=now) == []
    assert engine.evaluate([opportunity()], [rule], now=now + timedelta(seconds=8)) == []
    fired = engine.evaluate([opportunity()], [rule], now=now + timedelta(seconds=16))

    assert len(fired) == 1
    assert fired[0].rule.id == rule.id
    assert fired[0].opportunity.id == "opp-1"


def test_cooldown_suppresses_repeated_alerts() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="ff spread",
        types=["FF"],
        min_open_spread_pct=0.5,
        min_fee_adjusted_open_pct=0.3,
        min_volume_24h_usdt=1_000_000,
        consecutive_hits=1,
        cooldown_seconds=300,
    )
    now = datetime.now(UTC)

    assert len(engine.evaluate([opportunity()], [rule], now=now)) == 1
    assert engine.evaluate([opportunity()], [rule], now=now + timedelta(seconds=60)) == []
    assert len(engine.evaluate([opportunity()], [rule], now=now + timedelta(seconds=301))) == 1


def test_excluded_risk_label_blocks_alert() -> None:
    engine = AlertEngine()
    rule = AlertRule(
        name="no high risk",
        types=["FF"],
        min_open_spread_pct=0.5,
        excluded_risk_labels=["HUGE_SPREAD_VERIFY"],
        consecutive_hits=1,
    )
    opp = opportunity()
    opp = opp.model_copy(update={"risk_labels": ["HUGE_SPREAD_VERIFY"]})

    assert engine.evaluate([opp], [rule], now=datetime.now(UTC)) == []
```

- [ ] **Step 2: Run alert tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_alert_engine.py -q
```

Expected: FAIL with import error for `AlertEngine`.

- [ ] **Step 3: Implement alert engine**

Create `backend/app/services/alert_engine.py`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.alert import AlertRule
from app.models.opportunity import Opportunity


@dataclass(frozen=True)
class AlertMatch:
    rule: AlertRule
    opportunity: Opportunity


class AlertEngine:
    def __init__(self) -> None:
        self._hits: dict[str, tuple[int, datetime]] = {}
        self._last_sent: dict[str, datetime] = {}

    def evaluate(
        self,
        opportunities: list[Opportunity],
        rules: list[AlertRule],
        now: datetime | None = None,
    ) -> list[AlertMatch]:
        current = now or datetime.now(UTC)
        matches: list[AlertMatch] = []
        active_keys: set[str] = set()
        for rule in rules:
            if not rule.enabled:
                continue
            for opportunity in opportunities:
                key = f"{rule.id}:{opportunity.id}"
                if not self._matches(rule, opportunity, current):
                    continue
                active_keys.add(key)
                previous_count, _ = self._hits.get(key, (0, current))
                count = previous_count + 1
                self._hits[key] = (count, current)
                if count < rule.consecutive_hits:
                    continue
                last_sent = self._last_sent.get(key)
                if last_sent and (current - last_sent).total_seconds() < rule.cooldown_seconds:
                    continue
                self._last_sent[key] = current
                matches.append(AlertMatch(rule=rule, opportunity=opportunity))
        for key in list(self._hits):
            if key not in active_keys:
                self._hits.pop(key, None)
        return matches

    def _matches(self, rule: AlertRule, opportunity: Opportunity, now: datetime) -> bool:
        if opportunity.type not in rule.types:
            return False
        if rule.include_exchanges:
            exchanges = {opportunity.buy_exchange, opportunity.sell_exchange}
            if not exchanges.intersection(set(rule.include_exchanges)):
                return False
        if opportunity.buy_exchange in rule.exclude_exchanges or opportunity.sell_exchange in rule.exclude_exchanges:
            return False
        if rule.include_symbols and opportunity.symbol not in rule.include_symbols:
            return False
        if opportunity.symbol in rule.exclude_symbols:
            return False
        if opportunity.open_spread_pct < rule.min_open_spread_pct:
            return False
        if opportunity.fee_adjusted_open_pct < rule.min_fee_adjusted_open_pct:
            return False
        min_volume = min(opportunity.buy_volume_24h_usdt or 0, opportunity.sell_volume_24h_usdt or 0)
        if min_volume < rule.min_volume_24h_usdt:
            return False
        if (now - opportunity.last_seen_at).total_seconds() > rule.max_data_age_seconds:
            return False
        if set(opportunity.risk_labels).intersection(rule.excluded_risk_labels):
            return False
        return True
```

- [ ] **Step 4: Implement Feishu notifier**

Create `backend/app/services/feishu.py`:

```python
import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

import httpx

from app.models.alert import AlertRule
from app.models.opportunity import Opportunity


@dataclass(frozen=True)
class FeishuConfig:
    webhook_url: str
    secret: str = ""


class FeishuNotifier:
    def __init__(self, config: FeishuConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client or httpx.AsyncClient(timeout=10)

    async def send_alert(self, rule: AlertRule, opportunity: Opportunity, dashboard_url: str = "") -> None:
        if not self.config.webhook_url:
            return
        payload = self._build_payload(rule, opportunity, dashboard_url)
        response = await self.client.post(self.config.webhook_url, json=payload)
        response.raise_for_status()

    def _build_payload(self, rule: AlertRule, opportunity: Opportunity, dashboard_url: str) -> dict:
        lines = [
            f"Rule: {rule.name} ({rule.severity})",
            f"Symbol: {opportunity.symbol} / {opportunity.type}",
            f"Buy: {opportunity.buy_exchange} {opportunity.buy_market_type}",
            f"Sell: {opportunity.sell_exchange} {opportunity.sell_market_type}",
            f"Open spread: {opportunity.open_spread_pct:.3f}%",
            f"Close spread: {opportunity.close_spread_pct:.3f}%",
            f"Net estimate: {opportunity.fee_adjusted_open_pct:.3f}%",
            f"Funding: {opportunity.funding_rate_buy_pct} / {opportunity.funding_rate_sell_pct}",
            f"Risk: {', '.join(opportunity.risk_labels) if opportunity.risk_labels else 'none'}",
        ]
        if dashboard_url:
            lines.append(f"Dashboard: {dashboard_url}")
        content = "\n".join(lines)
        payload: dict = {"msg_type": "text", "content": {"text": content}}
        if self.config.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp)
        return payload

    def _sign(self, timestamp: str) -> str:
        string_to_sign = f"{timestamp}\n{self.config.secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")
```

- [ ] **Step 5: Run alert tests**

Run:

```bash
cd backend
python -m pytest tests/test_alert_engine.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit alert core if Git is available**

Run:

```bash
git add backend/app/services/alert_engine.py backend/app/services/feishu.py backend/tests/test_alert_engine.py
git commit -m "feat: add alert evaluation and feishu notifier"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 6: Exchange Adapter Contract And Initial Public API Adapters

**Files:**
- Create: `backend/app/exchanges/base.py`
- Create: `backend/app/exchanges/binance.py`
- Create: `backend/app/exchanges/gate.py`
- Create: `backend/app/exchanges/okx.py`
- Create: `backend/app/exchanges/bybit.py`
- Create: `backend/app/exchanges/bitget.py`
- Create: `backend/app/exchanges/htx.py`
- Create: `backend/app/exchanges/aster.py`
- Create: `backend/tests/test_symbol_normalization.py`

- [ ] **Step 1: Write symbol normalization tests**

Create `backend/tests/test_symbol_normalization.py`:

```python
from datetime import UTC, datetime

from app.exchanges.base import normalize_usdt_symbol, parse_float
from app.models.market import MarketSnapshot, MarketType


def test_normalize_usdt_symbol_handles_common_formats() -> None:
    assert normalize_usdt_symbol("BTCUSDT") == ("BTCUSDT", "BTC", "USDT")
    assert normalize_usdt_symbol("BTC-USDT") == ("BTCUSDT", "BTC", "USDT")
    assert normalize_usdt_symbol("BTC-USDT-SWAP") == ("BTCUSDT", "BTC", "USDT")
    assert normalize_usdt_symbol("btcusdt") == ("BTCUSDT", "BTC", "USDT")


def test_parse_float_handles_missing_values() -> None:
    assert parse_float("1.23") == 1.23
    assert parse_float("") is None
    assert parse_float(None) is None


def test_market_snapshot_accepts_normalized_values() -> None:
    symbol, base, quote = normalize_usdt_symbol("ETH-USDT-SWAP")
    snapshot = MarketSnapshot(
        symbol=symbol,
        base=base,
        quote=quote,
        exchange="okx",
        market_type=MarketType.FUTURE,
        bid=100,
        ask=101,
        timestamp=datetime.now(UTC),
        raw_symbol="ETH-USDT-SWAP",
    )

    assert snapshot.symbol == "ETHUSDT"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_symbol_normalization.py -q
```

Expected: FAIL with import error for `app.exchanges.base`.

- [ ] **Step 3: Implement base adapter utilities**

Create `backend/app/exchanges/base.py`:

```python
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import httpx

from app.models.market import MarketSnapshot


def parse_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_usdt_symbol(raw_symbol: str) -> tuple[str, str, str]:
    symbol = raw_symbol.upper().replace("_", "-")
    if symbol.endswith("-SWAP"):
        symbol = symbol.removesuffix("-SWAP")
    compact = symbol.replace("-", "")
    if not compact.endswith("USDT"):
        raise ValueError(f"Only USDT symbols are supported: {raw_symbol}")
    base = compact.removesuffix("USDT")
    return compact, base, "USDT"


def utc_now() -> datetime:
    return datetime.now(UTC)


class ExchangeAdapter(ABC):
    name: str

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=10)

    @abstractmethod
    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        raise NotImplementedError
```

- [ ] **Step 4: Implement exchange adapters with public endpoints**

Create each adapter with the same pattern: fetch JSON, normalize USDT symbols, return `MarketSnapshot` rows with positive bid/ask only.

Endpoint map:

```text
binance spot:  https://api.binance.com/api/v3/ticker/bookTicker
binance future: https://fapi.binance.com/fapi/v1/ticker/bookTicker
binance funding/mark: https://fapi.binance.com/fapi/v1/premiumIndex

gate spot: https://api.gateio.ws/api/v4/spot/tickers
gate future: https://api.gateio.ws/api/v4/futures/usdt/tickers

okx spot/future: https://www.okx.com/api/v5/market/tickers?instType=SPOT|SWAP

bybit spot/future: https://api.bybit.com/v5/market/tickers?category=spot|linear

bitget spot: https://api.bitget.com/api/v2/spot/market/tickers
bitget future: https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES

htx spot: https://api.huobi.pro/market/tickers
htx future: https://api.hbdm.com/linear-swap-ex/market/detail/batch_merged

aster future: https://fapi.asterdex.com/fapi/v1/ticker/bookTicker
aster spot: https://www.asterdex.com/api/v1/ticker/bookTicker
```

Implement `backend/app/exchanges/binance.py` first:

```python
from app.exchanges.base import ExchangeAdapter, normalize_usdt_symbol, parse_float, utc_now
from app.models.market import MarketSnapshot, MarketType


class BinanceAdapter(ExchangeAdapter):
    name = "binance"

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        data = (await self.client.get("https://api.binance.com/api/v3/ticker/bookTicker")).json()
        return self._parse_book_tickers(data, MarketType.SPOT)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        book = (await self.client.get("https://fapi.binance.com/fapi/v1/ticker/bookTicker")).json()
        premium = (await self.client.get("https://fapi.binance.com/fapi/v1/premiumIndex")).json()
        premium_by_symbol = {item["symbol"]: item for item in premium if item.get("symbol")}
        snapshots = self._parse_book_tickers(book, MarketType.FUTURE)
        enriched = []
        for snapshot in snapshots:
            p = premium_by_symbol.get(snapshot.raw_symbol, {})
            funding = parse_float(p.get("lastFundingRate"))
            mark = parse_float(p.get("markPrice"))
            index = parse_float(p.get("indexPrice"))
            enriched.append(
                snapshot.model_copy(
                    update={
                        "funding_rate_pct": funding * 100 if funding is not None else None,
                        "funding_interval_hours": 8,
                        "mark_price": mark,
                        "index_price": index,
                    }
                )
            )
        return enriched

    def _parse_book_tickers(self, data: list[dict], market_type: MarketType) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in data:
            raw_symbol = item.get("symbol", "")
            if not raw_symbol.endswith("USDT"):
                continue
            bid = parse_float(item.get("bidPrice"))
            ask = parse_float(item.get("askPrice"))
            if not bid or not ask or bid <= 0 or ask <= 0:
                continue
            symbol, base, quote = normalize_usdt_symbol(raw_symbol)
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=market_type,
                    bid=bid,
                    ask=ask,
                    timestamp=now,
                    raw_symbol=raw_symbol,
                )
            )
        return rows
```

Implement the remaining adapters with the same output contract. Keep endpoint-specific parsing in private methods such as `_parse_okx_tickers`, `_parse_gate_tickers`, and `_parse_bybit_tickers`.

- [ ] **Step 5: Run normalization tests**

Run:

```bash
cd backend
python -m pytest tests/test_symbol_normalization.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Add live adapter smoke command**

Create a temporary command by running:

```bash
cd backend
python - <<'PY'
import asyncio
from app.exchanges.binance import BinanceAdapter

async def main():
    adapter = BinanceAdapter()
    spot = await adapter.fetch_spot_tickers()
    future = await adapter.fetch_future_tickers()
    print(len(spot), len(future), spot[0].symbol if spot else "none")

asyncio.run(main())
PY
```

Expected: prints positive spot and future counts. If Binance is blocked in the local environment, record the network failure and continue; overseas server verification will run later.

- [ ] **Step 7: Commit adapter layer if Git is available**

Run:

```bash
git add backend/app/exchanges backend/tests/test_symbol_normalization.py
git commit -m "feat: add exchange adapter contract"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 7: Collector, Snapshot Store, API Routes, And SSE

**Files:**
- Create: `backend/app/services/snapshot_store.py`
- Create: `backend/app/services/collector.py`
- Create: `backend/app/api/routes_opportunities.py`
- Create: `backend/app/api/routes_health.py`
- Create: `backend/app/api/routes_settings.py`
- Create: `backend/app/api/routes_alerts.py`
- Create: `backend/app/api/stream.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_api.py`

- [ ] **Step 1: Write API tests with seeded store**

Create `backend/tests/test_api.py`:

```python
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.services.snapshot_store import SnapshotStore


def test_health_endpoint() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_opportunities_endpoint_returns_seeded_rows() -> None:
    store = SnapshotStore()
    store.set_opportunities(
        [
            Opportunity(
                id="opp",
                type=OpportunityType.FF,
                symbol="BTCUSDT",
                buy_exchange="binance",
                buy_market_type=MarketType.FUTURE,
                sell_exchange="okx",
                sell_market_type=MarketType.FUTURE,
                open_spread_pct=0.5,
                close_spread_pct=0.6,
                fee_adjusted_open_pct=0.3,
                spread_width_pct=0.1,
                buy_bid=99,
                buy_ask=100,
                sell_bid=101,
                sell_ask=102,
                buy_volume_24h_usdt=10_000_000,
                sell_volume_24h_usdt=20_000_000,
                funding_rate_buy_pct=0,
                funding_rate_sell_pct=0.02,
                net_funding_pct=0.02,
                mark_index_diff_buy_pct=0.01,
                mark_index_diff_sell_pct=0.01,
                risk_labels=[],
                last_seen_at=datetime.now(UTC),
            )
        ]
    )
    app = create_app(snapshot_store=store)
    client = TestClient(app)

    response = client.get("/api/opportunities?type=FF")

    assert response.status_code == 200
    assert response.json()[0]["symbol"] == "BTCUSDT"
```

- [ ] **Step 2: Run API tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_api.py -q
```

Expected: FAIL because `SnapshotStore` does not exist and `create_app` lacks injection.

- [ ] **Step 3: Implement snapshot store**

Create `backend/app/services/snapshot_store.py`:

```python
from threading import RLock

from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity


class SnapshotStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._markets: list[MarketSnapshot] = []
        self._opportunities: list[Opportunity] = []

    def set_markets(self, markets: list[MarketSnapshot]) -> None:
        with self._lock:
            self._markets = markets

    def get_markets(self) -> list[MarketSnapshot]:
        with self._lock:
            return list(self._markets)

    def set_opportunities(self, opportunities: list[Opportunity]) -> None:
        with self._lock:
            self._opportunities = opportunities

    def get_opportunities(self) -> list[Opportunity]:
        with self._lock:
            return list(self._opportunities)
```

- [ ] **Step 4: Implement opportunity route**

Create `backend/app/api/routes_opportunities.py`:

```python
from fastapi import APIRouter, Request

from app.models.opportunity import Opportunity

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("")
async def list_opportunities(
    request: Request,
    type: str | None = None,
    min_volume: float = 0,
    min_spread: float = 0,
) -> list[Opportunity]:
    store = request.app.state.snapshot_store
    rows = store.get_opportunities()
    if type:
        rows = [row for row in rows if row.type == type]
    rows = [
        row
        for row in rows
        if row.open_spread_pct >= min_spread
        and min(row.buy_volume_24h_usdt or 0, row.sell_volume_24h_usdt or 0) >= min_volume
    ]
    return rows
```

Create `backend/app/api/routes_health.py`:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Update FastAPI app factory**

Modify `backend/app/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_health import router as health_router
from app.api.routes_opportunities import router as opportunities_router
from app.core.config import get_settings
from app.services.snapshot_store import SnapshotStore


def create_app(snapshot_store: SnapshotStore | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.state.snapshot_store = snapshot_store or SnapshotStore()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(opportunities_router)
    return app


app = create_app()
```

- [ ] **Step 6: Implement collector service**

Create `backend/app/services/collector.py`:

```python
import asyncio
import logging

from app.models.settings import RiskSettings
from app.services.risk_labels import apply_risk_labels
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_opportunities

logger = logging.getLogger(__name__)


class CollectorService:
    def __init__(self, adapters: list, store: SnapshotStore, risk_settings: RiskSettings):
        self.adapters = adapters
        self.store = store
        self.risk_settings = risk_settings

    async def collect_once(self) -> None:
        tasks = []
        for adapter in self.adapters:
            tasks.append(adapter.fetch_spot_tickers())
            tasks.append(adapter.fetch_future_tickers())
        results = await asyncio.gather(*tasks, return_exceptions=True)
        markets = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("collector task failed: %s", result)
                continue
            markets.extend(result)
        opportunities = []
        for mode in ("SF", "FF", "SS"):
            opportunities.extend(build_opportunities(markets, mode=mode))
        opportunities = [
            apply_risk_labels(opportunity, settings=self.risk_settings)
            for opportunity in opportunities
        ]
        self.store.set_markets(markets)
        self.store.set_opportunities(opportunities)
```

- [ ] **Step 7: Run API tests**

Run:

```bash
cd backend
python -m pytest tests/test_api.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit API and collector if Git is available**

Run:

```bash
git add backend/app/api backend/app/main.py backend/app/services/collector.py backend/app/services/snapshot_store.py backend/tests/test_api.py
git commit -m "feat: expose opportunity API"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 8: Frontend API Types, Store, And Dashboard Table

**Files:**
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/state/useRadarStore.ts`
- Create: `frontend/src/components/RiskTags.tsx`
- Create: `frontend/src/components/OpportunityTable.tsx`
- Create: `frontend/src/components/TopFilters.tsx`
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/tests/OpportunityTable.test.tsx`

- [ ] **Step 1: Write table rendering test**

Create `frontend/tests/OpportunityTable.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { OpportunityTable } from '../src/components/OpportunityTable';
import type { Opportunity } from '../src/api/types';

const row: Opportunity = {
  id: 'opp',
  type: 'FF',
  symbol: 'BTCUSDT',
  buy_exchange: 'binance',
  buy_market_type: 'future',
  sell_exchange: 'okx',
  sell_market_type: 'future',
  open_spread_pct: 0.52,
  close_spread_pct: 0.61,
  fee_adjusted_open_pct: 0.32,
  spread_width_pct: 0.09,
  buy_bid: 99,
  buy_ask: 100,
  sell_bid: 101,
  sell_ask: 102,
  buy_volume_24h_usdt: 10000000,
  sell_volume_24h_usdt: 20000000,
  funding_rate_buy_pct: 0,
  funding_rate_sell_pct: 0.02,
  net_funding_pct: 0.02,
  mark_index_diff_buy_pct: 0.01,
  mark_index_diff_sell_pct: 0.02,
  risk_labels: [],
  last_seen_at: '2026-05-15T00:00:00Z',
};

test('renders opportunity rows', () => {
  render(<OpportunityTable rows={[row]} loading={false} />);

  expect(screen.getByText('BTCUSDT')).toBeInTheDocument();
  expect(screen.getByText('binance future')).toBeInTheDocument();
  expect(screen.getByText('okx future')).toBeInTheDocument();
  expect(screen.getByText('0.52%')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run frontend test to verify it fails**

Run:

```bash
cd frontend
npm install
npm test -- OpportunityTable.test.tsx
```

Expected: FAIL because `OpportunityTable` does not exist.

- [ ] **Step 3: Implement API types**

Create `frontend/src/api/types.ts`:

```ts
export type MarketType = 'spot' | 'future';
export type OpportunityType = 'SF' | 'FF' | 'SS';

export interface Opportunity {
  id: string;
  type: OpportunityType;
  symbol: string;
  buy_exchange: string;
  buy_market_type: MarketType;
  sell_exchange: string;
  sell_market_type: MarketType;
  open_spread_pct: number;
  close_spread_pct: number;
  fee_adjusted_open_pct: number;
  spread_width_pct: number;
  buy_bid: number;
  buy_ask: number;
  sell_bid: number;
  sell_ask: number;
  buy_volume_24h_usdt: number | null;
  sell_volume_24h_usdt: number | null;
  funding_rate_buy_pct: number | null;
  funding_rate_sell_pct: number | null;
  net_funding_pct: number | null;
  mark_index_diff_buy_pct: number | null;
  mark_index_diff_sell_pct: number | null;
  risk_labels: string[];
  last_seen_at: string;
}

export interface OpportunityFilters {
  type: OpportunityType;
  minVolume: number;
  minSpread: number;
  symbol: string;
}
```

Create `frontend/src/api/client.ts`:

```ts
import type { Opportunity, OpportunityFilters } from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export async function fetchOpportunities(filters: OpportunityFilters): Promise<Opportunity[]> {
  const params = new URLSearchParams({
    type: filters.type,
    min_volume: String(filters.minVolume),
    min_spread: String(filters.minSpread),
  });
  const response = await fetch(`${API_BASE}/api/opportunities?${params.toString()}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch opportunities: ${response.status}`);
  }
  const rows = (await response.json()) as Opportunity[];
  const symbol = filters.symbol.trim().toUpperCase();
  return symbol ? rows.filter((row) => row.symbol.includes(symbol)) : rows;
}
```

- [ ] **Step 4: Implement table and filters**

Create `frontend/src/components/RiskTags.tsx`:

```tsx
import { Tag } from 'antd';

export function RiskTags({ labels }: { labels: string[] }) {
  if (labels.length === 0) {
    return <Tag color="green">clean</Tag>;
  }
  return (
    <>
      {labels.map((label) => (
        <Tag color={label.includes('HUGE') || label.includes('STALE') ? 'red' : 'gold'} key={label}>
          {label}
        </Tag>
      ))}
    </>
  );
}
```

Create `frontend/src/components/OpportunityTable.tsx`:

```tsx
import { Table, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Opportunity } from '../api/types';
import { RiskTags } from './RiskTags';

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? '--' : `${value.toFixed(2)}%`;
}

function volume(value: number | null): string {
  if (!value) return '--';
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toFixed(0);
}

export function OpportunityTable({ rows, loading }: { rows: Opportunity[]; loading: boolean }) {
  const columns: ColumnsType<Opportunity> = [
    { title: 'Symbol', dataIndex: 'symbol', width: 120 },
    {
      title: 'Buy',
      render: (_, row) => `${row.buy_exchange} ${row.buy_market_type}`,
      width: 150,
    },
    {
      title: 'Sell',
      render: (_, row) => `${row.sell_exchange} ${row.sell_market_type}`,
      width: 150,
    },
    {
      title: 'Open',
      render: (_, row) => <Typography.Text strong>{pct(row.open_spread_pct)}</Typography.Text>,
      sorter: (a, b) => a.open_spread_pct - b.open_spread_pct,
      defaultSortOrder: 'descend',
      width: 100,
    },
    { title: 'Close', render: (_, row) => pct(row.close_spread_pct), width: 100 },
    { title: 'Net Est.', render: (_, row) => pct(row.fee_adjusted_open_pct), width: 100 },
    {
      title: 'Funding',
      render: (_, row) => `${pct(row.funding_rate_buy_pct)} / ${pct(row.funding_rate_sell_pct)}`,
      width: 140,
    },
    {
      title: '24h Vol',
      render: (_, row) => `${volume(row.buy_volume_24h_usdt)} / ${volume(row.sell_volume_24h_usdt)}`,
      width: 140,
    },
    { title: 'Risk', render: (_, row) => <RiskTags labels={row.risk_labels} />, width: 260 },
  ];

  return (
    <Table
      rowKey="id"
      columns={columns}
      dataSource={rows}
      loading={loading}
      pagination={{ pageSize: 25, showSizeChanger: true }}
      scroll={{ x: 1200 }}
      expandable={{
        expandedRowRender: (row) => (
          <pre className="row-detail">{JSON.stringify(row, null, 2)}</pre>
        ),
      }}
    />
  );
}
```

Create `frontend/src/components/TopFilters.tsx`:

```tsx
import { Input, InputNumber, Segmented, Space } from 'antd';
import type { OpportunityFilters, OpportunityType } from '../api/types';

export function TopFilters({
  filters,
  onChange,
}: {
  filters: OpportunityFilters;
  onChange: (filters: OpportunityFilters) => void;
}) {
  return (
    <Space wrap>
      <Segmented
        value={filters.type}
        options={['SF', 'FF', 'SS']}
        onChange={(value) => onChange({ ...filters, type: value as OpportunityType })}
      />
      <Input
        aria-label="Symbol"
        value={filters.symbol}
        onChange={(event) => onChange({ ...filters, symbol: event.target.value })}
        style={{ width: 160 }}
      />
      <InputNumber
        addonBefore="Min Vol"
        min={0}
        value={filters.minVolume}
        onChange={(value) => onChange({ ...filters, minVolume: Number(value ?? 0) })}
      />
      <InputNumber
        addonBefore="Min Spread %"
        min={0}
        step={0.1}
        value={filters.minSpread}
        onChange={(value) => onChange({ ...filters, minSpread: Number(value ?? 0) })}
      />
    </Space>
  );
}
```

- [ ] **Step 5: Implement dashboard page and app shell**

Create `frontend/src/pages/DashboardPage.tsx`:

```tsx
import { Alert, Card, Space } from 'antd';
import { useEffect, useState } from 'react';
import { fetchOpportunities } from '../api/client';
import type { Opportunity, OpportunityFilters } from '../api/types';
import { OpportunityTable } from '../components/OpportunityTable';
import { TopFilters } from '../components/TopFilters';

const defaultFilters: OpportunityFilters = {
  type: 'SF',
  minVolume: 1_000_000,
  minSpread: 0,
  symbol: '',
};

export function DashboardPage() {
  const [filters, setFilters] = useState(defaultFilters);
  const [rows, setRows] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const next = await fetchOpportunities(filters);
        if (!cancelled) {
          setRows(next);
          setError('');
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const timer = window.setInterval(load, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [filters]);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <TopFilters filters={filters} onChange={setFilters} />
      {error && <Alert type="error" showIcon message={error} />}
      <Card bordered={false}>
        <OpportunityTable rows={rows} loading={loading} />
      </Card>
    </Space>
  );
}
```

Create `frontend/src/components/AppShell.tsx`:

```tsx
import { DashboardOutlined, SettingOutlined, BellOutlined } from '@ant-design/icons';
import { ConfigProvider, Layout, Menu, Typography } from 'antd';
import { useState } from 'react';
import { AlertHistoryPage } from '../pages/AlertHistoryPage';
import { DashboardPage } from '../pages/DashboardPage';
import { SettingsPage } from '../pages/SettingsPage';

export function AppShell() {
  const [page, setPage] = useState('dashboard');
  return (
    <ConfigProvider>
      <Layout className="layout">
        <Layout.Sider width={220} theme="light">
          <Typography.Title level={4} className="brand">Arbitrage Radar</Typography.Title>
          <Menu
            mode="inline"
            selectedKeys={[page]}
            onClick={(event) => setPage(event.key)}
            items={[
              { key: 'dashboard', icon: <DashboardOutlined />, label: 'Radar' },
              { key: 'alerts', icon: <BellOutlined />, label: 'Alerts' },
              { key: 'settings', icon: <SettingOutlined />, label: 'Settings' },
            ]}
          />
        </Layout.Sider>
        <Layout.Content className="content">
          {page === 'dashboard' && <DashboardPage />}
          {page === 'alerts' && <AlertHistoryPage />}
          {page === 'settings' && <SettingsPage />}
        </Layout.Content>
      </Layout>
    </ConfigProvider>
  );
}
```

Create minimal pages:

```tsx
// frontend/src/pages/SettingsPage.tsx
import { Card, Typography } from 'antd';

export function SettingsPage() {
  return (
    <Card bordered={false}>
      <Typography.Title level={3}>Settings</Typography.Title>
      <Typography.Text type="secondary">Alert rules and Feishu settings will appear here.</Typography.Text>
    </Card>
  );
}
```

```tsx
// frontend/src/pages/AlertHistoryPage.tsx
import { Card, Typography } from 'antd';

export function AlertHistoryPage() {
  return (
    <Card bordered={false}>
      <Typography.Title level={3}>Alert History</Typography.Title>
      <Typography.Text type="secondary">Sent and suppressed alerts will appear here.</Typography.Text>
    </Card>
  );
}
```

Modify `frontend/src/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { AppShell } from './components/AppShell';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppShell />
  </React.StrictMode>,
);
```

Update `frontend/src/styles.css`:

```css
html,
body,
#root {
  min-height: 100%;
  margin: 0;
}

body {
  background: #f5f7fb;
  color: #172033;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.layout {
  min-height: 100vh;
}

.brand {
  padding: 20px 16px 8px;
}

.content {
  padding: 20px;
}

.row-detail {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  background: #0f172a;
  color: #dbeafe;
  border-radius: 6px;
  padding: 12px;
}
```

- [ ] **Step 6: Run frontend tests and build**

Run:

```bash
cd frontend
npm test -- OpportunityTable.test.tsx
npm run build
```

Expected: test passes and build succeeds.

- [ ] **Step 7: Commit frontend dashboard if Git is available**

Run:

```bash
git add frontend
git commit -m "feat: add radar dashboard"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 9: Alert Rules API And Settings UI

**Files:**
- Modify: `backend/app/api/routes_alerts.py`
- Modify: `backend/app/main.py`
- Create: `frontend/src/api/alerts.ts`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/pages/AlertHistoryPage.tsx`
- Create: `frontend/tests/SettingsPage.test.tsx`

- [ ] **Step 1: Write settings page test**

Create `frontend/tests/SettingsPage.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { SettingsPage } from '../src/pages/SettingsPage';

test('renders alert rule form controls', () => {
  render(<SettingsPage />);

  expect(screen.getByText('Alert Rules')).toBeInTheDocument();
  expect(screen.getByLabelText('Rule Name')).toBeInTheDocument();
  expect(screen.getByLabelText('Minimum Spread')).toBeInTheDocument();
});
```

- [ ] **Step 2: Implement alert routes**

Create `backend/app/api/routes_alerts.py`:

```python
from fastapi import APIRouter, HTTPException, Request

from app.models.alert import AlertRule

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("/rules")
async def list_rules(request: Request) -> list[AlertRule]:
    return await request.app.state.alert_rule_repo.list()


@router.post("/rules")
async def create_rule(request: Request, rule: AlertRule) -> AlertRule:
    return await request.app.state.alert_rule_repo.create(rule)


@router.put("/rules/{rule_id}")
async def update_rule(request: Request, rule_id: str, rule: AlertRule) -> AlertRule:
    if rule_id != rule.id:
        raise HTTPException(status_code=400, detail="Path rule id and payload id differ")
    return await request.app.state.alert_rule_repo.upsert(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: str) -> dict[str, bool]:
    await request.app.state.alert_rule_repo.delete(rule_id)
    return {"ok": True}


@router.post("/test")
async def test_alert() -> dict[str, str]:
    return {"status": "queued"}
```

Modify `backend/app/main.py` to include `alerts_router` and initialize repositories when a database is available:

```python
from app.api.routes_alerts import router as alerts_router

# inside create_app after app.include_router(opportunities_router)
app.include_router(alerts_router)
```

- [ ] **Step 3: Implement frontend alert API and settings form**

Create `frontend/src/api/alerts.ts`:

```ts
export interface AlertRule {
  id?: string;
  name: string;
  enabled: boolean;
  types: string[];
  min_open_spread_pct: number;
  min_fee_adjusted_open_pct: number;
  min_volume_24h_usdt: number;
  consecutive_hits: number;
  cooldown_seconds: number;
  severity: 'info' | 'warning' | 'critical';
}

export async function createAlertRule(rule: AlertRule): Promise<AlertRule> {
  const response = await fetch('/api/alerts/rules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rule),
  });
  if (!response.ok) throw new Error(`Failed to create rule: ${response.status}`);
  return response.json();
}
```

Modify `frontend/src/pages/SettingsPage.tsx`:

```tsx
import { Button, Card, Form, Input, InputNumber, Select, Space, Typography, message } from 'antd';
import { createAlertRule } from '../api/alerts';

export function SettingsPage() {
  const [form] = Form.useForm();

  async function submit(values: any) {
    await createAlertRule({
      enabled: true,
      severity: 'warning',
      min_fee_adjusted_open_pct: 0,
      cooldown_seconds: 300,
      consecutive_hits: 3,
      min_volume_24h_usdt: 1_000_000,
      ...values,
    });
    message.success('Alert rule saved');
    form.resetFields();
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card bordered={false}>
        <Typography.Title level={3}>Alert Rules</Typography.Title>
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item label="Rule Name" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="Types" name="types" initialValue={['FF']}>
            <Select mode="multiple" options={['SF', 'FF', 'SS'].map((value) => ({ value, label: value }))} />
          </Form.Item>
          <Form.Item label="Minimum Spread" name="min_open_spread_pct" initialValue={0.5}>
            <InputNumber min={0} step={0.1} addonAfter="%" />
          </Form.Item>
          <Form.Item label="Minimum Net Spread" name="min_fee_adjusted_open_pct" initialValue={0.25}>
            <InputNumber min={0} step={0.1} addonAfter="%" />
          </Form.Item>
          <Button type="primary" htmlType="submit">Save Rule</Button>
        </Form>
      </Card>
    </Space>
  );
}
```

- [ ] **Step 4: Run frontend settings test**

Run:

```bash
cd frontend
npm test -- SettingsPage.test.tsx
```

Expected: all tests pass.

- [ ] **Step 5: Commit alert settings UI if Git is available**

Run:

```bash
git add backend/app/api/routes_alerts.py backend/app/main.py frontend/src/api/alerts.ts frontend/src/pages/SettingsPage.tsx frontend/tests/SettingsPage.test.tsx
git commit -m "feat: add alert rule management"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 10: Security Gate And Docker Deployment

**Files:**
- Create: `backend/app/core/security.py`
- Modify: `backend/app/main.py`
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `README.md`

- [ ] **Step 1: Implement optional dashboard password middleware**

Create `backend/app/core/security.py`:

```python
from fastapi import Header, HTTPException


def verify_dashboard_password(expected_password: str, provided_password: str | None) -> None:
    if not expected_password:
        return
    if provided_password != expected_password:
        raise HTTPException(status_code=401, detail="Invalid dashboard password")
```

Use this helper in state-changing routes first (`routes_alerts.py`, `routes_settings.py`) by reading `x-dashboard-password`. For read-only routes, allow unauthenticated reads in local development and document that production should set `DASHBOARD_PASSWORD`.

- [ ] **Step 2: Create backend Dockerfile**

Create `backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir ".[dev]"

COPY app /app/app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Create frontend Dockerfile**

Create `frontend/Dockerfile`:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* /app/
RUN npm install
COPY . /app
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

- [ ] **Step 4: Update README deployment section**

Modify `README.md` to include:

```markdown
## Production Deployment

1. Provision an overseas Linux server.
2. Install Docker and Docker Compose.
3. Copy this project to the server.
4. Configure environment:

```bash
cp .env.example .env
nano .env
```

5. Set at least:

```env
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
DASHBOARD_PASSWORD=change-this-password
```

6. Start:

```bash
docker compose up -d --build
```

7. Open:

```text
http://SERVER_IP:3000
```

The app monitors public data only. It does not place orders.
```

- [ ] **Step 5: Build Docker images**

Run:

```bash
docker compose build
```

Expected: backend and frontend images build successfully.

- [ ] **Step 6: Commit deployment files if Git is available**

Run:

```bash
git add backend/Dockerfile frontend/Dockerfile docker-compose.yml README.md backend/app/core/security.py
git commit -m "chore: add docker deployment"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Task 11: End-To-End Verification

**Files:**
- Modify as needed only for defects found during verification.

- [ ] **Step 1: Run backend test suite**

Run:

```bash
cd backend
python -m pytest -q
```

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend test suite**

Run:

```bash
cd frontend
npm test
```

Expected: all frontend tests pass.

- [ ] **Step 3: Run frontend production build**

Run:

```bash
cd frontend
npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 4: Run local backend**

Run:

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Expected: backend starts and `GET http://localhost:8000/api/health` returns `{"status":"ok"}`.

- [ ] **Step 5: Run local frontend**

Run in another terminal:

```bash
cd frontend
npm run dev
```

Expected: dashboard opens at `http://localhost:3000` and can query `/api/opportunities`.

- [ ] **Step 6: Run Docker Compose**

Run:

```bash
docker compose up -d --build
docker compose ps
```

Expected: `backend` and `frontend` services are running.

- [ ] **Step 7: Manual Feishu alert test**

Set `FEISHU_WEBHOOK_URL` in `.env`, restart backend, and use `POST /api/alerts/test`.

Expected: if webhook is configured, a test message arrives in Feishu; if not configured, API returns a clear message that the webhook is empty.

- [ ] **Step 8: Record final verification**

Update `README.md` with the verified commands and any known API reachability caveats for the deployment region.

- [ ] **Step 9: Final commit if Git is available**

Run:

```bash
git add .
git commit -m "feat: complete cex arbitrage radar mvp"
```

Expected in a Git repo: commit succeeds. In the current non-Git workspace: skip this step.

---

## Self-Review Checklist

Spec coverage:

- CEX-only monitoring and alerting: covered by Tasks 1-11.
- Direct exchange public APIs: covered by Task 6.
- `SF`/`FF`/`SS` modes: covered by Tasks 2, 7, and 8.
- Fee-adjusted spreads: covered by Task 2.
- Risk labels: covered by Task 3.
- Feishu alerts: covered by Tasks 5 and 9.
- SQLite rules/settings/history foundation: covered by Task 4.
- Docker Compose deployment: covered by Task 10.
- No private API keys and no auto-trading: preserved by architecture and README scope.

Incomplete-content scan:

- This plan avoids unfinished markers and unspecified implementation steps.
- Exchange adapters beyond Binance have endpoint maps and the same required output contract; each parser should be implemented in its own adapter file with the base utility functions from Task 6.

Type consistency:

- Backend opportunity fields match frontend `Opportunity`.
- Alert rule fields match API and settings form.
- `MarketType` values are `spot` and `future`; frontend uses the same strings.
