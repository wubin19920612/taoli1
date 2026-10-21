# 跨市场差价固定排序：行情缺失时保留行位置

日期：2026-10-21（Asia/Shanghai）。模块仅限标的查询页跨市场差价表。

## 目标、基线与现场证据

- 用户反馈：点击“固定排序”后，刷新时行位置仍会变化。
- 分支：`codex/frontend-localization-polish`；本地基线 `f9d4caae3a7d91680756fe94a17cfbd0944b397d`；发布前生产应用 `df16fc815d4e180a8fe19e736c276a8852536f99`。
- 原因：旧实现只固定当前响应中存在的市场组的相对顺序。后端 `build_instrument_spreads()` 会排除不够新鲜的行情；某个交易所短暂缺失时，涉及它的行全部消失，其余行上移，恢复时再插回。
- 真实生产 MEWUSDT 浏览器采样复现：北京时间 09:44:57 起，固定后可见 FF 行数先为 10，09:45:18 变为 6，09:45:30 恢复 10；共同市场组相对顺序一致。这说明问题来自缺失行被移除，而非固定后重新按数值排序。证据 `output/spread-order-before-browser-20261021.json`。

## 已实现与代码入口

- `frontend/src/pages/InstrumentLookupPage.tsx`：`SpreadOrderLock.rows` 记录锁定市场组的顺序与最近一次有效市场信息。每次刷新优先使用当前响应；未返回的已固定组保留原槽位，标记 `quoteUnavailable`，新增组追加末尾。恢复后原位替换为当前行情。
- `SpreadQuoteCell`：暂缺时双边 Ask/Bid、24h 成交额、资金费率和结算信息全部显示 `—`，明确“行情暂不可用，等待刷新”。开/平仓价差与历史区间同时隐藏，避免旧数据看起来仍可交易；价差查询、建卡和反向建卡按钮均禁用。
- 全部价差暂缺时仍保留表格和解除固定按钮。计数包含固定占位，另行显示暂缺组数；搜索选项保留这些市场。解除固定后丢弃占位，只显示当前有效行情并恢复所选列排序。
- `frontend/tests/InstrumentLookupPage.test.tsx`：更新缺失市场组回归；新增全部行情缺失、搜索、部分恢复、实时报价更新、原 DOM 行复用、禁用建卡及解除固定清除占位的回归。

## 业务边界

- 固定市场组行序；买卖方向仍随当前较优方向更新。精确身份继续包含双方 exchange、market_type、DEX、raw_symbol，方向反转不成为新组；不合并 Hyperliquid 不同 DEX。
- 行情暂缺时缓存只用于市场身份和展示占位，不展示缓存价格、费率或历史值，不允许从占位行创建卡片。
- 用户主动改变搜索或屏蔽类型会改变可见行；切换标的或整页重载解除固定。真正下线的市场在本次固定期间保留占位，解除后移除。
- 不改后端新鲜度阈值、行情采集、价差计算、交易执行、手续费与倍率规则；不干预其他通知模块。

## 验证及交付

- 专项测试：42/42 通过；新增两项先在旧实现上失败，修复后通过。日志 `output/spread-order-page-tests-20261021.log`。
- TypeScript + Vite 生产构建通过，日志 `output/spread-order-build-20261021.log`。测试仍有既有 Ant Design message 的 act 警告，不影响通过。
- 桌面 1920px 和手机 390px 浏览器用真实生产采样作为可控响应：10 个可见 FF 组中移除 4 组、移除全部 10 组、自动刷新恢复及反向排列返回数据时，固定 key 序列始终一致；占位双边价格为 `—`，三个操作按钮禁用，恢复后价格变化，解除后按新数值重新排序。无整页横向溢出、无脚本错误。证据 `output/spread-order-local-browser-20261021.json` 及两张 `spread-order-local-gap-*` 截图。
- 部署和真实生产自动刷新的最终结果在交付后追加；可控响应验证不能替代真实生产验证。
- 发布流程依照 `docs/git-delivery-checklist.md`、`docs/linux-deployment.md`：仅提交本模块文件，推送当前分支，等待双镜像通过，备份并验证数据库，再以固定 SHA 镜像和 `git pull --ff-only` 更新；生产机不构建。

## 工作区与后续

- 开始时已有 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 的未提交修改；保留并排除在本次提交之外。
- 保留 `.worktrees/`、既有 pytest 临时目录、旧交接草稿及 `output/` 的研究、日志、截图和 DB/WAL/SHM。新增 `output/spread-order-*` 与验证脚本仅作本地证据，不提交。
- 下一功能模块应另开任务；继续本表时引用本文及 `docs/task-handoff-2026-10-08-instrument-spread-labels-lock.md`，以后者的“消失组不显示”规则被本文的固定槽位规则替代为准。
