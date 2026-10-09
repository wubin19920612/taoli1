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
- 功能提交 `1aec646858e6e559c705edf054f759bf4a7eac01` 已推送；CI `37896268167` 成功，两个 SHA 镜像 manifest 均已检查。独立临时 Nginx 容器完成协议验证后已关闭并自动移除，SSH 测试隧道已关闭。
- 预检和部署后均验证全部 46 个资源，其中 36 个超过 1 KiB 使用 gzip；解压字节及 MIME 均与原文一致。Accept-Encoding 缺省、br、gzip;q=0 返回原文，gzip/br,gzip 返回压缩版本；Vary、ETag/304、identity Range/206、SPA fallback 与 API 转发通过。
- 首页入口 811,598 → 240,406 bytes（-70.38%）；全部 JS/CSS 合计 1,793,565 → 560,903 bytes（-68.73%）。资源文件名/原文 SHA 与生产基线相同，用户未提交的 PairMonitor 代码没有进入镜像。额外磁盘空间为压缩副本，未声称浏览器内存或 JS 执行成本下降。
- Chromium 真实加载 dashboard、instrument、history 三个页面，分别实际读取 13/11/6 个 gzip 资源，无 pageerror；页面内容正常。未触发建卡、交易或消息发送。
- 主库备份 `backups/radar-before-1aec646858e6-20261009T070650Z.db`，616,259,584 bytes，SHA-256 `686bf080994ae82b4e3ccc9e9ae74941de042d92359a10d05b41d97db71e1c04`。
- Route 备份 `backups/squeeze-route-before-1aec646858e6-20261009T070650Z.db`，241,664 bytes，SHA-256 `b61419f5a7fa4b623a9a50917414393a95ac7c6807280f1a99ca955ad34c9d52`。两库 integrity_check=ok、容器/主机校验一致。
- 服务器 ff-only、固定镜像无构建启动成功，两容器 healthy；生产首页和代理 health 正常，11,841 行情、7,359 机会、无行情源错误。备份策略各保留 9 份、清理 1 份旧自动备份，未改策略。
- 证据：`output/static-assets-{preflight,production,browser}-20261009.json` 和 `output/static-assets-deploy-20261009.log`。当前生产应用为本轮功能 SHA，验收文档另作 skip-ci 提交。
- 前端仅配置/容器构建改变，以真实 Nginx 的字节一致性、编码协商、MIME/ETag/304 和页面/API 冒烟验证为主，不添加只复述配置的单元测试。

## 工作区与后续

- 仅本轮 Dockerfile、Nginx 配置、交接与总账进入提交；原有两份 PairMonitor 前端改动和所有未跟踪文件继续保留。
- 当前剩余 CPU 抽样（11,840 行情）：别名 12.12 ms，行情过滤 3.25 ms，机会过滤 10.13 ms，空观察名单过滤 6.27 ms，机会计算 225.28 ms；名单实际为空、用户别名仅一条。证据 `output/optimization-remaining-cpu-audit-20261009.json`。
- 上述小阶段目前没有足够证据支持新增复杂缓存/线程。网络复查另发现公开机会列表约 222 KB/次、仍未压缩；级别 1 的离线压缩在生产机约 1.05 ms，输出 36 KB，作为下一轮独立验证候选。
