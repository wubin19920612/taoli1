# 市场身份与价差有效性校验实现与交接

模块为“市场身份与价差有效性校验”。目标是解决 PRL 一类同名异币、单位不一致及参考行情被当作可成交价格的问题。所有同币价差展示、告警和 Astro 新建入口共用判定，保留明确指定的跨资产研究能力。

当前功能已实现并完成本地验证，实施基线为 `91a6d16`，分支仍为 `codex/frontend-localization-polish`。本节记录最终实现；后文保留原设计作为业务约束参考，其中原定全市场严格拦截由下面的用户上线选择取代。部署证据在交付后更新。

2026-10-15 实施时用户已授权实现，并明确选择分阶段上线：**未知身份先观察，保留现有未知市场流程；已确认不同资产和身份冲突直接拦截，逐步补齐资料后扩大严格范围。** 此选择优先于下文原设计中要求全市场立即 enforce 的条款。行情质量仍需如实标记，不能把参考价标为可成交价；已确认同名冲突的代码不能继续以未知映射绕过核验。

## 实施记录（2026-10-15）

- 已实现精确 `(exchange, market_type, dex, raw_symbol)` 登记、证据期限、规格指纹、CAS 版本修改、审计与去重事件。身份使用专用 SQLite 连接及事务锁，原表仅增列/增表，旧风险位不重排。
- 统一入口为 `services/pair_validation.py` 与 `services/market_validation_review.py`。机会生成、查询页、资金费雷达、告警、手动与自动 Astro 新建均接线。身份、规格、行情问题不能由人工 override 清空。明确跨资产研究只能手动；每次实际 SDK add 前重新检查最新精确市场和盘口。
- HL 中间价明确标记 reference/estimated，真实盘口通过原始 DEX/市场取回。USDC/USDT 换算使用有效的真实双边汇率，禁止默认 1:1；成交量未知保持未知。时效分别检查双腿及时间差，上游未提供时间时明确提示本地接收时间。
- 大价差需求驱动复核，复用已有深度成交算法；两次独立盘口间隔至少 1 秒，结果只对相同映射版本、规格、报价和有效期有效。并发、排队、缓存及失败冷却均有界，调度优先未检查市场避免大异常长期占满队列。
- PRL 有固定日期、7 天期限的初始官方证据，重启不会延长。官方目录按 Gate/Bitget 真实链与合约地址匹配，其他场所有限的原生资产及 Perle/Wormhole 使用明确证据；不按名称广泛合并。占位/无效地址、股票与 pre-market 不自动认证为加密资产。
- 设置页新增核验表、筛选、覆盖率、观察/严格切换、证据与单位编辑、变更影响预览及只读卡片审计；敏感写接口和卡片审计沿用仪表盘口令。前端显示 Perle/Pearl 分组、精确市场、待核验与参考价标识。
- 初始目录回放：12,042 个市场中 916 verified、11,126 pending、0 conflict；925 条目录变更写入（部分市场当前不活跃）。旧 6,936 个候选变为 6,929，只去掉 7 个 PRL 错配，保留其他 ticker；Perle 对 `io:PRL` 的同币机会为 0。此回放故意保留旧 quote_kind，仅验证身份影响，不是实盘成交模拟；参考价与行情质量变化须看部署后的实际接口。
- 本地验证：大范围后端 688 项通过、3 项失败，分别修复 SDK 错误分支误捕获、真实盘口 mock 缺失、过期手动行情旧预期；针对三项及相关模块复测 40 项通过。之后新增过期证据不得恢复错配、无效地址边界，核验/路由/盘口等 45 项通过，Bitget 3 项通过。前端相关 65 项覆盖通过，`npm run build` 通过；新后端模块 Ruff 通过。全量测试日志保留于本地 output，未重复运行整个 10 分钟大套件。
- Playwright 在 1440/390 宽度检查查询页、设置页、编辑框：无页面脚本错误、无横向溢出。本地 fixture 无生产凭据；仅用于布局核验。
- 最终采集链路及交易所容错回归另 59 项通过。
- 线上页面实查补齐摘要边界：未选择具体资产时，不混算价格区间、基差、成交额及比例，冲突提示置于摘要之前；选择资产后恢复该组汇总。同名资产尚无已核验 CoinGecko ID 绑定，因此不展示 ticker 推断的流通市值比例。补充回归后，查询页与核验面板 40 项通过，类型检查及生产构建再次通过。
- 已有 `PairMonitorPage.tsx` 与对应测试保持原样且排除提交。其余未跟踪 output、pytest、worktrees、其他交接文档和脚本均保留，不提交。

后续工作限定为补充未知市场的官方身份证据及观察复核负载；新模块请另开任务。已有外部卡片仅审计，本次不自动暂停、删除、改参或推断实际持仓。严格模式须由用户结合覆盖率另行切换。

## 1 基线与工作区

- 设计日期：2026-10-15，Asia/Shanghai。
- 仓库：`C:\Users\wubin\Desktop\code\codex\taoli1`。
- 当前分支：`codex/frontend-localization-polish`。
- 设计时本地 HEAD：`e25d5036f38faf349addc367d9a3f7e40a53cf64`。
- 前序只读排查观察到的生产源码 HEAD：`1ff56381cb95cb88f7cf42ef216f2ec721bb454c`。它与本地不同；不能假定线上镜像 SHA 等于源码 SHA。实现和部署前重新读取本地、远端、生产源码及运行镜像版本，核对差异。
- 已有未提交修改：`frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx`。保留，不覆盖，不整体纳入本任务提交。如确需同文件接入，先辨认已有差异，采用隔离 checkout 或精确补丁，只提交本任务部分。
- 已有未跟踪内容包括 `.worktrees/`、多个 `backend/.pytest_*` 临时目录、`output/` 中的探针与截图、`script/dexe_bybit_bitget_chain.py` 及其他模块交接文档；完整清单以开工时 `git status` 为准。禁止清理或纳入本次提交。
- 设计阶段未运行应用测试，未备份、部署或修改外部 Astro 卡片。此前 `/api/health` 只读核验为 ok、`exchange_errors={}`；不代表问题已经修复。

## 2 已确认问题与证据

### 2.1 PRL 错配

2026-10-15 北京时间约 09:11 至 09:13，生产 `/api/instruments/PRL` 和 `/api/opportunities?symbol=PRL&include_risky=true` 将 CEX `PRLUSDT` 与 Hyperliquid `io:PRL` 配对，后者明确保留 `dex=io`，但共同使用规范化 symbol `PRLUSDT`。

| 市场 | 身份证据 | 当时行情 |
| --- | --- | --- |
| Bitget PRLUSDT | 官方公告名称 Perle | 公共 ticker 买一 0.1009、卖一 0.1011；24h 成交额约 174270.92 USDT |
| Bybit PRLUSDT | 官方永续公告名称 Perle，官网 perle.xyz | 公共 ticker 买一 0.1008、卖一 0.1009；24h 成交额约 360201.19 USDT |
| Hyperliquid io:PRL | 官方 API annotation 返回 displayName=PEARL、keywords=[prl, pearl]；perpDexs 返回 io 为 EntropyIO | 公共 l2Book 买一 1.9314、卖一 1.9360；metaAndAssetCtxs 的 24h 名义成交额约 2477653.47 |

上述是不同请求时刻的证据快照，不能用来模拟同秒成交。CEX 资金费为 0.005%/4h；HL 当时约 0.03855%/1h。系统价格倍率均为 1；Bybit 的合约数量倍率字段缺失，不能把缺失当作已核验的 1。费用为系统估算，不是账户真实费率。

核验来源：

- [Bitget Perle 上币公告](https://www.bitget.com/zh-CN/support/articles/12560603881229)。
- [Bybit PRLUSDT 永续公告](https://announcements.bybit.com/zh-MY/article/new-listing-prlusdt-perpetual-contract-in-innovation-zone-with-up-to-25x-leverage-blt5c4784cb046e26e6/)。
- [Entropy 文档](https://docs.entropy.io/)确认其 DEX 名称为 io。
- [Hyperliquid Info API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)。公共 POST `https://api.hyperliquid.xyz/info`，查询体分别为 `{"type":"perpAnnotation","coin":"io:PRL"}`、`{"type":"metaAndAssetCtxs","dex":"io"}`、`{"type":"l2Book","coin":"io:PRL"}`、`{"type":"perpDexs"}`。实施时重新采集证据并记录时间；annotation 名称是证据之一，不应推广为仅凭 displayName 就能自动核验所有资产。

截图中 180.20% 可由项目当前均价分母公式复现：`2 × (1.9397 − 0.101) / (1.9397 + 0.101) × 100 ≈ 180.2029%`。核心错误是底层资产身份匹配；不得为了“修好数字”改变公式、猜测倍率或全局拉黑 PRL。

生产查询当时还返回三张涉及 HL 的 PRL 路线，分别对 Bitget、Bybit、Binance。仅能证明路线存在，不证明持仓、开关状态或创建来源；新任务应重新只读审计。

### 2.2 代码中的共同缺口

- `exchanges/hyperliquid.py::_parse_perp_rows` 去掉 DEX 前缀生成规范化 symbol；除优先盘口市场外，将 midPx 填为 bid/ask 且未标记这两个字段为估算。
- `services/spread_engine.py::_group_tradable_markets` 按 `snapshot.symbol` 分组。虽然机会 ID 保存原始市场身份，但原始身份没有决定“是否同一个资产”。
- `services/symbol_aliases.py` 处理显示名称与价格转换，没有独立资产 ID、来源及核验生命周期。旧别名不能自动升级成已核验身份。
- `services/instrument_spreads.py::build_instrument_spreads` 有独立比较流程。只修主机会引擎，详情页仍可能错误比较。
- `services/risk_labels.py` 有 `HUGE_SPREAD_VERIFY` 和人工维护的 `SAME_TICKER_RISK`；这属于价格/名单提示，不是身份认证。
- `services/astro_planner.py` 的人工 override 会把部分 blockers 转成 warnings。身份、单位和必需行情的阻断不能放在会被清空的集合里。
- `services/risk_labels.py::known_volume_24h_usdt` 取已知成交额的最小值；一侧未知不等于双边都满足条件。新资格判断必须保留各腿未知状态。
- `db/repositories.py` 将风险标签编码成固定位掩码。新增标签必须同步持久化，否则历史会丢失原因；既有 bit 含义不得重排。

## 3 本次实现范围和完成定义

本次 V1 必须完成：独立资产与精确市场登记、证据和版本、统一判定服务、PRL 拆分、字段级行情质量、异常复核、所有相关入口接线、最小核验管理界面、已有卡片只读影响报告、测试和部署交付。

V1 不建设通用网页爬虫、LLM 自动裁定系统、全网资产图谱、复杂统计异常模型或自动平仓系统。不修改其他模块的策略参数，不自动操作旧卡片，不扩展公告或指数业务。

验收必须同时满足：

1. Perle 的正常 CEX 配对仍可用；Perle 与 `io:PRL` 不再作为同币机会进入告警或新建卡。
2. DEX、原始市场、报价币种和合约规格不因规范化或 GC 路由丢失。
3. 未核验身份、单位不明、缺少必需可成交报价，不得通过人工 override 或显示风险开关进入同币交易动作。
4. 已核验同资产的真实大价差可以在复核通过后继续参与策略判断。
5. 未知市场仍可搜索和查看原始行情，用户能够知道被拦截的原因并完成核验。
6. 开启严格模式前，提交存量覆盖率和受影响列表；不能把机会数量归零或关闭原有工作流当作修复成功。

## 4 身份与数据模型

### 4.1 原始市场键

逻辑键使用结构化元组 `(exchange, market_type, dex, raw_symbol)`，必要时增加交易所产品类别。不要简单用冒号连接再拆分，因为 raw_symbol 自身可能包含冒号。

Hyperliquid 默认 DEX 显式记为 main，io/xyz/para 等分别保存。仅在缺少 dex 且 raw_symbol 自带唯一明确前缀时补全；显式 dex 与原始前缀冲突应报错，不选其一继续。只有 ticker 的旧卡片若可匹配多个市场，必须标为歧义。

市场代码被下架后重用时，以规格指纹和生效区间区分新旧版本；不能永久继承前一合约的核验。

### 4.2 新增持久化对象

建议按现有 SQLite schema 初始化方式扩展，使用专用 repository，避免所有逻辑继续堆进 SettingsRepository。

| 对象 | 核心字段 | 约束 |
| --- | --- | --- |
| AssetIdentity | asset_id、display_name、asset_kind、项目/网络/合约标识集合 | ID 稳定，不使用 ticker 作为唯一 ID；原生币允许没有合约地址 |
| MarketBinding | 原始市场键、asset_id、identity_status、unit_status、规格、evidence_refs、revision、verified_at、valid_until、spec_fingerprint | 一个精确市场当前只有一个有效版本；历史版本保留 |
| IdentityEvidence | source_type、官方 URL 或固定 API 查询信息、必要响应摘要/哈希、fetched_at | 不存凭据；没有证据不能标 verified |
| ValidationIncident | 精确市场或配对键、reason_code、first_seen、last_seen、state、次数、最近快照摘要 | 同一原因合并更新，保留状态转换；禁止每轮插入大量相同记录 |
| IdentityAuditEvent | 变更对象、旧新 revision、变更原因、操作者、时间 | 修改映射、拆分资产和撤销核验均留痕 |

`identity_status` 使用 `pending / verified / conflict / expired`。这描述单个绑定是否可信；两个各自 verified 的市场如果 asset_id 不同，配对关系仍是 different_asset。

规格至少区分：原始报价币种、结算币种、每份报价代表的底层单位、每张合约的数量、线性/反向等合约类别、跟踪对象或指数定义。数量、价格、费用换算采用明确方向的转换，不复用一个 multiplier 包办所有含义。

示例：交易所价格为每 1000 个 token 的报价，则标准单币价格为原价除以 1000、相同报价单位对应的底层数量乘以 1000。不得未经检查再次乘合约面值；以金额换算前后不变作为测试不变量。

### 4.3 统一判定结果

新增 `PairValidationResult`，至少包含：

- `comparison_kind`：`same_asset` 或 `custom_cross_asset`。
- 双腿精确市场键、asset_id、binding_revision、spec_fingerprint。
- `identity_relation`：`same / different / unresolved / conflict`。
- `unit_status`、`quote_status`、`anomaly_status`，分别表示单位、行情和异常复核情况。
- `capabilities`：允许原始行情查看、参考比较、同币机会、策略告警、自动建卡、人工同币建卡等独立能力。
- `blockers` 和 `warnings`：稳定 reason_code、中文说明、受影响腿、证据引用。
- `validated_at`、`expires_at`、双腿行情时间及本次报价指纹。

全部门槛通过仅表示可以进入原有策略校验，不代表适合交易或保证成交。额外业务限制依然生效。

## 5 核验规则与状态变化

### 5.1 身份证据

认可来源包括交易所官方资产元数据、明确的标的项目链接、合约规格、官方上币/迁移公告。链与合约地址需要一起匹配；不同链的包装资产只有明确的映射证据才能建立对应关系，不能按名称自动合并。

V1 采用“官方结构化数据辅助 + 持久化人工核验”方式，优先覆盖用户当前关注、规则涉及和已有卡片涉及的市场。交易所 API 不提供底层身份时，进入待核验；不以网络搜索片段、价格接近或走势相关作为自动认证。

旧 symbol_aliases 迁移为候选绑定。已经存在明确证据的转换可建立核验记录；没有证据的仍为 pending。旧 API 可兼容显示别名，但不能绕过身份系统单独改变交易资格。宽泛 HL 别名不得自动覆盖所有 DEX。

PRL 首批登记只覆盖本次重新核验的具体市场。以精确市场区分 Perle 和 Pearl，其他同 ticker 新市场默认 pending，不能继承结论。改 asset_id 不改交易所原始下单代码。

### 5.2 行情与单位

行情模型新增或补充 quote_kind、原始报价币种、报价来源、字段级时间和估算字段。`bid/ask` 缺失可以采用 nullable 新字段或明确 reference 子对象兼容旧模型；不得用 0 或未标注 midPx 填充必需报价。

HL 全部 midPx 代理都标为参考报价。为避免所有 HL 机会消失，按两步处理：参考价先用于发现候选；只有身份/单位已核验且接近触发条件的候选，按需获取各自精确 coin 的 l2Book，再计算交易机会。未知身份不应消耗高频盘口请求。

沿用现有 stale_after_seconds，新增可配置双腿最大时间差，V1 默认 10 秒；实际告警和建卡以二次盘口校验的更新数据为准。报价没有上游时间时可以记录本地接收时间及其可信度，但不能宣称是交易所事件时间。来源无法证明足够新时降级。

价格一致不等于有订单簿。区分可观察的买卖报价与目标金额实际深度；深度验证复用现有 OrderBookDepthValidator，对每条腿分别验证。盘口失败不得回退到 midPx 放行。

成交额、资金费周期、费率及下一次结算时间分别保留；缺失记 unknown。需要该字段的策略阻断，不需要资金费的现货比较不因资金费缺失被阻断。手续费和资金费未来收入的估算必须在输出中可见，不把预测当已结算。

USDT/USDC 等原始报价币种不能直接抹掉：记录转换来源和时间。V1 如采用近似平价，仅能输出明确标注的参考比较；可执行比较须有有效的共同计价转换。不能为了本次改造静默使用 1:1，也不能静默砍掉全部跨稳定币市场，必须在覆盖率报告说明采用的转换实现及影响。

### 5.3 异常复核

V1 以现有 huge_spread_pct 触发异常复核，保留现有百分比定义，不新增未经验证的复杂阈值。大价差不改变资产 ID，也不自动修改倍率。

状态为 `normal → pending_review → passed / failed`。通过条件是身份和单位有效、最新双腿真实盘口及目标金额深度有效、时间差合格、连续两次独立采样确认；两次采样间隔至少 1 秒，不阻塞采集主循环。随后仍执行原有策略风险条件。

复核结果绑定双腿 binding revision、规格和报价指纹，V1 最大有效期 15 秒，并不得超过任一行情或身份有效期。提交时拉取当前数据并重判；缓存结果只能减少重复请求，不能证明新价格仍可成交。已确认身份冲突不能因价格靠近自动恢复。

`HUGE_SPREAD_VERIFY` 需要与复核状态协调：pending/failed 拦截，passed 不再被同一个“待核验”标签永久阻断。可以保留独立的大价差提示，让用户原有策略决定是否接受；不得清除其他风险标签或自动放宽阈值。

## 6 统一执行边界

建议新增 `services/market_identity.py` 负责绑定解析，新增 `services/pair_validation.py` 负责纯判定，单独的异步复核协调器负责元数据与盘口 I/O。纯判定函数不访问网络。

处理链路：原始采集 → 精确市场解析 → 有证据的身份/单位转换 → 参考候选 → 真实盘口及异常复核 → 合格机会 → 既有策略条件 → 提交前复核。

| 入口 | 必须行为 |
| --- | --- |
| 主机会引擎 | 按已核验 asset_id 和可比规格形成同币候选；无映射保留在原始市场库，不按 ticker 默许 |
| 单币详情及交易所价差 | PRL 搜索可返回多个资产分组；默认禁止跨资产拼成同币表格；旧 symbol-only 查询返回歧义提示或候选，不能随机选中 |
| 告警及资金费机会 | 资格校验先于业务规则；include_risky、excluded_risk_labels 等只改变业务展示/筛选，不能放行身份阻断 |
| Astro 预览、人工、自动、预建、实盘 | 复用同一判定，并在最终 add 调用前校验最新 revision 和行情；manual_override 不能清空硬阻断 |
| GC 路线 | 先确认底层市场，再执行既有 Gate CrossEx 支持校验；GC 别名不生成新资产或替换 DEX |
| 自定义跨资产研究 | 显式 comparison_kind，精确指定双腿；可展示跨资产参考价差，不冒充 same_asset |
| 已有明确跨资产人工交易流程 | 保留原有权限边界和路线；双方身份、单位和必需行情仍需已核验；不能自动转为同币告警或扩大自动交易授权 |
| 历史、收藏和预设 | 逐步保存精确市场、资产 ID 和映射版本；歧义旧记录待选，不自动改绑；原始历史不覆盖 |

不得只在前端禁用按钮：后台直接请求、过期 preview、预建伪造 Opportunity、人工 override、反向建卡及重试路径都必须被测试覆盖。对同一动作内部应使用一致的 binding revision 快照；提交前发现 revision 变化则终止并重新预览。

建议 reason_code 至少包含 `ASSET_IDENTITY_UNVERIFIED`、`ASSET_IDENTITY_CONFLICT`、`DIFFERENT_UNDERLYING_ASSET`、`UNIT_UNVERIFIED`、`DEX_IDENTITY_CONFLICT`、`REFERENCE_QUOTE_ONLY`、`STALE_LEG_QUOTE`、`LEG_TIME_SKEW`、`QUOTE_CONVERSION_UNVERIFIED`、`DEPTH_UNCONFIRMED`、`SPREAD_REVIEW_PENDING`、`MAPPING_CHANGED`。后端以 code 判定，中文文案只用于展示。

## 7 最小管理接口与前端

建议新增 `/api/market-identities` 路由，复用现有受保护设置写入的认证和授权方式，不新增匿名可写接口。实际 URL 可依现有风格调整，但须提供以下能力：

1. 查询精确市场绑定、证据、当前状态和受影响配对。
2. 查询待核验事件及已有卡片影响，支持市场、DEX、状态筛选和分页。
3. 创建/修改绑定，要求 expected_revision、原因及证据；旧 revision 提交返回 409，禁止最后写入静默覆盖。
4. 撤销核验、拆分资产、补充官方倍率证据；变更立即失效相关缓存和旧资格。
5. 手动触发只读复核，合并重复运行，不直接创建卡片或下单。

如需要 API 拉取证据，只允许固定官方 provider 和固定查询参数；不要让管理员输入任意 URL 后由服务器直接抓取。证据 URL 可保存，但抓取必须按已支持来源受限执行，避免 SSRF。

前端以现有 SettingsPage 增加“市场核验”区域为主，不额外重做整个设置页。展示市场原始代码、DEX、资产名称、证据、单位和状态；修改提供可预览的影响数量。

InstrumentLookupPage 在同名异资产时分组显示。机会列表、详情和浮窗显示“参考报价/身份待核验/已确认不同资产”等准确状态，提供查看原因入口。空值显示“未知”，不显示 0；仍可看见原始行情。

V1 事件只在本地管理界面和日志记录；如复用已开启的异常通知，必须遵守现有通知授权和开关，不新增收件人、Webhook 或默认开启通知。通知以状态转换去重，不每轮重复发送。

## 8 存量卡片与历史处置

提供只读审计结果：card_id、精确双腿、匹配状态、可读阻断原因、建议处理。不得从“路线存在”推断卡片启用或仓位为零；无法读取的状态明确标 unknown。

本次可阻止本系统对不合格配对继续发起自动新建或增加风险敞口的动作，但不自动向 Astro 发送 pause/update/delete，不改腿，不平仓。即使新建入口已拦截，已有外部卡片仍可能继续运行；交付时必须单独报告，不能写成“全部已停用”。

身份限制不得阻碍已有仓位的风险降低操作或持仓查看。需要修改外部已有卡片时，作为独立、明确授权的处置步骤执行。

历史错误配对保留原始数据和原市场键，新增 invalidated/unverified 标记或附属失效记录；修复后的统计默认排除已知错误匹配，允许审计查看。不可把旧 Perle/Pearl 混合序列直接当作正确资产的历史。

现有机会 ID 的稳定性需做兼容评估。建议保留原精确双腿 ID 算法，以额外 mapping revision 表示资格变化，避免无必要地重置收藏、冷却与卡片去重；有歧义旧 ID 不可静默重定向。

## 9 代码入口与建议分工

以下路径相对仓库根目录，Sol 开工时需核对最新代码。

| 层 | 已有入口 | 改动重点 |
| --- | --- | --- |
| 模型 | backend/app/models/market.py、opportunity.py、pair_spread.py、settings.py | 身份引用、质量字段、资格结果及兼容默认值；默认不得等于 verified |
| 数据库 | backend/app/db/schema.py、database.py、repositories.py | 增量幂等迁移、新 repository、审计、风险标签编码 |
| 身份解析 | backend/app/services/symbol_aliases.py | 保留兼容能力，转换由精确市场规格驱动，禁止重复缩放 |
| 采集 | backend/app/exchanges/hyperliquid.py、services/collector.py | HL 参考报价、原始计价信息、候选与可执行机会分层 |
| 机会及详情 | backend/app/services/spread_engine.py、instrument_spreads.py、api/routes_instruments.py、api/routes_opportunities.py | 共用身份和资格规则，解决同名查询歧义 |
| 盘口及风险 | backend/app/services/orderbook_validator.py、risk_labels.py、alert_engine.py | 复用深度校验，分离硬阻断与可配置策略条件 |
| Astro | backend/app/services/astro_planner.py、astro_alerts.py、astro_preadd.py、live_pilot.py、api/routes_astro.py | 各入口及最后提交关口，保留 DEX/GC 支持规则 |
| 其他机会消费者 | backend/app/services/funding_arbitrage.py、opportunity_radar.py、pair_spread_query.py | 共用有效性结论；跨资产模式显式隔离 |
| 历史与查询 | backend/app/services/history.py、instrument_statistics.py、second_level_sampler.py、pair_spread_presets.py | 身份版本、旧记录歧义和统计污染处理，避免重写全部历史 |
| 接线 | backend/app/main.py | 共享只读 registry 快照、provider、协调器生命周期 |
| 前端 | frontend/src/api/types.ts、client.ts、pages/SettingsPage.tsx、InstrumentLookupPage.tsx、components/OpportunityTable.tsx、FloatingWatchPanel.tsx | 核验管理和清晰状态展示 |

新增文件建议：`models/market_identity.py`、`services/market_identity.py`、`services/pair_validation.py`、`services/market_validation_review.py`、`db/market_identity_repository.py`、`api/routes_market_identities.py`，以及对应测试。允许按最新结构微调文件名，不应把本模块散落成每个页面独立的 PRL 特判。

## 10 性能与部署切换

生产机资源有限。元数据核验与高频采集分开，采集循环只读不可变 registry 快照；映射版本改变时原子替换。按 asset_id 建索引，禁止每个行情周期对全市场做网络身份查证或全量两两比较。

盘口查询按市场键去重并复用现有 provider，V1 新增复核并发上限建议 3、单请求超时 5 秒、失败冷却 30 秒，均可配置。沿用现有更严格的限流；队列与缓存有上限和到期清理，失败不能阻塞其他交易所采集。

元数据默认复查周期可设 24 小时，已核验记录的有效期按来源/资产类别配置。接口失败时仅能使用未过期且无冲突的记录；没有记录或记录过期时自动动作降级，不回退 ticker。事件持久化按状态变化或节流写入，控制 SQLite 写锁和体积。

采用 `shadow / enforce` 两种运行模式。shadow 只生成对比报告，不能标为已修复；也不自动解除既有保护。enforce 才使统一资格决定自动动作。

切换步骤：

1. 用现有生产市场及规则的只读快照离线运行，对比旧/新候选及原因，避免为收集数据而开放自动动作。
2. 输出市场身份覆盖率、活跃规则/关注列表/卡片覆盖率、阻断分布、HL 参考行情与盘口补采成功率，以及正常样本差异。
3. 核验当前用户工作流涉及的正常市场，至少包括 BTC、ETH、PRL 正常 CEX 配对及已有 ANTHROPIC 倍率样本；对不能核验的列出明确清单，不为了覆盖率伪造 verified。
4. 确认没有未解释的机会数量骤降、正常跨稳定币失效或 GC/DEX 改绑，再在交付配置中启用 enforce。
5. 检查上线状态和只读实际接口。若只能完成 shadow 或关键市场仍无可用采集，报告部分完成和原因，不能宣布 V1 完成。

回滚优先回滚新应用的非身份相关故障，不删除新表和审计记录。若必须回到旧版本，会恢复错配风险，应明确报告并保留已知错误配对的防护；不能用恢复宽松 ticker 匹配作为无风险回滚。数据库恢复必须另行评估，禁止直接覆盖在线主库。

## 11 测试与验收矩阵

新增测试先用本次 PRL 官方数据建立脱敏固定 fixture，单元测试禁止依赖实时网络。真实接口只用于部署前后只读核验。

| 用例 | 必须断言 |
| --- | --- |
| Perle 对 Pearl，同 symbol | 不生成同币机会、不发策略告警、不调 Astro add；各自行情可见 |
| Bitget 对 Bybit 的 Perle | 身份和行情通过后保留正常机会 |
| HL 默认、io、xyz 同 ticker | 不串市场；无明确证据不互相继承映射 |
| 显式 dex 与 raw_symbol 前缀冲突 | 后端拒绝，不降级默认 DEX |
| 不同 ticker 同资产 | 有证据映射可比较，raw_symbol 不变 |
| 1000 单位及合约面值 | 价格/数量/名义金额一致，不重复缩放，不靠价格猜倍率 |
| 新市场无资料、旧别名无证据 | pending，原始行情可查，自动动作阻断 |
| 短暂失败、过期、规格变化 | 未过期记录按规则复用；过期或变更不能继续持旧资格提交 |
| 只有 mid/mark/index/last | 参考显示；不可伪装成真实 bid/ask |
| HL 候选按需补盘口 | 正确 coin 查询，成功进入资格判断，失败不回退参考价格放行 |
| 单腿陈旧、时间错位、单边深度/成交额缺失 | 保留具体腿原因，不能以另一腿替代 |
| 不同报价币种 | 有效换算与过期换算分别放行/阻断；原币种和估算状态可见 |
| 真实同资产巨大价差 | 连续独立盘口复核后恢复资格，不被待核验标签永久拦截 |
| 价格恢复但身份仍冲突 | 身份阻断保持，不自动合并 |
| 人工 override、include_risky、直接 POST | 不能绕过身份、单位和必需行情硬阻断 |
| preview 后映射改变 | 最终写入前拒绝，Astro add 调用次数为 0 |
| 自动、人工、预建、实盘、反向与 GC | 所有入口调用同一资格逻辑，DEX 保留，已有路由支持校验不退化 |
| 自定义跨资产研究及已支持人工路线 | 功能保留、模式明确，不生成同币自动信号 |
| 已有错误卡片 | 只读审计，无 pause/update/delete/平仓调用；风险降低操作不被误封 |
| 数据库升级及重启 | 幂等、记录/版本/风险原因不丢失；旧 schema 数据可读 |
| 并发修改和缓存失效 | expected_revision 冲突返回 409；旧 qualification 失效 |
| API 写权限和证据抓取 | 未授权不能改映射；任意外部 URL 不能触发服务端抓取 |
| 桌面和移动端 | 同名资产分组、未知值、阻断原因和核验操作清晰可用 |

定向回归至少涵盖已有 `test_symbol_aliases.py`、`test_symbol_alias_index.py`、`test_spread_engine.py`、`test_spread_engine_equivalence.py`、`test_instrument_spreads.py`、`test_instrument_lookup.py`、`test_risk_labels.py`、`test_astro_planner.py`、`test_astro_alerts.py`、`test_astro_preadd.py`、`test_astro_instrument_routes.py` 及实际受影响的盘口、collector、告警、历史和路由测试。按实现调整列表。

旧等价性测试的参考实现可能固化“按 ticker 即同资产”的旧行为：分别验证未受影响样本保持一致和有意修复样本发生预期变化，不能仅改参考结果以消除差异。

运行相关前端 Vitest，以及 `npm run build`（包含 TypeScript 和生产构建）。对新增管理写接口和数据库迁移做相应审查。记录耗时、网络请求数、采集周期与机会数量对比，避免全量 HL 补盘口拖垮采集。

## 12 推荐实施顺序

1. 重新核对基线、工作区和生产版本；阅读 AGENTS.md、Git 与部署文档；保存只读现状及卡片影响清单。
2. 先添加 PRL 错配、DEX 歧义、参考价伪装和人工绕过的失败测试，明确缺口。
3. 实现模型、增量表、repository、证据/版本和纯判定；种入有证据的精确 PRL 绑定及必要正常样本。
4. 修正行情来源语义，实现候选补盘口、单位和计价转换、有限异步异常复核；保证正常 HL 工作流有合格行情路径。
5. 接入所有同币机会消费者和最终交易写入关口，分离硬阻断与业务 override；处理历史及同名查询歧义。
6. 实现最小管理界面、审计输出和状态原因展示；保留已有未提交前端改动。
7. 跑验收矩阵、回归和构建；完成覆盖率对比及上线切换评估。
8. 按仓库流程提交、推送、备份、部署、只读线上验收；更新本交接文档的已完成项和证据。

## 13 Git 与生产交付

实现任务遵守 `docs/git-delivery-checklist.md` 和 `docs/linux-deployment.md`。只暂存本模块文件，禁止 force push、破坏性回退及 Git sudo。

生产机禁止现场构建。推送实现提交后，等待 GitHub Actions backend/frontend 两份精确 SHA 镜像成功，核对 manifest。部署前备份 `/data/radar.db`，按脚本同时处理存在的研究库，记录文件名、integrity_check 和容器/主机一致的 SHA-256。

服务器使用受控环境已有连接配置；主机地址、密钥位置及任何凭据不写入本文件或仓库。执行现有部署脚本，`git pull --ff-only` 更新，使用预构建镜像启动 Compose。不得 `docker compose down -v`。

上线检查双容器、前端页面、`/api/health`、精确 PRL 分组、正常 CEX 配对、HL 参考与真实盘口、被阻断配对的只读预览，以及正常 BTC/ETH/已核验倍率样本。生产核验不得调用真实建卡、开启交易或重放历史告警来制造通过证据。

最终交付报告必须列明：提交号、推送状态、测试/构建、数据库备份及校验、生产源码和双镜像版本、enforce 状态、身份覆盖率、真实接口结果、旧卡片残余风险、保留的其他任务改动和未跟踪产物。下一独立模块应新建任务。

## 14 可直接发送给 Sol 的任务说明

请阅读 `docs/task-handoff-2026-10-15-market-identity-validation.md`，实现其中 V1 的市场身份与价差有效性校验。按文档的代码基线重新核对当前分支与线上版本，不覆盖现有 PairMonitorPage 及其测试的未提交修改。重点解决 PRL 同名异币、HL DEX 身份、参考价冒充盘口、人工建卡绕过，以及所有同币入口的判定一致性；保留明确的自定义跨资产工作流。不要只加 PRL 黑名单或简单价差阈值过滤。先建立失败用例再实现，完成相关回归、前端构建、覆盖率评估，按仓库规则提交推送、备份部署并验证线上。不要修改已有外部卡片或开平仓，输出其只读影响报告。更新交接文档，说明覆盖范围、未核验市场和残余风险。
