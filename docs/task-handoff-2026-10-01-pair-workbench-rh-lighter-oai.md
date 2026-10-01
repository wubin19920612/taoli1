# Pair workbench: RH Lighter OAI alias

## Goal and scope

- Fix the workbench error `rh-lighter future symbol not found: OAIUSDT` for a four-hour, one-minute comparison of Hyperliquid `io:OAI` against RH Lighter.
- Backend-only symbol resolution. No changes to frontend forms, collectors, alerts, trading logic, or global ticker normalization.
- Branch: `codex/frontend-localization-polish`; local baseline: `23ece61`; production baseline: `cb2dc58c6507a231850fd39c35c98ce9e621df28`.

## Implementation and entry points

- `backend/app/services/pair_spread_query.py`: `LIGHTER_FUTURE_SYMBOL_ALIASES` and `PairSpreadQueryService._lighter_market`.
- Resolve requested `OAIUSDT` to the active RH Lighter `OPENAIUSDT` market only when an exact native market is absent. Obtain the actual market ID dynamically from the RH instance's market details; never hard-code it in application logic.
- Candles, executable WebSocket prices, and historical funding share this resolver.
- Preserve the requested normalized symbol in results and funding points; current-leg `raw_symbol` exposes the actual upstream market `OPENAI`.
- Preserve the Hyperliquid DEX `io` and original market `io:OAI`. Do not apply this alias to normal Lighter, spot markets, inactive markets, or unknown tickers.
- Prefer an exact active `OAI` market if RH Lighter introduces one later, irrespective of upstream list order.
- `backend/tests/test_lighter_adapter.py`: seven new regression cases covering aligned pair history, current/funding data, native precedence, and negative boundaries.
- HTTP entry point: `GET /api/pair-spread/query` with `leg1_exchange=hyperliquid`, `leg1_symbol=OAI`, `leg1_dex=io`, `leg2_exchange=rh-lighter`, `leg2_symbol=OAI`, `hours=4`, `interval_minutes=1`.

## Business and safety rules

- This is an explicit request alias, not proof that instrument valuation, settlement, or trading risks are identical across venues.
- Preserve native bid/ask prices, per-leg turnover, one-hour funding intervals, market multipliers, and estimated-field metadata. No changes to spread calculations or fees.
- Keep both Lighter instances' API/WebSocket endpoints and market caches isolated.

## Validation

- Before the fix: new regression selection failed with the original `symbol not found` error; six boundary cases passed.
- After the fix: seven new regression cases passed.
- Local live query on 2026-10-01: 239 aligned minute points, current raw markets `io:OAI` / `OPENAI`, one-hour funding for both legs, eight historical funding records, no warnings.
- Production baseline reproduced HTTP 502 with the exact original RH Lighter symbol error.
- Related backend suite: 208 passed in 891.36 seconds across `test_lighter_adapter.py`, `test_pair_spread_query.py`, `test_api.py`, `test_trade_availability.py`, and `test_instrument_lookup.py`.
- Ruff undefined-name/import/syntax checks (`--select F,E9`) and `git diff --check` passed. Both production images built successfully; the frontend image build includes TypeScript checks and Vite production compilation.

## Production and delivery

- Production baseline containers are healthy and tracked files are clean. Use the approved SSH alias from the controlled operator environment; do not store connection secrets here.
- Follow `docs/git-delivery-checklist.md` and `docs/linux-deployment.md`: push reviewed changes, wait for both SHA-pinned GitHub Actions images, then use `deploy/linux-update.sh`.
- The deployment script must back up `/data/radar.db` with `integrity_check=ok` and matching host/container SHA-256 before `git pull --ff-only`. Also preserve the separate squeeze-route database backup when present.
- No production builds on the 2 GiB host, no volume deletion, and no changes to its `.env`.
- Deployment and final acceptance completed; the exact application source/image commit is recorded below. This acceptance update is a subsequent documentation-only commit, not a new application deployment.

## Final acceptance (2026-10-01)

- Application commit: `e235c93384ac37a33e18ed6b6467f24678f178b1`, pushed to the existing branch without force push.
- GitHub Actions run `36840524286`: `build (backend)` and `build (frontend)` both succeeded. Both SHA-pinned image manifests were checked on the server before deployment.
- Deployment used `deploy/linux-update.sh`: checked SQLite backups first, then `git pull --ff-only`, prebuilt image pull, and Compose `up -d --no-build --wait`. No production build or volume deletion.
- Radar backup: `backups/radar-before-e235c93384ac-20261001T091002Z.db` (637628416 bytes); `integrity_check=ok`; matching container/host SHA-256 `e043e9b3e1c24db76dda3bad6f1db4810bde9e384f1042c66f3c369a7808e436`.
- Squeeze-route backup: `backups/squeeze-route-before-e235c93384ac-20261001T091002Z.db` (217088 bytes); `integrity_check=ok`; matching container/host SHA-256 `7e4f5ace41edadae4913d58a44f38d8a9652422a4c0a59972a52f209903ea437`.
- Both containers are healthy and run the `sha-e235c93384ac37a33e18ed6b6467f24678f178b1` images. Production tracked worktree is clean at that exact source commit.
- `/api/health`: `status=ok`, 11779 markets, no exchange errors. Frontend root returned HTTP 200 and the React root element.
- Original OAI alias query: HTTP 200, 240 aligned minute points from `2026-10-01T05:11:00Z` through `2026-10-01T09:10:00Z`, no warnings, current raw symbols `io:OAI` and `OPENAI`, one-hour funding on both legs, eight historical funding records.
- Native `OPENAI` RH query for the same closed four-hour window also returned HTTP 200; the complete historical spread points exactly matched the alias query.
- Residual risk: availability still depends on upstream markets/APIs, and unverified aliases remain errors by design. One existing Pydantic-related `datetime.utcnow()` deprecation warning appeared in the index-component API test; it does not affect this fix and was left unchanged.

## Existing artifacts and next steps

- Leave unrelated untracked `.worktrees/`, old backend pytest directories, prior handoff drafts, and `output/` research/screenshots untouched and unstaged.
- Additional aliases require explicit upstream identity verification and similarly restricted regression coverage; do not infer aliases by price similarity.
- Start a new chat for unrelated modules. This handoff supplies the context needed to continue this module without earlier chat history.
