# WP-T1 / T1-1 后端交接

## 目标、范围与基线

- 分支：`codex/zuudot-quote-age`。
- 基线：`ae985da48c0e28135dca2ced9edb9f7bad072be1`。
- 本任务只实现请求时的逐腿报价年龄、列表过期过滤和配对实时窗口完整性。
- 未修改前端、数据库锁、collector 循环、market_identity、告警及部署文件。
- 按用户要求仅本地提交，不推送、不部署。

## 已实现与代码入口

- `backend/app/services/quote_age.py`：统一计算 `age_ms`、`source_age_ms`、`time_source`，将无时区时间按 UTC 解释，保留时间相减的符号。
- `backend/app/services/spread_engine.py`：构造 Opportunity 时保存两腿的本地采集时间和来源时间；`opportunity_with_ages()` 在请求时返回带年龄的副本，不回写缓存。
- `backend/app/api/routes_opportunities.py`：按 RiskSettings 的 `stale_after_seconds`（默认 30 秒）逐腿过滤，然后排序、limit。
- `backend/app/services/opportunity_radar.py`：按配置的本地腿年龄（默认 120 秒）先排除超龄行情，再生成 peer 中位数、评分、排序及 limit。
- `backend/app/services/pair_spread_query.py`：组装 current 时重新计算两腿年龄；秒级实时序列返回窗口完整性。
- 对应 model 新字段均有默认值，旧构造参数仍可使用。

## API 与业务口径

- Opportunity：新增 `buy_age_ms` / `sell_age_ms`、`buy_source_age_ms` / `sell_source_age_ms`、`buy_time_source` / `sell_time_source`；以及供重算使用的 `*_local_timestamp` / `*_source_timestamp`。
- 雷达候选：新增 `long_age_ms` / `short_age_ms` 及对应 `*_source_age_ms`、`*_time_source`。
- PairSpreadCurrentLeg：新增 `age_ms`、`source_age_ms`、`time_source`。
- 年龄均在每次请求中根据同一次请求的时钟重新计算。过滤只比较本地年龄；`source_age_ms` 仅展示，可能为空或因上游时钟偏差为负。
- MarketSnapshot 来源时间优先用 `book_timestamp`，否则用 `upstream_timestamp`；book 来源标签保留 `quote_time_source`，否则为 `upstream` 或 `received`。配对当前腿使用其已有 `upstream_timestamp`，缺失则返回空来源年龄。
- 原 `buy_timestamp`、`sell_timestamp`、`last_seen_at` 的含义不变；本任务未放宽原有采集阶段和告警的其他校验。
- 旧 Opportunity 若没有新增的本地腿时间，保守地以 `last_seen_at` 计算两腿年龄，标记 `time_source="legacy"`，仍会在停采后过期。
- `/api/opportunities` 默认继续返回数组，并附带 `X-Excluded-Stale` 响应头。可选 `include_metadata=true` 返回 `{ "opportunities": [...], "excluded_stale": N }`，空数组时也能读取计数。
- 旧列表的 `excluded_stale` 统计全局风险设置过滤后、其他请求筛选前剔除的机会数，不受排序和 limit 影响。
- 雷达的 `excluded_stale` 位于原响应对象上，统计所选 anchor / peer 交易所支持的合约行情中、在分组和中位数之前排除的过期行情条数。
- 超龄均使用严格 `>`；恰好 30 秒 / 120 秒仍有效。
- 秒级配对实时响应新增 `expected_samples`、`actual_samples`、`max_gap_ms`、`complete`。历史 K 线响应的这些可选字段默认空值。
- `expected_samples = ceil(hours * 3600 / interval_seconds)`；`actual_samples = point_count`，按实际返回的窗口内样本计数；不补造缺失样本。
- `max_gap_ms` 包括窗口起点到首个样本、相邻样本、末个样本到请求时刻；无样本时等于完整窗口长度。窗口外的未来缓存点不参与返回或统计。
- `complete = actual_samples * 100 >= expected_samples * 95 AND max_gap_ms <= 3 * interval_seconds * 1000`。

## 验证

- 未找到项目或相邻 worktree 的锁定依赖虚拟环境，按用户允许的回退使用当前环境：Python 3.12.10、FastAPI 0.135.1、Pydantic 2.12.5、pytest 8.4.2。`backend/requirements.lock` 固定 FastAPI 0.162.0，因此尚未在锁定版本环境验证。
- 所有 pytest 命令都使用当前 worktree 下的 `--basetemp=.pytest_tmp_quoteage`。
- 相关测试命令：

  ```powershell
  python -m pytest backend/tests/test_quote_age.py backend/tests/test_spread_engine.py backend/tests/test_spread_engine_equivalence.py backend/tests/test_opportunity_radar.py backend/tests/test_pair_spread_query.py backend/tests/test_api.py -q -x --basetemp=.pytest_tmp_quoteage
  ```

- 相关测试最终汇总：`260 passed, 1 warning in 30.11s`。
- 新增测试覆盖停采 31 秒、阈值边界、逐腿过滤后排序和 limit、配置阈值、请求时重算及缓存不变、旧对象兼容、旧 peer 不进入中位数、双向雷达年龄、上游/book 来源、配对当前腿，以及 95% / 3 倍间隔 / 首尾缺口 / 空窗口边界。
- 全量命令：`python -m pytest backend/tests -q -x --basetemp=.pytest_tmp_quoteage`。
- 首轮全量：`1 failed, 1966 passed, 6 deselected, 12 warnings in 120.88s (0:02:00)`。失败为 `test_memory_probe.py::test_uvicorn_default_logging_emits_probe_json_to_container_stream`：当前环境的子进程将 `app` 解析到了另一个项目，导致 `ModuleNotFoundError: No module named 'src'`，与本次改动无关。
- 仅在测试进程的环境中把当前 worktree 的 `backend` 前置到 `PYTHONPATH` 后，该失败用例单独复验为 `1 passed in 1.75s`，随后重跑同一全量命令；未修改仓库或全局环境配置。
- 重跑全量汇总：`2574 passed, 6 deselected, 12 warnings in 156.20s (0:02:36)`。警告均为既有测试中的 `datetime.utcnow()` 弃用提示。
- 全量复跑的环境准备（仅当前 PowerShell 进程）：

  ```powershell
  $taskBackendPath = Join-Path (Get-Location) 'backend'
  $env:PYTHONPATH = if ($env:PYTHONPATH) { $taskBackendPath + [IO.Path]::PathSeparator + $env:PYTHONPATH } else { $taskBackendPath }
  python -m pytest backend/tests -q -x --basetemp=.pytest_tmp_quoteage
  ```

- `git diff --check` 通过。新增 helper 和测试的 Ruff 检查通过；受影响既有文件另有 5 个 Ruff 告警，已对照基线确认并非本次引入。

## 线上状态、保留文件与下一步

- 未推送、未备份线上数据库、未部署或进行线上验证；线上版本保持原状。
- 保留初始未跟踪的 `docs/zuudot-borrow-design-2026-11-01.md`，不纳入本次提交。
- `.pytest_tmp_quoteage/` 是本次测试的本地临时产物，不纳入提交。
- 下一模块请新建任务执行 T1-2 前端，按上述字段接入逐腿年龄、过期剔除计数与窗口不完整提示；旧列表可选用 `include_metadata=true` 读取 JSON 计数。
