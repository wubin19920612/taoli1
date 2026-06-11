# Index Component Change Alerts Design

Date: 2026-05-27
Status: Approved for implementation
Scope: Detect exchange index-component changes, alert, persist changes, and expose a dedicated frontend page

## Context

The Astro Lite "指数/成分" column represents:

```text
100 * (mark price - index price) / index price
```

Clicking the displayed value opens the index composition behind that exchange-symbol index price. The current radar only stores `mark_price`, `index_price`, and the derived `MARK_INDEX_DEVIATION` risk label. It does not store the component list used to calculate the index price.

Some symbols can have changing index constituents. Those changes are operationally important because the mark/index relationship can move for structural reasons rather than normal market drift.

## Goals

- Detect index-component changes per `exchange + symbol`.
- Alert when a known index composition changes.
- Persist a current baseline and an append-only change history in SQLite.
- Add a dedicated frontend page for index-component change records.
- Keep this separate from spread opportunity history and user-configured spread alert rules.
- Avoid blocking normal market collection when an index-component source is unavailable.

## Non-Goals

- Do not replace the existing mark/index deviation risk label.
- Do not require index composition support from every exchange in v1.
- Do not infer exact component composition from only `index_price`.
- Do not block normal alerting or opportunity collection when component fetch fails.

## Architecture

Add an `index_components` feature path with four layers:

- Models: normalized component snapshots and change records.
- Repository: `index_component_snapshots` for latest baselines and `index_component_changes` for history.
- Service: normalize components, compute stable hashes, compare current composition against the last baseline, persist changes, and send alert text.
- API/UI: list changes and snapshots, and expose a dedicated frontend page.

The provider interface is intentionally small:

```python
async def fetch_components(markets: list[MarketSnapshot]) -> list[IndexComponentSnapshot]
```

The first implementation can parse component payloads from supported sources and safely return an empty list when the source has no component data. This keeps the rest of the feature testable before every exchange-specific endpoint is known.

## Change Rules

- The identity key is `exchange + symbol`.
- The first successful component snapshot creates a baseline and does not alert.
- A later snapshot with the same component hash updates `last_seen_at` and does not alert.
- A later snapshot with a different component hash:
  - stores an `index_component_changes` row,
  - updates the baseline snapshot,
  - emits a Feishu alert when configured,
  - surfaces the record on the dedicated frontend page.

Component hashing uses normalized JSON with sorted keys and sorted components. Components are sorted by source and symbol so equivalent payload ordering does not trigger false changes.

## Data Model

`IndexComponent`:

- `source`: component source or exchange venue name.
- `symbol`: component symbol.
- `weight`: optional numeric weight.
- `price`: optional numeric price.
- `extra`: source-specific metadata.

`IndexComponentSnapshot`:

- `exchange`
- `symbol`
- `components`
- `component_hash`
- `source`
- `observed_at`

`IndexComponentChange`:

- `id`
- `exchange`
- `symbol`
- `old_hash`
- `new_hash`
- `old_components`
- `new_components`
- `added_components`
- `removed_components`
- `changed_components`
- `source`
- `alert_status`
- `created_at`

## Frontend

Add a sidebar entry named `指数成分变更`.

The page shows a compact table:

- time in UTC+8,
- exchange,
- symbol,
- summary counts,
- source,
- alert status,
- expandable component diff.

Filters:

- symbol text input,
- exchange text input,
- refresh button.

## Testing

Backend tests cover:

- schema initialization creates new tables,
- repository baseline and change list behavior,
- hash stability despite component ordering,
- no alert on first baseline,
- change detection records added/removed/changed components,
- API returns change records.

Frontend tests cover:

- page fetches and renders change records,
- filters are included in the API request,
- expanded rows show added/removed/changed component details.

