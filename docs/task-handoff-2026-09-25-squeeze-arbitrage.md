# 妖币价差收敛套利：设计与实施交接

日期：2026-09-25（Asia/Shanghai）。交付对象：6-sol。所属模块：价差与资金费率告警。

## 1. 目标、授权和本次交付范围

用户要求从 G、AKE、TUT、LSK 等异常波动案例提炼共性，提前找到空头拥挤市场，在强平和剧烈波动时寻找价差收敛套利；已接受策略设计，要求形成详细交接供另一任务实施。

产品目标：提前建立结构观察池，使用同一底层资产两个市场的实际可成交价差形成候选，记录双腿成本、风险和模拟成交，验证可执行性后再决定真实交易。

本次仅新增交接文档，没有实施策略、改变线上配置、发送通知、创建 Astro 卡片或下单。下一任务从 S1 开始，每个阶段独立验证、交付并更新本文。代码部署不代表获准开启实盘；S1—S3 默认无真实下单、借币、转账或自动建卡。需要接入实发通知时沿用用户已配置的通知权限和开关，测试使用 fake sender。

验收目标分开记录：

1. 工程验收：身份、数量、时序、公式正确；数据可审计；资源有界；无意外副作用。
2. 策略验收：固定参数下、包含失败和未成交的前瞻模拟净收益与风险报告。工程通过不等于策略盈利。

## 2. 实施基线与仓库变化

- 本次核对分支：`codex/frontend-localization-polish`。
- 本次核对 HEAD：`03188c75e8d6e1e6461bd1ad3072cfa227647bf7`，`docs(notifications): record production delivery [skip ci]`。
- 开始时无已跟踪修改、暂存区为空；大量未跟踪文件归属其他任务，见第 16 节。
- 2026-09-21 研究时基线为 `87977fc`，不能据此假设旧模块仍然存在。
- 2026-09-25 已退役“1分钟价差信号”和“新币极速”：不得恢复旧服务、旧 worker、旧路由、旧菜单、旧自动建卡路径。参见 `docs/task-handoff-2026-09-25-monitor-pruning.md`。
- 通知模板、公告调度已有更新，参见 `docs/task-handoff-2026-09-25-notifications.md`。
- `docs/task-handoff-2026-09-25-architecture-optimization.md` 是并行任务的设计资料，当前未跟踪；可参考有界任务、缓存、请求预算思想，不修改、不提交该文件，不把整套架构重构并入本任务。
- 生产机 2 GiB，必须执行当前 `docs/linux-deployment.md` 的外部预构建镜像方案，不能按旧对话在生产现场构建。

开始实施必须重新运行 `git status --short --branch`、`git branch --show-current`、`git log -1 --oneline`。不要回退到本文基线；其他任务可能继续推进。

## 3. 研究证据和必须保留的不确定性

### 3.1 已有探索结果

研究使用 Binance 公开 K 线、历史原始 OI、全体账户多空比、Alpha 市场信息。原区间约 2026-09-07—09-20，扩展区间约 2026-08-24—09-07。原区间 13 个组合信号中 6 个未来 24 小时最高价较入场参考价上涨至少 50%；扩展区间 21 个信号中 5 个。11/34 仅是探索案例统计。

历史案例线索（以下为分钟收盘价差，不是实际可成交收益）：

| 案例 | 观察到的现货相对永续溢价 | 观察结果 | 设计启示 |
| --- | ---: | --- | --- |
| LSK | 约 +21.64% | 约 1.77 小时后回到绝对价差 0.3% 内 | 候选，但需要空现货能力 |
| IOST | 约 +23.29% | 约 2.73 小时 | 现货容量仍需验证 |
| BTR | 约 +16.04% | 约 22 分钟 | 值得分钟/逐笔回放 |
| SOPH | 约 +17.97% | 约 5.25 小时 | 方向失败也可能出现收敛 |
| ONG | 约 +9.49% | 原观察窗末仍约 +9.14% | 不能假设快速收敛 |
| VVV | 约 -7.10% | 峰值 Alpha 分钟零成交 | 陈旧价格假机会 |
| USELESS | 约 -4.89% | 峰值 Alpha 分钟零成交 | 陈旧价格假机会 |

永续/单个对应现货市场成交额比，成功组中位约 37 倍，失败组约 64 倍；大于 1000 倍的 4 个案例未命中上涨目标。此比率不是全市场衍生品/全市场现货比，不能跨 Alpha 和主流现货市场不加区分地解释，也不能据 4 个样本固化成通用禁入阈值。

### 3.2 不得直接用于实盘的统计

- 新增区间先按事后当日日线异动选择市场，存在选择偏差；两区间仓位采样分别为 1 小时和 2 小时。不能声称 32.4% 是样本外胜率，也不能与另一时段基础信号的 13.1% 直接比较。
- 未来最高价达到目标不代表止盈先于止损。BTR 曾先触及 -15% 再大涨；同一根 K 线内先后不明须降到细粒度验证，无法确定时采用保守或 ambiguous 标签。
- “OI回落后距高点约1小时”受观察窗右侧截断和触发小时内高低价顺序影响，不能用作倒计时规则。重新计算时从信号可用时间后的下一条数据开始，并给每个触发一致长度的未来窗口。
- OI 下跌可能来自主动平仓、清算等，不能仅凭 OI 下降断言发生空头爆仓。
- 美元 OI 随价格机械放大；新增仓位必须看原始 OI 及其单位，不使用 `sumOpenInterestValue` 的涨幅替代。
- 全体账户多空比是账户数量代理，不能解释为全市场空头名义金额大于多头。
- 负资金费率不是已验证必要条件；费用展示和持仓现金流仍必须纳入。
- 当前未找到可复现的本轮研究原始数据集、固定脚本和完整交易账本。对话中的临时脚本输出不是审计级回测产物。实施时重新采集并保存来源、参数、时间范围、版本、缺口和校验摘要。
- 此前当前市场扫描是 2026-09-21 的快照，不是 2026-09-25 当前推荐。
- 跨交易所永续是设计上的优先路径，历史主要验证的是 Binance 现货—永续；其收益和容量尚未验证。

## 4. 交易路径与身份契约

| 路径 | 组合 | 必须具备 | 模式 |
| --- | --- | --- | --- |
| 跨所永续 | 空贵所、多便宜所 | 同底层、线性合约数量对齐、各账户保证金 | 主路径，先模拟 |
| 永续溢价 | 买现货、空永续 | 真实现货卖盘、永续买盘 | 辅助路径 |
| 现货溢价 | 借币卖现货、多永续 | 可借数量、真实借贷利率与召回风险 | 无借币数据则阻断 |
| 已有库存转换 | 卖原现货、多永续，之后换回 | 已有库存与持仓基准 | 单独库存增强账本 |

库存转换保留方向敞口，不得显示为从现金开始的市场中性套利。现货更贵时“买现货、空折价永续”方向错误，禁止作为收敛组合。

市场身份建议字段：`exchange / market_type / raw_symbol / dex / chain_id / contract_address / quote_asset / settlement_asset / contract_size / price_multiplier / quantity_step / min_qty / min_notional / asset_id / verification_source / verified_at / verification_status`。

- ticker 只用于搜索，不作为同币证明。地址大小写规则按链处理；Solana 等大小写敏感地址不能统一小写。
- 区分原始合约单位和基础币数量；明确原始单位乘多少才得到基础币，价格乘多少才得到统一单位价格，保证名义价值守恒。舍入后重新核对两腿残余 delta。
- 第一版只允许已验证的线性合约和可归一报价；反向合约、交割合约、期权、不同包装/桥接资产未经专门模型不得自动合并。
- Hyperliquid 必须保留具体 DEX 与原始市场；Lighter 与 RH-Lighter 不混为同一来源。
- USDC/USDT 等跨报价比较需要同步可成交换汇和费用模型。第一版可只允许同报价；跨报价缺模型时明确阻断，不能假设固定 1:1。
- 当前市场 TRADING 不证明事件时可交易；退市和新上市时间进入历史 universe。

研究映射种子（不是实盘白名单；执行前核对官方上市信息和当前元数据）：

| 永续原始市场 | 现货市场 | 链/资产说明 |
| --- | --- | --- |
| Binance GUSDT | Binance spot GUSDT | Gravity；Alpha ALPHA_282 是 GIANTS，禁止配对 |
| Binance TUTUSDT | Binance spot TUTUSDT | Tutorial，需当前核对 |
| Binance LSKUSDT | Binance spot LSKUSDT | Lisk |
| Binance AKEUSDT | ALPHA_331USDT | BSC `0x2c3a8ee94ddd97244a93bc48298f97d2c412f7db` |
| Binance BTRUSDT | ALPHA_340USDT | BSC `0xfed13d0c40790220fbde712987079eda1ed75c51` |
| Binance 龙虾USDT | ALPHA_772USDT | BSC `0xeccbb861c0dda7efd964010085488b69317e4444` |
| Binance AINUSDT | ALPHA_260USDT | BSC `0x9558a9254890b2a8b057a789f413631b9084f4a3` |
| Binance SKRUSDT | ALPHA_605USDT | Solana `SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3`；永续身份待复核 |
| Binance USELESSUSDT | ALPHA_295USDT | BSC `0xba38b3c706f7a515ff7c8db04daa0a134ec46d2b`；永续身份待复核 |
| Binance VVVUSDT | ALPHA_106USDC | Base `0xacfe6019ed1a7dc6f7b508c02d1b04ec88cc21bf`；跨报价 |
| Binance SOPHUSDT | Binance spot SOPHUSDT | ALPHA_204 已退市，不能用旧行情替代 |
| Binance IOST/ONE/ONG/VTHO/AVA/EPIC USDT 永续 | 同名 Binance 普通 USDT 现货 | 每个原始 symbol 分别核实 |

## 5. 观察池与阶段识别

第一版按已完成的小时 K 线计算；存储 UTC，展示北京时间。每个输入保存 `event_time`、`received_at`、`available_at`，回放只能使用决策时已可得的数据。

定义：t 为刚完成的小时桶，C 为收盘价，V 为报价币成交额。

```text
return_4h = C[t] / C[t-4] - 1
return_24h = C[t] / C[t-24] - 1
volume_ratio = mean(V[t-3:t]) / median(V[t-171:t-4])
```

上式区间均含端点：分母取信号最近 4 小时之前的完整 168 小时，排除信号窗口污染。这是新实现的明确口径，不声称完全复现旧临时脚本的 164 小时分母。缺失小时、基线为零、上市不足均标记 insufficient_data，不以零填充。

OI 用决策截止前最新可用原始数量，24 小时前参考用不晚于参考时刻的数据；允许误差不得超过一个配置采样周期。记录实际年龄，超龄不触发。

```text
oi_current_growth = OI_now / OI_24h_ago - 1
oi_peak_growth = max(OI in prior 24h) / OI_24h_ago - 1
oi_drawdown = OI_now / max(OI in prior 24h) - 1
account_ratio_change = ratio_now / ratio_24h_ago - 1
```

初始观察条件：`(return_4h >= .15 OR return_24h >= .30) AND volume_ratio >= 5 AND account_ratio <= .85 AND oi_peak_growth >= .08`。

- 满足后保留 72 小时，同一结构事件通知冷却 48 小时；更新指标不等于重复创建事件。
- 观察池冷却与具体交易路线冷却分开，禁止用一个全局 ticker 冷却误屏蔽另一条有效路线。
- 建仓观察：价格强、OI增长、账户比降低，OI接近高点。
- 逼空待确认：价格仍强、OI回落至少10%；有观测到的空头强平上升时提高证据等级，无强平数据标记 unknown，不作零值。
- 尾部风险：价格动量走弱、价差回落、强平观测减弱；作为风险标签，不由单一阈值自动反向下注。
- 阶段标签与交易状态机独立；OI出清并不禁止所有价差交易，也不触发裸空。
- 方向信号失败后继续监测，保留 SOPH 类候选。人工白名单可以进入观察池，但不能绕过身份和盘口门槛。

## 6. 盘口、强平和成本数据

### 6.1 盘口质量

使用目标基础币数量 q 逐档计算四个 VWAP：贵腿开仓卖出、贵腿平仓买入、便宜腿开仓买入、便宜腿平仓卖出。深度不足必须返回不可执行，不能只计算已覆盖数量后声称 q 已成交。

初始门槛：行情本地接收年龄 <=1 秒、两腿源时间差 <=1 秒（源时间不可得则降级并阻断可执行级别）；两腿最近60秒存在真实成交；连续至少3秒/3个独立有效快照通过。参数可配置，但不得放宽来掩盖陈旧数据。

必须检查序列号、快照/增量衔接、重连、盘口交叉、负数量；有缺口时重建并暂停信号。仅 REST 且无法达到时效的来源允许进入 research_only，不伪装秒级执行。

Alpha 能否取得可靠可交易深度需先做 capability 检查。若只有 ticker/K线，该腿只能用于研究观察，不能生成 executable 信号。

### 6.2 强平流

核对 Binance 当前公开流协议后接入 `!forceOrder@arr` 或其当时受支持等价流。BUY 表示被强制买回的空头，SELL 表示被强制卖出的多头。按协议的实际累计成交量/平均成交价与事件身份去重，不能把重复更新的累计值重复计入，也不能一律用委托量乘委托价。

公开流可能节流、只报告每时间窗最近事件，不能称为全市场完整强平金额。字段记录 `observed_liquidation_notional`、覆盖状态、断线区间、来源协议；断线不填零，REST不能完整回补时保留缺口。第一版它是辅助指标，不作必需盈利信号。

### 6.3 费率和数量

每腿保存 maker/taker 费率来源、账户档位或保守假设；默认模拟按 taker，不能默认为零。资金费保存 rate、interval、next_settlement、actual/estimated、source_time，按每次实际结算时的持仓和名义价值计费。

原始 OI 单位按交易所定义记录，不能把所有交易所原始数值直接相加。美元 OI保留作规模展示；数量 OI用于结构变化。

借币必须记录可借数量、查询时间、利率单位/周期与费用计算规则；未知借贷能力不是可执行。手续费扣币方式造成的基础币数量不足也需进入残余敞口核对。

## 7. 可成交价差、收益与进场状态机

约定 H 为固定贵腿，L 为固定便宜腿，均已换算为每基础币相同报价。交易创建后锁定腿身份，不能因相对价格翻转而交换腿。

```text
D_open(q) = VWAP_H_bid(q) - VWAP_L_ask(q)
D_close(q) = VWAP_H_ask(q) - VWAP_L_bid(q)
gross_pnl = q * (D_open_at_fill - D_close_at_exit)
net_pnl = gross_pnl - entry_fees - exit_fees
          + signed_funding_cashflows - borrow_cost - other_realized_costs
```

现货—永续图表可以展示 `(spot - perp)/spot`；交易触发用实际价格差与固定参考价。确认收敛时固定峰值事件的参考价和 q，不能仅因两腿一起涨价导致分母上升，就误判百分比价差收窄。

预计净边际必须明确目标退出残差 D_target：`q*(D_open-D_target)-预计四次手续费+预计有符号资金费-借贷费用-执行误差缓冲`。D_target 可取预先固定的正常价差水平，记录其时点；不得默认为零而隐藏假设。行情异常时不更新正常基线以追随异常。

VWAP已含当前盘口冲击，不重复扣同一份滑点；额外缓冲用于延迟、盘口撤单等不确定性。模拟收益与实际收益字段分开。账户资金收益率分母包括两腿占用及预留保证金，不只取一腿初始保证金。

状态机：

```text
WATCHING -> DISLOCATION -> CONFIRMING -> PAPER_PENDING -> PAPER_OPEN -> EXITING -> CLOSED
任意开仓前状态 -> BLOCKED（身份/质量/深度/成本不满足）
PAPER_PENDING -> RECOVERY（部分成交/另一腿失败）-> CLOSED 或 PAPER_OPEN
PAPER_OPEN -> EXITING（止盈/止损/超时/质量风险）
断线/重启 -> RECOVERING（恢复事件和持仓账本，核对后再接收新开仓）
```

初始参数（研究配置、版本化保存）：

| 参数 | 初始值 | 说明 |
| --- | --- | --- |
| 异常监测价差 | 1% | 还应高于该路线正常区间 |
| 最低预计净空间 | 1% 固定参考单腿名义 | 已含退出残差与全部预计成本 |
| 峰值观察窗 | 60秒 | 仅使用当时已观测值 |
| 从峰值收缩 | 20% | 有效开仓边际仍须足够 |
| 收缩持续 | >=3秒且>=3有效快照 | 不能用重复缓存快照凑数 |
| 单腿盘口冲击上限 | 0.2% | 相对该腿最优价，按 q 计算 |
| 试算单腿名义档位 | 100/500/1000 USDT | 都是模拟容量，不是实盘额度 |
| 捕获目标 | 预计收敛空间的65% | 同时净收益为正，退出成本已计入 |
| 无改善复核 | 30分钟 | 记录动作理由和是否减仓 |
| 常规持仓复核 | 2小时 | 延长需成本/质量/保证金再次通过 |
| 最长持仓 | 6小时 | 退出受限时记录未决状态，不能伪造已平仓 |
| 模拟单笔最大损失预算 | 分配模拟资本的0.25% | 按两腿净清算价值衡量 |
| 同时交易数 | 3 | 同资产路线合并敞口控制 |
```

复核点要实现为确定性策略动作或明确等待人工，不写成无行为的日志。第一版可采用30分钟无改善减半、2小时仍未达目标退出、6小时硬性停止持有计划；模拟引擎必须记录真实可平的盘口，盘口缺失时保持未决估值及风险，而非凭旧价结案。不同退出变体各自配置/版本运行，不事后挑选最好的一条。

## 8. 保证金、敞口和模拟执行

- 两腿固定基础币 delta 对齐；最小数量/步长导致残余时设明确上限并展示。
- 两账户余额隔离，盈利不能假设自动转到亏损腿。进入候选前检查交易额度、维持保证金分层、标记价格机制。
- 压力情景至少包括两腿共同上涨100%、价差额外扩大5/10个百分点、费率变为1小时结算且成本恶化、某腿临时不可交易；不是只测净 delta 为零。
- 若缺少可靠维持保证金参数，标记 collateral_model_incomplete，只做研究模拟，不宣称通过清算风险验收。
- 不在价差继续扩大时无限补仓。分批加仓必须作为独立规则，第一版关闭。
- 交易流程不依赖临时跨所转账；模拟初始余额及预留资金明确记录。
- 模拟订单至少经过配置延迟（候选 250/500/1000ms 敏感性），用到达时的新盘口填单，不用信号盘口直接成交。
- 第一版模拟可成交限价/IOC及部分成交，量不超过可见深度；不模拟“挂单必成”，不计未验证 maker 优惠。
- 单腿失败有明确补齐最大时长、额外成本预算和回撤动作；补不齐则模拟撤单并关闭裸腿。失败交易计入总收益，不能丢弃。
- 每个信号/模拟单有稳定 idempotency key，重启后恢复余额、未平仓和未决状态；未知结果不能自动当作未成交重试。
- 所有执行接口在 S1—S3 只绑定 paper adapter。生产采集开关与交易开关独立，禁止通过开启监控间接调用 Astro/Live Pilot/私有下单。

## 9. 工程结构与现有入口

先复用现有能力，避免把新策略直接塞进巨型 `main.py` 或 `pair_spread_query.py`。建议新增专属 `backend/app/services/squeeze_arbitrage/` 包，领域计算保持纯函数，provider、repository、runner、paper ledger 分离；名称是建议，必须与当前项目约定协调。

| 已存在入口 | 可复用/需检查 |
| --- | --- |
| `backend/app/models/pair_spread.py` | `PairSpreadOpenInterestPoint` 当前历史仅美元 OI；快照已有 `open_interest_contracts`，新增字段兼容旧客户端 |
| `backend/app/services/pair_spread_query.py` | Binance OI取 `sumOpenInterestValue`，需同时保留 `sumOpenInterest`、单位、源时点；不要无效破坏原展示 |
| `backend/app/services/symbol_aliases.py` | 别名和倍率；补精确身份审核，不靠别名证明同币 |
| `backend/app/services/trade_availability.py` | 交易状态与诊断；未知/陈旧必须保留 |
| `backend/app/services/opportunity_trade_availability.py` | 机会到精确市场诊断的桥接 |
| `backend/app/services/funding_research/depth.py` | 深度遍历参考；当前按目标名义估算，需审核是否适合固定基础币 q，不能直接假设双腿等量 |
| `backend/app/services/funding_research/paper.py`、`repository.py` | 模拟与研究存储参考，先核对单位和副作用 |
| `backend/app/services/spread_engine.py` | 机会与市场配对规则；保持现有通用行为 |
| `backend/app/services/negative_basis_monitor.py` | 现有监控生命周期参考，不复用其状态导致串策略 |
| `backend/app/services/alert_messages.py` | 现有精简消息，保留原始腿、倍率、费用周期和估算标识 |
| `backend/app/db/schema.py`、`repositories.py` | 增量迁移与事务；新策略仓储尽量独立，保留旧表 |
| `backend/app/main.py` | 只做依赖装配和受开关控制的启动/关闭；API-only模式不启动采集 |
| `frontend/src/components/AppShell.tsx`、`frontend/src/pages/InstrumentLookupPage.tsx` | 新入口或现有页链接，保持已删除旧页面继续404/默认路由 |
| `backend/app/api/routes_pair_spread.py` | 图表/快照接口兼容性 |

旧 `minute_signal_scan.py` 及路由已删除，不要从历史恢复。仓库若仍有离线研究脚本，仅审查算法参考，不重新挂载旧产品。

## 10. 存储、API 与页面契约

建议逻辑实体（按 S1—S3 逐步增加，不要求一次建齐）：

- `market_identity`：精确腿身份、核实来源、历史有效区间、能力集合。
- `positioning_sample`：原始OI/单位、美元OI、账户比、观测时点/可用时点、质量标识；唯一键包含 market + source + event_time + sampling_period。
- `liquidation_observation`：原始方向、实际成交字段、去重身份、观测名义额、覆盖状态。
- `watch_event`：规则版本、触发输入、阶段迁移、到期时间、冷却信息。
- `route_evaluation`：两腿身份、固定q、四向VWAP、盘口年龄/序列、双边成交额、全部成本、预计净空间、拒绝原因。
- `paper_order / paper_fill / paper_position / cashflow`：完整可恢复账本，费用和资金费独立流水。
- `research_run`：数据范围、参数快照、代码版本、缺口、数据文件校验摘要和结果。

存储高频数据要有容量预算：全市场仅低频扫描；高频盘口初始最多10条活跃路线，观察池最多30个资产。原始事件盘口仅保留有界前后窗，压缩研究文件并设置明确保留期；SQLite记录索引/聚合/账本，不把全市场全量 L2 无限写入 radar.db。初始研究文件预算1 GiB只是上限候选，必须结合磁盘余量调整；清理只限本模块自有可重建文件，不清理交易账本或用户文件。

候选 API（新增命名空间，不复活旧接口）：

```text
GET  /api/squeeze-arbitrage/status
GET  /api/squeeze-arbitrage/watchlist
GET  /api/squeeze-arbitrage/events
GET  /api/squeeze-arbitrage/routes
GET  /api/squeeze-arbitrage/paper/positions
GET  /api/squeeze-arbitrage/paper/trades
GET  /api/squeeze-arbitrage/settings
PUT  /api/squeeze-arbitrage/settings
```

GET 不创建卡片、下单或开启 worker。设置写接口继承项目鉴权，校验并发上限/风险参数，不允许任意外部 URL 或密钥进入响应。列表有分页与上限；时间、bps/比例/百分数单位统一在DTO中定义，避免0.01同时表示1%和0.01%。

页面最少展示：标的/阶段、精确多空腿、结构指标、候选数量、四向VWAP、可成交/仅研究/阻断状态及原因、双方成交额、当前/预估费率与真实周期、净空间估计、数据年龄、模拟持仓/收益/风险。结构预警与可交易提示视觉区分；不要把历史成功次数展示为实时胜率。

## 11. 性能、降级与运行隔离

- 全市场1小时信号增量计算，初始基线回补有分页/并发/限流预算；不能每秒拉528个历史序列。
- HTTP客户端复用，按交易所权重限流；429遵守Retry-After，断线指数退避；不要绕过受限来源。
- 后台任务有唯一owner、心跳、last_success、queue_depth、覆盖缺口和取消收尾；重启不会重复派发。
- 网络采集与SQLite批量写入解耦、有界队列；过载时标记丢失/降级，禁止假装完整。
- 生产 backend 限额现为768 MiB、frontend 96 MiB。实施先测30分钟新增负载；不得使盘口任务/现有行情明显超时。可选研究负载优先收缩。
- 健康接口不止进程存活；新模块status应显示启用状态、数据时效、退避、存储异常、模拟未决风险。
- 不把本任务扩大为全项目Transport/数据库/页面重构；若共用组件正在被另一任务修改，协调当前接口并小范围适配。

## 12. 分阶段实施与每阶段交付

### S1：原始OI、强平观测与结构观察池（下一任务的范围）

1. 重新核对基线和已有精确市场模型；补历史原始OI数量/单位/来源，兼容美元OI展示。
2. 新增独立策略配置、研究数据仓储及迁移；默认监控关闭，API-only无后台采集。
3. 实现 Binance 原始OI/账户比增量采集和闭合小时特征，保留缺口、回补范围和可用时间。
4. 实现公开强平流观测、去重、重连及覆盖状态，明确节流不完整语义。
5. 实现观察池、阶段标签、到期/冷却持久化；开放status/watchlist/events接口及一个最小只读UI。
6. 用回放fixture验证，生产采集启用仅使用公开数据且有资源预算；不连接真实执行。

S1验收：价格翻倍但数量OI不变时不能报新增仓位；账户比/OI陈旧不触发；缺口不补零；重启观察池不丢/不重复；公开强平重复更新不重复计量；禁用开关无worker；现有接口/退役行为保持。

### S2：精确路线、指定数量VWAP与收敛确认

依赖S1交付。实现已核实线性同报价市场白名单；先选择 Binance 与一种项目已有且能力合格的第二交易所，其他通过能力接口渐增。实现目标q双腿深度、费用和资金费周期、质量阻断、峰值收缩状态机、拒绝原因、只读机会UI。Alpha无可靠深度时只研究。

S2验收：身份/DEX/倍率不串线；同涨而百分比压缩不误触发；q一致且舍入可审计；盘口缺口/陈旧/零成交/无借币均正确阻断；每次候选有完整输入快照与成本分解。

### S3：前瞻模拟交易与冻结参数验证

依赖S2交付。实现模拟余额、延迟填单、IOC/部分成交、裸腿恢复、实际资金费现金流、借币费用、退出和重启恢复。建立固定参数、固定路线的前瞻观察与报告，不择优删除失败事件。

建议至少连续观察14天且积累30个独立价差事件后评审；不足时结论为样本不足，不能凑数或宣布通过。按资产/事件聚类，避免把同一波行情每秒信号视为独立样本。该门槛不构成盈利保证。代码可先完成部署，统计验收尚未达成应继续明确标注。

S3报告：净收益/交易、总资金收益率、最大回撤、单腿失败率、未决数量、持仓时长、价差最大不利扩大、每腿最低保证金、每档容量、资金费影响；按市场路径分组。没有足够独立样本前不输出实盘建议。

### S4：真实执行（当前不实施）

必须另开任务，用户明确指定交易所账户、资产白名单、额度、损失预算与操作权限后再实施。先复核S3账本与异常恢复，再讨论微额试运行；本交接不能被当成真实订单、借币、资金转移或自动建卡授权。

## 13. 测试矩阵与研究复现

新增测试重点为业务边界，使用fake clock/transport/database/executor；测试不得导入会启动真实worker或通知的生产入口。

| 领域 | 必测情景 |
| --- | --- |
| 时间 | 未完成K线、不足168小时、采样延迟、as-of边界、缺口、不等周期、晚到数据 |
| OI | 价格涨数量不变；美元/数量单位不同；合约倍率变化；原始单位不可比 |
| 身份 | G与GIANTS、中文ticker、重复Alpha ticker、不同DEX、1000倍合约、USDC/USDT缺换汇 |
| 盘口 | 不足量、零成交、陈旧、交叉盘口、序列缺口、重连、源时间缺失、部分深度 |
| 公式 | 两腿固定q；开平仓四向VWAP；价格共同上涨；四次手续费；负费率；异周期结算；借币利息 |
| 状态机 | 峰值仅看过去；不足持续时间；重复快照；路线锁定；冷却/到期/重启；价格反转 |
| 执行模拟 | 信号后延迟价格改变；单腿拒绝/部分成交；余额不足；最低量舍入；恢复幂等 |
| 退出 | 不收敛、手续费吃掉收益、缺盘口未决、2h/6h边界、资金费跨时点、止损先于止盈 |
| 运维 | 429/断线/DB失败/有界队列；旧库升级/新库初始化；禁用与API-only；旧退役接口 |

回归优先阅读 `backend/tests/test_pair_spread_query.py`、`test_symbol_aliases.py`、`test_orderbook_validator.py`、`test_spread_engine.py`、`test_repositories.py`、`test_api.py`、`test_alert_loop.py`；按存在情况选择 funding_research 相关测试。前端运行新增页/API类型及相关导航测试，再 `npm run build`。共享行情/存储变动扩大到相应全量回归。

研究数据回放：冻结参数后按时间划分训练/验证/前瞻；universe只包含当时已上市资产，同时保留后来退市者；不得按未来日高先选市场再报告总体命中率。每事件存 input availability、信号时间、最早执行时间、真实/模拟成交、全部成本和失败原因。历史无L2只能报告价格研究，不能伪造订单簿或无滑点实盘PnL。

## 14. 数据源索引

实施前核对官方当前文档、权重、可用周期和保留期，不硬编码历史接口能力：

- Binance永续K线：`GET https://fapi.binance.com/fapi/v1/klines`。
- Binance原始/美元OI：`GET https://fapi.binance.com/futures/data/openInterestHist`，分别为 `sumOpenInterest` / `sumOpenInterestValue`。
- 全体账户比：`GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio`。
- 永续元数据：`GET https://fapi.binance.com/fapi/v1/exchangeInfo`。
- 普通现货元数据/K线：`https://api.binance.com/api/v3/exchangeInfo`、`/api/v3/klines`。
- Alpha元数据：`https://www.binance.com/bapi/defi/v1/public/alpha-trade/get-exchange-info`。
- Alpha token元数据：`https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list`。
- Alpha K线：`https://www.binance.com/bapi/defi/v1/public/alpha-trade/klines`；历史数据存在不等于当前可交易。
- 资金费与强平流：采用实施时官方受支持接口，明确历史实际费率、当前预估和公开流覆盖差异。

## 15. 发布、回滚与当前线上状态

本次文档任务未联系生产，当前生产版本未实时复核。仓库最近通知交接记录的功能部署是 `7a02ce21df06763342c9500726099813bc9a0254`，两容器healthy与备份成功属于该任务当时的证据，不是本策略部署证明。

本策略尚无新数据库表、worker、测试通过记录或线上策略结果。不得将文档交付写成策略已经上线。

每阶段业务实现遵守 AGENTS.md：完成代码和相关验证；只暂存本阶段文件；提交推送当前目标分支；等待两个固定SHA预构建镜像；按 `docs/linux-deployment.md`、`deploy/linux-update.sh` 先备份 `/data/radar.db`，核对 integrity_check 和 SHA256；服务器 `git pull --ff-only`，拉镜像后 `up -d --no-build --wait`；核对容器、health、策略status和真实接口/UI。

禁止生产机 `docker compose build`/`up --build`，禁止 `down -v`、force push或破坏性Git回退。不写服务器地址、密钥位置、Webhook或任何秘密到本文。

回滚先禁用新开仓和派发，保存未决模拟/真实账本；使用兼容旧版的增量schema，优先回退镜像和关闭新worker，不删除表。恢复旧数据库可能丢失备份后账本，不能当普通回滚动作。

本交接是纯文档，提交推送后不触发业务部署、数据库备份或服务重启；实现者在S1及后续业务阶段执行完整上线流程。

## 16. 已有未跟踪文件、检查结果和交接状态

本次开始已存在：`.worktrees/`、`backend/.pytest_module_prune_20260925/`、`docs/task-handoff-2026-09-24-spread-alerts-aster-production.md`、`docs/task-handoff-2026-09-25-architecture-optimization.md`、`script/dexe_bybit_bitget_chain.py`，以及 `output/` 内大量截图、浏览器profile和验证脚本。全部属于其他任务；实施前重新列出，不能批量git add、删除或覆盖。

部分pytest缓存目录存在Windows权限警告；不能据此清理或更改ACL。

本次完成：现行项目约束、部署流程、模块退役记录和关键代码入口核对；研究限制修正；完整策略、数据契约、阶段任务与验收文档。仅进行文档内容/路径/差异检查，不运行业务测试或生产入口。

## 17. 可直接发给 6-sol 的起始指令

> 阅读 AGENTS.md 和 docs/task-handoff-2026-09-25-squeeze-arbitrage.md。当前任务属于“价差与资金费率告警”，只实施 S1：原始 OI、强平观测与结构观察池，验收通过并完成交付后再为 S2 新开任务。先核对当前分支/HEAD/工作区和旧分钟价差信号退役状态；不要恢复旧模块，不把全项目架构优化并入任务。补历史原始 OI 及单位/时间质量，接入有覆盖状态的公开强平观测，实现闭合小时特征、持久化观察池/冷却、status/watchlist/events接口和最小只读页面。严格保留原始市场、DEX、链/资产身份及倍率；缺数据不能补零或当成可交易。默认无真实下单、借币、转账或自动建卡，测试使用 fake transport/clock/sender，API-only不启动采集。所有阈值作为版本化研究参数，不能把对话统计当已验证胜率。保留其他任务改动；完成有意义的业务/故障测试和前端构建、只暂存本任务文件、提交推送，并按现行低内存预构建镜像流程备份、部署、验证真实接口，更新本文交接。生产启用仅限有资源预算的公开数据采集，实盘执行留待独立授权。若受阻明确记录已完成和未完成部分，不声称策略已盈利或已完成尚未进行的线上验证。

## 18. S1 实施与线上交付续记（2026-09-26，北京时间）

### 18.1 范围、分支与基线

- 本轮开始分支 `codex/frontend-localization-polish`，HEAD `12cbea904d708223e60de04ae7c30bcccf4c65b9`；无已跟踪修改或暂存文件，原有未跟踪内容继续保留。
- S1 代码提交依次为 `255814c`（结构观察基础）、`2b7ba6b`（单字符 G 市场校验）、`4cac06b`（HTTP 数据可用时点）、`f7311e7`（窄屏标题布局）。均已推送；S1 最终业务镜像 SHA 为 `f7311e747a510f91f275e45a8aed1f0226cbe08c`。
- 本阶段只涉及挤仓结构观察。原分钟信号及新币极速接口未恢复；不下单、不借币、不转账、不自动建卡，也未启用任何实盘执行器。

### 18.2 已完成能力和代码入口

- `backend/app/services/squeeze_arbitrage/`：独立配置、Binance 公共小时 K 线/原始 OI/美元 OI/全体账户比采集、闭合小时特征、强平公开流解析与重连、观察事件持久化及数据保留。运行上限为 5 个指定原始 USDT 永续市场、30 个活跃观察事件；完整小时只扫描一次，失败按 300 秒间隔重试。
- `backend/app/services/squeeze_arbitrage/features.py`：按原始市场键选取 OI 和账户比各自截止前可得的样本；精确要求 172 个连续小时 K 线、OI 样本连续、端点年龄不超过一个采样周期。量 OI 用 `sumOpenInterest` 判变化，美元值只作规模展示；四小时信号窗口与前 168 小时成交额基线分离。缺口、超龄、单位或来源变更不触发。
- `backend/app/services/squeeze_arbitrage/repository.py` 与 `backend/app/db/schema.py`：新增独立表，保存原始市场核验信息、请求回补范围、事件/接收/可用时间、强平去重累计值、覆盖缺口、质量结果、72 小时观察到期与 48 小时冷却；仅清理本模块可重建样本，不删除事件或用户文件。
- `backend/app/main.py` 与 `backend/app/api/routes_squeeze_arbitrage.py`：仅常规采集入口且 `SQUEEZE_MONITOR_ENABLED=true` 启动新 worker；API-only 和默认关闭不启动。GET `/api/squeeze-arbitrage/status`、`/watchlist`、`/events` 均只读且有列表上限。
- `frontend/src/pages/SqueezeArbitragePage.tsx`：新只读菜单，显示运行状态、精确原始市场、数据质量、观察阶段；无交易操作。手机标题与全局菜单按钮的间距已实测修正。

### 18.3 验证和生产状态

- `backend/tests/test_squeeze_arbitrage.py`：13 项通过，覆盖价格放大但原始 OI 不变、陈旧/缺失比率与 OI、混合原始市场、完整小时、重复观察、重启存储、强平累计更新去重、覆盖断线、API-only、单字符 `GUSDT`、fake provider 及晚到 HTTP 响应。`test_pair_spread_query.py`、`test_repositories.py`、`test_api.py` 等共享回归此前 155 项通过；S1 后续局部修复均跑了对应新增测试。
- 前端生产构建通过，`AppShell` 12 项通过。全量前端测试初跑 187 项通过、5 项因新增菜单使旧顺序断言失效；更新断言后最终复跑 20 个文件、192 项全部通过。后续 S2 新增路线视图测试后再次全量复跑为 21 个文件、193 项通过。
- 生产使用两份精确 SHA 预构建镜像，脚本先备份、校验 `integrity_check=ok` 和主机/容器 SHA-256，再 `git pull --ff-only`、`up -d --no-build --wait`。最后一次备份为 `backups/radar-before-f7311e747a51-20260925T165309Z.db`，SHA-256 `89c1559edd744daed49469102b94ea2b4134813afdeaf1bd4b2478019795f0e4`；后端、前端容器均 healthy。
- 生产 `.env` 仅新增 `SQUEEZE_MONITOR_ENABLED=true` 及 `LSKUSDT,TUTUSDT,GUSDT,AKEUSDT,BTRUSDT` 五个公共 Binance 原始市场，修改前已在服务器内保留受控配置备份。生产 `/api/health` 为 `ok`；S1 status 为 enabled，五个市场元数据核验通过，首轮每市场 172 根 K 线和 30 个 OI 样本，质量均为 `ready`。watchlist/events 为空，属于当前未触发观察条件；旧 `/api/minute-signals/scan` 保持 404。
- 公开 `!forceOrder@arr` 连接显示 `throttled_public_stream`，完整覆盖始终为 false；目前未观测到目标市场强平消息，不能把零记录解释为零强平。线上只读页面 HTTP 200，桌面/手机浏览器均核对了数据与布局。
- 最终镜像自 2026-09-25 16:54:17 UTC 运行至 17:24:45 UTC，完成约 30 分 28 秒持续观察。两容器均 healthy；最终资源瞬时样本：后端 CPU 14.64%、内存 376.7 MiB/768 MiB，前端 CPU 0%、内存 4.477 MiB/96 MiB；启动约 3 分钟时后端内存 354.9 MiB。17:00 UTC 下一闭合小时扫描在 17:04:24 UTC 完成，五个市场均取得 172 根 K 线、30 个 OI 样本且质量为 `ready`，`last_error=null`。`/api/health` 为 `ok`，现有交易所状态均 healthy，watchlist/events 为空，旧 `/api/minute-signals/scan` 为 404。CPU 为采样时刻数值，不代表 30 分钟平均；公开强平流仍无目标市场消息，不能据此验收真实强平事件。

### 18.4 已知限制和 S2 起点

- S1 的 raw OI 单位保存为 Binance USD-M 合约原始单位，尚未将跨所合约数量换算为同一基础币；市场核验状态只供结构研究，不是 S2 可交易身份白名单。
- 公开强平流节流且缺稳定订单 ID，按可见事件身份与累计成交增量去重，仍可能漏报或存在无法消歧的更新；需继续看覆盖状态和断线缺口，不能声称全市场强平金额。
- S1 尚无盘口、费率成本、借币能力、模拟收益或盈利验收。跨所路线、指定基础币 q 的四向 VWAP、质量阻断和收敛确认由 S2 实施；真实执行属于另行授权的 S4。
- 其他任务的 `.worktrees/`、pytest 缓存、`output/` 截图/脚本、并行交接与探测脚本未暂存或修改。下阶段先复核分支/线上 SHA/工作区，并从本节和第 6、7、12、13 节实施 S2；按当前用户授权，S2 阶段交付后继续 S3。S3 的前瞻观察至少 14 天且 30 个独立事件，未满足前结论只能是样本不足。

## 19. S2 隔离库修复与线上交付续记（2026-09-26，北京时间）

### 19.1 范围、基线与实现

- 本轮接续分支 `codex/frontend-localization-polish`，起始 HEAD `85ac47bf5ae66934f357cf729126d966ef68c9cc`。原 S2 路线研究已推送，但其高频写入与 S1 共用 `radar.db` 时造成主库 `database is locked`，因此生产先以 `SQUEEZE_ROUTE_ENABLED=false` 恢复 S1。不能将 `85ac47b` 记为 S2 完成交付。
- 修复提交 `a585ecbaf626e252e87b214653777d7b69d3deaa` 已推送到当前分支。`backend/app/main.py` 将文件型路线数据移至同卷 `radar-squeeze-route.db`；`route_repository.py` 一次性迁移主库旧路线状态、最新评估、事件和 worker 状态，记录迁移标记，保留主库旧表，不重复覆盖新库后续写入。
- `route_runner.py` 使用容量 30 的有界写队列；独立采集与写入，数据库忙锁时重试，队列溢出计数并重置连续确认。status/API/UI 显示队列、最近采集、忙锁和丢样。路线仍仅使用 Binance/Bybit 公共 REST 的精确 LSKUSDT 线性 USDT 永续，结论恒为 `research_only`，不连接实盘执行器。
- `deploy/linux-update.sh` 对已存在的路线库增加单独 SQLite 在线备份、`integrity_check` 与主机/容器 SHA-256 校验；两类自动备份分别保留。首次从旧脚本部署时，路线库尚不存在，因此脚本只备份主库；后续部署将分别备份两库。

### 19.2 本地与生产验证

- 隔离库迁移、旧五列 worker 表、主库持写锁而路线库仍可写、忙锁积压恢复和队列丢样等定向测试通过。共享后端回归 234 项通过；前端相关 13 项通过、生产构建通过；备份保留策略 4 项通过；Git Bash `bash -n deploy/linux-update.sh` 通过。路线仓储、runner 和测试文件 Ruff 通过；`main.py` 与部署 Python 文件仍有既有 Ruff 问题，未进行无关整理。
- GitHub Actions 精确 SHA 构建成功，两份镜像 manifest 均含 `linux/amd64`。生产配置备份 `backups/env-before-route-enable-20260925T1944Z.env` 与修改前 `.env` 的 SHA-256 同为 `84052381bf64d1a324d4ccfdc37384d281ed656e9799ec4144f80b9da5e8a78f`，随后仅将 `SQUEEZE_ROUTE_ENABLED` 设为 `true`。不在仓库保存配置内容。
- 部署脚本先备份主库 `backups/radar-before-a585ecbaf626-20260925T194526Z.db`，`integrity_check=ok`，容器和主机 SHA-256 均为 `e3e66fa0190d5b60c7f1a6f0e30b9b372f33a868d771f815c3c2bda48616bbf8`；服务器 `git pull --ff-only` 到 `a585ecb`，使用两份同 SHA 预构建镜像 `up -d --no-build --wait`，两容器 healthy。
- 自约 19:46 UTC 启动至 20:17 UTC，完成约 31 分钟持续观察。S1 在 20:02 完成 20:00 UTC 闭合小时采集，五市场均为 `ready`、各 172 根 K 线和 30 个 OI 样本、`last_error=null`。S2 20:17:52 UTC 仍持续采集，`queue_depth=0`、`storage_failure_count=0`、`dropped_scan_count=0`、`last_error=null`；新路线库迁移标记存在且 `integrity_check=ok`。普通 `opportunity_history` 20:13 UTC 仍新增记录；窗口内后端日志检索共享库锁错误为 0。
- `/api/health` 为 `ok`，既有交易所状态均 healthy；只读路线接口返回精确双腿、三档固定基础币数量、四向 VWAP、费率周期和成本拆解，来源标为 `research_only_public_rest`。旧 `/api/minute-signals/scan` 为 404，前端 HTTP 200；桌面 1440px 和手机 390px Playwright 检查无页面错误和横向溢出。最终瞬时资源样本：后端 CPU 3.45%、内存 384 MiB/768 MiB，前端 CPU 0%、内存 5.723 MiB/96 MiB；20:09 曾有后端 CPU 119.15% 的瞬时样本，20:13 复测为 10.60%，不把单点值解释为持续平均。

### 19.3 限制与 S3 起点

- 当前 watchlist、route-events 均为空，未自然触发收敛确认或模拟成交；S2 只完成工程与线上稳定性验收，不能据此宣称路线可执行或盈利。公开强平流仍是节流观测，缺失不代表零。
- 只保留本模块已跟踪文件并推送；`.worktrees/`、`output/`、其他任务交接和权限受限的 pytest 缓存未清理或提交。
- 按第 7、8、12、13 节继续 S3：建立独立持久化 paper 余额/订单/成交/仓位/现金流，延迟后用新盘口执行 IOC 与部分成交，裸腿限时恢复、逐次实际资金费结算、失败和未成交保留、退出与重启恢复；使用固定参数和精确路线生成前瞻报告。Binance 历史资金费公开响应含结算 `markPrice`；Bybit 历史费率不含结算 mark，若用历史 mark K 线计算金额须明确标为代理。无足够维持保证金参数时标记 `collateral_model_incomplete`。S3 上线后的 14 天与 30 个独立事件未完成前，报告结论只能是“样本不足”。真实下单、借币、转账和自动建卡仍不在范围内。

## 20. S3 前瞻模拟账本与线上交付续记（2026-09-26，北京时间）

### 20.1 范围、基线与代码

- 本轮继续“价差与资金费率告警”模块。分支 `codex/frontend-localization-polish`；接续时 S1/S2 已交付，生产基线 `a585ecbaf626e252e87b214653777d7b69d3deaa`，本地 S3 初稿未提交。S3 业务提交 `a0f4a2c3461d24e36f3c2cc2fa558314278cc618` 和连续覆盖修复提交 `808f5b0f77aeba26bac899077ac4245d54947337` 已推送；其他任务未跟踪内容未暂存。
- `paper_models.py`、`paper_engine.py`：冻结 `squeeze-paper-s3-v1` 参数，固定 Binance/Bybit 精确 LSKUSDT 双永续研究路线；两所各 10,000 USDT 模拟账户、100 USDT 单腿目标名义、500 ms 延迟、独立新盘口序列、可见深度 IOC/部分成交、保守 taker 费、裸腿恢复与未决状态、30 分钟减半/2 小时复核退出/6 小时最长持仓。账户不跨所划转，模拟占用按 1 倍名义计；没有可靠维持保证金分层，始终标记 `collateral_model_incomplete`。
- `paper_repository.py`、`paper_runner.py`：余额、完整订单/成交/现金流、交易与事件游标写入独立 `radar-squeeze-route.db`，单次快照事务提交并按事件 ID、盘口时间和订单/资金费 ID 去重；旧版 paper 表增量迁移。worker 只扫描活跃或仍有资金费缺口的交易。路线盘口停更时仍用公开历史核对资金费，不推进盘口游标；超时无盘口的敞口保留为未决。连续观察起点与覆盖断档次数持久化，采集或 worker 间断超过 15 秒后，恢复时重新计算 14 天窗口。
- `paper_funding.py`、`paper_report.py`：Binance 用公开历史结算记录内实际 `markPrice`，Bybit 用结算后已闭合的一分钟 mark K 线收盘代理且显式标记。缺失结算不补零，已平仓但资金费未齐的交易不进入已结清收益。报告保留全量失败/未成交事件和成本、每档容量、按路线分组、未决敞口与保守压力估计；连续 14 天且本窗口至少 30 个独立事件前固定为 `sample_insufficient`，即使达到门槛也只显示“可复核”，不自动给盈利或实盘结论。
- `SQUEEZE_PAPER_ENABLED` 默认关闭，只有常规采集入口显式启用才启动 worker；API-only 不启动。`GET /api/squeeze-arbitrage/paper/status`、`/positions`、`/trades`、`/report` 与原 status 内 paper 段均只读并对列表设上限。`SqueezePaperView.tsx` 将模拟账本、结算来源、风险和样本门槛接入现有页面。双永续路线无借币费用；本阶段没有任何真实下单、借币、转账、通知或自动建卡调用。

### 20.2 本地验证与首轮线上证据

- 前瞻执行、步长/倍率、部分成交与裸腿恢复、资金费实值/代理、缺口重试、断流期间结算、重启幂等、旧表迁移、连续观察门槛、报告和 API-only 的假盘口/假时钟测试通过。共享后端回归 189 项通过；连续覆盖修复后的 S1/S2/S3 定向回归 47 项通过，最后一次结算连续性补丁的 paper 单测 15 项通过。全量前端 22 文件、194 项通过；相关视图测试、类型检查与生产构建通过。S3 新增文件及路线/API 定向 Ruff 检查通过；`main.py` 仍有既有 Ruff 项，未扩大整理。
- 首轮业务镜像 `a0f4a2c` 的 GitHub Actions 构建成功，两份 manifest 均含 `linux/amd64`。服务器修改前配置备份为 `backups/env-before-paper-enable-20260925T213324Z.env`，备份与原 `.env` SHA-256 均为 `d73019863ccfe95bc660a1319be862b62f783e1af3429b73825fba21f6fede0d`；仅新增 `SQUEEZE_PAPER_ENABLED=true`，S1/S2 开关保持 true。配置内容与凭据未写入仓库。
- 首轮脚本备份主库 `backups/radar-before-a0f4a2c3461d-20260925T213449Z.db`，`integrity_check=ok`、容器/主机 SHA-256 `ad0dcf9f20c39bce4603a8ec502695cefcba03ec59786e1c2f8e5db873299781`；路线库 `backups/squeeze-route-before-a0f4a2c3461d-20260925T213449Z.db`，`integrity_check=ok`、SHA-256 `4e7a1be8de6a17c8ca2a6c214acc6134148346599859ed661cbe6c65bce486fe`。`git pull --ff-only` 到该完整 SHA、两份预构建镜像 `up -d --no-build --wait` 后均 healthy；脚本按既有策略清理一份过期的自动主库备份，其余备份保留。
- 21:37 UTC 首轮实际接口：`/api/health=ok`，S1 五市场最近完整小时为 21:00 UTC，均继续采样；S2 路线采集 enabled，`queue_depth=0`、忙锁/丢样计数为 0、`last_error=null`，精确腿 `bybit|future|LSKUSDT|` / `binance|future|LSKUSDT|` 恒为 `research_only`。S3 worker enabled 且游标推进，两账户初始余额各 10,000 USDT，持仓/交易均为空；报告 0 个独立事件、`sample_insufficient`、`profitability_conclusion=null`。旧 `/api/minute-signals/scan` 保持 404，前端 HTTP 200；近 10 分钟纸面 worker/数据库锁错误日志计数为 0。
- Playwright 对生产桌面 1440px 和手机 390px 打开“模拟账本”，确认显示“样本不足”、无页面异常或页面级横向溢出。手机先用现有“隐藏关注浮窗”按钮收起全局浮窗，之后 tab 可点击。截图及验证脚本位于未跟踪 `output/squeeze-s3-production-{desktop,mobile}.png`、`output/verify-squeeze-s3-production.mjs`，未提交。

### 20.3 待完成的前瞻观察与边界

- 尚无自然确认的路线事件、模拟填单或真实资金费跨仓结算，不能声称策略盈利、容量可实盘执行、保证金风险已通过或 14 天/30 事件验收已完成。公开 REST 盘口仅为研究来源，Bybit 历史 mark 为代理。`collateral_model_incomplete` 不会因代码部署消失。
- 后续持续看 paper/route status 的游标、连续覆盖起点、断档、丢样、资金费缺口和未决敞口；达到连续 14 天且同窗口 30 个独立事件后，人工复核净收益、双账户资金和压力场景。S4 真实执行需另开任务并由用户明确指定账户、资产白名单、额度与损失预算，本文不提供真实交易授权。
- 其他任务的 `.worktrees/`、`output/` 既有产物、其他交接/探测脚本及权限受限 pytest 缓存均保留。S3 的本地临时 pytest 目录和上述截图/验证脚本也未纳入提交。后续业务修复与部署见 20.5 节。

### 20.4 808f5b0 镜像部署与早期验证

- 连续覆盖修复 `808f5b0f77aeba26bac899077ac4245d54947337` 的 GitHub Actions 构建成功，两份镜像 manifest 均含 `linux/amd64`。再次运行现有部署脚本，主库备份 `backups/radar-before-808f5b0f77ae-20260925T215129Z.db` 的 `integrity_check=ok`、容器/主机 SHA-256 `2142d1bc054335420fb27c6c39d46ad43cfd64f3e4b25c55b7896dacc55ca90e`；路线库备份 `backups/squeeze-route-before-808f5b0f77ae-20260925T215129Z.db` 的 `integrity_check=ok`、SHA-256 `81c47c62460ff22b1574d3f80bf395e35084bb9ed981b0b4cbe27f51d2cf001a`。服务器 `git pull --ff-only` 到该完整 SHA，用两份同 SHA 镜像 `up -d --no-build --wait`，后端、前端均 healthy。生产 `.env` 未再次修改。
- 21:54 UTC 路线库升级后的 `PRAGMA integrity_check=ok`；既有 paper run 保留原 `started_at`，迁移所得 `continuous_started_at` 为 21:36:13 UTC，`coverage_gap_count=0`、`coverage_gap_open=false`。808f5b0 镜像的 `/api/health=ok`，paper status、positions、trades、report 及路线接口正常；paper 游标和路线采集时点继续前进，持仓/交易仍为 0，报告仍为 `sample_insufficient`、`profitability_conclusion=null`。桌面/手机 Playwright 再验均无页面错误或页面级横向溢出。
- 该镜像启动约 1 分钟的瞬时资源样本：后端 180.6 MiB/768 MiB、前端 3.414 MiB/96 MiB；稍后样本为后端 356 MiB/768 MiB、前端 5 MiB/96 MiB。一次聚合 status 在 12 秒请求限时内未返回，随即复测为 HTTP 200、约 0.54 秒；需继续观察延迟，而不能把单次成功视为持续性能保证。
- 21:52:50 UTC 主库 `HistoryRecorder._prune` 的既有 `VACUUM` 路径出现一次 `cannot VACUUM - SQL statements in progress`，来自普通 collector 的历史清理，不是 S3 路线库写入。普通 `opportunity_history` 最新时间随后从 21:54:56 增至 21:56:57 UTC；paper 失败和 `database is locked` 日志匹配为 0。这条主库清理异常应在独立运维/历史模块任务中排查，若后续重复则优先保障普通行情写入。

### 20.5 连续窗口可信度修复与最终部署

- 在交付复核中发现：若 worker 停止，原报告只按日历时间和持久化 `coverage_gap_open` 判定，停机期间可能继续累积 14 天；旧 paper 表迁移把没有断档记录的旧 `started_at` 当作连续起点。这两种情况都可能在将来误报 `ready_for_review`。修复提交 `7827a814999311ff57fa1f7d63fbbb668c116413` 已推送：报告还要求 `last_success_at` 距查询时间为 0 至 15 秒；新增一次性 `coverage_tracking_version=1` 迁移，将既有 run 的连续起点重置到首次具备完整追踪的升级时点，并保留原始 `started_at`、余额、事件游标及交易。重复初始化不重复重置。
- 修复后的纸面账本 18 项、S1/S2/S3 组合 48 项、共享 API/仓储/价差回归 194 项全部通过；共享回归仅有既有 `datetime.utcnow()` 弃用警告。三个修改文件的 Ruff 与 `git diff --check` 通过。前端源代码未变，上轮全量前端 22 文件、194 项与生产构建通过；该 SHA 的 GitHub Actions backend/frontend 两项构建均成功，两份 manifest 都含 `linux/amd64`。
- 再次运行 `deploy/linux-update.sh`：主库备份 `backups/radar-before-7827a8149993-20260925T222505Z.db` 的 `integrity_check=ok`，容器/主机 SHA-256 为 `9c314f20533989eda46fcbce8edc8907c5e937cd4fcfa7867402a7ec0062767c`；路线库备份 `backups/squeeze-route-before-7827a8149993-20260925T222505Z.db` 的 `integrity_check=ok`，SHA-256 为 `cd9bc316cd5b4e57e8abc630661ae1598ae816f64d9fd92fd436f669dc29ed02`。服务器 `git pull --ff-only` 至该完整 SHA，以两份同 SHA 预构建镜像 `up -d --no-build --wait`，两容器 healthy；生产配置未改。脚本按既有保留策略自动删除较旧的 `radar-before-a585ecbaf626-20260925T194526Z.db`，保留本轮和 808f5b0、a0f4a2c 的主库备份及三份路线库备份。
- 升级后路线库只读 `PRAGMA integrity_check=ok`、`coverage_tracking_version=1`。paper run 原 `started_at=2026-09-25T21:36:13Z` 保留，可信连续窗口改从 `2026-09-25T22:26:24Z` 起算，`coverage_gap_count=0`、`coverage_gap_open=false`。`/api/health=ok`，22:31 UTC 路线和 paper 游标继续推进，路线队列、存储失败、丢样均为 0；两模拟账户各 10,000 USDT，暂无交易/持仓。报告为 0 个独立事件、`sample_insufficient`、`profitability_conclusion=null`，普通 `opportunity_history` 已写入到 22:30 UTC。
- 新镜像通过只读 Playwright 复核：桌面 1440px、手机 390px 均打开模拟账本并显示“样本不足”，无页面错误或页面级横向溢出；截图仍存于未跟踪 `output/squeeze-s3-production-{desktop,mobile}.png`，未纳入 Git。
- 自约 22:26 UTC 启动至次日 01:37 UTC，完成约 3 小时 11 分钟运行观察，两容器保持同一完整 SHA 且 healthy。S1 已完成 01:00 UTC 闭合小时：五个精确 Binance 原始市场均为 `ready`、各 172 根 K 线及 30 个 OI 样本、`last_error=null`。公开强平流仍标记 `throttled_public_stream` 且 `public_stream_complete=false`，没有目标市场消息不能解释为零强平。
- 01:36 UTC S2 路线与 S3 paper 游标仍贴近当前时间：路线 `queue_depth=0`、`storage_failure_count=0`、`dropped_scan_count=0`、`last_error=null`，精确 Bybit/Binance LSK 双永续路线仍为 `research_only`；paper `coverage_gap_count=0`、`coverage_gap_open=false`、`last_error=null`，两个账户仍各 10,000 USDT。尚无自然确认事件、模拟成交或持仓，报告仍为 `sample_insufficient`、`profitability_conclusion=null`，14 天/30 事件的前瞻评审尚未发生。
- `/api/health=ok`、前端页面 HTTP 200，旧 `/api/minute-signals/scan` 仍为 404；普通 `opportunity_history` 至少写入到 01:35:31 UTC。本镜像自启动以来的日志检索未发现 `squeeze paper monitor failed`、`squeeze route scan failed`、`database is locked` 或 `cannot VACUUM`；先前 808f5b0 镜像发生的一次主库 `VACUUM` 异常仍保留在 20.4 节作为待关注历史问题。01:37 UTC 瞬时资源样本为后端 386.2 MiB/768 MiB、前端 5.645 MiB/96 MiB；CPU 瞬时 6.24%/0%，不能视为观察窗口平均值。

## 21. S1 当前候选发现修正（2026-09-26，北京时间）

### 21.1 起因、范围与交付代码

- 用户指出 S1“数据覆盖”仍为 AKE、BTR、G、LSK、TUT 五个历史样本，明确要求寻找当前可能异动的市场。生产 API 在改动前确实持续采这五个原始市场；`ready` 仅表示历史输入完整，不能解释为当前机会。
- 本轮仍属“价差与资金费率告警”模块，仅更新 S1 候选发现、观察池展示和审计。分支 `codex/frontend-localization-polish`，起点 `48528517299b94575d21d42b7a63a52afbe36acb`；业务提交 `fea864b9347d7df1bf4b520d9366cb547a555c03` 已推送。S2/S3 的固定 LSK 双永续研究路线与前瞻 paper 样本没有被候选轮换改写。
- `discovery.py`、`provider.py`：默认自动模式每个已闭合小时用 Binance USD-M `exchangeInfo` 与全市场 24h ticker 初筛。仅接受可交易线性 USDT 永续和不超过 5 分钟的 ticker；初筛范围为 24h 涨幅 2%—35%、成交额 100 万—2.5 亿 USDT、24h 价格区间 >=6%、交易数 >=300。按接近适度日涨幅排序，只深入查询最多 12 个原始市场。默认排除截图中的五个旧市场。
- `runner.py`：对最多 12 个初筛市场各查询 172 根已闭合小时 K 线、30 个原始 OI 与账户比样本；只有数据质量 `ready` 且 4h 涨幅 1%—15%、24h 涨幅 3%—35%、量能比 >=1.5、原始 OI 24h 增长 >=1%、账户比 <=1.1 的市场进入早期候选。满足原 S1 严格结构事件条件的市场也可进入候选。按量能、原始 OI、近期涨幅与账户比排序，最多保留 5 个；不满足时允许 0 个，不补旧币。正常每小时最多 2 次全市场请求加 36 次逐市场请求；429 重试仍按 provider 既有限额。
- `repository.py`：每次筛选追加保存规则版本、来源时间、入选指标和最多 12 个拒绝原因；API `/api/squeeze-arbitrage/status` 的 `discovery` 返回当前候选及初筛样本。超过 2 小时或后续刷新失败，当前名单失效。页面“数据覆盖”和“观察池”只显示有效入选市场，旧事件仍留在历史。生产旧 `.env` 中的 `SQUEEZE_MONITOR_SYMBOLS` 只有显式设置 `SQUEEZE_MONITOR_MODE=fixed` 才会启用；默认 `auto` 使用上述排除名单。
- 页面分开展示当前异动候选、没有入选时的本轮初筛样本及原因、数据覆盖与正式结构事件。早期候选不是可成交路线，也不构成下单、借币、转账或自动建卡授权；公开强平 `!forceOrder@arr` 仍是节流辅助观测。

### 21.2 验证与上线前状态

- 后端全量 844 项通过；追加空候选诊断后，S1/S2/S3/API 133 项通过；最后追加筛选快照审计后，S1/S2/S3 52 项通过。Ruff 与 `git diff --check` 通过。前端全量 23 文件、195 项通过，最后定向 3 文件、4 项与生产构建通过。已有 `datetime.utcnow()` 弃用警告未并入本模块处理。
- 生产改动前版本 `7827a814999311ff57fa1f7d63fbbb668c116413`，两容器 healthy，S1 的五个旧样本仍为最近覆盖；目标业务镜像与实际候选的发布证据见 21.3 节。上线前只读 Binance ticker 初筛可见 WAXP、JOE、REZ、PROM、AVNT、MAGMA 等新市场，但它们当时仅是行情初筛，并未验证结构或盈利。
- 本地其他任务的 `.worktrees/`、`output/`、未跟踪交接/探测文件与权限受限 pytest 缓存原样保留；本轮新增的 `backend/.pytest_squeeze_dynamic_20260926r{1,2,3}/` 也未提交。线上仍需观察实际候选数量、API 错误、请求资源、两库与 S2/S3 持续状态；当前阈值是工程初值，不能当成已验证的策略收益参数。

### 21.3 预构建镜像、备份、部署与真实候选

- GitHub Actions 运行 `36224728072` 的 backend/frontend 两项矩阵作业均 `success`。两份 `sha-fea864b9347d7df1bf4b520d9366cb547a555c03` 镜像 manifest 均包含 `linux/amd64`；后端 index digest `sha256:0e2dc95eb3015812d4dcc9460adb8c6dc0d3d87c9852203c3b440a5c85bfe516`，前端 `sha256:618373430a349dc83ec0485cec9c24bfa44fa45f779e5f9af288681de4bcd051`。
- 使用 `deploy/linux-update.sh` 先备份主库 `backups/radar-before-fea864b9347d-20260926T064934Z.db`，`integrity_check=ok`，容器/主机 SHA-256 一致，为 `855f6b073492402d611966fd8e1eb991de0cfe26a6ad7a6ab25995caf72c3432`；路线库备份 `backups/squeeze-route-before-fea864b9347d-20260926T064934Z.db`，`integrity_check=ok`，一致的 SHA-256 为 `db5041f8f78bdd1a8651c1090a1b62e55ee24f1f98a1a7f08635de4bae2a094b`。脚本按既有保留规则删去两库各一份较早的自动备份；未动手工备份。
- 服务器从 `7827a81` 以 `git pull --ff-only` 快进到完整业务 SHA，拉取两份同 SHA 的预构建镜像，以 `docker compose up -d --no-build --wait` 启动；后端、前端均 healthy。部署后 `/api/health=ok`、前端 HTTP 200、旧 `/api/minute-signals/scan` 为 404；一次资源快照为后端约 358.6 MiB/768 MiB、前端约 4.5 MiB/96 MiB，后端 CPU 瞬时约 34%，不代表持续平均。
- S1 启动后首次自动筛选于 2026-09-26 06:50:54 UTC 完成，对应 06:00 UTC 闭合小时：24h ticker 基础条件通过 210 个，深入核验 12 个，正式当前候选 2 个。`SPKUSDT` 为 4h +4.38%、24h +15.69%、量能 6.05x、原始 OI 24h +42.72%、账户比 1.05、24h 成交额约 28.6M USDT；`REZUSDT` 分别约 +1.70%、+12.86%、2.10x、+14.21%、0.93、17.6M USDT。两市场各 172 根小时 K 线、30 个 OI 样本且 `ready`，`last_error=null`；原 AKE/BTR/G/LSK/TUT 不在当前候选与数据覆盖里。`active_watch_count=0`，说明没有达到更严格的正式结构事件门槛。
- 强平覆盖仍是 `throttled_public_stream`、`public_stream_complete=false`、`open_gaps=0`。S2 固定 Bybit/Binance LSK 双永续路线仍为 `research_only`，队列、存储失败、丢样均为 0；S3 worker 继续推进且无覆盖缺口，报告 0 个独立事件、`sample_insufficient`、`profitability_conclusion=null`。旧路线的固定研究样本没有因 S1 候选轮换而变成当前 S1 推荐。
- 生产页面通过实际 HTTP 地址的 Playwright 桌面 1440px、手机 390px 检查：均显示当前候选、无旧 AKE 行、无脚本错误，`body.scrollWidth` 等于视口宽度；截图留在本机 Codex 可视化目录 `squeeze-current-{desktop,mobile}.png`。一次并行请求 `/watchlist` 在 5 秒内超时，随后复测该接口 HTTP 200/约 2 毫秒、`/status` HTTP 200/约 4 毫秒；需继续观察是否重复。后端日志中另有 Gate 公告分类抓取 traceback，属于公告模块，未见本轮 squeeze 采集、路线或数据库锁错误。
- 这仅证明动态候选首轮采集与展示正常；筛选参数没有前瞻盈利验证，尚无自然结构事件或 paper 成交，S3 的 14 天/30 独立事件门槛也未达到。真实下单、借币、转账和自动建卡仍不在授权范围。

### 21.4 下一闭合小时的轮换复核

- 2026-09-26 07:05:56 UTC，S1 正常完成 07:00 UTC 闭合小时扫描：24h ticker 基础条件通过 207 个、深入核验 12 个、入选 0 个，`last_error=null`。上一小时的 SPK、REZ 在本小时因 4h 动量或账户比不再满足早期条件而退出当前名单；本轮初筛的拒绝原因仍在 `discovery.screened`，并未用旧五币或上一小时的候选补位。`/watchlist=[]`，历史筛选记录按每次运行追加保留。
- 实际生产页面的第二轮 Playwright 桌面 1440px、手机 390px 均显示“本轮初筛（未入选）”和拒绝原因，旧 AKE 不出现、无脚本错误、页面宽度等于视口宽度；截图 `squeeze-empty-{desktop,mobile}.png` 留在本机 Codex 可视化目录。07:09 UTC 左右 S2 路线与 S3 paper 游标仍继续推进；这次 0 候选是条件筛选结果，不是采集关闭或采集失败。
