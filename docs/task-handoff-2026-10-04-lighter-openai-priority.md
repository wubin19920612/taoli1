# 标的查询交接：普通 Lighter OPENAI 优先采集

日期：2026-10-04（Asia/Shanghai）

## 目标与范围

- 修复 OPENAIUSDT 标的查询中只有 RH Lighter、没有普通 Lighter 行情的问题。
- 本次只修改 Lighter 自动行情采集的优先市场名单和对应回归测试；不改 Astro、账户连接、下单、交易判断、倍率或资金费率计算。
- 验收目标：普通 Lighter 和 RH Lighter 的原生 OPENAI 同时出现在真实行情列表，分别保留来源、盘口和实例身份。

## 分支、基线与交付

- 分支：codex/frontend-localization-polish。
- 本地开始基线：1277ddbb7214a478578e72818f983a2dcab91d68。
- 部署前生产版本：09f2a30e920c2aa0b985889a6d23e8947e596f8e。
- 功能提交：9a6810883dd163f340faa9ee37eee172f8d208d6；已提交并推送 GitHub，没有 force push。
- 本交接记录属于部署验收后的文档补充；生产应用和镜像仍固定到上述功能提交，不为文档变更重启服务。

## 根因、修复与关键入口

- backend/app/exchanges/lighter.py：LighterAdapter 自动采集最多 48 个永续市场，原优先名单只有 ANTHROPIC、HOOD，其余按 24h 成交额排序。
- 2026-10-04 官方普通实例 orderBookDetails 中，OPENAI 为 active、market_id=192，采样时成交额排序第 67，因此被自动采集名单排除；RH 的 OPENAI 为 market_id=42、成交额排名靠前，所以页面只显示 RH。
- 将 OPENAI 加入 priority_perp_symbols。继续保持 48 个市场的订阅上限、优先订阅和缺失优先盘口时的失败关闭规则，不扩大全市场 WebSocket 负载。
- backend/tests/test_lighter_adapter.py：参数化低成交额优先采集、缺失真实盘口和 Pair 查询实例隔离回归，新增 OPENAI 样例。
- backend/app/services/collector.py：运行两套独立适配器，未修改。
- backend/app/api/routes_instruments.py：GET /api/instruments/OPENAIUSDT，未修改。
- frontend/src/pages/InstrumentLookupPage.tsx：直接显示新增的真实市场，未修改。

## 重要业务规则

- 普通 Lighter 使用自己的 REST/WS、原始 OPENAI 和市场 ID 192；RH 使用独立 REST/WS、原始 OPENAI 和市场 ID 42。不得互补、复制或混用盘口和市场 ID。
- 普通实例现货报价资产为 USDC，RH 实例为 USDG；页面规范符号沿用 OPENAIUSDT，不据此声称抵押/结算资产相同。
- 两套资金费率周期仍为 1h；funding_next_time 仍标为预估，bid/ask 来自真实订单簿，不制造或估算盘口。
- 保留双方各自 24h 成交额、手续费诊断及价格/数量倍率；公开市场 active 和盘口可用不等于账户可以成交。本次没有访问账户私有信息、发送探测订单或执行交易。
- Hyperliquid DEX、原始市场及 OAI/OPENAI 自定义双腿规则未改变，不新增隐式别名。

## 测试与构建

- 先在旧实现复现两个 OPENAI 回归失败：低成交额被排除、缺失 OPENAI 盘口未触发优先市场错误；修复后通过。
- 后端：python -m pytest -q tests/test_lighter_adapter.py tests/test_collector.py tests/test_instrument_lookup.py tests/test_instrument_spreads.py tests/test_pair_spread_query.py tests/test_trade_availability.py -p no:cacheprovider --basetemp=C:/Users/wubin/AppData/Local/Temp/taoli1-lighter-openai-20261004：124 passed。
- 定向 Ruff --select F,E9 与 git diff --check 通过；对实例隔离、资金周期、订阅上限及缺失盘口行为完成差异复核。
- 前端：npm test -- tests/InstrumentLookupPage.test.tsx：30 passed；存在既有 React act 警告，没有测试失败。
- npm run build：TypeScript 检查和 Vite 生产构建通过，前端源码未改动。
- 本地真实 REST/WS 采集：两实例各返回 48 个永续市场，都含 OPENAI，原始盘口和来源不同。
- GitHub Actions 生产镜像工作流 run 37169253832：backend、frontend 两个任务均成功；部署前在服务器核验两份 SHA 固定镜像的 manifest。

## 备份与生产部署

- 部署脚本先备份 /data/radar.db 至 backups/radar-before-9a6810883dd1-20261004T015351Z.db，大小 627765248 字节；integrity_check=ok，宿主机/容器 SHA-256 一致：7b02845f756df9d0173a1880866444b537c0155ccc5d0f7be1bc5fc404030208。
- 同时备份 /data/radar-squeeze-route.db 至 backups/squeeze-route-before-9a6810883dd1-20261004T015351Z.db，大小 217088 字节；integrity_check=ok，SHA-256 一致：a7ab22052792edbccf17dfcc27f7c7f9e6615437c93bbecde5ddd4b5cff88a43。
- 服务器使用 git pull --ff-only 更新到功能提交，拉取两份预构建镜像，再执行 Docker Compose up -d --no-build --wait。没有生产机本地构建、数据库恢复或卷删除。
- 既有备份保留策略自动删除了 3 份较旧 radar 备份和 2 份较旧 squeeze-route 备份；本次两份新备份均保留。
- backend/frontend 均 healthy，镜像标签及服务器 HEAD 均为功能提交；服务器跟踪文件工作区干净。
- /api/health：status=ok，采样市场数 11797，exchange_errors={}；lighter 和 rh-lighter 采集状态均 healthy。前端首页 HTTP 200。

## 线上实际验收

- 直连后端与经前端代理 GET /api/instruments/OPENAIUSDT 均返回两条独立 OPENAI 行情；覆盖由 7/9 升为 8/9，市场由 1 现货、7 永续升为 1 现货、8 永续。
- 2026-10-04 09:58:58（Asia/Shanghai）采样：普通 Lighter bid/ask=1674.67/1674.85，RH=1666.22/1666.37；来源分别为普通 Lighter 与 Robinhood Lighter 公共数据，资金周期均为 1h。价格仅为验收时点快照，不是交易建议。
- 经前端代理 GET /api/trade-status/OPENAIUSDT：errors={}；普通和 RH 都返回各自的 active 公开状态来源，桌面页面可以分别看到实时盘口深度和公开交易限制诊断。
- Chromium 桌面 1440px、手机 390px 都显示 Lighter 和 RH Lighter，两端无页面横向溢出，没有 pageerror；诊断加载完成。
- 未跟踪验收截图：output/lighter-openai-production-desktop-20261004.png、output/lighter-openai-production-mobile-20261004.png。

## 已知边界、未跟踪产物与下一步

- 全局自动采集仍保持 48 个永续市场；其他未加入优先名单的低成交额市场可能不进入全局快照，本次没有改成全市场扫描。
- 上游 REST/WS 可能短时失败，优先盘口缺失会失败关闭而不是制造价格；公开诊断不保证账户余额、地区权限或实际下单可用。
- 工作区开始时已有 .worktrees/、backend/.pytest_*、其他模块交接文档、output/ 截图和脚本、script/dexe_bybit_bitget_chain.py 等未跟踪文件，全部保留，没有暂存、删除或覆盖。
- 本任务只提交 Lighter 适配器、对应测试与本交接记录；新生成的验收截图保留在本地，不提交仓库。
- 本模块已完成实现、验证、推送、备份、部署及线上验收。其他模块请新建任务，并以本记录继续交接；若再排查某个 Lighter 标的，先比较两实例官方市场详情、成交额排名、优先名单、真实订单簿和采集状态。
