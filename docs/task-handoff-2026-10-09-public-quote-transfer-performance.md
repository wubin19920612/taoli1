# 公开行情接口传输性能交接

日期：2026-10-09。本轮目标是减少频繁刷新的公开行情 JSON 传输量，保留业务数据和刷新周期。

## 基线与范围

- 分支 `codex/frontend-localization-polish`，本地基线 `c8e1984`，生产基线 `1aec646858e6e559c705edf054f759bf4a7eac01`。
- 唯一业务配置入口 `frontend/nginx.conf`：仅精确匹配 `/api/opportunities`、`/api/markets`、`/api/health` 的公开 JSON 启用 gzip level 1，阈值 1 KiB，压缩输出缓冲 8 × 4 KiB。
- 正则 location 中 `proxy_pass http://backend:8000` 不包含 URI，保留完整原始路径和查询参数；其他代理头及 `proxy_buffering off` 与既有 API location 相同。
- 原文按客户端协商继续可用，Vary: Accept-Encoding 区分编码变体。静态资源增加 `gzip_proxied any`，带 Via 的反向代理请求也能使用已有压缩副本。
- SSE、品种查询、账户/控制接口继续使用原代理配置；没有缓存行情、删字段、降低刷新频率或调整价格/费率/风控计算。

## 证据与验证

- 原线上机会列表约 222 KB/次，不压缩；生产机独立 gzip level 1 的 21 次中位约 1.05 ms、输出约 36 KB。level 6 约 2.56 ms，本轮选 level 1 降低 CPU 开销。
- 使用既有镜像挂载候选 Nginx 配置，在独立 96 MiB 临时容器、loopback 端口验证；`nginx -t` 通过。没有修改运行中的服务配置、数据库或业务状态。
- 真实 Nginx 的 5 组结果与 identity 原文逐字节一致：默认机会列表、资金费率升序/包含风险、BTC/FF 筛选、BTC 合约行情、health。所有组首次比较即处于同一快照。
- 120 条机会 222,353 → 38,298 bytes（-82.78%）；BTC 合约行情 9,598 → 1,919 bytes；health 2,176 → 343 bytes。Nginx 流式输出与独立 gzip 的体积略有差异，不混用两者数值。
- Via/gzip、gzip;q=0、br/identity、少于阈值的空列表、POST/HEAD 405、MIME、受保护接口 401、未压缩的品种接口、SSE 立即返回首事件且无压缩均通过。
- 真实 Chromium：dashboard 的 opportunities/health 确认 Content-Encoding: gzip；dashboard/instrument/history 页面和懒加载资源正常，无 pageerror。
- 实际工作站至服务器同一 SSH 隧道、同一 keepalive 连接，11 轮交错请求完整接收中位 **239.82 → 158.89 ms**（约 -33.75%）。这是该连接样本，不代表所有用户或全站 p95；样本有一次 gzip 649 ms 尾部值，不据此宣称尾延迟改善。
- 服务器本机 9 轮交错中位 **35.69 → 39.58 ms**，说明压缩确实有 CPU/内存成本；公网传输样本的节省大于这项成本。本轮属于以少量压缩成本换取约 83% 流量减少，不能称为零开销或所有指标同时下降。
- 证据：`output/api-compression-benchmark-20261009.json`、`output/public-quote-gzip-{preflight,network-benchmark,browser}-20261009.json`；验证脚本同目录。未增加仅复述配置的单元测试；沿用前一轮 TypeScript/Vite 构建，当前只改变 Nginx。

## 发布与线上验收

- 功能提交 `16a1185fe7cbb156255bc32300cee191ed2dd976` 已推送，CI `37898998427` 成功。两份固定 SHA manifest 可用，实际前端镜像 `nginx -t` 通过。
- 主库备份 `backups/radar-before-16a1185fe7cb-20261009T072837Z.db`，616,677,376 bytes，SHA-256 `8a8e5a1f93d4db147c7b6e3cc0c0d1c8286fb1b847f48016163b56e67e3405ff`。
- Route 备份 `backups/squeeze-route-before-16a1185fe7cb-20261009T072837Z.db`，241,664 bytes，SHA-256 `10f0feea55d545f515badabb656654dd0a02afe40c2412a96f028c8d5fb284c3`。两库 integrity_check=ok，容器/主机校验一致。
- 服务器 ff-only、预构建镜像无构建启动成功，两容器 healthy；备份既有策略各保留 9 份、清理 1 份旧自动备份。
- 生产协议重测全部通过：120 条机会 221,769 → 38,129 bytes，5 组内容字节一致，方法/协商/SSE/认证状态保持；服务器本机中位 identity/gzip 为 41.70/44.69 ms，少量本地压缩成本与预检一致。
- 生产再次验证 46 个静态资源、36 个 gzip 副本，全部字节一致；health 为 ok，11,841 行情、7,846 机会、10 源 healthy、错误为空。
- 临时验证容器、SSH 隧道和本轮 `/tmp` 配置均已清理；未删除或改动用户数据。
- 证据 `output/public-quote-gzip-production-20261009.json`、`output/static-assets-after-public-gzip-20261009.json`、`output/public-quote-gzip-deploy-20261009.log`。验收文档另作 skip-ci 提交，生产镜像保持功能 SHA。

## 工作区及后续

- 仅提交本轮 Nginx 配置、本交接与总账，保留两份 PairMonitor 用户修改及全部既有未跟踪产物。
- 本轮临时验证环境已清理；本地 output 证据保留未跟踪。
- 剩余候选按实际负载复核后决定；不能将减少字节误报为减少业务计算，不能把受保护接口的 401 当作该业务性能测量。
