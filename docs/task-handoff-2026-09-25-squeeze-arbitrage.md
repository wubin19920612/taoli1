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
