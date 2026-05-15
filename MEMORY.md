# MEMORY

- [ZHIPU/02513 分钟价差分析](output/zhipu_analysis_summary.json) — 汇率换算后的分钟价差、港股休盘段变化与资金费率。
- [Pulse Lite 套利指标分析](output/pulse_lite_analysis_summary.json) — 复算 SF/FF/SS 价差、资金费率与流动性过滤榜单。
- [CEX 套利雷达设计](docs/superpowers/specs/2026-05-15-arbitrage-radar-design.md) — 监控告警版，直接采集交易所公开 API，飞书告警。
- [CEX 套利雷达实施计划](docs/superpowers/plans/2026-05-15-arbitrage-radar.md) — 后端、前端、告警、部署和验证任务拆分。
- [CEX 套利雷达首版实现](README.md) — FastAPI + React/Vite + SQLite + Docker Compose，支持规则告警和飞书 webhook。
