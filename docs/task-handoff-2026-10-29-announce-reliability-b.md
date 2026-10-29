# 公告抓取可靠性 B 交接（2026-10-29）

## 目标、范围和基线

- 按 `docs/announce-reliability-b-spec-2026-10-29.md` §2 实现中文源超时预算、缓存合并和轮询失败日志去重。
- 工作区：`.worktrees/announce-b`；分支：`codex/announce-reliability-b`。
- 开始时 HEAD：`fbd7678`（任务书提交）；其父提交及任务书代码基线为 `dd981ae`。
- 开始时工作区干净。本次仅修改两个服务文件，新增本测试文件及交接文档，共四个文件；已有测试未修改。
- 用户要求离线执行，未使用 WSL、未联网；本次只做本地提交，不推送、不部署。

## 已完成功能和关键入口

### `backend/app/services/announcement_localization.py`

- 模块常量 `LOCALIZATION_SOURCE_TIMEOUT_SECONDS = 2.5` 同时约束 `_fetch` 的 `asyncio.timeout` 与 HTTPX 各阶段预算，connect 仍取 `min(3, timeout_seconds)`。
- `_refresh` 将本轮翻译合并到旧缓存，同 key 的新结果覆盖旧结果，其余旧条目保留。
- 缓存重试时间按本轮结果计算：有翻译结果时 120 秒，没有翻译结果时 30 秒；不会因保留旧缓存而把全失败后的重试延长到 120 秒。
- `_source_errors` 是服务实例内按 URL 保存的异常类型：首次或类型变化记 WARNING，相同类型重复记 DEBUG，成功解析后记一条 INFO 恢复日志并清除该 URL 状态。

### `backend/app/services/announcement_polling.py`

- `poll_source` 闭包内保存该 provider 的失败状态，抓取/处理与可选本地化分别累计，互不清除；无全局错误状态。
- 错误签名包含异常类型，`httpx.HTTPStatusError` 额外包含响应状态码。
- 首次失败或签名改变时 WARNING 带 traceback；相同签名的重复 WARNING 不带 traceback，并记录 `consecutive_failures=N`。
- 连续失败次数跨签名变化继续累计；对应阶段成功时 INFO 记录 `recovered after N failures`，随后清除签名和计数。
- 本地化失败仍记录 `localize_error` 并继续处理原始公告；抓取失败仍走原有 observation/error 流程。
- `_record_poll`、history 聚合、轮询间隔、提醒循环、抓取外层 20 秒与本地化外层 3 秒均未修改。原有宽异常隔离仅将日志转交闭包助手，保留对应 Ruff 定点说明。

## 测试与复核

先新增 `backend/tests/test_announce_reliability_b.py`，运行后才修改服务代码。

- RED：`python -m pytest -q tests/test_announce_reliability_b.py --basetemp=../.pytest-tmp/red` → `11 failed, 1 passed in 4.74s`。
- GREEN：`python -m pytest -q tests/test_announce_reliability_b.py --basetemp=../.pytest-tmp/green` → `12 passed in 3.03s`。
- §3 公告回归：将 `tests/test_announcement*.py` 的五个文件显式展开后传入 pytest，使用 `--basetemp=../.pytest-tmp/regression` → `105 passed in 2.47s`。
- Ruff：两个服务文件与新增测试文件 → `All checks passed!`。
- 后端全量：在 `backend` 下运行 `python -m pytest -q --basetemp=../.pytest-tmp/x`，退出码 0，原始汇总行如下：

  ```text
  2241 passed, 6 deselected, 12 warnings in 152.50s (0:02:32)
  ```

  12 条警告来自 `tests/test_api.py` 和 `tests/test_index_components.py` 的 `datetime.utcnow()` 弃用提示；未改动相关模块。
- AST 对比确认 `poll_state_key`、`_record_poll`、`_wait` 和 `poll_reminders` 与任务开始时完全一致。
- `git diff --check`：通过，提交前再次核对。

新增测试使用 HTTPX MockTransport、fake provider、内存 repository 和可注入时钟；包含实际 2.5 秒慢源取消、全失败保留缓存及 30 秒重试、部分源成功覆盖、按 URL/类型去重、取消传播、两种轮询日志的去重与恢复、HTTP 500→503 签名变化、provider 状态隔离和 observation/history 兼容性。现有 `conftest.py` 离线防护保持启用，项目配置默认排除 network/benchmark 标记。

## Git、线上状态和残余风险

- 提交信息按用户指定，末尾含 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。交付提交为本文件所在提交，可用 `git log -1 --format=full` 查看完整信息。
- 未推送、未读取或修改生产环境；未执行备份、部署和线上接口验收。任务书要求上线另行批准。
- 离线测试证明本地逻辑，未验证真实中文源延迟或线上日志；旧缓存条目按任务书保留，未新增淘汰策略。
- 按用户要求在 `backend` 下用 PowerShell `New-Item -ItemType Directory -Force -Path ../.pytest-tmp` 创建临时目录（等价于 `mkdir -p ../.pytest-tmp`）。全量测试会在 `.pytest-tmp/x/` 下生成未跟踪测试数据库；这些产物保留且不暂存。

## 后续

如需上线，应在另一个运维任务中申请批准，再按部署文档完成推送、镜像、数据库备份和线上验收。其他模块在新任务中继续，以本交接与任务书作为入口。
