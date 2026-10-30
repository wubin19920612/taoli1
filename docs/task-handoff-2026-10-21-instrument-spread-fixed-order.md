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
- 可控响应验证不能替代真实生产验证，真实行情观察结果见下文。
- 发布流程依照 `docs/git-delivery-checklist.md`、`docs/linux-deployment.md`：仅提交本模块文件，推送当前分支，等待双镜像通过，备份并验证数据库，再以固定 SHA 镜像和 `git pull --ff-only` 更新；生产机不构建。

## 线上交付记录

- 功能提交 `cf92fc97219380ea8385373f08efe93e47ca748c` 已推送当前分支；[GitHub Actions 39529696057](https://github.com/wubin19920612/taoli1/actions/runs/39529696057) 的 backend/frontend 均成功，两份 SHA 镜像 manifest 已核验。
- 生产 Git 与双容器镜像均为上述提交，受跟踪工作区干净；`git pull --ff-only` 和 Compose `up -d --no-build --wait` 成功。前后端 healthy，后端直连及前端代理 `/api/health` 均返回 `status=ok`，12189 markets，`exchange_errors={}`，所有交易所状态 healthy。
- 主库备份：`backups/radar-before-cf92fc972193-20261021T015237Z.db`，656990208 bytes；`integrity_check=ok`，主机与容器 SHA-256 一致：`2010d74af2b005f03814c6949646ead1ed523abd604cd7801cdac03d6f1cdd96`。
- Squeeze route 备份：`backups/squeeze-route-before-cf92fc972193-20261021T015237Z.db`，241664 bytes；`integrity_check=ok`，主机与容器 SHA-256 一致：`c1c26b875328cfcfd617fe6ada08c75fb76aae7db33abf89236a23c81bcd431b`。保留策略本次删除 0 份备份。
- 真实生产浏览器 MEWUSDT、仅显示 FF：初始采样加 8 次自动刷新共 9 个快照，10 行 key 序列始终一致；暂缺组数依次为 `0,0,0,0,0,4,0,0,0`，行情缺失及下一次恢复时没有移除或插入已固定行；缺失行双边价格为 `—`、操作按钮禁用。报价继续变化，页面无脚本错误。
- 解锁后开仓价差降序、390px 手机无整页横向溢出，均经真实生产页面验证。最终证据 `output/spread-order-production-final-20261021.json`，原始监测为 `spread-order-production-browser-20261021.json`，解锁及手机补充为 `spread-order-production-finish-20261021.json`；截图 `spread-order-production-1920-20261021.png` 和 `spread-order-production-390-20261021.png`。监测脚本初次将开/平仓单元格一起检查排序而误报，已修正为仅检查开仓列并补测通过，原始失败日志保留。
- 部署和健康原始记录：`output/spread-order-deployment-20261021.log`、`output/spread-order-production-health-20261021.log`。本文的交付补充以独立 `[skip ci]` 文档提交推送，不重复部署应用。

## 工作区与后续

- 开始时已有 `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 的未提交修改；保留并排除在本次提交之外。
- 保留 `.worktrees/`、既有 pytest 临时目录、旧交接草稿及 `output/` 的研究、日志、截图和 DB/WAL/SHM。新增 `output/spread-order-*` 与验证脚本仅作本地证据，不提交。
- 下一功能模块应另开任务；继续本表时引用本文及 `docs/task-handoff-2026-10-08-instrument-spread-labels-lock.md`，以后者的“消失组不显示”规则被本文的固定槽位规则替代为准。
