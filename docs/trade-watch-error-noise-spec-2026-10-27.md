# 交易可用性观察项报错降噪：任务书（Claude，2026-10-27，待用户确认）

来源：M2-1 交接 `docs/task-handoff-2026-10-25-trade-availability-cache.md` §3.1（分支 `codex/m2-1-cache`）。那次调查只在本机复现，没有确认线上根因；本任务书补上了线上证据。基线：`f7f56a9`（当前生产版本）。

## 1 线上证据（2026-10-27 09:50 CST，只读）

- 最近 1 小时 `RuntimeError: 聚合行情中未找到指定原始市场` 出现 **174 次**，每次都带完整 traceback。按观察项统计：

| 次数 | 观察项 | 原因 |
|---|---|---|
| 161 | `bitget / future / STGUSDT`（dex 为空，创建于 2026-09-28） | **Bitget 已下架**：公开接口 `/api/v2/mix/market/contracts?symbol=STGUSDT` 返回 `40309 The symbol has been removed`；聚合行情里也没有这个市场。每 20 秒报错一次 |
| 9 / 3 / 1 | Hyperliquid `W` / `ZETA` / `SAGA`（dex=main） | 偶发，当前 `last_error` 为空，同期日志中有 `httpx.ConnectTimeout`，属于上游瞬时失败 |

- 结论：线上的主因是 §3.1 中的“候选为空 / 真实下架”。现有 4 个观察项的身份字段都是规范形式（exchange 小写、HL dex=main），**没有证据表明身份规范化有问题**，所以这次不改规范化逻辑。

## 2 现状（`backend/app/services/trade_availability.py`）

- `TradeAvailabilityMonitor.check_once`（1596 行起）：找不到 market 时抛出 `RuntimeError("聚合行情中未找到指定原始市场")`（1622 行）。1695–1705 行的 `except Exception` 每次都调用 `logger.exception` 打印完整 traceback，并把 `last_error`、`last_checked_at`、`updated_at` 写入数据库。
- `fetch_status` 返回结果里的 `result.errors`（`dict[str, str]`，模型在 `app/models/trade_availability.py:158`）**没有被 monitor 读取**，所以诊断阶段的具体原因（例如 HL 上游未返回指定市场）会被通用文案覆盖。

## 3 目标与设计（最小改动）

1. **日志降噪**：同一个观察项，只有当本次的错误文本与 `watch.last_error` 不同时（第一次出错或原因改变）才调用 `logger.exception`；错误文本相同时改为 `logger.debug`，并且不带 traceback。恢复成功时（`last_error` 被清空），用 `logger.info` 打印一行“已恢复”。数据库写入（`last_checked_at`/`last_error`/`updated_at`）的时机和内容保持不变。
2. **保留具体原因**：找不到 market 且 `result.errors` 非空时，错误文本改为 `聚合行情中未找到指定原始市场；诊断：<按 key 排序后的第一条 error，截断到 200 字符>`；`result.errors` 为空时，文案保持原样。异常类型仍是 `RuntimeError`。
3. 不做：自动停用或删除观察项、改轮询间隔、身份规范化（§3.1 的建议 1、2）、改前端。

## 4 文件范围与测试

- 修改：`backend/app/services/trade_availability.py`（只改 `check_once` 中 1610–1622 行附近的匹配失败分支，以及 1695–1705 行的异常块）
- 新建：`backend/tests/test_trade_availability_monitor_errors.py`、`docs/task-handoff-2026-10-27-trade-watch-errors.md`
- 禁止修改：其他所有文件。

测试（先写；fake status_service，不联网，用 `caplog` 断言日志）：
1. 同一个观察项连续 3 次因同一原因失败：只有第 1 次有 ERROR 级别且带 traceback 的日志，第 2、3 次没有；每次 `upsert` 的 `last_error`、`last_checked_at` 与旧实现相同。
2. 失败原因改变（A → B）时，B 第一次出现会再打一次 ERROR。
3. 先失败后恢复：`last_error` 被清空，有一条 INFO 级别的“已恢复”日志，原有的恢复通知、事件和转账查询逻辑不变（沿用 `tests/test_trade_availability.py` 中已有的恢复用例做回归）。
4. 找不到 market 且 `result.errors={"k2": "x", "k1": "RuntimeError: Hyperliquid 公开接口未返回指定 DEX 原始市场"}` 时，`last_error` 为 `RuntimeError: 聚合行情中未找到指定原始市场；诊断：RuntimeError: Hyperliquid 公开接口未返回指定 DEX 原始市场`；`errors` 为空时文案不变；超长 error 截断到 200 字符。
5. 多个观察项互不影响：A 持续失败不会压制 B 第一次失败时的 ERROR 日志。
6. 回归：`tests/test_trade_availability.py`、`tests/test_trade_availability_cache.py`、`tests/test_opportunity_trade_availability.py`，以及后端全量（`--basetemp=<worktree 内目录>`）。

## 5 上线与运维（部署需用户批准）

- 不影响内存，可以和 M2-3a/M2-3b 一起排队，但仍然逐个上线。
- **不必等代码上线的运维动作（由用户决定）**：`STGUSDT` 在 Bitget 已下架，可以在前端交易状态页停用或删除这个观察项，这样能立即消除约 93% 的报错日志。
- 上线后观察 1 小时：同一个观察项的 ERROR 日志只出现一次；`last_error` 仍然能在页面上看到。
- 回滚：revert 提交。

## 6 派发

- Codex：gpt-6.1-sol xhigh（单文件，边界清晰）。worktree `.worktrees/trade-watch-errors`，分支 `codex/trade-watch-errors`，基于 `f7f56a9`。
