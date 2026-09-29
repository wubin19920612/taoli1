# Gate / Hyperliquid 公告来源恢复

## 目标与基线

本任务只处理公告模块：恢复 Gate 被网页拦截后的采集，补足 Hyperliquid 官方新闻公告，同时保留原生永续市场上下架观察。分支 `codex/frontend-localization-polish`，基线 `c479ab4`，修改前线上业务版本 `d0074c16d2d3196d8d82f9a236b1794f18d0fb2b`。

## 排查证据

- Gate 原网页源被 EdgeOne 拦截；数据库最后成功记录停在 9 月 14 日，SAMSUNG 公告 `102003` 未入库。来源详查见 `docs/task-handoff-2026-09-29-gate-announcement-sources.md`。
- Hyperliquid 的 `meta` 状态采集仍运行：检查时状态表更新到 2026-09-29 06:54:07 UTC，234 个市场；公告库只有 `hyperliquid-meta-universe` 的 179 条 listing 和 1 条 delisting，没有官方维护/周报等消息。
- Hyperliquid 官网 `https://hyperliquid.xyz` 直接链接 `https://t.me/hyperliquid_announcements`。生产能读取其公开频道页面 `https://t.me/s/hyperliquid_announcements`，无需账户、Bot Token 或发送消息权限。

## 实现

- `backend/app/services/announcement_public_pages.py`：使用标准库 HTMLParser 读取 Telegram 公开频道的正文、原始 post ID 和带时区发布时间，不执行网页脚本。只接受固定官方频道身份；正文空但含媒体的公告提供原文入口。挑战页或没有有效日期的内容触发明确错误。
- `backend/app/services/announcements.py`：新增 `HyperliquidOfficialAnnouncementProvider`，来源身份 `hyperliquid-official-announcements`。与原 meta provider 并行采集，单个源失败不丢弃另一个源的结果。
- 官方消息保留原始正文（摘要最多 4000 字符）与官方永久链接，不将 HIP-3 的 DEX 限定名称推测匹配为原生交易市场。没有接入各 HIP-3 部署方的独立消息渠道。
- Hyperliquid meta 基线改为查询指定 source，避免新官方消息排到前面后误判原市场基线缺失。空/格式异常的市场快照不覆盖状态、不生成全市场下架；重新上线的既有市场可以再次检测。
- Gate 遇到 HTTP 错误或返回无公告列表的挑战页时，通过 Jina Reader 读取官网总页及股票股息专栏。并发最多两次备用请求，每个最长 15 秒；保留原 source、文章 ID 和发布时间，沿用去重。若官网总页直接可用，不调用备用服务。
- Gate 本次使用网页备用读取，不把 `summary_all` WebSocket 当成整个公告中心的替代品：其官方协议当前没有列出股息/研究院/活动等所有网站分类。
- `frontend/src/pages/AnnouncementsPage.tsx`：说明 Hyperliquid 官方频道和原生市场范围、HIP-3 限制、Gate 第三方备用读取及延迟风险。

## 通知与风险边界

沿用记录交易所、各分类通知开关、来源水位、30 分钟新公告窗口和持久化去重。新 Hyperliquid source 首次同步静默；恢复 Gate 时历史记录保持 muted。没有伪造发布时间或人工补发 SAMSUNG。

Gate Reader 是外部依赖，禁用缓存请求并不保证所有上游缓存均被绕过；无法承诺无延迟或无漏报。官网首屏本次存在 10 条置顶和 5 条近期记录，长时间断线期间可能有无法补齐的缺口。Hyperliquid 公开频道首屏本次是 20 条；官方 Discord/X 或各 DEX 未转发到该频道的信息不保证覆盖。真实新事件发布到飞书的延迟，必须用后续自然事件单独验证。

## 验证

- 新增 `backend/tests/test_announcement_public_sources.py`，覆盖官方消息真实时间/身份/多行正文、媒体消息、无效页面拒绝、新 source 历史静默及重启去重、Gate HTTP 567/200 挑战页与备用源部分失败、meta 来源隔离、重新上线与坏快照保护。
- 后端公告相关测试及前端公告页面测试、类型检查和生产构建在交付阶段执行，最终结果见下方线上交付记录。
- 修改后的采集器曾仅加载到生产容器的临时 Python 进程中做只读探测：Hyperliquid 解析 20 条官方公告，最新 `593 / Weekly Update / 2026-09-28T08:24:27Z`；Gate 总页和股息专栏各 15 条，包含 `102003 / SAMSUNG / 2026-09-29T02:53:36Z`。探测未写数据库、未启动业务循环、未发送通知。
- 新增解析器和测试文件 Ruff 无告警；公告服务既有 Ruff 告警保留，交付前对比确认不增加新告警。

## 工作区与线上交付

仅暂存本任务的采集实现、测试、页面说明及两份来源交接文档。保留已有 `.worktrees/`、pytest 临时目录、`output/**`、其他交接和未跟踪脚本。没有新增依赖或 schema 迁移。

### 测试与提交

- 后端：`python -m pytest tests/test_announcement_public_sources.py tests/test_announcements.py tests/test_api.py -k 'announcement or hyper' -q`，79 passed、104 deselected；新增来源测试单独运行 11 passed。
- 前端：`npm test -- --run tests/AnnouncementsPage.test.tsx`，5 passed（已有 React act 警告）；`npm run build` 的 TypeScript 与 Vite 构建通过。
- 新增文件 Ruff 通过；原 announcements 服务既有诊断修改前后均为 37 条，未增加诊断；`git diff --check` 通过。
- 功能提交 `3e34ce6ca17f3fbbf376702ba725195b7e5d26b1` 已推送当前分支。GitHub Actions 前后端镜像构建均成功：https://github.com/wubin19920612/taoli1/actions/runs/36536208109 。

### 备份与部署

2026-09-29 使用 `deploy/linux-update.sh` 更新，服务器 `git pull --ff-only`，拉取预构建镜像后启动 Compose，未在服务器构建，也未删除数据卷。线上功能版本为 `3e34ce6ca17f3fbbf376702ba725195b7e5d26b1`；后续仅交付记录的文档提交不需要重新部署。

服务器仓库内备份：

- `backups/radar-before-3e34ce6ca17f-20260929T073426Z.db`，627425280 字节，SQLite integrity_check 为 ok；容器与宿主 SHA256 一致：`34f84214637c81752607df5b170f9a02bb16a1d238b09711a41336e5d694cf79`。
- `backups/squeeze-route-before-3e34ce6ca17f-20260929T073426Z.db`，217088 字节，integrity_check 为 ok；SHA256：`0d7e857ee626aa8b58da347845484483cdbd7aced8bbec917a8529fd0bc52c22`。

### 线上验收

- 前后端容器使用对应功能提交的镜像，均健康；`/api/health` 返回 200 / ok，Gate、Hyperliquid 公告接口均返回 200。
- 公告总开关与其他公告通知均启用；首次历史通知关闭；轮询配置 30 秒、通知时效 30 分钟。配置间隔不等于实测端到端延迟。
- Hyperliquid 官方源入库 20 条，全部 muted；最新是 `593 / Weekly Update`，首次抓取时间 `2026-09-29T07:35:54.045906Z`。后续查询仍为 20 条，没有重复入库。
- Gate 恢复记录 30 条：28 muted、2 sent，后续查询数量与状态一致。`102011 / 股票新人见面礼：报名即领，首充交易叠加奖励` 和 `102010 / 主流币双响礼：邀友 100% 抽 BTC、ETH，好友交易再领最高 $32 BTC` 均为 other 类别，实际发送流程成功。
- SAMSUNG 股息公告 `102003` 已入库，发布于 `2026-09-29T02:53:36Z`，抓取于 `2026-09-29T07:35:55.470621Z`；因超过 30 分钟窗口保持 muted，未人工补发。
- sent 是应用发送器成功状态，未独立确认用户终端收件；上述 Gate 通知属于恢复时捕获的近期事件，不能视为正常轮询延迟测量。部署后尚无新的 Hyperliquid 自然公告可验证从发布到飞书的完整链路。

下一步：观察官方频道下一条自然新公告及其发送状态；若继续提升 Gate 可靠性，可单独实现官方 WebSocket 补充源，但必须保留网页股息等分类，不能将其宣称为完整公告中心替代。
