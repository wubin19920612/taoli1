# crossex-exec：Bitget 无设置记录的合约也自动设 3 倍（2026-11-18）

## 问题
`tools/crossex_exec/accounts.py`：`check_instruments()` 对 Bitget 腿调用 `_bitget_leverage(symbol)`。该函数在 `GET /api/v3/account/settings` 的 `symbolConfigList` 里**找不到该合约的行**时直接 `raise ValueError('leverage_set_skipped')`，所以从没被设置过的合约（实盘 11-18 11:25 的 MMTUSDT）永远不会进入 `ensure_opening_leverage` 自动设置。而且这一步不在 ensure 里，没有 `LEVERAGE_OUTCOME` 诊断事件。

## 要做
1. `_bitget_leverage(symbol, deadline=None, *, allow_missing=False)`：
   - 当 `allow_missing=True` 且该合约 **0 行**时返回 `None`（表示"未知/未配置"）；
   - 1 行但 `marginMode != 'crossed'`（逐仓）、多行、字段异常、settings 读取失败、账户模式校验失败：行为不变，仍 `leverage_set_skipped`；
   - 默认 `allow_missing=False` 时行为与现在完全一致。
2. `check_instruments()` 的 Bitget 分支用 `allow_missing=True`；返回 `None` 时把该腿当作杠杆不符加入 `mismatches`（与 CrossEx 一致）。
3. `ensure_opening_leverage()` 首次读取（`before`）对 Bitget 用 `allow_missing=True`；`None` 时照常走 `_LeverageAttempt.set_and_confirm`（Bitget `set-leverage` 带 `marginMode='crossed'`，已有实现）。**回读必须严格**：用 `allow_missing=False`，必须恰好 1 行、`crossed`、杠杆 = 3，否则 `readback_mismatch` → `leverage_set_failed`。审计 `before` 记 `{"leverage": null, "marginMode": null}`（不要伪造 crossed）。
4. 诊断：`check_instruments()` 中 Bitget 设置检查导致的 `leverage_set_skipped`，要在 engine 调用处（`_ingest` 里调用 `check_instruments` 的地方）记录一条 `LEVERAGE_OUTCOME` 事件，字段与 ensure 里的一致：`exchange='bitget'`、`symbol`、`step='check_instruments'`、`outcome='leverage_set_skipped'`、`stage=None`，以及一个白名单的 `cause`：`settings_read_failed` / `settings_invalid` / `not_crossed` / `duplicate_rows` / `invalid_leverage`（由 `_bitget_leverage` 通过一个简单的属性或返回的异常属性传出，不要 `str(error)`，不得含密钥）。诊断写入失败不得改变拒单结果。
5. 其它行为不变：不改下单、限额、停开锁、限频、冷却、每小时上限、5 秒时限；不改 transport.py；逐仓合约仍不自动切换。

## 验收
- 新测试（加到 tools/crossex_exec/tests/test_auto_leverage.py）：
  - Bitget 合约无行 → 发一次 set-leverage（marginMode=crossed, leverage=3）→ 回读 1 行 crossed/3 → 本次复核继续。
  - 无行 → 设置后回读仍无行 → `leverage_set_failed`，无停开锁、不调 balance。
  - 无行 → 回读为 isolated 或 5 倍 → `leverage_set_failed`。
  - 逐仓 / 多行 / settings 读取失败 → 不发写，`leverage_set_skipped`，且有 `LEVERAGE_OUTCOME`（step=check_instruments，对应 cause）。
  - 已有 crossed/3 → 不发写。
  - 默认 `allow_missing=False` 的调用方行为不变（已有测试保持通过）。
- 全量：PYTHONPATH 设为 tools/crossex_exec/tests，`python -m pytest tools/crossex_exec/tests -q -p no:cacheprovider` 全绿，不得放宽已有断言。
- 只改 tools/crossex_exec/accounts.py、engine.py（诊断记录处）和 tests/test_auto_leverage.py；不要 git commit；不要联网、不要对真实交易所发请求。
- 报告：改动文件、行号、测试汇总行。
