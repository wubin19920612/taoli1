# Astro Card Defaults Design

## Goal

Astro card creation should produce safer, more useful default parameters from the current opportunity or alert instead of static placeholder values.

The first version keeps card creation in safe mode:

- `status=false`
- `disableOpen=true`
- no automatic trading

The user still reviews and enables cards in Astro.

## Scope

This design covers:

- Default card parameters for alert-created cards.
- Default card parameters for realtime opportunity one-click card creation.
- User-editable and persisted global defaults for position sizing fields.
- A lightweight funding-aware `closePosition` algorithm.

This design does not cover:

- Fully automated trade execution.
- A complete statistical model for spread reversion or premium-index forecasting.
- Historical optimization of card thresholds.

## Position Defaults

Add persisted Astro card defaults to the app settings store.

Initial fields:

- `maxTradeUSDT`: global default position value. Default: existing env/config value.
- `leverage`: global default leverage. Default: existing env/config value.
- `minNotional`: global default minimum notional. Default: existing env/config value.
- `maxNotional`: global default maximum notional. Default: existing env/config value.
- `closePositionBufferPct`: safety buffer used when Astro requires `closePosition < openPosition`. Default: `0.1`.
- `unfavorableFundingWeight`: how strongly unfavorable predicted funding raises the close threshold. Default: `1`.
- `closePositionFloorPct`: bottom-line spread-disappeared close target. Default: `0`.

Saved app settings override env/config values. Env/config values remain fallback/bootstrap defaults.

## Open Condition

`openPosition` always comes from the current opportunity value:

- Realtime opportunity card creation uses that row's `open_spread_pct`.
- Alert-created card creation uses the triggering opportunity's `open_spread_pct`.

Alert rule thresholds do not override `openPosition`.

## Close Condition

`closePosition` is computed by a lightweight funding-aware planner.

The base target is spread disappearance:

- `closePosition = closePositionFloorPct`
- initial default is `0%`

Then the planner evaluates funding:

1. Prefer predicted/next funding fields if available:
   - `net_funding_next_hourly_pct`
   - `net_funding_next_daily_pct`
   - or raw next funding rates normalized by each side's settlement interval.
2. If predicted funding is unavailable, fall back to current normalized funding:
   - `net_funding_hourly_pct`
   - `net_funding_daily_pct`
   - or raw current funding rates normalized by each side's settlement interval.
3. If net funding is favorable or unknown, keep the base spread-disappearance target.
4. If net funding is unfavorable, raise the close threshold by the unfavorable funding cost estimate:
   - `candidate = closePositionFloorPct + abs(unfavorableFundingPct) * unfavorableFundingWeight`

The funding calculation must respect different settlement cycles:

- Buy-side funding rate is divided by `buy_funding_interval_hours`.
- Sell-side funding rate is divided by `sell_funding_interval_hours`.
- Net hourly funding is `sell_hourly - buy_hourly`.
- Daily values are derived from hourly values, not from a fixed 8-hour assumption.

Astro validity is enforced after the funding calculation:

- `closePosition` must be lower than `openPosition`.
- If the calculated close value is greater than or equal to open, use `max(openPosition - closePositionBufferPct, 0)`.

The preview response should explain which source was used:

- predicted funding, current funding, or unknown funding
- buy/sell funding intervals
- normalized hourly or daily net funding
- whether the close threshold was adjusted to satisfy Astro validation

## Frontend

Settings page:

- Add an "Astro card defaults" section.
- Let the user edit and save the global defaults listed above.
- Values are loaded from the backend settings endpoint and saved with dashboard password protection.

Preview/create modal:

- Show generated `openPosition`, `closePosition`, `maxTradeUSDT`, `leverage`, `minNotional`, and `maxNotional`.
- Let the user edit position sizing values before creating a card.
- Add an option to save the edited position sizing values as the global default.
- Keep threshold editing out of the first version unless needed after testing; threshold generation should be visible and explainable first.

## Backend

Add a settings model and repository methods for Astro card defaults.

Add settings API endpoints:

- `GET /api/settings/astro-card`
- `PUT /api/settings/astro-card`

Update Astro planning so it receives:

- the opportunity
- the persisted Astro card defaults
- optional per-request overrides from the preview/create modal

Both alert auto-create and realtime one-click create use the same planner.

## Error Handling

If funding data is missing:

- Do not block card creation.
- Use `closePositionFloorPct`.
- Add a warning explaining that funding-aware adjustment could not be applied.

If saved defaults are invalid:

- Backend validation rejects them.
- Frontend shows the validation error.

If the generated Astro payload is invalid:

- Do not submit to Astro.
- Return blockers in preview/create response.

## Testing

Backend tests:

- Persisting and reading Astro card defaults.
- Env/config fallback when no saved settings exist.
- `openPosition` uses current opportunity open spread.
- Favorable predicted funding keeps close target at spread-disappeared floor.
- Unfavorable predicted funding raises close threshold.
- Funding normalization uses each side's settlement interval.
- Current funding fallback works when predicted funding is unavailable.
- Astro validation adjusts close below open when needed.
- Alert-created and realtime-created cards use the same defaults/planner.

Frontend tests:

- Settings page loads and saves Astro card defaults.
- Preview/create modal shows generated values.
- Editing position sizing values affects the create request.
- "Save as global default" persists position sizing defaults.

