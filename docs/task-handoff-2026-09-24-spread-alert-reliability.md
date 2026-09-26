# 价差告警投递冷却与 SF 资金费复核交接

日期：2026-09-24。模块：价差与资金费率告警。

## 目标、范围和基线

- 目标：修复选中机会在实际投递前进入完整冷却的问题，并在发送前重新检查 SF 最新资金费方向。
- 分支：`codex/frontend-localization-polish`；本次起点 `fc0233b`，代码提交 `6926681`、`88d0564`。未改线上规则、告警阈值、交易方向或其他模块。
- 此前 Aster TAKEUSDT 采集修复为 `e4fc83f`；此次工作不改变其行情适配器。

## 实现与业务规则

- `backend/app/services/alert_engine.py`：`evaluate` 只选出候选，不再预记成功冷却。告警循环取得 `sent`、`failed` 或 `muted` 结果后调用 `record_delivery_result`；只有 `sent` 使用规则完整冷却。
- `failed` / `muted` 首次 60 秒后可重试；同一机会连续未送达时按 60、120、240、480、最多 900 秒递增退避，且不超过规则自身冷却时长。机会不再符合规则时清除退避；成功发送也清除退避。这样兼顾恢复速度与持续静默时的事件量。
- `backend/app/main.py`：最新快照验证复用引擎的 SF 负资金费判断；资金费已转负时记录 `muted`，不创建 Astro 卡片，也不发飞书。预测费率可用时优先使用，缺失时使用当前费率；这仍不是对下一结算费率的保证。
- `backend/tests/test_alert_engine.py`、`backend/tests/test_alert_loop.py`：覆盖成功冷却、失败与静默重试、重复静默退避、批次未选中机会、最新 SF 资金费翻负，以及投递状态回传。

## 验证与生产状态

- 本地最终代码：告警相关 132 项通过，后端全套 801 项通过（13 条第三方弃用警告）；改动过的引擎和测试文件 Ruff 通过，`git diff --check` 通过。`backend/app/main.py` 现有 10 条 Ruff 告警与修改前基线一致，未顺带改动。
- Git：两个代码提交已推送；GitHub Actions 的后端、前端镜像任务均成功，两个 `sha-88d05647bce57a668cf0596a9d142f1117a4ef6a` 标签可读取。
- 生产：按 `deploy/linux-update.sh` 先备份再 `git pull --ff-only`，使用预构建镜像完成更新；前后端均 healthy。后端及前端代理 `/api/health` 为 `ok`，`exchange_errors={}`，Gate/Aster 采集 healthy；最近告警循环日志未见错误。
- 最终更新前备份：`backups/radar-before-88d05647bce5-20260924T053011Z.db`，`integrity_check=ok`，SHA-256 `8f5fc805d02a1b4815859d25e5185c77bb22aa1ce95ecc4d608cdb64f2cabf5a`。前一次中间更新也保留了 `backups/radar-before-6926681fb8a4-20260924T051046Z.db`。

## 遗留与下一步

- 生产唯一启用的 `guize1` 仍是 `types=["FF"]`、连续 6 次命中、冷却 30000 秒。截图中的 Gate 现货买入 / Aster 永续卖出 TAKEUSDT 是 SF，仍不会被此规则通知；本任务未擅自扩大规则范围。
- 未制造虚构信号或调用真实飞书测试发送。测试已覆盖 `failed`、`muted` 和资金费翻负行为，线上代码及健康状态已确认，但仍需等待自然达标机会与飞书事件来完成真实通知验收。
- 本地已有其他任务的 `.worktrees/`、`output/`、脚本和旧生产排查草稿，均未纳入本模块提交或清理。下一任务若要监控指定 SF 路线，应先确认范围，并设计精确匹配买卖交易所、市场类型和原始标的的规则，不能只在现有全市场规则勾选 SF。
