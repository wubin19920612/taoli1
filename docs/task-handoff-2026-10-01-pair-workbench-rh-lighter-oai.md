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
- Related backend suite and static checks: running before delivery; record final counts in the acceptance section.

## Production and delivery

- Production baseline containers are healthy and tracked files are clean. Use the approved SSH alias from the controlled operator environment; do not store connection secrets here.
- Follow `docs/git-delivery-checklist.md` and `docs/linux-deployment.md`: push reviewed changes, wait for both SHA-pinned GitHub Actions images, then use `deploy/linux-update.sh`.
- The deployment script must back up `/data/radar.db` with `integrity_check=ok` and matching host/container SHA-256 before `git pull --ff-only`. Also preserve the separate squeeze-route database backup when present.
- No production builds on the 2 GiB host, no volume deletion, and no changes to its `.env`.
- Deployment and final acceptance are pending at initial implementation commit; append verified results after deployment.

## Existing artifacts and next steps

- Leave unrelated untracked `.worktrees/`, old backend pytest directories, prior handoff drafts, and `output/` research/screenshots untouched and unstaged.
- Additional aliases require explicit upstream identity verification and similarly restricted regression coverage; do not infer aliases by price similarity.
- Start a new chat for unrelated modules. This handoff supplies the context needed to continue this module without earlier chat history.
