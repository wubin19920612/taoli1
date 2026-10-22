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

后端 FS、身份目录、Astro planner、账户回归与后续追加测试的最终结果见下方部署补记。前端 FS 6项及 AppShell12项通过，类型检查/Vite生产构建通过。现有 Starlette 弃用告警和 React act 测试告警保留；首次新前端断言因手机合并文本失败，已修正选择器，失败日志未删除。

模拟覆盖账户权限不足、零库存、身份冲突/变化、过期、账户禁用/修改、在途配置变化、查询失败；Astro并发去重、已有卡片、写入结果不确定、跨重启 pending、dry run 与失败脱敏。真实公开三轮见 `output/fs-public-probe-20261022.json`；不能将模拟成功说成真实账户或实盘收益验证。

## 工作区与后续

保留 `frontend/src/pages/PairMonitorPage.tsx` 和 `frontend/tests/PairMonitorPage.test.tsx`；起始/交付哈希分别为 `11379f2cb0881b274a998a4a4534a698c4dd94b0080de3de691f4dbf27440d38`、`0376b098c2274e60666915709d9d1183decac45781a861faac452aacb00500ab`。

所有既有未跟踪文件、`.worktrees/`、pytest临时目录、DB/WAL/SHM、日志、截图保留。本轮 `output/fs-*20261022*` 为不提交的证据。自动建卡信号仍依赖公开利率和配置手续费、静态退出盘口假设；真正借贷能力与交易执行留在 Astro 侧由用户复核。其他模块请新建任务。

## 部署补记

待完成预构建镜像、双库备份和线上验证后更新本节；当前不能把尚未部署的改动写成线上已完成。
