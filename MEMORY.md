# MEMORY

- [ZHIPU/02513 分钟价差分析](output/zhipu_analysis_summary.json) — 汇率换算后的分钟价差、港股休盘段变化与资金费率。
- [Pulse Lite 套利指标分析](output/pulse_lite_analysis_summary.json) — 复算 SF/FF/SS 价差、资金费率与流动性过滤榜单。
- [CEX 套利雷达设计](docs/superpowers/specs/2026-05-15-arbitrage-radar-design.md) — 监控告警版，直接采集交易所公开 API，飞书告警。
- [CEX 套利雷达实施计划](docs/superpowers/plans/2026-05-15-arbitrage-radar.md) — 后端、前端、告警、部署和验证任务拆分。
- [CEX 套利雷达首版实现](README.md) — FastAPI + React/Vite + SQLite + Docker Compose，支持规则告警和飞书 webhook。
- [CEX 套利雷达风险过滤](README.md) — 默认隐藏低成交额、过期、异常大价差、同名币等不可行动机会。
- [CEX 套利雷达可调过滤与采集加固](README.md) — 面板可选隐藏标签与成交额 K 阈值，Binance/OKX 采集短超时和保底快照。
- [CEX 套利雷达风险参数中文化](README.md) — 风险标签显示中文名和代码，参数页解释触发逻辑并支持阈值保存。
- [CEX 套利雷达空成交额过滤修正](README.md) — LOW_VOLUME 不再把 null 当 0，MARK_INDEX_DEVIATION 默认显示。
- [CEX 套利雷达成交额阈值同步](README.md) — 首页和新增告警规则默认跟随已保存的低成交额 K 阈值。
- [CEX 套利雷达全局排除设置](README.md) — 支持黑名单标的和忽略交易所，列表、健康统计、采集和告警同步生效。
- [CEX 套利雷达 Hyperliquid 接入](README.md) — 新增 Hyperliquid 现货/永续和 builder perp dex 股票类标的，midpoint 映射 bid/ask。
- [CEX 套利雷达轻量历史](README.md) — 每 120 秒按阈值记录 Top100 机会，3 天保留并定期 VACUUM，适配小硬盘。
- [CEX 套利雷达资金费率预测](README.md) — 展示当前/预测资金费率、下一次结算时间，并按小时/日归一化净资金费。
- [CEX 套利雷达 OKX 资金费率加固](README.md) — OKX funding 改为 `instId=ANY` 全量请求，失败时保留合约盘口。
- [CEX 套利雷达表格排版优化](README.md) — 工具栏可收缩，机会表格使用固定列宽和资金费率分组，避免字段重叠。
- [CEX 套利雷达低成交额修正](README.md) — null 不当作 0；但任一已知侧为 0 或低于阈值时触发 LOW_VOLUME。
