# Funding Rate Arbitrage Design

Date: 2026-05-26
Status: Design direction approved; awaiting written spec review
Scope: Independent funding-rate arbitrage strategy page and backend preview engine

## Context

The existing product is centered on spread arbitrage opportunities. It already collects useful funding fields from futures markets, including current funding rate, predicted next funding rate, next settlement time, funding interval, mark price, index price, depth, and volume. However, those fields are currently folded into the spread-arbitrage signal through `combined_open_edge_pct`.

Funding-rate arbitrage needs a separate strategy. It should not reuse the spread-arbitrage entry threshold as its primary decision rule. The core question is different:

- Spread arbitrage asks whether the open spread is large enough to enter and later close as the spread decays.
- Funding-rate arbitrage asks whether the expected funding income over the next and following cycles still exceeds execution cost, basis drift, convergence risk, and forced-deleveraging risk.

The funding strategy will be implemented as a separate page and backend service path. It must not change the current spread-opportunity table, alert rules, or live-pilot behavior unless a later implementation plan explicitly connects them.

## Goals

- Add an independent funding-rate arbitrage page.
- Rank `SF` and `FF` candidates by expected carry profitability, not by spread-open edge.
- Estimate the next funding cycle and rolling hold decision without dailyizing or annualizing funding rates.
- Make entry, hold, and exit decisions explicit and explainable.
- Account for the risk that funding convergence also causes basis convergence or basis reversal.
- Surface ADL and liquidation risk as a conservative risk proxy until real exchange account-risk fields are available.
- Keep the first version suitable for small live-gray testing while still safe enough to inspect before trading.

## Non-Goals

- Do not merge this page into the existing spread-arbitrage opportunity table.
- Do not use `combined_open_edge_pct` as the funding strategy score.
- Do not support `SS` on the funding page, because spot-spot routes do not create funding carry.
- Do not claim exact ADL probability in v1. The current data model has no direct ADL ranking, maintenance margin, position-side crowding, or account liquidation-distance fields.
- Do not build a full private-account position manager in this design.
- Do not implement automatic order placement in this design document. Implementation can later add a controlled gray-trading path after preview behavior is verified.

## Strategy Model

The recommended first version is a rolling one-cycle carry model.

At every refresh, the engine evaluates:

```text
expected_cycle_pnl_pct =
  next_cycle_funding_edge_pct
  + expected_basis_change_pct
  - estimated_open_cost_pct_if_not_open
  - estimated_close_cost_pct
  - slippage_buffer_pct
  - adl_risk_penalty_pct
  - confidence_penalty_pct
```

The decision rule is:

- Enter when `expected_cycle_pnl_pct >= min_entry_edge_pct`.
- Hold while `expected_cycle_pnl_pct >= min_hold_edge_pct`.
- Exit when `expected_cycle_pnl_pct < min_exit_edge_pct`, or when a hard risk rule fires.

This is intentionally rolling. The strategy does not exit merely because one funding settlement has passed. It keeps holding as long as the next-cycle expected total return remains positive after cost and risk adjustments.

## Candidate Types

### SF: Spot-Future Carry

Canonical direction:

- Buy spot.
- Short perpetual future.

Primary positive-carry case:

- The perpetual future's next funding rate is positive.
- The short future leg is expected to receive funding.
- The spot leg has funding rate `0`.

Negative funding can still be evaluated, but it should normally reject entry unless the basis-change estimate more than compensates. In practice, strongly negative funding should be skipped in live-gray mode.

### FF: Future-Future Carry

Canonical direction:

- Long the lower-funding or more favorable perpetual.
- Short the higher-funding perpetual.

Funding edge:

```text
short_leg_next_funding_pct - long_leg_next_funding_pct
```

The existing opportunity orientation already exposes buy and sell legs, but the funding page should not blindly inherit spread orientation. For funding candidates, the engine should be allowed to build the best carry direction from the two futures legs:

- If exchange A funding is materially higher than exchange B, short A and long B.
- If exchange B funding is materially higher than exchange A, short B and long A.

### SS: Excluded

`SS` has no funding carry and should be excluded from the funding strategy page and settings. This should be a hard default, not merely a UI filter.

## Funding Edge

Use next-cycle funding rates directly. Do not annualize or dailyize.

For a single candidate:

```text
next_cycle_funding_edge_pct = short_leg_next_funding_pct - long_leg_next_funding_pct
```

If a side is spot, its funding rate is `0`.

If `funding_next_rate_pct` is missing:

- Use current funding rate only as a fallback.
- Mark the candidate as lower confidence.
- Apply `confidence_penalty_pct`.
- Show `funding_source = predicted | fallback_current | missing`.

If both next and current rates are missing for any futures leg:

- Reject the candidate from entry.
- Display it only in a diagnostic "missing funding" bucket if needed.

## Basis Risk Model

Funding carry is not enough. When a high funding rate starts to converge, the basis can also converge or reverse. A trade may collect funding but lose more on the long/short mark-to-market.

The v1 model should estimate basis exposure separately from funding.

For each candidate:

```text
current_basis_pct = 2 * (short_leg_bid - long_leg_ask) / (short_leg_bid + long_leg_ask) * 100
exit_basis_pct = 2 * (short_leg_ask - long_leg_bid) / (short_leg_ask + long_leg_bid) * 100
basis_width_pct = abs(exit_basis_pct - current_basis_pct)
```

For an open position, unrealized basis movement should be measured from the entry basis:

```text
basis_pnl_pct = entry_basis_pct - current_exit_basis_pct
```

Positive `basis_pnl_pct` means the basis has moved in the carry position's favor. Negative `basis_pnl_pct` means the position has collected or may collect funding while losing more on mark-to-market basis movement.

For a preview-only candidate, approximate the next-cycle basis risk as:

```text
expected_basis_change_pct = -basis_reversion_penalty_pct
```

Where `basis_reversion_penalty_pct` starts as a configurable conservative penalty derived from:

- current basis width,
- mark/index deviation,
- recent basis volatility from opportunity history,
- and time to next funding settlement.

The UI must show basis risk as a separate field. It must not hide basis risk inside funding edge.

## Entry Timing

Entry timing should be driven by settlement proximity, funding confidence, and basis safety.

Hard entry blockers:

- `SS` route.
- missing funding on a futures leg.
- stale market data.
- below minimum 24h volume.
- insufficient top-of-book or estimated executable depth.
- mark/index deviation above the hard threshold.
- ADL risk proxy above the hard threshold.
- expected cycle PnL below entry threshold.

Soft penalties:

- fallback current funding instead of predicted next funding,
- mismatched funding settlement times between legs,
- very short time to settlement where execution may miss the funding timestamp,
- very long time to settlement where funding can change materially before capture,
- high basis width,
- unstable recent basis history.

Recommended entry windows:

- Avoid entering too close to settlement, because execution may miss the funding snapshot.
- Avoid entering too early if predicted funding is unstable and basis is wide.
- Prefer candidates inside a configurable window such as 5 to 90 minutes before next settlement, unless the next-cycle edge is large enough to justify earlier entry.

## Hold And Exit Timing

The strategy should reevaluate open or simulated positions every refresh.

Hold when:

- next-cycle expected PnL remains positive after risk adjustments,
- ADL risk proxy remains below the hard threshold,
- basis loss since entry remains below the configured stop,
- funding is still favorable or near-neutral after accounting for basis recovery,
- both legs remain liquid and fresh.

Exit when any of these occurs:

- `expected_cycle_pnl_pct < min_exit_edge_pct`,
- funding edge flips materially against the position,
- expected funding income is smaller than estimated close cost and basis risk,
- basis loss since entry exceeds stop threshold,
- mark/index deviation spikes above the hard threshold,
- ADL risk proxy crosses the hard threshold,
- either exchange leg becomes stale, illiquid, or missing funding,
- settlement time becomes unknown for a futures leg.

Exit should be explicit in the page as a recommendation:

- `ENTER`
- `HOLD`
- `EXIT_SOON`
- `EXIT_NOW`
- `BLOCKED`

## ADL And Liquidation Risk

The current project has no direct ADL data fields. The v1 engine should therefore use a conservative ADL risk proxy and label it clearly.

Potential proxy inputs:

- absolute mark/index deviation,
- very high positive funding on the short leg,
- very high negative funding on the long leg,
- high basis width,
- low volume,
- thin depth,
- stale data,
- exchange-specific missing risk data,
- high configured leverage.

Example proxy:

```text
adl_risk_score =
  mark_index_component
  + extreme_funding_component
  + basis_width_component
  + liquidity_component
  + leverage_component
```

The score should map to:

- `LOW`
- `MEDIUM`
- `HIGH`
- `BLOCKED`

`HIGH` should heavily penalize ranking. `BLOCKED` should reject entry and recommend exit for active positions.

The UI must avoid presenting this as exact ADL probability. Label it as "ADL risk proxy".

## Configuration

Add independent funding-strategy settings. These should not reuse live-pilot settings.

Suggested fields:

| Setting | Default | Purpose |
| --- | ---: | --- |
| `enabled` | `false` | Enables funding strategy preview/tracking. |
| `max_candidates` | `50` | Number of candidates displayed. |
| `min_entry_edge_pct` | `0.03` | Minimum expected next-cycle PnL for entry. |
| `min_hold_edge_pct` | `0.00` | Continue holding while expected edge is non-negative. |
| `min_exit_edge_pct` | `0.00` | Exit once expected edge falls below this. |
| `min_funding_edge_pct` | `0.02` | Minimum raw funding edge before cost/risk. |
| `min_volume_24h_usdt` | `1000000` | Liquidity floor. |
| `max_mark_index_deviation_pct` | `1.00` | Hard risk blocker. |
| `max_basis_width_pct` | `3.00` | Hard or strong soft blocker. |
| `slippage_buffer_pct` | `0.05` | Extra execution cost buffer. |
| `basis_risk_weight` | `1.00` | Multiplier for basis risk penalty. |
| `confidence_penalty_pct` | `0.02` | Penalty when next funding is inferred from current rate. |
| `min_minutes_to_settlement` | `5` | Avoid entering too close to settlement. |
| `max_minutes_to_settlement` | `90` | Preferred entry window upper bound. |
| `adl_block_score` | `80` | Hard ADL proxy block threshold. |
| `leverage` | `1` | Used for ADL risk proxy and future card planning. |
| `notional_per_symbol_usdt` | `100` | Gray-test sizing. |
| `prefer_hyperliquid` | `true` | Tie-breaker only, not a profitability override. |

These settings can live in a new model such as `FundingArbitrageSettings`.

## Backend Components

### Funding Candidate Builder

New service:

```text
backend/app/services/funding_arbitrage.py
```

Responsibilities:

- Build `SF` and `FF` funding candidates from current market snapshots or opportunities.
- Orient routes by funding carry direction, not spread opportunity orientation.
- Compute next-cycle funding edge.
- Compute entry cost, estimated close cost, basis risk penalty, confidence penalty, and ADL proxy.
- Produce an explainable decision and rank.

The builder should not import or call `combined_open_edge_pct`.

### Funding Models

New response models:

```text
FundingArbitrageCandidate
FundingArbitrageDecision
FundingArbitrageSettings
FundingArbitragePreview
```

Candidate fields:

- `id`
- `symbol`
- `type`
- `long_exchange`
- `long_market_type`
- `short_exchange`
- `short_market_type`
- `funding_source`
- `current_funding_edge_pct`
- `next_funding_edge_pct`
- `minutes_to_settlement`
- `entry_basis_pct`
- `exit_basis_pct`
- `basis_width_pct`
- `basis_risk_penalty_pct`
- `estimated_cost_pct`
- `expected_cycle_pnl_pct`
- `adl_risk_score`
- `adl_risk_level`
- `decision`
- `decision_reasons`
- `risk_labels`
- `volume_24h_usdt`
- `depth_usdt`
- `uses_hyperliquid`

### API

Add new endpoints:

```text
GET /api/funding-arbitrage/preview
GET /api/funding-arbitrage/settings
PUT /api/funding-arbitrage/settings
```

The preview endpoint should return ranked candidates and summary counts:

- total pairs evaluated,
- displayed candidates,
- blocked by missing funding,
- blocked by liquidity,
- blocked by ADL proxy,
- blocked by expected PnL,
- candidates by decision.

### History

The current history recorder only selects rows by minimum open spread. Funding strategy needs its own history collection or relaxed selection path.

Add one of:

- a new `funding_candidate_history` table, or
- an extension to the existing opportunity history recorder that samples funding candidates separately.

Recommended v1: add a new funding history table later in implementation planning. For the first UI preview, current snapshot plus existing opportunity history is enough for basic basis statistics, but not enough for robust strategy backtesting.

## Frontend Page

Add a separate route:

```text
/funding-arbitrage
```

The page should use dense operational layout:

- summary strip,
- settings drawer or compact settings panel,
- candidate table,
- selected-candidate detail panel,
- funding/basis history chart if data exists.

Main table columns:

- Decision
- Symbol
- Type (`SF` or `FF`)
- Long leg
- Short leg
- Next funding edge
- Expected cycle PnL
- Basis risk
- Minutes to settlement
- ADL proxy
- Volume/depth
- Funding source
- Reasons

The page should avoid spread-arbitrage labels such as "open spread threshold" as primary UI copy. Basis can be displayed, but it should be named basis risk or basis PnL, not spread opportunity.

## Ranking

Primary sort:

```text
expected_cycle_pnl_pct desc
```

Tie-breakers:

1. lower ADL risk score,
2. higher next funding confidence,
3. higher volume/depth,
4. preferred settlement window,
5. Hyperliquid preference if configured,
6. lower basis width.

The rank should remain explainable. Each row should show the top reasons behind its decision.

## Safety Rules

The first implementation should be preview-first.

- Settings default to disabled.
- No automatic order placement.
- No reuse of spread live-pilot auto-card behavior.
- SS excluded.
- Candidates with missing futures funding are blocked.
- Candidates with ADL proxy `BLOCKED` are blocked.
- Candidates with stale data are blocked.
- Strong negative funding against the selected direction is blocked.
- The UI must show when predicted funding is unavailable and current funding was used as fallback.

## Testing

Backend tests:

- SF positive funding creates `ENTER` when basis and ADL risk are low.
- SF positive funding rejects entry when basis risk exceeds funding edge.
- FF orientation chooses the higher-funding leg as short.
- FF reverses orientation when the other leg has higher funding.
- SS is excluded.
- Missing next funding falls back to current funding with lower confidence and penalty.
- Missing all funding on a futures leg blocks entry.
- ADL proxy high score blocks entry.
- Hold remains `HOLD` while expected next-cycle PnL is positive.
- Hold becomes `EXIT_NOW` when expected next-cycle PnL turns negative.

Frontend tests:

- Page renders independent funding settings.
- Candidate table shows `SF` and `FF`, never `SS`.
- Expected cycle PnL, funding source, settlement time, and ADL proxy are visible.
- Blocked candidates show clear reasons.

## V1 Implementation Decisions

- Build funding candidates from raw `MarketSnapshot` pairs. This keeps FF direction independent from spread-opportunity orientation.
- V1 preview is stateless for unopened candidates and supports optional simulated positions only if the implementation can store entry basis without touching live trading.
- Do not submit funding-carry candidates to Astro in v1. Existing Astro cards are spread-position based and should not be reused until the mapping is verified.
- Treat direct ADL, leverage bracket, open interest, and liquidation-risk APIs as later data enrichments. V1 uses only the ADL risk proxy described above.

## Acceptance Criteria

- A new independent funding-rate arbitrage page exists.
- The page does not depend on spread alert rules or live-pilot settings.
- The backend returns funding candidates with explicit `ENTER/HOLD/EXIT/BLOCKED` decisions.
- Raw funding edge, basis risk, estimated costs, ADL proxy, and expected cycle PnL are displayed separately.
- `SS` routes are absent from funding strategy results.
- Funding rates are shown and calculated per cycle, not annualized or dailyized.
- Candidates remain holdable across cycles as long as expected next-cycle PnL stays positive.
