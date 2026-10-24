# 自建套利系统已拆分为独立仓库

2026-10-02，用户要求自建套利系统与价差雷达独立，仅通过SDK/API通信。

- 新私有仓库：https://github.com/wubin19920612/own-arbitrage
- 本机：`C:\Users\wubin\Desktop\code\codex\own-arbitrage`
- 初始分支：`codex/bootstrap`
- 价差仓库基线：`992b439`，本次不修改业务代码或部署。

新仓库拥有独立控制API、TypeScript SDK和契约；初始P0提供healthz、能力查询、SF/FF配置校验，无持久化或交易。后续账本、策略、网关、密钥、界面和部署都归新仓库。禁止共享radar.db或依赖雷达内部模块。

已有研究文档/证据13份已复制到新仓库`docs/research/astro/`，保留来源提交和SHA-256。历史资料中“沿用Python雷达作为界面/系统组成部分”的架构建议由用户的新决定覆盖，雷达只作为可选客户端。

通信方式：后端HTTP `/v1`，或新仓库`@own-arbitrage/sdk`。服务token归价差后端，浏览器不持token，交易密钥不跨系统。SDK尚未发布npm，可构建并安装contracts/sdk两个tgz包；Python可直接按HTTP契约调用。

本轮新仓库本地TS构建、15项API/SDK测试、生产依赖审计通过；SDK打包后独立项目导入通过。这些不代表套利执行器或Arcus已实现。当前雷达尚未实际调用新服务；通信接入后续另开模块验证。

本次旧仓库只提交本交接文件，不更改/备份数据库或部署。其他现有改动及未跟踪文件保留。后续开发请切换新仓库，并读取其README、docs/architecture.md及docs/task-handoff-2026-10-02-bootstrap.md。
