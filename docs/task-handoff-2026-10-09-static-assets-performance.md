# 静态资源传输性能交接

日期：2026-10-09。目标是在同一对话的下一轮优化中减少页面下载量，不增加生产实时压缩开销。

## 范围与基线

- 分支 `codex/frontend-localization-polish`，代码基线 `5b20d6d`，生产基线 `d3542a139c52b51badc54f8ba25ade75f5022c80`。
- 实际前端容器在 Accept-Encoding: gzip 下仍发送原文：入口 JS 700,625 bytes、主 CSS 110,973 bytes；离线压缩分别约 223,102/17,670 bytes。证据 `output/static-assets-before-20261009.json`。
- 验收目标：支持 gzip 的客户端读取预压缩资源，解压后与 identity 原文逐字节一致；不支持或拒绝 gzip 的客户端读取原文；缓存协商、SPA 页面及 API 正常。

## 实现

- `frontend/Dockerfile`：在 CI 的 Node 构建阶段，对超过 1 KiB 的 JS/CSS 执行 `gzip -9 -k`；保留原文，压缩结果随镜像发布。
- `frontend/nginx.conf`：只在现有静态页面 location 启用 `gzip_static on` 和 `gzip_vary on`。Nginx 根据 Accept-Encoding 选择文件，Vary 区分缓存变体；API 的独立代理 location 不改变。
- 不引入运行时压缩、不更改 HTML/JS/CSS 内容、缓存期限、前端业务逻辑或 API；小资源保留原文，避免 gzip 头部抵消收益。
- 生产 nginx 1.27.5 已确认编译 `http_gzip_static_module`。生产机不构建镜像。

## 验证及发布

- 本地 `npm run build`（TypeScript + Vite）通过；本地构建含既有未提交 PairMonitor 修改，CI 仅构建本次受控 Git 文件。
- 后续补齐 CI 镜像检查、独立容器协议验证、数据库备份、部署与线上结果。
- 前端仅配置/容器构建改变，以真实 Nginx 的字节一致性、编码协商、MIME/ETag/304 和页面/API 冒烟验证为主，不添加只复述配置的单元测试。

## 工作区与后续

- 仅本轮 Dockerfile、Nginx 配置、交接与总账进入提交；原有两份 PairMonitor 前端改动和所有未跟踪文件继续保留。
- 当前剩余 CPU 抽样（11,840 行情）：别名 12.12 ms，行情过滤 3.25 ms，机会过滤 10.13 ms，空观察名单过滤 6.27 ms，机会计算 225.28 ms；名单实际为空、用户别名仅一条。证据 `output/optimization-remaining-cpu-audit-20261009.json`。
- 上述小阶段目前没有足够证据支持新增复杂缓存/线程；后续完成网络与运行状态复查，再做整体停止审计。
