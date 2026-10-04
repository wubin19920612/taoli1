# Pair workbench: explicit symbols, OAI alias rolled back

## Goal and scope

- User clarified that the workbench should use custom left/right symbols, not infer that RH Lighter OPENAI is OAI.
- Remove the alias introduced by `e235c93384ac37a33e18ed6b6467f24678f178b1`. The previous acceptance record `d62dd4c7a2b21e6ba4e68499dfcaf1f34dc49b1b` is historical and no longer describes the intended behavior.
- Existing branch: `codex/frontend-localization-polish`; rollback starting point: `d62dd4c7a2b21e6ba4e68499dfcaf1f34dc49b1b`.
- No history rewrite, force push, frontend changes, or changes to unrelated modules.

## Correct configuration and business rules

- Select custom symbols in the workbench.
- Left leg: Hyperliquid futures, DEX `io`, symbol `OAI` (raw market `io:OAI`).
- Right leg: RH Lighter futures, symbol `OPENAI` (raw market `OPENAI`).
- Neither side is implicitly renamed to the other. RH Lighter must reject `OAIUSDT` when only `OPENAI` exists.
- Preserve exchange instance, native market ID, Hyperliquid DEX and raw market identity. Do not equate contracts based on similar names or prices.
- Preserve funding intervals, native executable bid/ask, per-leg turnover, market multipliers, fees and estimated-field metadata; no spread/trading calculation changes.

## Implementation and entry points

- `backend/app/services/pair_spread_query.py`: remove `LIGHTER_FUTURE_SYMBOL_ALIASES` and its fallback lookup from `_lighter_market`. Application code is restored exactly to the pre-alias baseline `23ece61`.
- `backend/tests/test_lighter_adapter.py`: remove the prior alias tests and add one regression ensuring native `OPENAIUSDT` resolves while `OAIUSDT` does not silently resolve to it.
- `frontend/src/pages/PairMonitorPage.tsx`: existing custom-symbol mode is used without changes.
- HTTP validation route: `GET /api/pair-spread/query`; use `leg1_exchange=hyperliquid`, `leg1_symbol=OAI`, `leg1_dex=io`, `leg2_exchange=rh-lighter`, `leg2_symbol=OPENAI`, `hours=4`, `interval_minutes=1`.

## Validation and delivery status

- Local validation: 76 tests passed in 49.86 seconds across `test_lighter_adapter.py` and `test_pair_spread_query.py`; Ruff `--select F,E9` and `git diff --check` passed. The application service exactly matches its pre-alias `23ece61` version.
- Live local custom query: 240 aligned minute points, raw markets `io:OAI` and `OPENAI`, one-hour funding on both legs, no warnings. RH `OAIUSDT` correctly raises `symbol not found` rather than resolving to OPENAI.
- Git delivery and production rollback acceptance are complete; details below. Production before rollback was running application commit `e235c93384ac37a33e18ed6b6467f24678f178b1`.
- Follow `docs/git-delivery-checklist.md` and `docs/linux-deployment.md`: scoped commit/push, both SHA-pinned GitHub Actions images, SQLite backups before `git pull --ff-only`, then Compose update with no production build or volume deletion.
- Verify explicit custom-symbol query returns HTTP 200 with original raw markets, and the old RH `OAI` request no longer succeeds through an implicit alias.
- Server connection details and credentials stay in the controlled operator environment, not this document.

## Final rollback acceptance (2026-10-01)

- Application/source/image commit: `52df4a67f36c95fb59ba9fe89394b9e95fda5819`, pushed without force push. This acceptance update is a subsequent documentation-only commit, not a second application change.
- GitHub Actions run `36843848946`: both `build (backend)` and `build (frontend)` succeeded; frontend compilation includes TypeScript checks and the Vite production build. Both image manifests were verified on the server before updating.
- Deployment used the approved script: SQLite backups first, `git pull --ff-only`, SHA-pinned image pulls and Compose `up -d --no-build --wait`. No old database restore, production build or volume deletion.
- Radar backup: `backups/radar-before-52df4a67f36c-20261001T093859Z.db` (627466240 bytes), `integrity_check=ok`, matching host/container SHA-256 `1f4857fb6dac3d8777106ac4cc80d051b5c38371ccc23dad01dfa8205bd66732`.
- Squeeze-route backup: `backups/squeeze-route-before-52df4a67f36c-20261001T093859Z.db` (217088 bytes), `integrity_check=ok`, matching host/container SHA-256 `37a2369390814104c1cd70077da5d26537d0ad0d7e77de7d40e022e4701b4ce9`.
- Both production containers are healthy on `sha-52df4a67f36c95fb59ba9fe89394b9e95fda5819`. Tracked server worktree is clean at that source commit.
- `/api/health`: `status=ok`, 11782 markets and no exchange errors; frontend root returned HTTP 200 with the React root element.
- Correct custom query: HTTP 200, 240 aligned minute points from `2026-10-01T05:39:00Z` through `2026-10-01T09:38:00Z`, raw markets `io:OAI` / `OPENAI`, one-hour funding on each leg, eight historical funding records and no warnings.
- Old same-symbol RH `OAI` query now correctly returns HTTP 502 containing `rh-lighter future symbol not found: OAIUSDT`; this verifies that implicit alias behavior is gone. Select custom mode and use OPENAI explicitly instead.
- Residual risk: wrong saved/form symbols still fail intentionally, and market availability depends on upstream APIs. No other rollback failures were observed; existing unrelated artifacts remain untouched.

## Existing artifacts and next steps

- Preserve unrelated untracked `.worktrees/`, old backend pytest directories, previous handoff drafts and `output/` research/screenshots.
- Previous alias deployment and backups remain historical; do not delete them to perform this rollback.
- Use a new chat for unrelated modules. No additional ticker aliases should be inferred without explicit user approval.
