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
- Git delivery and production acceptance are pending at the initial rollback commit; append verified results below. Production before rollback is running application commit `e235c93384ac37a33e18ed6b6467f24678f178b1`.
- Follow `docs/git-delivery-checklist.md` and `docs/linux-deployment.md`: scoped commit/push, both SHA-pinned GitHub Actions images, SQLite backups before `git pull --ff-only`, then Compose update with no production build or volume deletion.
- Verify explicit custom-symbol query returns HTTP 200 with original raw markets, and the old RH `OAI` request no longer succeeds through an implicit alias.
- Server connection details and credentials stay in the controlled operator environment, not this document.

## Existing artifacts and next steps

- Preserve unrelated untracked `.worktrees/`, old backend pytest directories, previous handoff drafts and `output/` research/screenshots.
- Previous alias deployment and backups remain historical; do not delete them to perform this rollback.
- Use a new chat for unrelated modules. No additional ticker aliases should be inferred without explicit user approval.
