# Astro Manual Card Create Design

## Context

The realtime opportunities page already has an Astro dry-run button per row. It opens a modal that shows the planned Astro pair payload and field assumptions. Alert-triggered Astro card creation now exists as a safe backend service that creates or updates paused, disable-open cards.

The manual card creation feature should reuse the same backend safety rules while giving the operator one final preview before submitting to Astro.

## Goals

- Let an operator create or update an Astro card from a realtime opportunity.
- Keep the first version safe:
  - `status=false`
  - `disableOpen=true`
- Preserve the current preview flow and add a confirm action inside the preview modal.
- Keep manual card creation independently controllable from alert auto-create.
- Fix Astro update payloads to include the existing pair `id` returned by SDK `list`.

## Non-Goals

- Do not enable live trading.
- Do not submit unsupported types such as `SS`.
- Do not overwrite same-name cards with different type or exchange route.
- Do not add a full Astro card management screen.

## Configuration

Add a manual write switch:

```env
ASTRO_MANUAL_CARD_CREATE=false
```

Manual card writes require:

- `ASTRO_MANUAL_CARD_CREATE=true`
- `ASTRO_DRY_RUN_ONLY=false`
- complete Astro SDK configuration

Alert auto-create remains controlled separately by:

```env
ASTRO_ALERT_AUTO_CREATE=false
```

This allows manual confirmed card creation without enabling alert-driven automation.

## Backend API

Add:

```http
POST /api/astro/opportunities/{opportunity_id}/card
```

The route requires the dashboard password header, matching `GET /api/astro/pairs`.

Behavior:

1. Find the opportunity in `snapshot_store`.
2. Plan the pair payload with `AstroPairPlanner`.
3. Submit through the shared safe Astro card service.
4. Return `AstroAlertActionResult`.

Result examples:

- `Astro: 已创建暂停卡片 BTC FF binance->okx，禁开=true`
- `Astro: 已更新暂停卡片 BTC FF binance->okx，禁开=true`
- `Astro: dry-run 模式开启，未写入 Astro`
- `Astro: 已跳过，Astro 已存在同名 BTC 但类型或交易所不同`

## Shared Submit Service

The current `AstroAlertService` should be generalized enough to support two modes:

- alert auto-create, gated by `ASTRO_ALERT_AUTO_CREATE`
- manual create, gated by `ASTRO_MANUAL_CARD_CREATE`

Both modes use the same idempotent write behavior:

- no same-name pair: `add`
- same `name/type/buyEx/sellEx`: `update`
- same name but different route or type: skip conflict

For `update`, the service must include the existing pair `id` from `list`. If the matching existing pair has no `id`, the service should return a failed/skipped result instead of submitting an invalid update.

## Frontend

The existing Astro preview modal gains a footer action:

- `创建暂停卡片` when there is no existing same-route card.
- `更新暂停卡片` may be returned after submit or after a future preflight. Version 1 may use a generic `创建/更新暂停卡片` label because the backend makes the final idempotent decision.

The button is disabled when:

- preview is loading
- there is no preview plan
- `plan.can_submit=false`
- a submit request is already in flight

On click:

1. Call `POST /api/astro/opportunities/{opportunity_id}/card`.
2. Show success/error message.
3. Keep the modal open and display the returned Astro result line.

## Testing

Backend:

- Config default and env override for `ASTRO_MANUAL_CARD_CREATE`.
- Manual disabled returns disabled and does not call Astro.
- Dry-run returns skipped and does not call Astro.
- Missing opportunity returns 404.
- Unsupported type returns skipped.
- Add creates paused disable-open card.
- Update includes existing pair `id` and keeps paused disable-open.
- Same-name conflict skips without mutation.

Frontend:

- Astro preview modal shows the submit button.
- Button is disabled when `can_submit=false`.
- Clicking submit calls the new API.
- Successful result is shown in the modal.

Verification:

```powershell
python -m pytest backend/tests -q
cd frontend
npm test
npm run build
```
