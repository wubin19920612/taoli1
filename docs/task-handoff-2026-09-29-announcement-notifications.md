# 其他交易所公告通知交接

## 目标与范围

除了上/下币及 Launchpool，也采集并发送维护、活动、产品/API 调整等其他公告；增加独立的其他公告通知开关。沿用飞书渠道、记录交易所、新公告时间窗口和持久化去重。

分支 `codex/frontend-localization-polish`；基线 `80c5487`。本次仅公告模块，不改价差、交易执行或其他通知配置。

## 入口与业务规则

- `backend/app/models/announcement.py`：新增 `other_alerts_enabled`，默认 false，兼容已有配置；生产按用户要求单独开启。
- `backend/app/services/announcements.py`：其他类型按独立开关发送；仍受记录交易所、bootstrap、来源水位和新公告窗口限制；历史 muted 记录不因开关开启而补发，重复源 ID 不再发送。
- 原 `alert_exchanges` 仍是该所全部类型和事件提醒的额外授权，不受三个分类开关限制；这次不修改该配置。
- Binance 使用不指定 catalogId 的 CMS 接口，保留每个栏目名称和最新 20 条，实际返回上币、下币、新闻、活动、维护、API、空投 7 栏目。其他类型不抓详情，避免拖慢首条通知。
- OKX/Bybit/Bitget 增加不限定分类的综合公告请求；OKX 最新页面、Bitget 最新新闻不再丢弃 OTHER。综合源失败不丢弃已获取的专用上/下币数据。
- Gate 增加公告总页解析，不对总页使用上币标题白名单。原专用上/下币筛选保留。
- `frontend/src/pages/AnnouncementsPage.tsx`、`frontend/src/api/types.ts`：新增「其他公告飞书提醒」和状态标签，说明不同开关范围以及 Hyperliquid 的现有覆盖限制。
- API 沿用 `GET/PUT /api/settings/announcements`，没有数据库 schema 迁移。

## 验证

- `pytest tests/test_announcements.py tests/test_api.py -k announcement -q`：66 passed；覆盖独立开关、显式交易所覆盖、记录范围、历史静默、窗口、重启去重、综合源内容和故障隔离、设置读写。
- 前端 `AnnouncementsPage.test.tsx`：5 passed；旧设置缺字段兼容、新开关保存与原设置保留。
- `npm run build`（含 TypeScript）通过；`git diff --check` 通过。
- Ruff 公告服务文件基线与本次各 37 条既有告警，比较诊断 code/message 没有新增；未在本模块任务里批量修复旧风格问题。
- 只读公共源验证：Binance 实际返回 OTHER 79 条，Bybit 28 条，OKX 6 条（可与专用栏目重叠，最终按源身份去重）。Bitget 不限分类 API 实际返回分红调整等其他公告；完整 fetch 曾出现本地连接超时，需以线上后续结果为准。

## 已知覆盖限制

- Gate 当前公告源本机返回挑战页，生产只读探测 HTTP 567，无法保证这段期间的公告及时性。不能将空结果当作没有公告。
- Hyperliquid 现有源是 meta 市场状态变更，只覆盖上下架，并非完整新闻公告源。
- 不补发旧公告；启动后先等扩展栏目记录为历史，再开启其他通知。实际新公告到飞书端到端送达需自然事件证据，不以本地 fake sender 测试或健康检查冒充。

## 线上交付

更新前生产运行 `6423d3eaaab459843c0caee314fcc1f4b2b49f6e`，tracked 文件干净，两容器 healthy，磁盘约 20 GiB 可用。公告轮询 enabled、30 秒，新公告窗口 30 分钟；上/下币和 Launchpool 通知已开、alert_exchanges 为空、bootstrap 关闭。

- 功能提交 `d0074c16d2d3196d8d82f9a236b1794f18d0fb2b` 已推送；GitHub Actions 两份镜像成功，完整 SHA 标签核验后通过 `deploy/linux-update.sh` 部署。前后端均运行该 SHA，容器 healthy；`/api/health` 返回 200 / ok。
- 主库备份 `backups/radar-before-d0074c16d2d3-20260929T014951Z.db`，623955968 字节；`integrity_check=ok`，容器与主机 SHA-256 均为 `ef45468fd21af2a881ff325393eb3ebea4b78bf93a8a268bca5f4a45fc43f5f0`。
- 配套库备份 `backups/squeeze-route-before-d0074c16d2d3-20260929T014951Z.db`，217088 字节；`integrity_check=ok`，SHA-256 `8e74362f53c4332ad1c116e7aa38f4fa83e7a1d97f0634ebbb7a3aea638118e2`，容器/主机一致。
- 部署后首批真实 OTHER 已入库：Binance 79、OKX 5、Bybit 28、Bitget 12；最新记录 fetched_at 均在本次部署后，首批历史公告状态 muted。Gate 0 条，与实测上游拦截一致，不宣称覆盖成功。
- 首批入库后通过受认证的 settings API 仅将 `other_alerts_enabled` 改为 true，读取回验一致；上/下币、Launchpool、事件提醒仍为 true，bootstrap 仍 false，alert_exchanges 仍为空，轮询/窗口仍 30 秒/30 分钟。认证凭据仅在容器环境内部使用，不输出或保存。
- 1440px/390px 线上浏览器验证：新开关 checked、状态标签显示开启、其他公告记录可见，无 JS 页面异常和文档横向溢出。截图 `output/other-announcements-production-{1440,390}.png`，脚本 `output/verify-other-announcements-production.mjs`，不提交。
- 本次没有人工补发历史消息或发送伪造测试公告；尚无开启后自然新公告的实际飞书送达样本。本交接追加提交只改文档，线上功能版本仍为上述 SHA。

## 工作区与下一步

保留已有 `.worktrees/`、pytest 临时目录、其他交接、`output/**` 和未跟踪脚本。本次只读源探测产物 `output/announcement-*.txt` 不提交。后续若需修复 Gate 源或接入 Hyperliquid 官方综合公告，需继续明确来源及实测覆盖；其他功能模块请新建任务。
