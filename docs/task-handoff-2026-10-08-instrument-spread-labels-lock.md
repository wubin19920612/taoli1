# 交接：跨市场差价数据标签与固定排序

日期：2026-10-08（Asia/Shanghai）

## 目标与范围

- 模块：标的查询页的跨市场差价表。
- 验收目标：双边的资金费率、结算周期、24h 成交额、盘口角色与更新时间易于区分；点击固定排序后，行情刷新继续更新数据且已有市场组保持相对顺序，解除后恢复所选列的实时排序。
- 当前分支：codex/frontend-localization-polish；本次基线 159750d（Arcus 生产验收文档），生产应用基线 43f9345d7b07a306f4b0a1294bcc36c841cbf058。
- 本次仅修改前端显示、排序与回归测试，不改变后端接口或资金费率计算。

## 已完成与代码入口

- frontend/src/pages/InstrumentLookupPage.tsx：SpreadQuoteCell 分别显示买卖盘口、24h 成交额（USDT）、每期资金费率、每几小时结算、下次结算时间（北京时间）与行情更新时间；成交额悬停可查看完整金额。
- 同文件 spreadGroupKey：使用双方 exchange、market_type、dex、raw_symbol 的无方向市场组身份；保留 Hyperliquid 的主/子 DEX 与原始市场，最优买卖方向反转后仍复用同一行。
- 同文件 spreadSort / spreadOrderLock：保留原有列排序功能；固定当前所选排序的全部市场组顺序，新增组追加末尾，消失组不显示旧数据，重新出现的组回到记录顺序。搜索和屏蔽筛选不覆盖固定顺序，切换标的解除固定。
- 固定时按钮显示“解除固定”，aria-pressed=true；锁定期间列排序暂时关闭，解锁后恢复原来所选的排序列与方向。
- frontend/src/styles.css：成交額、费率和周期的独立标签与清晰字号；手机端补充 Ask/Bid 买卖角色、24h 历史最大价差说明，并将操作按钮独立铺满一行。
- frontend/tests/InstrumentLookupPage.test.tsx：回归覆盖零费率、缺失/非法周期、现货不适用、按字段预估标志与资金费率周期；固定后的实时价格更新、方向反转、DEX 隔离、新增/消失/恢复、搜索、解锁、用户指定列排序及换标的。

## 业务规则与限制

- 资金费率是该市场每次结算的费率，保留各自的 1h/4h/8h 等周期；正值多头支付空头，负值空头支付多头，不把不同周期的原始费率直接相减。零费率正常显示，未知费率/周期明确“未提供”；现货不假造费率或周期。
- 成交额为各市场最近 24h 的金额，显示 USDT 口径，沿用后端现有数据口径；资金费率、成交额等字段的预估标志优先读取精确市场 estimated_fields，缺少字段列表时保守沿用 is_estimated。
- 价格沿用后端倍率归一结果；双边显式显示价格倍率，保留数量乘数与数据源的已有提示。
- 开/平仓价差仍来自 Ask/Bid，明确未扣手续费及滑点；24h 最大价差来自同分钟收盘价，仍标为历史预估，不能当作历史可成交价差。
- 固定的是本页当前标的的市场组排序，不冻结行情、不保存过时的消失行情；真实市场增删、方向变化导致屏蔽类型变化仍可能改变可见行数。整页重新加载或换标的会解除固定。
- 不处理同名币归并、原生 USD/USDT 换汇、手续费个性化、其他页面或交易执行。

## 本地验证

- 标的查询专项测试 37/37 通过，TypeScript 与 Vite 生产构建通过；最终版检查记录存于 output/spread-labels-lock-tests-20261008.json。
- 浏览器使用生产只读接口采集的 METUSDT 行情作为本地可控响应（13 个精确市场、78 个市场组，默认可见 21 组），将返回顺序和价差排名反转并更新 Ask：10 秒自动刷新后 21 个可见组的 key 顺序一致，价格更新，无浏览器脚本错误。
- 1920px 桌面与 390px 手机显示检查，无整页横向溢出；截图为 output/spread-labels-lock-local-1920-20261008.png 与 output/spread-labels-lock-local-390-20261008.png。这些可控响应不等于部署后的真实行情验收。
- 无后端代码变更，后端验收使用真实生产健康接口和标的接口。

## Git 与线上交付

- 按 docs/git-delivery-checklist.md 与 docs/linux-deployment.md，仅暂存本次页面、样式、专项测试与本文档。
- 等待两份固定 SHA 镜像完成后，部署脚本备份 /data/radar.db 和存在的 squeeze route 数据库，核验 integrity_check 与主机/容器 SHA-256，再 git pull --ff-only 与 Compose up -d --no-build --wait。生产机不执行构建，不删除数据卷，不修改 .env。
- 发布前确认生产分支正确、受跟踪工作区干净，两个容器 healthy，/api/health status=ok 且 exchange_errors 为空，可用磁盘约 17 GiB。
- 功能提交 6883583d1292b4b663f60f396b576f6a99905812 已推送当前分支；GitHub Actions 37751400608 的 backend/frontend 两项构建均成功，两份 SHA 镜像 manifest 已在生产机核验。最初 Git HTTPS TLS 握手失败，使用单次命令的 http.version=HTTP/1.1 与 http.sslBackend=openssl 后推送成功，没有关闭证书校验或改全局配置。

## 线上最终验收

- 生产源码与两个容器镜像均为 6883583d1292b4b663f60f396b576f6a99905812；受跟踪工作区干净，两个容器 healthy。本文最后的验收补充作为单独文档提交，不是第二次应用部署。
- /data/radar.db 备份：backups/radar-before-6883583d1292-20261008T084257Z.db，610893824 bytes；integrity_check=ok，容器/主机 SHA-256 一致：8cc6640bf504bf7e24faeeca0743e93845461ff3756aec66e96f7ed48a0e2901。
- Squeeze route 备份：backups/squeeze-route-before-6883583d1292-20261008T084257Z.db，241664 bytes；integrity_check=ok，容器/主机 SHA-256 一致：6d7b66c35062c558b08dc1be97106940b8a3530645a66444c91f64a9cc981910。
- 更新使用 git pull --ff-only 和固定 SHA 镜像的 Compose up -d --no-build --wait，没有在生产机构建。既有部署备份保留策略自动保留本次备份及选中的历史备份，清理了 2026-10-07 的 345fd90 重复近期备份。
- 后端和前端代理 /api/health 均 HTTP 200，status=ok，11841 markets，exchange_errors 为空，所有交易所 healthy。未改后端 API 或交易执行。
- 经验证的 SSH 隧道访问真实生产前端（HTTP 200），1920px 桌面 METUSDT 差价表固定后观察到 7 次成功行情返回：原有可见市场组的相对顺序一致，双方时间戳与报价更新；新增/可见市场组变化使行数从 21 变为 22，未沿用旧行情。解除固定后按当前可成交开仓价差降序排列。
- 390px 手机真实页面显示 21 个组，每组均有买入 Ask（卖一）/卖出 Bid（买一）角色标签、资金费率、各自结算周期、24h 成交额；固定按钮 aria-pressed=true。桌面/手机均无整页横向溢出、无浏览器脚本错误，实际下次结算时间为北京时间。
- 线上截图：output/spread-labels-lock-production-1920-20261008.png、output/spread-labels-lock-production-390-20261008.png，便于查看表头与前三行的裁剪为 output/spread-labels-lock-production-preview-20261008.png；真实页面/行情验证 JSON、部署与健康日志也保留为本任务 output 产物，不提交仓库。
- 已知限制：固定顺序只在本页当前标的有效；整页重载或换标的解除。市场真实增删或被屏蔽类型的方向改变仍会影响可见行数；现有历史统计缺失/加载中的提示、诊断过期提示按原逻辑保留，不把这些情况伪装成新数据或零值。

## 工作区与下一步

- 任务开始时已有 frontend/src/pages/PairMonitorPage.tsx 与 frontend/tests/PairMonitorPage.test.tsx 的未提交修改；保留且不暂存、不发布这些修改。
- 既有未跟踪的 .worktrees/、pytest 产物目录、两份旧交接草稿及 output/ 的日志/截图/研究资料均保留。
- 本次新 output/spread-labels-lock-* 采集 JSON、测试报告、浏览器截图与部署日志不提交仓库。
- 下一功能模块请新建任务并引用本文档；若继续改同表，保留精确市场身份、字段预估标志、双边周期与成交额、Ask/Bid 和费用/倍率口径。
