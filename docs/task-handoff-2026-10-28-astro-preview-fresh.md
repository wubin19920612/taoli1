# Astro 人工预览/创建 fresh 双腿复核交接

## 目标、分支和范围

- 工作树：`.worktrees/astro-preview-fresh`；分支：`codex/astro-preview-fresh`。
- 基线：`74c8f2300b0f13a19993318824d12bbfa9dc25a0`。
- 依据：`docs/task-astro-preview-fresh-review-20261028.md`，含文末补充授权。
- 在上一轮未提交修改上继续完成 3 个失败用例；用户要求仅本地 commit，不 push、不部署。

## 实现和关键入口

- `backend/app/api/routes_astro.py`：人工预览和创建通过 `_review_manual_opportunity` 取得 fresh 双腿，重建价差并保留原始市场、DEX、费用和比较类型；复核异常产生中文阻断，预览 HTTP 200。
- `backend/app/services/market_validation_review.py`：增加默认 False 的 `fetch_books_when_unverified`，仅人工入口开启。参数进入 inflight key，避免人工请求和默认请求互相复用不同策略的结果；沿用原有 semaphore 和请求合并。
- `backend/app/services/astro_alerts.py`：仅人工创建的前置校验改用 fresh 结果重建机会；自动路径以及原有提交前 `fresh=True` 复核保持原样。
- `backend/app/services/astro_planner.py`：校验阻断按 message 去重，保留上一轮现有修改。
- 测试入口：`test_astro_preview_fresh.py`；原有 `test_api.py` 和 `test_astro_instrument_routes.py` 的 mock/盘口 fixtures 适配 fresh 调用，不放宽原断言。

## 业务边界

- 未核验身份/单位仍保留最终阻断，manual override 不能清空硬阻断。
- 不修改 `pair_validation.py`、`market_identity_catalog.py`、前端、部署文件或环境配置。
- 后台未核验复核、默认 fresh 复核及自动建卡不增加盘口请求；新增测试断言 fetch 未调用。
- 新增并发测试覆盖相同人工请求合并，以及默认请求与人工策略隔离。
- 外部 Astro client 在测试中为 mock，无真实建卡或交易。

## 实际验证记录

以下命令均在本工作树 `backend` 下执行。

```text
python -m pytest tests/test_astro_preview_fresh.py -q --basetemp=.pytest_preview_fresh_repro -p no:cacheprovider
3 failed, 11 passed in 1.42s

python -m pytest tests/test_astro_preview_fresh.py -q --basetemp=.pytest_preview_fresh_fixed -p no:cacheprovider
14 passed in 4.04s

python -m pytest tests/test_astro_preview_fresh.py -q --basetemp=.pytest_preview_fresh_regression -p no:cacheprovider
23 passed in 7.11s

python -m pytest tests -q -k "astro or validation or instrument" --basetemp=.pytest_preview_fresh_a -p no:cacheprovider
571 passed, 1499 deselected in 39.23s

python -m ruff check app/api/routes_astro.py app/services/astro_planner.py
All checks passed!

python -m pytest tests -q --basetemp=.pytest_preview_fresh_full -p no:cacheprovider
2066 passed, 4 deselected, 12 warnings in 116.14s (0:01:56)
```

`git diff --check` 已通过（无输出）。全量测试仅运行一次；仓库 `pyproject.toml` 默认排除 network/benchmark 测试，因此有 4 deselected。12 条警告为索引成分相关测试触发的 `datetime.utcnow()` 弃用警告，未扩大范围修改。

## 线上状态、产物与下一步

- 本任务未 push、未连接服务器、未备份或部署线上；本地测试不等于线上验收。
- 原有 `.pytest_preview_fresh_a` 已移存为 `backend/.pytest_preview_fresh_a_previous_20261028/`，保留上一轮证据。新测试产生的 `.pytest_preview_fresh_a/`、`.pytest_preview_fresh_full/` 等产物不提交。
- 用户提供的未跟踪任务书保持原样、不纳入代码提交。
- 本模块后续如需发布，应在新任务中基于本提交独立安排推送、备份、部署和线上验收。
