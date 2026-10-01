# Pair workbench: saved custom preset ordering

## Goal, scope and baseline

- User reported that saving Hyperliquid `io:OAI` against RH Lighter `OPENAI` appears to turn it into the old same-symbol OAI preset.
- Module: pair workbench saved presets. Preserve both symbols, Hyperliquid DEX and right-leg multiplier through save, reload and selection.
- Branch: `codex/frontend-localization-polish`; local baseline: `8be52ed`; production baseline: `52df4a67f36c95fb59ba9fe89394b9e95fda5819`.
- No symbol aliases, backend/schema changes, preset migration, deletion or trading-calculation changes.

## Verified root cause and implementation

- Read-only production inspection confirmed two distinct records: an older OAI/OAI same-symbol preset and a newer OAI/OPENAI custom preset. The custom pair was already stored correctly.
- `frontend/src/pages/PairMonitorPage.tsx`, `groupSavedPairPresets`: sorting previously forced all same-symbol groups ahead of every custom group, regardless of saved time. This displayed the old OAI group before the newly saved custom pair.
- Group sorting now prioritizes the latest saved timestamp. The existing same/custom tie-break and deterministic title tie-break remain for equal timestamps. Grouping and each group's own preset order are unchanged.
- The newly saved custom group displays `OAI / OPENAI`; clicking it restores custom mode, explicit `OPENAIUSDT`, `io` DEX and the stored multiplier.
- Keep the user's older OAI/OAI record unchanged; it is separate data, not a corrupted or renamed version of the custom preset.
- Do not reintroduce the implicit OAI-to-OPENAI alias removed by the prior rollback. See `docs/task-handoff-2026-10-01-pair-workbench-rh-lighter-oai.md` for its historical context.

## Business and safety rules

- Preserve per-leg exchange, market type, DEX, normalized/native market identity, multipliers and funding metadata.
- This change only affects display ordering. Executable bid/ask, per-leg turnover, funding periods, estimated fields, fees and spread calculations are unchanged.
- Validation must not modify real production presets. Browser save requests are intercepted in memory; production preset inspection and custom query validation use read-only requests.

## Tests and validation

- `frontend/tests/PairMonitorPage.test.tsx`: new parameterized cases for multipliers 1 and 2 verify actual save payload, distinct preset preservation, newest custom group first, reload and re-selection with both symbols/DEX/multiplier intact. A third case ensures a genuinely newer same-symbol group still sorts first.
- Before the fix, both custom-pair cases failed specifically on group ordering after successfully asserting correct persisted fields; the same-symbol recency case passed.
- After the fix, all three new cases passed; the full workbench suite passed 43 tests.
- Existing backend preset storage suite: 2 passed. TypeScript checks, Vite production build and `git diff --check` passed.
- Browser desktop/mobile verification and production deployment are pending at the initial implementation commit; append verified acceptance results below.

## Delivery and production

- Follow `docs/git-delivery-checklist.md` and `docs/linux-deployment.md`: scoped commit/push, both SHA-pinned GitHub Actions images, integrity/SHA-checked SQLite backups before `git pull --ff-only`, and Compose update with no production build or volume deletion.
- Verify both containers and `/api/health`, then the actual saved list on desktop/mobile and explicit OAI/OPENAI query behavior.
- Server connection details and credentials stay in the controlled operator environment, not this document.

## Existing artifacts and next steps

- Preserve unrelated `.worktrees/`, old pytest directories, prior handoff drafts and `output/` research/screenshots.
- Task-generated `output/pair-custom-presets-*.log` and browser screenshots are validation artifacts and are not included in the scoped source commit.
- Existing Ant Design InputNumber deprecation/test warning noise is outside this display-order fix; do not change unrelated components to suppress it.
- Use a new chat for unrelated modules. This handoff allows continuing saved-preset work without earlier chat context.
