# Astro 人工建卡提速交接（2026-10-28）

## 目标与范围

任务书：`docs/task-astro-create-speed-20261028.md`。仅优化人工预览、人工建卡；不改自动建卡/preadd 的复核及等待语义。

- 工作树：`.worktrees/astro-create-speed`。
- 分支：`codex/astro-create-speed`。
- 基线：`11ee8632a28698230ea47280561b3efa1672b415`。
- 本次按用户要求只 commit，提交消息 `perf(astro): faster manual card create`；不 push、不部署、不运行 WSL 或前端。
- 仅改三个许可的后端源文件、对应测试和本交接文档。未改 pair_validation、catalog、adapter、限频参数、部署文件或环境配置。

## 实现与入口

1. `backend/app/services/market_validation_review.py`
   - `review(priority=False)` 和 `qualify_opportunity(priority=False)`：人工复核使用容量 2 的 `manual_semaphore`；默认后台通道不变。inflight key 包含 priority。
   - `_book` 仍经过原 `book_semaphore` 和原 adapter 请求，不绕过限频。
   - `ManualReview` 保存本请求的 validation、两腿、单调时钟完成时间、风险参数和计时指标。5 秒内复用；超过 5 秒重新优先复核。提交前检查 registry epoch、两腿 revision/fingerprint；身份状态、FX、风险参数或校验有效期变化也不能直接沿用旧结果。
   - 完整原始盘口通过 ContextVar 仅保留在当前请求中；共享盘口缓存仍只存最佳档。标准深度校验可复用同一份 20 档请求结果；自定义校验器、需要更多档位、过期或缺失盘口时走原校验器。
2. `backend/app/api/routes_astro.py`
   - 人工机会与合约查询两入口都向 service 传递同一个 `ManualReview`。
   - 保留 `should_enforce` / planner 阻断和风险 warning。
   - `_create_astro_card` / `_build_astro_preview` 在 finally 中各输出一行 JSON timing（包括失败返回）。只记录标的、计数和毫秒数，不记录凭据。
3. `backend/app/services/astro_alerts.py`
   - `handle_manual_create` / `_handle` 复用请求内复核；直接调用人工 service 也有独立上下文和计时。
   - `asyncio.gather(return_exceptions=True)` 并行 list_pairs 与深度检查，等两者完成才进入 add。list 的 AstroClientError 仍返回 action=list；深度错误为 warning。
   - 仅人工入口传 `add_restart_delay_seconds=0`，现有逐 variant 逻辑只将此值用于最后一张；中间仍使用默认 3 秒。自动/preadd 默认延迟不变。
   - 仍逐次检查映射变化、should_enforce 和原正向价差规则，保留 Hyperliquid 的具体市场及 DEX。

## 测试改动

- 新增 `backend/tests/test_astro_create_speed.py`：后台队列被 Event 卡住仍可人工复核、盘口信号量计数、单卡/双卡等待顺序、单次 fresh 计数、5.000/5.001 秒边界、完整盘口复用、depth/list 并行与错误语义、映射/身份变化、自动/preadd 调用及 sleep 原样保留、JSON 日志字段，共 19 项。
- `test_astro_preview_fresh.py` 中旧的三次 fresh 断言按任务目标改为严格断言一次 fresh + priority=True；保留实际提交、价格阈值和其余业务断言。
- `test_api.py`、`test_astro_instrument_routes.py` 只适配人工提交测试替身的新增 keyword 参数及 depth callback，和模拟 qualify 的 priority 参数；未放宽已有业务断言。

## 实际验证命令与原始汇总

以下命令均在本工作树 `backend` 目录运行。失败记录保留，不将失败轮次表述为通过。

```text
python -m pytest tests/test_astro_create_speed.py -q --basetemp=.pytest_create_speed_red -p no:cacheprovider
9 failed, 1 passed in 3.08s
```

以上为首次 red，运行时尚未修改实现代码。

```text
python -m pytest tests/test_astro_create_speed.py -q --basetemp=.pytest_create_speed_green -p no:cacheprovider
10 passed in 0.94s

python -m pytest tests -q -k "astro or validation or instrument" --basetemp=.pytest_create_speed_a -p no:cacheprovider
21 failed, 570 passed, 1535 deselected in 39.69s

python -m ruff check app/api/routes_astro.py app/services/astro_alerts.py app/services/market_validation_review.py
Found 2 errors.
[*] 1 fixable with the `--fix` option (1 hidden fix can be enabled with the `--unsafe-fixes` option).
```

上述筛选失败为测试替身签名和旧三次复核断言；lint 为导入空行及 dict literal，均在许可范围修正。

```text
python -m pytest tests/test_astro_create_speed.py tests/test_astro_instrument_routes.py tests/test_astro_preview_fresh.py tests/test_api.py -q -k "astro or validation" --basetemp=.pytest_create_speed_fix -p no:cacheprovider --tb=short
1 failed, 93 passed, 95 deselected in 20.82s

python -m ruff check app/api/routes_astro.py app/services/astro_alerts.py app/services/market_validation_review.py
All checks passed!

python -m pytest tests/test_astro_create_speed.py -q -k slow_list --basetemp=.pytest_create_speed_state_red -p no:cacheprovider --tb=short
1 failed, 5 passed, 13 deselected in 1.37s

python -m pytest tests/test_astro_create_speed.py -q --basetemp=.pytest_create_speed_green2 -p no:cacheprovider
19 passed in 1.05s
```

补测中首次失败为将身份冲突提示误写成映射 revision 提示，已区分；随后 red 确认 5 秒内身份状态变化不能复用旧放行结果，先观察失败再修复。

最终指定验收：

```text
python -m pytest tests -q -k "astro or validation or instrument" --basetemp=.pytest_create_speed_a -p no:cacheprovider
600 passed, 1535 deselected in 40.65s

python -m pytest tests -q --basetemp=.pytest_create_speed_full -p no:cacheprovider
2131 passed, 4 deselected, 12 warnings in 128.85s (0:02:08)

python -m ruff check app/api/routes_astro.py app/services/astro_alerts.py app/services/market_validation_review.py
All checks passed!
```

完整测试的 12 条 warning 为 index component 相关测试中 `datetime.utcnow()` 的 DeprecationWarning，未扩展范围修改。

## 工作区与交付状态

- 开始时唯一未跟踪文件为用户任务书 `docs/task-astro-create-speed-20261028.md`；保留且不纳入本提交。
- pytest 产生 `.pytest_create_speed_a/`、`.pytest_create_speed_full/` 等临时产物，保留、不暂存、不提交；以最后 `git status --short` 为准。
- 提交前执行 `git diff --check`、逐文件核对差异、显式暂存许可文件、核对 `git diff --cached --stat` 和 `git diff --cached --check`；提交后核对 `git log -1 --oneline` / `git status --short --branch`。
- 线上状态未改动、未连接生产服务器；没有进行线上备份、部署或线上性能验收。

## 剩余边界与下一步

- 当前证据是本地测试及静态检查，不代表真实交易所/astro-core 的端到端时延。人工仍受 book_semaphore、交易所限频、网络、list/add 和 core 重启时延约束。
- 同一请求等待超过 5 秒或身份/有效期变化时会再次复核，这是提交安全所需行为。并发合并复核的等待方若没有捕获完整盘口，初版退回原深度获取流程时会绕过盘口信号量；该问题已按下节修复。
- 后续部署或其他模块请另开任务，并依据本分支提交重新执行相应部署授权与生产验收。

## 审查后修复（2026-10-28）

- 本节交付要求覆盖上文初版的“只 commit、不 push”：按用户明确指令，只推送备份分支 `codex/astro-create-speed`，不推送 `codex/frontend-localization-polish`，不部署。
- 起始提交：`9069a662bca2354ffcac9c8341fdbc00818eee7b`。`git fetch origin` 后，确认目标为 `8779d40678ff6ddc0a49522a74ddac85a9430e9b`；`git rebase origin/codex/frontend-localization-polish` 无冲突，重放后的 Astro 提交为 `7f65f4d3cf196c84af85ae4e422653f6da33bbf2`。
- 仅修复标准深度校验器在并发合并复核后缺失原始盘口的回退。`ManualReview.depth_validator` 用 `_LimitedBookAdapter` 包装原 validator 的 adapter，保留原始市场标识、请求深度和校验逻辑；通过 `MarketValidationService._fetch_book` 取得共享 `book_semaphore` 后才调用真实 adapter。
- `_book` 也调用同一 `_fetch_book` 入口，保持原超时在取得信号量后开始。深度回退维持原无额外 review timeout 的语义；不返回已归一化盘口，避免深度校验重复单位转换。其他回退条件、自定义 validator、自动/preadd 路径和 adapter 限频参数不改。
- 新增真实并发合并测试：用 Event 暂停 owner，让 waiter 加入同一个 inflight；确认 owner 有两腿盘口、waiter 没有，再占满信号量。断言释放前调用次数保持 `[1, 1]` 且任务等待，释放后 `[2, 2]`、深度验证通过，并核对 Hyperliquid 原始市场 `xyz:JIO` 和请求参数。

先只加测试、未修改实现时，在 `backend` 运行：

```text
python -m pytest tests/test_astro_create_speed.py -q -k coalesced_manual_depth --basetemp=.pytest_create_speed_fallback_red -p no:cacheprovider
E           assert [2, 2] == [1, 1]
1 failed, 19 deselected in 1.28s
```

修复后：

```text
python -m pytest tests/test_astro_create_speed.py -q --basetemp=.pytest_create_speed_fallback_green -p no:cacheprovider
20 passed in 1.01s

python -m pytest tests -q --basetemp=.pytest_create_speed_full2 -p no:cacheprovider
2140 passed, 4 deselected, 12 warnings in 136.09s (0:02:16)

python -m ruff check app/api/routes_astro.py app/services/astro_alerts.py app/services/market_validation_review.py
All checks passed!
```

全量测试的 12 条 warning 仍为 index component 相关的 `datetime.utcnow()` 弃用提示。`git diff --check` 通过；提交消息为 `fix(astro): keep manual depth fallback behind book semaphore`。提交及远端最终 SHA 以本次交付报告和 Git 引用为准。

原有未跟踪任务书和 pytest 临时目录均保留，不纳入提交。未连接生产服务器，未备份、部署或进行线上性能验收。后续部署或其他模块继续使用新任务。
