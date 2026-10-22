# FS 多交易所观察池交接

## 用户要求、范围与基线

用户在上一轮交付后要求补充其他交易所；本轮因此解除原“仅 Bybit”范围限制，但继续限定同平台“多 USDT 永续、借币卖出现货”。默认覆盖 Bybit、Binance、OKX、Bitget、Gate。沿用“信号达标自动建 Astro 停用卡片，重复检查，禁止开仓”；不实际借币/下单、不更改账户或杠杆、不发外部通知。不扩大到跨平台或无对应现货借贷数据的 DEX。

分支 `codex/frontend-localization-polish`，开始时本地/远端 `3863dc5`，线上 Git 与两个镜像 `243ae4687313001fe5c4656a96290622f500ca5d`，容器 healthy。先前背景见 [Bybit/Astro 交接](task-handoff-2026-10-22-fs-astro-signals.md)。

## 实现

- `backend/app/services/fs_public.py`：新增四个平台只读公开适配，复用现有快照池发现负费率，保留真实来源时间。精确原始市场格式和正常 USDT 同平台路线匹配仅用于发现，绝不作为身份核验。排除 alias/R token 混用。
- `fs_monitor.py`：五个平台故障隔离、轮流分配总深查名额，默认总数仍为15；有现货的优先。某平台失败保留其他平台；公开借贷查询失败仍保留其候选与阻断。单候选12秒超时、全轮50秒上限、全局并发5、各平台请求间隔130ms（Bybit120ms）。约10秒起始周期、15秒配对复核、30秒盘口、60秒其他输入不放宽；慢查询仍可能过期。
- `fs_public.py` 将 OKX/Gate 订单簿张数换算为基础币，交易数量步长/下限同样换算，保留原合约乘数用于与身份绑定比较。OKX 当前 SPOT/SWAP/MARGIN 规格和 funding/mark 重新查询；不把 collector 的推测结算周期作为 OKX 自动信号。
- `fs_economics.py` 按现货/永续各自原始市场核对规格与盘口，兼容旧 Bybit 数据。未改压力利润门槛、未知值阻断与账户成本层级。
- `fs_astro.py` 使用候选交易所和双腿原始市场，仍限原生同平台 FS、status=false、disableOpen=true。重新取样必须保持相同 exchange/symbol/base/双腿原始市场和身份版本。持久化去重按交易所隔离；Bybit 继续读写旧 symbol 键，其他平台使用 exchange:symbol，避免升级后丢失旧 pending/created 记录。不同交易所同名标的不会互相压掉。
- `models/fs_monitor.py` 增加 exchanges 配置（默认五家，至少一家）、双腿 raw symbol、原始合约乘数、每平台状态。旧数据库设置缺失 exchanges 自动应用默认，不需修改用户账户或其他阈值。
- `FsMonitorPage.tsx` 增加扫描交易所选择、平台状态/条数和交易所列；桌面、手机、历史均按 exchange:symbol 唯一键显示，未知利率不再标成公开普通档位。Bybit 额外账户核验仍可选，绝不将其凭据发送到其他平台。

## 数据边界与官方依据

| 平台 | 本轮可取得数据 | 仍阻断的来源缺口 |
| --- | --- | --- |
| Bybit | 延续公开支持、公开利率、双腿规格/盘口 | 账户额度/库存/真实手续费未实测 |
| OKX | 公开基础日借币利率除24换算小时；当前市场/杠杆产品规格、带ts盘口、资金费率/周期/标记价 | 每个候选仍须已有精确身份与单位绑定；真实账户利率/额度未知 |
| Bitget | 公开全仓借币支持、规格、带ts盘口、已有行情负费率 | 借币利率需认证接口，本轮未知并阻断自动信号 |
| Gate | 公开逐仓产品支持、规格、带时间盘口，合约张数转基础币 | 借币利率需认证接口；产品支持不表示 Astro 账户当前借得到 |
| Binance | 行情/市场规格/深度；现货与USDT永续发现 | SAPI资产参考/利率需账户接口；现货REST深度没有交易所时间戳，保留 missing_source_time，不以收到时间伪装 |

官方来源：
- [OKX public interest-rate-loan-quota](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-interest-rate-and-loan-quota)：basic.rate 是日利率；不把 quota 当库存。本地实时公开接口核验成功。
- [Gate isolated margin](https://www.gate.com/docs/developers/apiv4/en/isolated-margin/)：currency_pairs 是公开产品参考；estimate_rate 需要签名，小时利率随池深变化。本地未签名实测返回缺少 Timestamp。
- [Binance margin borrow API](https://developers.binance.com/docs/margin_trading/borrow-and-repay/Margin-Account-Borrow-Repay)：利率为 USER_DATA。公开 allAssets 未签名实测返回 API-key format invalid。
- [Bitget 公开产品接口](https://api.bitget.com/api/v2/margin/currencies)：本地实时核验 isCrossBorrowable/status；未使用其私有利率接口。

未引入新币身份白名单，沿用现有 registry。MEW/BMT 的 Bybit 四条旧证据仍固定2026-10-29 09:20 UTC到期，不能推广到其他平台。其他交易所身份 pending/conflict 继续挡住自动建卡。

## 验证记录

本地真实公开探测：五个平台均有候选，默认各3条、共15条，正常轮次约5秒；OKX公开利率及基础币盘口成本可计算。也观察到公开请求短时失败，失败平台和借贷来源明确降级，不复用旧利率。证据 `output/fs-multi-probe-20261022.py` / `.json`。这些是公开数据验证，不是账户或成交验证。

新增模拟测试覆盖五平台停用卡映射、同名跨平台持久化去重、保留旧Bybit pending、复核不能切换平台、OKX乘数与registry一致性、日利率换算、张数换算、原来源时间保留、R token排除、单平台失败隔离/选择过滤、利率失败不假设0。前端覆盖同名市场分开展示、平台错误与未知利率。

完整测试及线上交付结果见下方部署补记。首轮后端旧错误断言已保持兼容；前端重复文案键导致类型检查失败已修复。Windows 测试首次创建新增 HTTP 客户端加载TLS证书较慢，已延迟到实际首次查询，保留TLS校验。中断/失败日志均保留，不能把中断回归算通过。

## 工作区保护与后续

原有 PairMonitorPage.tsx / PairMonitorPage.test.tsx 未修改，哈希仍分别为 `11379f2cb0881b274a998a4a4534a698c4dd94b0080de3de691f4dbf27440d38` / `0376b098c2274e60666915709d9d1183decac45781a861faac452aacb00500ab`。所有旧未跟踪文件、DB/WAL/SHM、日志保持；新增探测/测试证据不提交。

后续应观察首个自然达标的真实 FS add 和回读；不得降低身份/成本/时间门槛制造验收。Binance/Bitget/Gate 的私有利率如需接入，应明确 Astro 是否提供只读借贷数据或由用户选择账户，当前 SDK 列表/建卡链路不能代替借贷报价。现有跨客户端同时建卡没有服务端幂等键的限制仍在；不自动重试不确定提交，不自动重建用户删除的卡片。
