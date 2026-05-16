# MEMORY

- [ZHIPU/02513 分钟价差分析](output/zhipu_analysis_summary.json) — 汇率换算后的分钟价差、港股休盘段变化与资金费率。
- [Pulse Lite 套利指标分析](output/pulse_lite_analysis_summary.json) — 复算 SF/FF/SS 价差、资金费率与流动性过滤榜单。
- [CEX 套利雷达设计](docs/superpowers/specs/2026-05-15-arbitrage-radar-design.md) — 监控告警版，直接采集交易所公开 API，飞书告警。
- [CEX 套利雷达实施计划](docs/superpowers/plans/2026-05-15-arbitrage-radar.md) — 后端、前端、告警、部署和验证任务拆分。
- [CEX 套利雷达首版实现](README.md) — FastAPI + React/Vite + SQLite + Docker Compose，支持规则告警和飞书 webhook。
- [CEX 套利雷达风险过滤](README.md) — 默认隐藏低成交额、过期、异常大价差、同名币等不可行动机会。
- [CEX 套利雷达可调过滤与采集加固](README.md) — 面板可选隐藏标签与成交额 K 阈值，Binance/OKX 采集短超时和保底快照。
