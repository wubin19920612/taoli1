# Premium eligibility scan reuse — 2026-10-29

## Scope and baseline

- Module: multi-exchange premium monitoring; reuse eligibility scanning within a snapshot generation.
- Specification: `docs/premium-scan-dedupe-spec-2026-10-29.md`, sections 2–4 and approved section 6.
- Branch: `codex/premium-scan-dedupe`.
- Actual starting HEAD: `3b697ab6393ac92f62b63050d97ea165a6773849` (specification commit), based on the documented `8950523` baseline.
- Resumed from `f7a5a49fbed6190defe6e89aa02100e13d6bbdd9` (approved identity-check specification), preserving the previous uncommitted implementation and tests.
- Only the service, new scan-cache test file, and this handoff are in the delivery scope.
- User explicitly requires Windows-native, offline tests and a local commit. No WSL, remote push, deployment, backup, or live production validation was performed.

## Implementation and preserved behavior

- `_scan_eligibility`: one time-independent filter/group/key/index pass.
- `_eligible_at`: checks binding expiry using the current `now`, then requires two distinct exchanges. Keeps first-live-group order, per-group market order, and last-live-duplicate semantics.
- `eligible_groups` keeps its public signature and uncached behavior.
- `_current_eligibility`: one cache entry containing `(store.generation, settings.model_dump_json())`, the scan, and the exact input list copy returned by `get_markets()`. A hit also requires equal lengths and `all(a is b for a, b in zip(old, new))` via `_same_market_rows`. No IDs or hashes replace object identity; the stored list keeps the old rows alive. Settings are still loaded on every evaluation. The previous cached scan and input list are released before replacement.
- `run_once` and `_evaluate_results` reuse this cache. Unique market keys use its index directly, with current group/expiry checks. Duplicate keys require a projection from cached keys to preserve the old winning-row semantics when an occurrence expires; no full-market `MarketKey` reconstruction occurs on cache hits.
- Matching, message formatting, cooldowns, batch size, querying, concurrency, and result-key calculation remain unchanged.

## Resolved binding-refresh conflict

`backend/app/api/routes_market_identities.py::_refresh_store` replaces annotated markets through `set_markets`, but does not call `mark_published`. `SnapshotStore.set_markets` does not increase generation. Thus binding changes on this real path can leave the prescribed generation/settings cache key unchanged.

Section 6 explicitly approves the input-row identity guard implemented above. The four original reference-comparison tests for binding revision, fingerprint, asset ID and identity status were left unchanged: reproduced `4 failed, 15 deselected in 1.04s` before the fix, then all passed in the final regression. An additional test verifies retention of the exact input copy, hits on new copies containing the same rows, invalidation on equal-valued replacement/order/length changes without a generation bump, and release of the old scan.

## Final local validation

- Tests were written before implementation, with the old grouping, run loop and evaluation retained as an oracle; the old index comprehension is extracted into `legacy_by_market` solely for separate benchmark timing.
- Regression (scan-cache tests plus both section 4 regression files): `67 passed, 1 deselected in 17.21s`.
- Full suite with the requested `--basetemp=../.pytest-tmp/x2`: `2194 passed, 5 deselected, 12 warnings in 174.28s (0:02:54)`. The warnings concern deprecated `datetime.utcnow()` in API/index-component tests; excluded network/benchmark tests follow the existing default marker configuration.
- Explicit benchmark run: `1 passed, 19 deselected in 3.67s`.
- Ruff check for the two Python files passes.
- Manual diff review and `git diff --check` pass; existing tests were not relaxed or edited.
- Scan counters: `SCAN_COUNTS: first_batch=1 same_values=0 generation_change=1 settings_change=1 return_to_old_settings=1`.
- Benchmark: `MICROBENCH rows=12400 groups=16: legacy_scans=17 cached_scans=1 legacy_scan_seconds=2.509625 cached_scan_seconds=0.343256`.
- Identity timing: `IDENTITY_CHECK rows=12400 checks=16: total_seconds=0.008365 per_check_seconds=0.000523`.
- Benchmark measures accumulated grouping/index work on the frozen implementation and cache lookup/fingerprint/input copy/identity/time projection on the new implementation, during 16 explicitly gated, out-of-order group completions. Identity timing separately wraps the actual `_same_market_rows` calls (length and row-identity comparison); its total is already included in cached time. It excludes synthetic row construction, event waits, alert delivery and message generation. Timings are local measurements, not production CPU acceptance.
- Test fixtures block external network; stores, settings, notifier, point acquisition, and clock are fake. Sixteen completions compare all notification content, AlertEvent fields (UUID entropy fixed), cooldowns, status, cursor and settings-read counts after every completion.
- Covers generation changes, settings value equality/mutations/reversion, blacklist changes, binding changes, disabled monitor, thresholds, expiry exactly at `now`, failed send backoff, duplicate-key order/time reversal, cached-key reuse and old-cache release.

## Commands and local artifacts

Run from `backend` in PowerShell:

```powershell
New-Item -ItemType Directory -Force ../.pytest-tmp | Out-Null
python -m pytest -q -s tests/test_multi_exchange_premium_scan_cache.py tests/test_multi_exchange_premium.py tests/test_premium_index_query.py --basetemp=../.pytest-tmp/identity-regression-20261029
python -m pytest -q -s -m benchmark tests/test_multi_exchange_premium_scan_cache.py --basetemp=../.pytest-tmp/identity-benchmark-20261029
python -m pytest -q --basetemp=../.pytest-tmp/x2
python -m ruff check app/services/multi_exchange_premium.py tests/test_multi_exchange_premium_scan_cache.py
```

`New-Item -ItemType Directory -Force` is the native PowerShell equivalent of the requested `mkdir -p`.

At resumption, the service was modified and the test, handoff and `.pytest-tmp/` were untracked. Untracked `.pytest-tmp/` contains local test evidence and must remain uncommitted. New test and handoff files are intended task files. No existing user work was removed or overwritten. Only the three specified files are staged for delivery; the existing regression files and specification remain unchanged.

## Next action

Local delivery uses commit subject `perf: reuse premium eligibility scan within a snapshot generation` and trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Push/deployment remain outside this offline task; no production version, backup or live validation is claimed. Continue any other module or separately authorized production delivery in a new task using this handoff.
