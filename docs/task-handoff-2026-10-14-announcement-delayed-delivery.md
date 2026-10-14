# 公告及时发现修复交接（取代此前放宽窗口方案）

## 目标与范围

用户要求查清 Bitget COPPER 公告为什么晚约 85 分钟才发现，并改善通知时效。仅处理公告采集、轮询、通知判定和诊断；不涉及交易执行。用户明确要求“不补发历史公告”。此前把窗口放宽至 180 分钟没有解决发现延迟，已经撤回：生产配置和代码默认值均恢复 30 分钟。

## 分支和基线

- 分支：`codex/frontend-localization-polish`。
- 本轮基线：`fed6552`；本轮修改前线上功能提交：`5625b90c53a06aa49418d4a91af10492ecc8d1d0`。
- 最初调查前线上版本：`ad5f4fe1e878840fb06fbc4f436fb0520e779899`。

## 已证实的时间线与证据边界

- 公告 ID：`12560603897463`；官方文章：<https://www.bitget.com/support/articles/12560603897463>。
- 数据库发布时间：2026-10-14 03:27:19 UTC（北京时间 11:27:19）。
- 数据库首次发现：04:52:18.064022 UTC（12:52:18），间隔 5099.064022 秒，约 85 分钟。
- 记录类别 `latest_news:news`，来源 `bitget-public-annoucements`，状态 `muted`。未进入飞书发送，不是飞书排队了 85 分钟。
- 旧通知规则同时使用来源最大发布时间和 30 分钟时效窗口，该条两项均不满足。去除水位否决只能解决乱序漏报，不能让采集变快。
- 同源在 03:28、03:48、03:52、04:00 UTC 均有其他公告入库，不是整段时间服务完全停止。
- 04:00:14.950 UTC 入库维护公告 97486/97485，而目标尚未入库。按最初代码顺序，general 请求之前的 latest_news 请求已返回，至少目标标注发布约 33 分钟后仍未通过这一链路得到可解析记录。旧代码不校验业务错误，不能把“HTTP 返回”写成“业务响应正常”。
- 最初 Bitget 发现仅使用公开 API 的英文源；中文请求只补充已发现记录，不能发现英文列表中不存在的新 ID；没有直接监听官网列表。
- 2026-10-14 实查官网 articleDetails：中英文 showTime 都是 1791948439000；中文 longCreateTime=1791948457605（约 11:27:37），英文=1791948685366（约 11:31:25），aiTranslatedFlag 分别为 0/1。元数据不是历史公网可访问性的快照，不能据此承诺网站从该时刻就可抓取；翻译创建差约 4 分钟也不能解释全部 85 分钟。
- 当前 API cache-status=BYPASS、cf-cache-status=DYNAMIC，requestTime 当前；加随机参数返回相同 ID。没有证据指向当前 CDN 缓存问题。
- **缺失当时逐轮原始响应和完整耗时记录，无法还原全部 85 分钟，也不能断言全部由 Bitget API 同步延迟造成。** 目前确定的是发现阶段延迟、单 API 发现缺口和旧规则静默；没有将推测写成完整历史根因。

## 修复与关键入口

- `backend/app/services/announcements.py`：新增 `BitgetWebsiteAnnouncementProvider`，直接并行读取官网综合公告 `12508313443483` 与新闻 `360007868532` 的中英文页面。
- 从 `window.__ZEUS_REACT_QUERY_STATE__` 解码 JSON，不执行网页 JavaScript；精确匹配 section 查询；保留官方 ID、时间、标题、链接。遍历整页，避免置顶旧文造成提前停止。独立中文发现不依赖英文同步。
- 官网与 API 共用原有 source 和公告 ID，数据库唯一约束保证只发一次；保留历史 muted 状态，不回放旧公告。
- API 保留六栏目并发（上限 3）与故障隔离；官方接口每页最多 10，实测 limit=20/50 均返回 40017，没有通过虚增 limit 假装修复。
- `backend/app/services/announcement_polling.py`：每个来源独立定时执行，慢来源不会阻止其他来源下一轮；设置仍为 30 秒。抓取每轮最多 20 秒；可选中文补充最多 3 秒，失败仍处理原始公告。到期提醒只有一个循环执行，避免多来源重复发送。
- 在已有 `announcement_provider_state` 保存各来源最新轮询时间、抓取/翻译/处理耗时、原始返回 ID、解析后 ID/发布时间、栏目或页面错误、入库数量。相同结果合并计数，保留最近 120 组变化；不保存私密配置。
- `backend/app/api/routes_announcements.py`：`GET /api/announcements/sources?exchange=bitget&include_history=true` 可查看诊断；默认不包含历史。
- `backend/app/models/announcement.py`、`frontend/src/pages/AnnouncementsPage.tsx`：默认窗口恢复 30 分钟。先前已部署的“按 ID 去重、窗口内乱序可通知”和延迟提示保留。

## 测试与边界

- 后端公告、API、飞书相关测试：145 passed / 101 deselected；新增核心用例 9 个。后续遥测字段细化后针对采集/轮询重新运行，22 passed。
- 新测试覆盖 API 空列表时官网发现、只有中文可见、双渠道去重、挑战页识别、非法 ID/缺时间、慢源隔离、抓取超时恢复、翻译超时不丢公告、诊断接口与历史上限、默认窗口不发送 85 分钟旧公告。
- 前端公告页 5 passed；类型检查和生产构建成功；存在既有 React act 提示。
- 新模块/API/测试 Ruff 通过，主服务保留 39 条既有诊断；本次新增代码无额外诊断。
- 人工审查仅本任务文件；没有启动其他代理，没有发送测试飞书或历史补发。
- 30 秒是轮询目标，不是从交易所后台发布时间到手机接收的保证；官网延迟、页面改版、网络与发送耗时仍有影响。未出现自然新公告前不能把模拟发送当成飞书实测。

## 线上状态与交付

- 窗口已通过生产认证设置接口恢复 30，读回核验；其他设置保持原值。
- 本轮功能提交、生产镜像：`1ff56381cb95cb88f7cf42ef216f2ec721bb454c`，已推送；构建运行 `38590287691` 的 backend/frontend 均 success，部署前两份 manifest 核验通过。
- 生产已通过 `deploy/linux-update.sh` 备份、`git pull --ff-only`、拉取预构建镜像并执行 Compose `up -d --no-build --wait`，两容器 healthy；没有在生产机构建。
- 主库备份：`backups/radar-before-1ff56381cb95-20261014T153442Z.db`，623169536 字节；SHA-256 `9aeae2ecb614ebe433e47aa0f1c28a63c6399d0b36a2d8bf57c9c78dab554756`。
- 配套库备份：`backups/squeeze-route-before-1ff56381cb95-20261014T153442Z.db`，241664 字节；SHA-256 `12a18ae9a4f87ef4219f79e62c031f926141e2b03d6d51709fbfd2ca44719cc0`。
- 两份备份均 integrity_check=ok，主机与容器 SHA-256 一致。
- 上线前使用生产网络只读执行新官网 provider：四页各 20 条，合并 40 条，耗时 0.222 秒，含目标 COPPER 及中文标题，没有调用发送器。
- 上线后连续三轮（15:36:05、15:36:35、15:37:05 UTC）检查：API 56 条、官网 40 条，均成功且均包含目标，无栏目/页面错误。官网抓取 0.593/0.311/0.336 秒，总处理 0.662/0.391/3.490 秒；API 总处理 1.085/0.393/3.509 秒。第三轮数据库处理约 3 秒，仍未阻塞下一轮定时。
- API 起始间隔 29.999971/29.999964 秒；官网 29.999906/29.999885 秒。每个来源独立运行；慢源隔离另有阻塞模拟测试。
- 其他六个 provider 最新状态也均为 ok；前端 `/` 和通过前端代理的 `/api/announcements/sources` 均 HTTP 200，`/api/health` 为 ok。
- 15:37:28 UTC 实查官网综合英文页、新闻中文页：Cache-Control=no-cache、CF-Cache-Status=EXPIRED，SSR dataUpdatedAt 与请求时间相差约 0.1 秒，未发现当前页面由旧 CDN 缓存响应的证据。
- 官网补充入库 18 条既有记录，全部 muted，最新发布时间为 11:00:35 UTC；没有补发历史消息。COPPER 仍只有一条，原始 fetched_at 和 muted 均保持不变；生产窗口和轮询均再次读取核验为 30 分钟/30 秒。
- 尚未遇到部署后的自然新公告，未声称已经实测新公告飞书端到端送达。
- 上一轮备份：`backups/radar-before-5625b90c53a0-20261014T151127Z.db`，623169536 字节，integrity_check=ok，SHA-256 `cbc488479c034e20b266b188a88d8751d7067b0530b50160410d922c5bd8a1af`。

## 工作区和继续工作

- 保留其他任务对 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 的修改。
- 保留 `.worktrees/`、既有 pytest 目录、`output/`、未跟踪交接和脚本；不提交密钥、Webhook、.env。
- 下一次同类延迟先查询 sources 历史，对比原始返回 ID、解析结果与首次入库时间，区分上游列表不可见、解析失败、请求失败、调度或处理慢。
- 其他功能模块请另开任务，并引用本交接。
