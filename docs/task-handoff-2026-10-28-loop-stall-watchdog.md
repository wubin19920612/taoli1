# 事件循环卡顿看门狗交接（2026-10-28）

## 目标、范围与基线

- 任务书：`docs/task-loop-stall-watchdog-20261028.md`，执行文末“验收标准更正”。
- 工作区：`.worktrees/loop-stall-watchdog`；分支：`codex/loop-stall-watchdog`。
- 起始 HEAD：`73813d9b22c941a93a6cc4d87598c4f9774afad2`。
- 接续已有未提交的 `backend/app/main.py`、新模块和新测试；未回退原有实现。
- 交付范围仅新诊断模块、main 生命周期接入、新测试及本交接文档。未修改业务、采集、限频、部署配置或 `.env`。
- 用户明确要求不部署；本任务没有执行线上备份、SSH、容器更新或线上验收。

## 实现与入口

- `backend/app/runtime/loop_stall_watchdog.py:LoopStallWatchdog`：循环协程每 100ms 更新单调时钟心跳并记录循环线程 ID；daemon 观察线程正常每 100ms 检查心跳。
- 默认开启；`LOOP_STALL_WATCHDOG_ENABLED` 的 `0/false/off/no`（忽略大小写及两端空白）关闭整个线程。`LOOP_STALL_THRESHOLD_SECONDS` 默认 1.0 秒，非法、非有限或非正值回退到默认值。
- 心跳年龄超过阈值后立即采样，后续间隔 250ms；一次卡顿最多尝试 16 次，失败采样也消耗预算。
- 通过 `sys._current_frames()` 采集循环线程的栈；保留 app 帧及最内层最多 3 个外部帧，总共最多 20 帧，按外到内输出相对路径、行号和函数名。不记录源码、局部变量或异常详情，不保留 frame 引用。
- 心跳恢复后，专用 `loop_stall` logger 输出单行紧凑 JSON：`loop_stall`、`duration_ms`、`samples`、最多 3 组 `top` 栈及计数。`samples` 为成功采样数量。
- 滚动 600 秒内最多尝试输出 30 条卡顿日志；日志写入失败也计入预算。采样或日志失败后继续观察，每个实例最多尝试一次固定文本 warning。
- `backend/app/main.py:create_app`：与 `event-loop-lag` 同处注册，`critical=False`、`restart=False`；通过 `app.state.loop_stall_watchdog` 暴露实例；lifespan 结束先停止观察线程并 `join(timeout=1.0)`，再停止 supervisor。
- 本轮对新模块的宽泛异常捕获及测试中的故意阻塞逐处添加带原因的 Ruff 豁免，保留任务要求的故障隔离和真实阻塞测试。未新增全局忽略配置。

## 验证环境与结果

本地 Windows，Python 3.12.10、pytest 8.4.2、Ruff 0.16.8。以下验收命令均从 `backend` 执行。

```text
python -m ruff check app/runtime/loop_stall_watchdog.py tests/test_loop_stall_watchdog.py
All checks passed!

python -m pytest tests -q -k "watchdog or stall or supervisor or health or main" --basetemp=.pytest_watchdog_a -p no:cacheprovider
94 passed, 2073 deselected in 28.39s
```

`main.py` 使用 `git show 73813d9b22c941a93a6cc4d87598c4f9774afad2:backend/app/main.py` 提取基线，分别对基线 stdin（指定 `--stdin-filename app/main.py`）及现文件运行 Ruff JSON 输出，比较规则与消息的多重集并断言错误数均为 6。原始汇总：

```text
main.py baseline Ruff errors: 6
main.py current Ruff errors: 6
baseline: {'I001': 1, 'UP035': 1, 'RUF100': 3, 'SIM114': 1}
current: {'I001': 1, 'UP035': 1, 'RUF100': 3, 'SIM114': 1}
PASS: identical Ruff diagnostic codes/messages; no new errors
```

直接检查 `main.py` 的 Ruff 退出码仍为 1（6 条基线问题），按更正标准验收；新模块和测试检查退出码为 0。

全量测试原始汇总（退出码 0）：

```text
python -m pytest tests -q --basetemp=.pytest_watchdog_full -p no:cacheprovider
2163 passed, 4 deselected, 12 warnings in 142.78s (0:02:22)
```

12 条 warning 为 `datetime.datetime.utcnow()` 的 DeprecationWarning，来自 `test_api.py` 的一项指数成分接口测试和 `test_index_components.py`，本任务未改这些模块。`git diff --check` 无输出，退出码 0。

新增测试覆盖真实 `time.sleep(1.5)` 阻塞、日志唯一性和阻塞函数栈、健康循环无输出、环境开关、阈值回退、采样间隔和预算、top 分组、滚动限流、栈筛选和截断、采样/日志/warning 异常恢复、启用及禁用时的 lifespan 关闭和 join 超时。

## Git 交付及保留产物

- 提交信息：`feat(runtime): loop stall watchdog logs blocking stacks`。最终提交号从 `git log -1 --format=%H -- docs/task-handoff-2026-10-28-loop-stall-watchdog.md` 获取；最终推送回执及完整 SHA 随交付答复提供。
- 已成功远端预检：`refs/heads/codex/frontend-localization-polish` 为 `73813d9b22c941a93a6cc4d87598c4f9774afad2`；提交后、推送前必须再次核对；不匹配即停止推送。
- 按任务书依次执行 `git push origin HEAD:codex/loop-stall-watchdog` 和 `git push origin HEAD:codex/frontend-localization-polish`，不使用 force。推送后通过 `git ls-remote` 核对两个分支等于本地完整 HEAD。
- 首次沙箱内远端读取报 `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS (0x8009030e)`；使用获批权限后远端读取成功。
- 原有未跟踪任务书 `docs/task-loop-stall-watchdog-20261028.md` 保留，不加入本次提交。
- 本轮测试生成的 `backend/.pytest_watchdog_a/`、`backend/.pytest_watchdog_full/` 保留，不加入提交；其中测试数据库等产物未清理。

## 已知边界与下一步

- 这是诊断工具，不消除原有循环卡顿，不改变交易或采集行为。
- `duration_ms` 为恢复心跳与卡顿前最后心跳的差值，包含心跳调度粒度；观察线程也依赖操作系统调度和 Python GIL，不能保证捕获一直持有 GIL 的原生调用。
- 卡顿日志在心跳恢复后产生；恢复前进程退出或服务被强制终止，可能没有该次汇总。长卡顿最多保留前 16 次采样。
- 停止线程采用最多 1 秒的单次 join；若外部日志 handler 自身永久阻塞，不能保证观察线程立即退出，daemon 属性避免它阻止进程退出。
- 本地测试不代表 Linux 容器或生产验收。默认 pytest 配置排除显式标记为 network、benchmark 的用例。
- 由 Claude 在独立部署任务中核对最终提交和远端，按仓库部署文档完成数据库备份、镜像及服务更新，再观察 `loop_stall` 日志定位周期性阻塞点。后续性能修复另开任务，以本交接文档为入口。
