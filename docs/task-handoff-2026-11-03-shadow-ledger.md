# Shadow ledger 阶段 1 交接（2026-11-03）

- 工作树：`taoli1/.worktrees/shadow-ledger`；分支 `codex/shadow-ledger`；基线 `81585e43d60b03d3ca9df14b3f9379c5f108c183`。
- 范围：成交额缺失规则、执行通道、影子账本、假机会评分、统计接口及中文页面、默认关闭配置。禁止改采集调度、Astro/arb-mvp 建卡下单、opportunity_history 写入。
- PM 已确认并行 `codex/s6-sqlite-maintenance` 只追加 config/main，不改 schema/repositories；本分支 config/main 也仅追加。
- 当前状态：本机后端 2476 passed、前端 413 passed、tsc 退出 0；独立基准中位数 29.85 ms，RSS 增量 24.82 MiB。性能 ≤5 ms 硬断言失败，不可视为生产可启用版本。没有推送或部署。

入口：

- `backend/app/services/execution_routes.py`：目录精确匹配与缓存，HL 原始 DEX 必须保留。
- `fake_opportunity.py`：有界历史与五项评分，仅展示、不拦告警。
- `shadow_ledger.py`：固定买/卖方向，收益=开仓价差−平仓价差；两个变体分行；3000 是行上限；600 秒冷却；真实 book 报价年龄≤10秒。
- `shadow_runtime.py`：消费者接入、展示副本、初始化/运行/关闭异常隔离、批量写重试。
- `backend/app/db/schema.py` 的 `initialize_shadow_schema` 和 `repositories.py` 的 `ShadowTradeRepository`：单独表及恢复。
- `shadow_stats.py`、`backend/app/api/routes_shadow.py`：已平仓、默认去假、北京时间分日，三条带现有认证的 GET 接口。
- `frontend/src/pages/ShadowLedgerPage.tsx`、`api/shadow.ts`：页面与数据契约。

重要限制：默认 SHADOW_ENABLED=0；资金费/滑点为估计；检查点每60秒；原始市场与倍率保留；判假也开影子单；不得把 executable 当作账户可交易或下单授权。没有真实下单。

验证命令、原始汇总、环境版本、最终性能数值、币安空成交额调查、偏离说明，统一记录在 [详细报告](shadow-ledger-report-2026-11-03.md)。本机 Python 不是锁定依赖 venv；PM 必须补锁定环境验收。前端只运行离线测试与 tsc，未启动浏览器服务。

下一步仍属于本模块：先处理完整消费者的性能预算，再进行独立只读审查与 PM 锁定依赖验收；真实行情10分钟检查必须另有明确授权，本次禁止。若日后准备部署，必须重新执行项目备份/推送/部署规范，本次均未做。

工作区保留：用户提供的未跟踪 `docs/shadow-ledger-spec-2026-11-03.md` 不提交；本任务 `.pytest-tmp/` 原始日志/SQLite 测试库及离线安装的 `frontend/node_modules/` 为被忽略的本地产物，保留。不得清理或覆盖其他任务文件。
