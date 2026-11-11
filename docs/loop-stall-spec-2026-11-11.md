# 主循环卡顿修复任务书 2026-11-11

设计稿（必读，业务规则以它为准）：`docs/loop-stall-fix-design-2026-11-11.md`。用户已拍板 D1–D4 全部按建议：F1+F2+F3 一起做；告警循环改用 async 过滤；验收完即部署；GC 参数不动。

## 1. 要做的
- **F1 去重复计算**（`services/market_validation_review.py`、`services/pair_validation.py`）
  - `validate(..., annotated=False)`：为 True 且 `_mutation_epoch` 与最近一次 `prepare()` 完成时一致，就跳过 `annotate()`；否则走原路径。
  - `validate_pair(..., quote_fingerprint=None)`：调用方已算过就直接复用。
  - 采集构建阶段（`collector.py` 约 262–276 行）和 `filter_opportunities` 的重算都传 `annotated=True`。先核对 `store.get_all_markets()` 存的就是 `prepare()` 的输出，并写测试固定这一点。
  - `_market_index()` 按快照发布版本缓存：store 每次发布版本号 +1，版本不变就复用。
- **F2 让出主循环**
  - 新增 `filter_opportunities_async(opportunities, risk, budget=None)`，`main.py` 告警循环（约 695 行）改用它；同步版保留。
  - `prepare()` 中大价差候选的组合循环和 collision 无效化循环加 `maybe_yield()`，只在 cooperative 尝试里加；第三次尝试保持无 await。
  - `compact_markets.pack_async(markets, budget)`：输出与 `pack` 完全相同；`_store_adapter_result` 改用它。若它是同步函数，就沿调用链改成 async，改动尽量小。
  - `multi_exchange_premium._scan_eligibility`：加让出点，调用链里挑改动最小的做法。
- **F3 观测**：告警循环每 300 秒打一行 JSON 日志 `{"alert_filter_timing":...}`，含次数、`filter_ms` 的 p50 和 max、重算条数。

## 2. 约束
- 只改上面提到的文件及其测试，外加 `services/snapshot_store`（或 store 所在文件）里的版本号。
- 不改复核的判定逻辑、原因码和能力；不改 `orderbook_validator.py`、`spread_engine.py` 的判定；不改 compose、部署脚本、`.env`、前端。
- 已有测试和断言不得放宽。不 push、不 SSH、不跑 WSL、不联网。按 test/feat/docs 分开 commit，不 amend。

## 3. 测试
- **对照**：构造真实规模的假快照（约 1.2 万行情、约 4000 机会），覆盖 collision、USDC 汇率、Gate `received_batch`/`ws`、过期绑定、shadow 和 enforce 两种模式。
  - 新旧路径的 `PairValidationResult` 除 `validated_at` 外逐字段相等。
  - `filter_opportunities` 与 `filter_opportunities_async` 输出相同。
  - 采集一轮发布的 markets、opportunities 与改前相同。
- **让出**：假时钟或计数下，`filter_opportunities_async`、`pack_async`、`prepare` 组合循环和 `_scan_eligibility` 都会按预算让出；`pack_async` 与 `pack` 的输出逐项相等。
- **缓存**：`_market_index` 版本不变时复用，发布后失效；epoch 变化时 `annotated=True` 退回完整路径。
- **基准**：同一份假快照，锁定 venv 跑 3 次。对比构建阶段复核总耗时、告警重算耗时、最长单段同步时间（两次让出之间的最大间隔），三项都写进交接文档。目标：前两项下降 ≥40%，最长单段 ≤200ms。
- **全量测试**：用锁定 venv
  `C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad/venv-lock/Scripts/python.exe`，
  在 `backend` 目录、`PYTHONPATH=.` 下运行
  `-m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/loop-stall-full`。
  贴原始汇总行，并单独贴新测试的汇总行。

## 4. 交付
交接文档 `docs/task-handoff-2026-11-11-loop-stall.md`，写清：
- 文件清单和提交 hash；
- 每个让出点的位置；
- 基准数字；
- 剩余风险。
