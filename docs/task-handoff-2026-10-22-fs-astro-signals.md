# Bybit FS 信号与 Astro 自动建卡交接

## 范围、基线与用户澄清

本模块延续 Bybit 负费率 FS 观察池。用户本轮明确：借币与执行使用 Astro，taoli1 只找信号并自动建卡，检查重复卡片。新卡停用且禁止开仓；不下单、不借币、不改交易所杠杆、不发送外部通知，不扩展其他平台。

分支 `codex/frontend-localization-polish`。开始时本地/远端 `737b02a`，生产 Git 与两镜像 `86047e2`，容器 healthy、health ok。两份 PairMonitor WIP 不属于本次改动。

## 实现与入口

- `fs_economics.py`：新增独立 `signal_ready/signal_blockers`。公开信号允许未配置账户/假设手续费；真实账户失败、容量不足、身份冲突、过期等继续阻断。既有 discovered/quota_sufficient/cost_pass 不冒充账户验证。
- `fs_astro.py`：复用 `AstroSdkClient` 和 `AstroPairPlanner` 的 FS 参数映射；固定 Bybit 原生路线、停用/禁止开仓、杠杆1、初始单笔和持仓范围10 USDT。查列表→查持久化提交记录→重新完整采样→检查身份版本、全局过滤和配置代次→记 pending→add→记 created。最多每轮一次新增；无更新/开启/发送消息能力。
- `fs_repository.py`：新增 `fs_astro_attempts`，随主库备份。pending 在重启后仍阻止重发；列表确认已存在则记 reconciled。已创建后即使列表里消失也不重建，需人工核对。站内事件有 signal_ready/astro_pending/astro_created/astro_reconciled。
- `fs_monitor.py`：有候选时10秒起始周期，逐行发布，空池60秒配置周期；原有50秒上限/并发3/请求120ms节流不变。使用实时身份 registry 重注释精确市场；账户与身份变更、来源过期会降级信号和成本状态。慢查询可能仍有过期空档，这是诚实的采样限制。
- `fs_identity.py` + 原 `market_identity_catalog.py`：只增 MEWUSDT/BMTUSDT 四个精确市场的证据绑定；两边当前规格必需，永续 fullName 必须匹配。证据到2026-10-29 09:20 UTC（韩国时间18:20）固定到期，重启不续期；保留原目录的人工决定/冲突保护。
- `FsMonitorPage.tsx`：信号与账户状态分开、Astro 自动建卡开关、提交状态、身份细节、采样耗时/下次刷新；每秒重绘有效期，API失败撤下旧结果；保留可选账户读取入口。

## 查明的问题与证据

- 线上账户连接列表为空且 FS account_id=null，未查询任何真实 Bybit 私有账户端点。用户澄清后不再要求新增 taoli1 账户作为建卡条件。真实额度/库存/账户利率/真实手续费仍未实测。
- 旧目录 Bybit 身份规则仅覆盖少数永续，缺少这些现货腿，不是交易所不支持。MEW 双腿 pending，需正式证据绑定。
- 当前 MET 冲突来自全局同名市场 `METUSDT` 中，Bitget `RMETUSDT` 为 equity，而 Meteora 精确市场为 crypto；Bybit 双腿仍 pending，所以不能跨过同名冲突保护。证据 `output/fs-met-lookup-20261022.json`。本轮不擅自解除 MET 冲突。
- MEW：[Bybit 官方说明](https://www.bybit.com/en/learn/memes/what-is-mew-cat-coin) 明确 MEW/USDT 现货与 MEWUSDT 永续；BMT：[官方说明](https://www.bybit.com/en/learn/crypto/what-is-bubblemaps-bmt) 同样明确双腿。结合实时 instruments-info 的 raw symbol、baseCoin、fullName、USDT quote/settle、LinearPerpetual、Trading 及数量规则核验。沿用身份库现有对应 asset_id，没有按同名 ticker 推广到其他币。
- 旧调度是扫描耗时加60秒等待，而盘口30秒/配对复核15秒，必有较长空档。真实公开数据三轮新扫描耗时8.797/8.312/8.109秒，每轮结束15/15候选仍新鲜，BMT identity verified，但压力净收益不达标，signal_ready=false。未降低门槛制造信号。
- Astro 真接口列表读取成功：configured=true、dry_run_only=false，初始114张卡片，FS为0。仅查询，不修改旧卡片。

## 测试与证据边界

后端 FS、身份目录、Astro planner、账户回归最终86项通过（`output/fs-backend-final-20261022.log`）。前端 FS 6项及 AppShell12项通过，类型检查/Vite生产构建通过（`output/fs-frontend-20261022a.log`、`output/fs-frontend-20261022c.log`、`output/fs-build-20261022b.log`）。现有 Starlette 弃用告警和 React act 测试告警保留；首次新前端断言因手机合并文本失败，已修正选择器，失败日志未删除。

模拟覆盖账户权限不足、零库存、身份冲突/变化、过期、账户禁用/修改、在途配置变化、查询失败；Astro并发去重、已有卡片、写入结果不确定、跨重启 pending、dry run 与失败脱敏。真实公开三轮见 `output/fs-public-probe-20261022.json`；不能将模拟成功说成真实账户或实盘收益验证。

## 工作区与后续

保留 `frontend/src/pages/PairMonitorPage.tsx` 和 `frontend/tests/PairMonitorPage.test.tsx`；起始/交付哈希分别为 `11379f2cb0881b274a998a4a4534a698c4dd94b0080de3de691f4dbf27440d38`、`0376b098c2274e60666915709d9d1183decac45781a861faac452aacb00500ab`。

所有既有未跟踪文件、`.worktrees/`、pytest临时目录、DB/WAL/SHM、日志、截图保留。本轮 `output/fs-*20261022*` 为不提交的证据。自动建卡信号仍依赖公开利率和配置手续费、静态退出盘口假设；真正借贷能力与交易执行留在 Astro 侧由用户复核。其他模块请新建任务。

## 部署补记

- 功能提交 `243ae4687313001fe5c4656a96290622f500ca5d` 已推送。GitHub Actions [39756267151](https://github.com/wubin19920612/taoli1/actions/runs/39756267151) 前后端镜像成功，两标签 manifest 已核对。服务器 git pull --ff-only 到该SHA，通过 `deploy/linux-update.sh` 拉取同SHA预构建镜像并 `up -d --no-build --wait`，生产机构建次数为0。
- 主库备份 `backups/radar-before-243ae4687313-20261022T093455Z.db`，719351808字节，SHA256 `4a2903d4d9ec6041510e0ff2f3c2a4bc652b85ffc4bf12946f59e572d9474aa5`。
- 路线库备份 `backups/squeeze-route-before-243ae4687313-20261022T093455Z.db`，241664字节，SHA256 `32e057154a7f4032b29cebac42880b2448c57c818b5591c28080771d6e88a2e1`。两库 integrity_check=ok，容器/宿主机哈希一致。既有部署保留策略各保留9份备份，日志 `output/fs-deployment-20261022.log`。
- 前后端 healthy，生产 tracked clean，`/api/health` status=ok、12201市场、exchange_errors={}。启动约两分钟时 backend 551.6MiB/768MiB、frontend 4.18MiB/96MiB；不是长期负载证明。部署前 backend 715.9MiB/768MiB，不能把重启后下降直接归因为本次优化。
- 根据用户明确选择，已通过认证 settings API 设置 `astro_auto_create=true`，其他配置保持原值，account_id仍null。无密码 FS report=401；认证 report/settings/history/events正常，历史行强制 stale 且 signal_ready=false。
- 2026-10-22 18:37–18:38（韩国时间）9次采样：开启配置后的第1次为空池/在途失效，随后逐行补齐，后6次连续样本15/15未过期；实测轮次约9.4–11.9秒。BMT identity verified，signal_ready=false，阻断为 cost_threshold_not_met；MET conflict；最终13 unverified、1 conflict、1 verified。MEW当时未入选前15，但其现货/永续绑定API均verified。四绑定到期均为2026-10-29T09:20:00Z。
- Astro真实列表读取通过，验证结束112张、FS=0（起始114张；本模块没有删除/更新操作，未归因其他卡片数量变化）。没有当前达标信号，因此本轮没有真实 add；不能声称真实 FS 建卡/真实借贷/成交已验收。自动 add、同路线跳过、网络不确定持久化与重启恢复是模拟覆盖；自然信号出现后的首次真实新增与列表回读仍需观察。
- 桌面1440px、手机390px的真实部署页面及配置开关、详情展开验证通过，0页面错误、无整页横向溢出。截图 `output/fs-astro-production-{1440,390}-20261022.png`、详情截图及 `output/fs-astro-production-browser-20261022.json`；完整API证据 `output/fs-production-verification-20261022.json`。
- 日志抽样未见FS堆栈/500；原有 `trade_availability` 市场缺失、公告来源567及公告数据库写入异常保留在 `output/fs-production-logs-20261022.log`，属于其他模块，未扩展修复。
- 最终交接与 next-task 另作 `[skip ci]` 文档提交；线上运行镜像仍为本节功能SHA。
