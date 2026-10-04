# Gate 公告源验证与接入交接

后续同日的业务修复与部署记录见 `docs/task-handoff-2026-09-29-announcement-source-recovery.md`。下面保留来源发现时的证据和状态，不能将当时“未接入”的状态当成最新交付结论。

## 范围与状态

2026-09-29 北京时间约 14:00–14:35，只读核验 Gate 公告源，目标是找到能覆盖 SAMSUNG 分红公告的来源。本轮没有修改业务代码、生产配置或数据库，也没有发送飞书测试消息。

本地分支 `codex/frontend-localization-polish`，基线 `c479ab4`；业务版本沿用此前部署的 `d0074c16d2d3196d8d82f9a236b1794f18d0fb2b`，本轮未部署。开始及记录结论前 tracked 工作区干净，保留现有未跟踪产物。本文是研究交接，不代表线上采集恢复。

## 已找到的源

### 1. Gate 公告 WebSocket：生产容器已实测可用

- 地址：`wss://api.gateio.ws/ws/v4/ann`。
- 官方协议：<https://www.gate.com/docs/developers/announcements/ws/en/>。
- 官方文档直接访问受到拦截，本次通过 Jina Reader 阅读其公开内容；运行测试直接连接 Gate WebSocket，没有通过 Jina 转发 WebSocket。
- 发现线索：<https://github.com/chainmyway/cex-monitor/blob/HEAD/src/gate.rs>；随后用官方协议和生产实测确认，未执行该仓库代码。
- `announcement.summary_listing`、`announcement.summary_delisting`、`announcement.summary_all` 均返回订阅成功，并收到真实公告。
- 不存在的 `announcement.summary` 明确返回 `Unknown channel`，因此成功响应并非对任意频道无条件接受。
- 中文 payload 为 `["cn"]`，英文为 `["en"]`，也支持两者。官方文档说明不支持 `!all`。
- 2026-08-27 官方变更记录新增 `summary_all` 和 `summary_ipos`。

订阅示例（time 替换为当前 Unix 秒）：

```json
{"time":1790662038,"channel":"announcement.summary_all","event":"subscribe","payload":["cn"]}
```

生产容器的中英文独立订阅测试：订阅确认约 0.21–0.29 秒，缓存公告约 1.21–1.29 秒返回。这是连接后的快照返回耗时，**不是公告发布到通知送达的延迟**。实际样本：

- ID 从 `origin_url` 提取为 `102005`。
- 标题：Gate CFD 私域带单上线：支持最高 1,000 人跟单及最高 50% 分润。
- `origin_url=https://www.gate.io/article/102005`。
- `published_at=1790654028`，即北京时间 2026-09-29 11:53:48。
- 下架频道返回 `101657`：Gate 关于下架 CDL、PNDO 等 21 个代币的公告。

**覆盖边界：**官方 `summary_all` 聚合当前受支持的十种 `result.type`：`listing`、`delisting`、`fee`、`etf`、`deposit_withdrawal`、`rename`、`precision`、`engine_upgrade`、`stock_split`、`ipos`，以后新增类型自动包含。文档没有列出股票股息派发、研究院或活动类型，不能据此承诺覆盖整个官网公告中心。实测 `summary_all` 的初始快照停在 11:53 的 CFD 公告，而网页存在 13:23 的 ETH 理财公告。

**恢复与去重：**官方说明订阅后每种语言发送最新缓存公告，随后推送新公告；没有在该协议中发现历史列表/按游标回放接口。多类型归属可能导致同一文章按类型重复推送，跨类型不保证顺序。不能把每 30 秒连一次、取一次最新快照当成完整流式采集。长连接、心跳、断线重订阅及列表补缺都需要单独实现。

### 2. 官网列表与详情：已获取目标原文，当前经第三方传输

已实测的官方页面：

- 公告总页：<https://www.gate.com/zh/announcements>。
- 近期公告：<https://www.gate.com/zh/announcements/lastest>（官网分类标识确实拼作 `lastest`）。
- 股票股息派发：<https://www.gate.com/zh/announcements/dividenddistribution>。
- SAMSUNG 原文：<https://www.gate.com/zh/announcements/article/102003>。

生产主机通过 `https://r.jina.ai/` 加上述完整 HTTPS URL 读取公开页面，设置 `X-Return-Format: html`；验证时还请求 `X-No-Cache: true`。返回 HTML 内保留 `__NEXT_DATA__`，能够提取 Gate 原始 ID、发布时间和分类。该路径属于 **Gate 官方内容 + 第三方阅读服务传输**，不是 Gate 官方 REST API。原始官网和 `apim.gateapi.io` 的直连拦截没有因此解除。

实测列表一次约 2.10 秒、详情一次约 1.28 秒，均为读取请求耗时，不构成长期稳定性、源实时性或通知端到端时延证明。14:34:45 的专栏样本中，SAMSUNG 的相对发布时间与绝对时间推算页面生成约在 14:34:44；近期总页推算约在 14:34:02。即使请求禁用缓存，也不能假定所有上游缓存被完全消除。

目标公告已在总页、专栏、详情三处核对一致：

```json
{
  "id": 102003,
  "title": "Gate 关于 SAMSUNG 永续合约分红派息结算的公告",
  "release_timestamp": "1790650416",
  "cate_id": 109,
  "url": "/announcements/article/102003"
}
```

发布时间为北京时间 **2026-09-29 10:53:36**。官网分类 `id=109`、`cate=dividenddistribution`，名称为“股票股息派发 / Dividend Distribution”。该专栏首屏共 15 条，首条是 SAMSUNG，其后包括 APD `101988`、CSCO `101986`、STRC/BEN/SYK/MPWR `101982` 等真实股息公告。

详情页数据位于 `props.pageProps.detail`，正文位于 `detail.desc`，本次读取长度为 3225 字符，`created_t=1790650416`。正文与截图一致，并明确北京时间 2026-09-29 16:00 结算。现有 Gate 详情解析器主要查 `articleDetail`，若接入应兼容真实 `detail.desc` 结构。

进一步在生产容器直接调用现有 `GateAnnouncementProvider._parse_page(html, "dividenddistribution")`（仅解析，不入库、不发送）：返回 15 条，目标 `announcement_id=102003`、`kind=other`、`source=gate-next-announcements`、`published_at=2026-09-29T02:53:36+00:00`、URL 为 `https://www.gate.com/announcements/article/102003`。这验证了该列表内容与现有公告模型/来源身份兼容，尚不构成业务接入或送达验收。

总页/近期页本次首屏 15 条中有 10 条置顶、5 条非置顶。不能按页面顺序或 ID 大小判断发布时间，也不能把总页首屏当成完整历史。尝试 `?page=2`、`?page=3` 后，`__NEXT_DATA__` 中列表仍与首页相同；尚未验证真实分页协议或客户端分页后的 DOM，不能声称分页补取可用。专栏扩展比简单修改 page 参数更有证据支持。

### 3. 仍不可用或未验证的路径

- 原 `https://apim.gateapi.io/announcements` 及专用栏目：此前生产容器返回 567；本轮干净本地浏览器也最终显示 EdgeOne 拦截页。初次 200 可能仅为 JS 挑战页。
- `www.gate.com` 官网、公开文档和所测静态脚本直接访问返回 403。
- 此前猜测的 `/api/v4/announcements` 为 404，不能用来替代公告源。
- 此前两个 Telegram 候选页面没有当前公告样本，也没有充分官方归属/覆盖证据，不作为来源。
- 本轮没有验证到官方 HTTP 历史公告 JSON 接口，也没有验证任何第三方长期 SLA、匿名限流额度或新事件实时推送完整性。

## 建议接入方案与验收

1. 官方 WebSocket 保持长连接，订阅中文 `summary_all`；以官方文章 ID 做稳定身份。保留原 source 身份或显式迁移去重，不能换 source 后重复通知同一文章。
2. 官网近期页和必要分类列表补足股息、维护以外的产品/活动/研究等内容。若采用 Reader，明确它是外部依赖，设置超时、退避、频率预算、解析失败状态和新鲜度校验。单次成功不等于适合直接承诺 30 秒送达。
3. 统一 `gate.io/article/<id>` 与 `gate.com/zh/announcements/article/<id>` 的文章身份，语言差异不能造成重复。只有同所同文章的可靠 ID 可合并，不以标题模糊匹配替代。
4. WebSocket 失联、订阅被拒、读取挑战页、无 `__NEXT_DATA__` 或 schema 改变应可观测；不能与“成功但无新公告”混为一谈。
5. 同时保留原始 `published_at` 和采集时间。已有 30 分钟通知窗口、bootstrap 静默、历史去重规则需要明确沿用；本次 SAMSUNG 已过窗口，不能通过伪造发布时间触发新公告通知。
6. 覆盖验证至少包括：真实 SAMSUNG 样本解析、列表中的其他公告、多源重复、初始缓存快照、乱序、断线重连、第三方超时/限流/陈旧页面；上线后等待新的自然公告验证发布到发送的真实延迟。

## 代码入口与交付边界

- `backend/app/services/announcements.py`：`GateAnnouncementProvider`、`MultiAnnouncementProvider`、`AnnouncementMonitor.process`。
- `backend/app/models/announcement.py`：通知配置与公告模型。
- `backend/tests/test_announcements.py`：分类、窗口、水位、去重、来源隔离测试。
- 现有功能交接：`docs/task-handoff-2026-09-29-announcement-notifications.md`。

本轮完成的是来源发现与只读验证。本文为本轮新建、尚未提交的交接文档；未接入业务采集器、未进行 Git 代码交付、未新增生产备份或部署、未人工补发 SAMSUNG。下一步可据此实施公告模块修复；实施后按项目既有测试、提交推送、预构建镜像、备份和部署流程交付，生产 2 GiB 主机不构建镜像。
