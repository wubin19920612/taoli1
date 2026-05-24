# Astro Alert Auto Card Design

## Context

The project already has a safe Astro SDK dry-run path:

- `AstroSdkClient` can sign SDK requests and call `list`.
- `AstroPairPlanner` can convert a live opportunity into a proposed Astro pair payload.
- `/api/astro/preview/{opportunity_id}` lets the frontend preview the card payload.
- The alert loop creates Feishu notifications and stores `AlertEvent` records when rules match live opportunities.

The next step is an alert follow-up action: when an alert fires, the backend should use the Astro SDK to create or update an Astro card for that symbol/opportunity.

Because this touches trading configuration, the first version must be safe by default. It creates or updates a paused card only. It must not enable trading automatically.

## Goals

- When an alert matches an opportunity, optionally create or update a corresponding Astro card through the SDK.
- Keep the created/updated Astro card paused and unable to open positions:
  - `status=false`
  - `disableOpen=true`
- Preserve normal alert behavior if Astro is unavailable or returns an error.
- Avoid duplicate card creation across repeated alerts and backend restarts.
- Surface the Astro action result in alert history and Feishu message text.
- Keep the existing dry-run preview workflow available.

## Non-Goals

- Do not automatically enable live trading.
- Do not submit unsupported pair types.
- Do not overwrite a same-name Astro card that appears to describe a different type or exchange route.
- Do not implement a full Astro card management UI in this iteration.
- Do not rely on undocumented SDK fields for UI-only options such as group, slow open mode, rush mode, or A-leg-first behavior.

## SDK Endpoint

Use the SDK pair endpoint documented by Astro:

- Path: `/{admin_prefix}/api/config/sdk-update-pair`
- Method: `POST`
- Auth headers:
  - `x-timestamp`
  - `x-nonce`
  - `x-sign`
- Actions:
  - `list`
  - `add`
  - `update`
  - `delete`

The SDK document notes that `add` restarts `astro-core` and recommends waiting about 3 seconds. It also documents a rate limit of 20 requests per 10 seconds. This feature should call Astro sequentially and avoid bulk create bursts in the alert loop.

## Configuration

Add one explicit opt-in switch:

```env
ASTRO_ALERT_AUTO_CREATE=false
```

The backend may call Astro `add` or `update` only when all of these are true:

- `ASTRO_ALERT_AUTO_CREATE=true`
- `ASTRO_DRY_RUN_ONLY=false`
- Astro SDK config is complete:
  - `ASTRO_SDK_BASE_URL`
  - `ASTRO_ADMIN_PREFIX`
  - `ASTRO_API_KEY`

Default behavior remains safe:

- Auto-create is disabled.
- Dry-run remains enabled unless the operator explicitly changes it.

Existing Astro defaults are reused for card parameters:

- `ASTRO_DEFAULT_MAX_TRADE_USDT`
- `ASTRO_DEFAULT_LEVERAGE`
- `ASTRO_DEFAULT_MIN_NOTIONAL`
- `ASTRO_DEFAULT_MAX_NOTIONAL`

## Card Field Mapping

The first version maps only fields documented by the Astro SDK and already previewed by the dry-run planner.

| Frontend Field | SDK Field | Value |
| --- | --- | --- |
| Coin/name | `name` | Base asset name, e.g. `BTCUSDT -> BTC` |
| Status | `status` | Always `false` |
| Type | `type` | Supported opportunity type, initially `SF` and `FF` |
| Open spread | `openPosition` | Opportunity open spread percent divided by 100 |
| Close spread | `closePosition` | Opportunity close spread percent divided by 100 |
| Position limit | `maxTradeUSDT` | Config default |
| Leverage | `leverage` | Config default |
| Exchange A/B | `buyEx` / `sellEx` | Opportunity buy/sell exchanges |
| Start time | `startTime` | `"0"` |
| Min single notional | `minNotional` | Config default |
| Max single notional | `maxNotional` | Config default |
| Disable open | `disableOpen` | Always `true` |
| Disable close | `disableClose` | `false` |

Fields visible in the Astro frontend but not clearly documented in the SDK pair payload are intentionally not sent in version 1:

- group
- open-full disable-open
- slow open mode
- rush mode
- submit B leg only after A leg fills

## Supported Types

The first version supports the existing planner's documented types:

- `SF`
- `FF`

`SS` remains blocked because the SDK document and the observed frontend type list do not confirm `SS` support. Other types such as `FS`, `SR`, and `FR` can be added later after their local opportunity semantics and SDK mapping are verified.

## Idempotency And Duplicate Handling

Before submitting a card, the backend calls `list`.

Matching rules:

- If no existing pair has the same `name`, call `add`.
- If an existing pair has the same `name`, `type`, `buyEx`, and `sellEx`, call `update`.
- If an existing pair has the same `name` but different `type`, `buyEx`, or `sellEx`, skip the Astro write and record a clear conflict message.

This avoids creating duplicate cards and avoids silently overwriting a manually configured card for the same base asset.

The planner currently uses base asset names, such as `BTC`, because the Astro UI and SDK examples use coin names in that form. This does mean same-asset multi-route cards can conflict in version 1. The conflict is intentional and safe; a later version can introduce a naming mode after confirming how Astro treats names.

## Backend Components

### AstroSdkClient

Extend `AstroSdkClient` with pair mutation methods:

- `add_pair(pair)`
- `update_pair(pair)`

Both methods use the existing signed `_post` implementation and validate `code == 0`.

### AstroAlertService

Add a focused service responsible for alert follow-up actions.

Inputs:

- Opportunity
- Astro planner
- Astro SDK client
- Settings

Output:

- Structured action result with fields such as:
  - `enabled`
  - `status`
  - `action`
  - `message`
  - `pair_name`
  - `pair_type`

Possible statuses:

- `disabled`
- `skipped`
- `created`
- `updated`
- `failed`

The service handles all Astro exceptions internally and returns a failed result instead of raising into the alert loop.

### Alert Loop Integration

For each alert match:

1. Build the normal alert message.
2. Run the Astro alert follow-up service.
3. Append the Astro result to the message.
4. Send Feishu using the appended message, or update the Feishu notifier path so the same text is sent.
5. Store the same final message in `AlertEvent`.

Astro failure must not prevent Feishu send or alert event creation.

## Message Examples

Append one concise line to the alert text:

- `Astro: 已创建暂停卡片 BTC FF binance->okx，禁开=true`
- `Astro: 已更新暂停卡片 BTC FF binance->okx，禁开=true`
- `Astro: 已跳过，SS 类型暂不支持`
- `Astro: 已跳过，Astro 已存在同名 BTC 但类型或交易所不同`
- `Astro: 创建失败，Astro HTTP 429: rate limit`

## Error Handling

- SDK config incomplete: return `disabled` or `skipped`, depending on whether auto-create is enabled.
- `ASTRO_DRY_RUN_ONLY=true`: skip writes and report that dry-run mode is active.
- Planner blockers: skip and include the blocker text.
- SDK `list` failure: return failed result, do not attempt `add` or `update`.
- SDK `add/update` failure: return failed result.
- Existing same-name conflict: skip without mutation.
- Successful `add`: wait about 3 seconds before any subsequent add in the same alert loop iteration.

## Testing

Add backend tests first.

`AstroSdkClient` tests:

- `add_pair` sends `{"action":"add","pair":...}` with the existing signature scheme.
- `update_pair` sends `{"action":"update","pair":...}`.
- Nonzero SDK response raises `AstroClientError`.

`AstroAlertService` tests:

- Auto-create disabled does not call Astro.
- Dry-run mode skips writes.
- Unsupported type returns skipped result.
- No existing pair creates a paused, disable-open card.
- Existing same-name same-route pair updates a paused, disable-open card.
- Existing same-name different-route pair skips without mutation.
- SDK errors return failed result and do not raise.

Alert loop/API tests:

- When Astro action succeeds, alert event message includes the Astro result.
- When Astro action fails, alert event is still created and contains the failure result.

Existing verification commands should remain green:

```powershell
python -m pytest backend/tests -q
npm test
npm run build
```

## Rollout

1. Ship code with `ASTRO_ALERT_AUTO_CREATE=false`.
2. Verify preview behavior still works.
3. Configure Astro SDK credentials.
4. Set `ASTRO_DRY_RUN_ONLY=false` and `ASTRO_ALERT_AUTO_CREATE=true` only when ready to create paused cards.
5. Trigger one controlled alert and confirm the Astro card appears paused and disable-open.
6. Keep live enablement as a separate future decision.
