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


## 最终交付与线上验收

- 功能提交 `79eef76143702c395afbe41db228316e977ec9ca` 已推送并部署。GitHub Actions [39771211633](https://github.com/wubin19920612/taoli1/actions/runs/39771211633) backend/frontend 均成功；部署前两镜像manifest返回200。服务器以 `git pull --ff-only` 更新到该SHA，使用预构建镜像 `up -d --no-build --wait`，没有生产构建。
- 部署前主库备份 `backups/radar-before-79eef7614370-20261022T113334Z.db`，719351808字节，SHA256 `ea71b3281a66c811a6b21da310b257090450b8f21f84259f7550c6279380f109`。
- 路线库备份 `backups/squeeze-route-before-79eef7614370-20261022T113334Z.db`，241664字节，SHA256 `ac8fbb4f832fc891165bbfe924181c600fab1fe71dfdee920f9f221d1c3dacab`。两库 integrity_check=ok、容器/宿主机哈希一致。既有部署保留策略各清理1份旧常规备份、各保留9份；本轮未手工清理。部署完整日志 `output/fs-multi-deployment-20261022.log`。
- 最终后端98项通过（FS监测/多平台适配/Astro桥接/planner/身份目录/账户连接），见 `output/fs-multi-backend-20261022e.log`。前端FS7项＋AppShell12项=19项通过，见 `output/fs-multi-frontend-20261022a.log`；类型检查/Vite生产构建通过，见 `output/fs-multi-build-20261022b.log`；修改的后端文件 Ruff 与 git diff --check 通过。原有 Starlette 弃用/React act 告警保留。
- 线上 Git/两镜像均为功能SHA，tracked clean，前后端 healthy；health status=ok、12204市场、exchange_errors={}。启动后短期内存 backend627.6MiB/768MiB、frontend4.176MiB/96MiB，仅是短期采样。
- 韩国时间2026-10-22 20:35:33～20:36:10，8次真实API采样均五平台各3条，共15条；扫描3.110～5.355秒，没有快照 stage=stale。注意“观察快照未过期”不等于其所有来源齐全：Binance仍明确缺少盘口时间，私有利率未知仍阻断。认证接口200，无密码401，历史强制stale且signal_ready=false。
- 自动建卡设置仍true，账户仍null，未更改原有规模/收益/手续费阈值。现场没有信号通过，Astro真列表114张、FS=0，本轮未执行真实FS add。新增跨平台去重、异常重试停止等由模拟验证；首个自然达标信号的真实写入与回读仍待观察。未验证任何新平台的真实账户利率/额度/库存/手续费，没有借币或下单。
- 当前样本：Bybit BMT/MEW身份通过但压力净收益不达标；OKX MEW有身份/最大市价数量阻断，ZIL/LPT有身份/成本阻断；Bitget SAND身份通过但账户借币利率未知；其他pending继续阻断。不能将Bybit证据复制给OKX同名市场。
- 桌面1440px、手机390px实际生产页面、五平台配置选项、自动建卡开关通过，0页面脚本错误，无整页横向溢出。证据 `output/fs-multi-production-{1440,390}-20261022.png`、`output/fs-multi-production-browser-20261022.json`、`output/fs-multi-production-verification-20261022.json`。首次浏览器验证因本机SSH隧道断开失败，重建本任务隧道后通过；不是生产页面故障。
- 日志抽样保留在 `output/fs-multi-production-logs-20261022.log`，无本模块异常栈或接口500。最终交接另作 `[skip ci]` 文档提交，运行镜像保持功能SHA。
