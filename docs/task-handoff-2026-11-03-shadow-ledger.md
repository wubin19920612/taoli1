# Shadow ledger 阶段 1 交接（2026-11-03）

- 工作树：`taoli1/.worktrees/shadow-ledger`；分支 `codex/shadow-ledger`；基线 `81585e43d60b03d3ca9df14b3f9379c5f108c183`。
- 范围：成交额缺失规则、执行通道、影子账本、假机会评分、统计接口及中文页面、默认关闭配置。禁止改采集调度、Astro/arb-mvp 建卡下单、opportunity_history 写入。
- 前期接入已与并行 SQLite 任务核对范围；本轮只修改影子配置默认值及影子消费者入口，不改 schema/repositories 或其他消费者。
- 当前状态：PM 性能返工完成，本机后端 `2484 passed, 8 deselected`、前端 `31 files / 413 passed`、tsc 退出 0；两个基准全部通过。压力/真实规模事件循环中位 2.844/0.979 ms，RSS 增量 26.398/6.145 MiB。压力工作线程墙钟中位 19.809 ms，余量较小；尚非生产可启用验收。没有推送或部署。
- 本轮基线 `dc0fec6`；实现 `52c5e62`，测试 `b0766fd`，本次 docs 提交见 `git log`。初始工作区干净。

入口：

- `backend/app/services/execution_routes.py`：目录精确匹配与缓存，HL 原始 DEX 必须保留。
- `fake_opportunity.py`：有界历史与五项评分，仅展示、不拦告警。
- `shadow_ledger.py`：固定买/卖方向，收益=开仓价差−平仓价差；两个变体分行；默认 1500 行，显式配置最多 3000 行；600 秒冷却；真实 book 报价年龄≤10秒。
- `shadow_runtime.py`：`submit` 单轮互斥且忙时跳过，`consume` 可等待；不可变输入、asyncio.to_thread 计算/JSON/持久化、同代展示副本、整份状态发布、取消及关闭安全。API status 含 skipped_rounds。
- `shadow_ledger.py`：精确 MarketKey 反向索引、报价内容修订与定时堆并行触发；共享变体元数据、PendingRows 延迟展开，每批最多 200 行且独立提交。
- `test_shadow_performance_safety.py`：8 项线程/一致性/分块/定时回归；`test_shadow_benchmark.py`：压力及真实规模硬断言，CPU 时钟外另有墙钟中位保护。
- `backend/app/db/schema.py` 的 `initialize_shadow_schema` 和 `repositories.py` 的 `ShadowTradeRepository`：单独表及恢复。
- `shadow_stats.py`、`backend/app/api/routes_shadow.py`：已平仓、默认去假、北京时间分日，三条带现有认证的 GET 接口。
- `frontend/src/pages/ShadowLedgerPage.tsx`、`api/shadow.ts`：页面与数据契约。

重要限制：默认 SHADOW_ENABLED=0；资金费/滑点为估计；检查点每60秒；原始市场与倍率保留；判假也开影子单；不得把 executable 当作账户可交易或下单授权。没有真实下单。

验证命令、原始汇总、环境版本、最终性能数值、币安空成交额调查、偏离说明，统一记录在 [详细报告](shadow-ledger-report-2026-11-03.md)。本机 Python 不是锁定依赖 venv；PM 必须补锁定环境验收。前端只运行离线测试与 tsc，未启动浏览器服务。

下一步仍属于本模块：进行独立只读审查、PM 锁定依赖和生产同构资源验收；本机新预算已过但余量及长尾须复核。真实行情10分钟检查必须另有明确授权，本次禁止。若日后准备部署，必须重新执行项目备份/推送/部署规范，本次均未做。

工作区保留：规格 `docs/shadow-ledger-spec-2026-11-03.md` 已在基线 `dc0fec6` 跟踪，本轮未修改；本任务 `.pytest-tmp/` 原始日志/SQLite 测试库及离线安装的 `frontend/node_modules/` 为被忽略的本地产物，保留。不得清理或覆盖其他任务文件。

本轮没有未跟踪的交付源文件；忽略目录内原始验证日志及诊断产物保留。前端首次并行测试有一个旧 AppShell 等待失败，未改断言，隔离全量重跑 413 passed；完整记录见报告。后续另一个无依赖模块请另开任务，不扩展本次阶段 1 的验收边界。
