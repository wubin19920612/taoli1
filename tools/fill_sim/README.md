# fill-sim

独立、纯标准库的公开盘口模拟服务，运行目标 Python 3.10 / SQLite 3.37。代码不导入 backend，不下单，不使用交易所认证接口。业务规则见 `docs/fill-sim-spec-2026-11-09.md` 及其引用设计稿；触发器以任务书的 API 轮询约定为准。

## 本地验证

在仓库根目录执行。解析/引擎测试阻断 socket connect；HTTP 截止时间与 guard 退出测试仅访问测试自建的 127.0.0.1 服务。受限环境请先将 TEMP/TMP 指向可写临时目录：

```powershell
python -m unittest discover -s tools/fill_sim/tests -v
python -m compileall -q tools/fill_sim
```

显式联网冒烟，仅公开 GET，无 taoli1 token：

```powershell
python -m tools.fill_sim.smoke --include-spot --fixtures tools/fill_sim/tests/fixtures
```

冒烟顺序获取六家 BTC 合约/现货规格和 50 档深度，以及六家合约资金费快照，打印解析结果与四档模拟 fills；跨所 BTC 示例是非同步快照的工程演示，不能用于交易判断。费用来自 `deploy/fill-sim.env.example`；2026-11-09 官方核对出处及未确认项见本轮交接。

进入 `tools/` 后可使用任务书的命令形式：

```sh
python3 -m fill_sim report --db ../var/fill_sim.db --days 7
python3 -m fill_sim run --env-file /path/to/fill-sim.env
```

`run` 的参数可覆盖 env：`--api-base`、`--db`、`--out`、`--fees-json`、`--exchange-rate`、`--global-rate`、`--guard-file`。API token 只从 `TAOLI1_API_TOKEN` 获取，不提供 token 命令行选项，避免进入进程列表。显式 env 文件只允许 fill-sim 的配置字段，拒绝其他字段；不自动读取项目 `.env`。`report` 不请求网络；只有实际 `run` 使用 taoli1 token。

## 调度与记账口径

- 固定 `GET /api/shadow/trades?limit=50`，无 status 参数，成功请求完成后至少间隔 1 秒；失败间隔 2/4/8/16/30 秒；HTTP socket 超时 3 秒。响应不得超过 50 行、8 MiB，不跟随重定向。
- ID 持久去重；旧于 120 秒记 stale；同币、无方向交易所对有持仓或在途机会时拒绝重复。各腿始终保留 exact raw symbol、market type 和倍率。仅支持 USDT 线性永续和 USDT 现货；非 1 的价格单位倍率、反向/交割合约、无法确认的单位按 mismatch/unsupported 拒绝，不猜 ticker 前缀倍率。
- 并发 10 是机会组上限；每组最多 3 个延迟变体 × 4 档金额 = 12 个独立账本仓位。它们是互斥的研究场景，不能把总美元收益理解成同一资金账户的组合收益。每组跟踪共享一次两腿盘口，降低请求量。
- 初次两腿并行抓取。第二次抓取在初次较晚返回的盘口之后至少 1 秒启动；`leg_delay` 混合初次 A / 第二次 B，不额外抓取。记录全部实际收到时间和交易所时间。因调度有实际超时差，`plus_1s` 是最小等待 1 秒，不保证精确延迟。
- 启动加载并刷新已缓存规格，并预热六家 BTC 合约；新市场按 exact key 获取规格，等待最多 6 秒，计入 10 个机会的在途额度。规格 TTL 6 小时；刷新失败不允许新开仓使用过期规格。冷缓存超时记 `spec_unavailable`，不排无限队列。
- Decimal 吃单；两腿共同数量使用步长的最小公倍数向下取整，保证同时可整除。先检查两侧 50 档是否均能覆盖目标金额，再取共同数量；实际名义额可小于目标档位，记录实际金额。Gate/OKX 合约盘口的张数乘面值。
- 开仓阈值按设计公式扣除预估四笔 taker 费后净 10 bp。平仓回补开仓的原始币数，不按新价格另算数量；实际开平四笔费用按各自成交金额计。净 bp 分母为买腿实际开仓金额。收敛判断使用开平两腿 VWAP 的毛价差，与 shadow conv-half 对齐。
- 24h 且收敛过半与即时 half 收敛逻辑重叠，代码在 24h 后将符合条件的退出标成 `timeout_24h_converged`；48h 强制退出。数据连续失败 60 秒使用各腿最后成功盘口，标记 stale，排除主收益。正常 60 秒采样不被误判为断流；重启间隔超过 60 秒则视为数据中断。
- 如强平时最后盘口仍不足以回补全部数量，退回上次可完整平仓的盘口并标 stale；从未有可平仓盘口则记 `unpriced_exit`，没有虚构收益。
- 只有一层发送准入：每所 ≤2/s、全局 ≤8/s 滚动窗口（可调低），规格、盘口、资金费共用预算；最多 4 个公开 HTTP 请求在途。一个优先队列统一调度：持仓优先，有新开仓等待时每 4 个发送名额至少 1 个归新开仓及其规格/资金费依赖。本地无额度时等下个名额，不推进持仓下次跟踪时间。429/418/限频码暂停至少 60 秒并指数退避至 3600 秒；尊重更长的数字 Retry-After，Bybit 403 保守暂停至少 600 秒。

## 资金费和手续费边界

按“PM decisions 第一轮补充”，独立请求公开资金费，不读取或信任上游 `payload.tracking`、`fill_sim_funding` 或影子最终 funding_bps。各所端点、字段和官方来源见本轮交接。

- 在首轮盘口前获取合约腿的 rate / next / interval，并保存原始市场 key、捕获时间、来源 URL。rate 是小数比率，next 是 UTC 秒，interval 是秒；现货为 `{"spot":true}`，资金费恒为 0。Binance/Aster 需要 premiumIndex 和 fundingInfo 两个公开请求，其余每腿一个。Binance fundingInfo 返回配置列表；只提取该 exact symbol，不访问行情列表。
- `exchange|market_type|raw_symbol` 缓存 300 秒，最多 2048 个市场；成功及失败结果均缓存，失败仍是未知而非零。已过 next 结算点的成功快照不能用于新开仓。市场缓存只在内存，重启后重取；已开仓快照随仓位持久化，不被以后刷新覆盖。
- 异步等待资金费最多 7 秒；单个 HTTP 请求允许 8 秒总截止，所以等待到期可能先按未知资金费继续，取消无依赖的剩余排队资金费任务。盘口进入公平队列，最多 4 个公开请求在途。超时或解析失败记 funding_errors；本地无额度等下个名额，远端限频记 skipped_rate_limit。事件超过 120 秒后取消未完成的开仓阶段并记 skipped_stale_trigger。等待占用 10 个机会组限额，算入实测延迟。三变体和四金额档共用快照；延迟变体跨过快照结算时间时保守标未知。
- 合约在持仓跨结算时，以开仓费率和间隔重复估算：多头支付、空头收取。`funding_estimated=true`；不声称使用了最终结算费率。字段缺失、错误市场、缺少周期、非有限数字、已过期结算点、带服务端时间但偏差超过 90 秒的响应均不能当已知。
- `funding_known=false` 的仓位排除主收益和盈利通过线，保留原有 `provisional_price_only` 辅助统计。该辅助统计沿用所有非 stale/unpriced 已平仓样本（也包含已知资金费样本），不是独立的未知资金费样本集。
- 状态和日报新增 `funding_unknown`、`funding_observations`、`funding_unknown_ratio`：按三变体 × 四金额档的仓位数计数，任一腿未知即该仓位未知，比例=未知数/观察仓位数，无仓位为 null。日报窗口包括当日开仓且仍未平仓的仓位及当日平仓的仓位；status 的这三项采用今日 UTC 同一口径，另有 `open_funding_unknown_ratio` 覆盖所有当前未平仓仓位。

默认费用在 `default-fees.json`，env 样例同步列出；`--fees-json` / `FILL_SIM_FEES_JSON` 可以整体覆盖，显式缺项或 null 不会偷偷补默认。费率单位为小数比率，基于普通 USDT 加密资产市场的公开 VIP0/普通等级，不计代币折扣、返佣和活动。六家现货默认已补齐；Aster USDT 合约由 0.00035 改为 0.0004。PM 已在官方动态费率表核实 Binance U 本位 Regular User 的 USDT taker 0.0500%，默认值为 0.0005，与 env 样例一致，不采用 BNB 抵扣。`unconfigured_fees` 在状态/日报/report 列出最近一次 run 配置缺项（无运行配置时列出软件默认缺项），不代表逐笔历史费率。

现货出现在 sell 一侧时，在规格/资金费/盘口请求前记 `unsupported_spot_short`，不开仓，保留在过滤通过事件和覆盖率分母。现货 buy + 合约 sell 允许模拟。借贷利息、库存/保证金/账户权限和实际撮合仍不在本模型内。特殊板块、Aster Group B/RWA、Bybit 创新区/盘前等费率不能套用普通默认；部署前须确认实际市场范围和账户地区。

## 持久化、报表、资源

- 单写进程锁、WAL、FULL 同步，事件/成交/持仓更新在事务内完成。重启恢复持仓和 seen ID；未完成的延迟阶段记 `interrupted_restart`，不使用重启后的盘口假冒原延迟。
- UTC 按天将已完成的整个事件族移入 `fill_sim-YYYY-MM-DD.db`。活动族留在主库，结束后下次日切归档。归档迁移暂切 DELETE journal，使用 SQLite 多库原子事务复制/删除，随后恢复 WAL；seen tombstone 保留 30 天。归档和日报保留 30 天；不会操作 radar.db。
- 日报每 5 分钟重算今日和昨日；触发/还在率按发现日，收益按平仓日。报表读取数据库日期窗口，聚合前丢弃盘口大字段。记录事件跳过原因、实测 300 美元还在率、各档均值/胜率/USD、容量曲线、各变体、影子偏差分位数、币/所对/价差档和 top3/其余。
- 90% 下界为单侧按“完整平仓日”重抽样 2000 次、每次样本按交易数加权的均值第 10 百分位；不是逐笔 bootstrap。少于两天返回未知；总观察时长还须达到七天。
- 30 秒写 `out/status.json`：进程运行时间、RSS、历史峰值、各所请求/限频/错误/暂停、API 失败、机会组/子仓位、今日事件/300 档开仓数、守护原因。按时间窗口持久化运行证据，旧运行时长和当前健康快照不能使最近七天自动通过。
- `report --days 7` 逐项输出 PASS / FAIL / UNKNOWN；缺资源基线证据为 UNKNOWN，退出码 1；全部通过退出码 0。
- systemd 模板不会自行安装。准备代码时保留 `tools/fill_sim` 布局，部署根目录为 `/home/ubuntu/wubin/fill-sim`，预先建立可写 `var/`、`out/`，其余只读。资源超限/守护异常退出码 78，`RestartPreventExitStatus=78` 阻止自动重启绕过守护。

## guard.json 字段契约（由 PM 生成）

PM 的服务器监控通过 `FILL_SIM_GUARD_FILE` / `--guard-file` 指定绝对路径。fill-sim 只读该文件，本包不生成生产监控数据。文件必须是 UTF-8 **无 BOM** JSON 对象；建议每 30 秒或更快在同目录写临时文件，再原子 rename/replace，避免读到半份 JSON。额外字段忽略。

| 字段 | JSON 类型 / 单位 | 必填与缺失行为 | 有效值及用途 |
| --- | --- | --- | --- |
| `timestamp` | 有限 number，UTC Unix **秒**（可含小数，不是毫秒或字符串） | 必填；缺失/类型错误 → `guard_metrics_unavailable` | 检查时 `-5 <= now-timestamp <= 90`；超过 **90 秒**或超前超过 **5 秒** → `guard_metrics_stale`。边界 90/-5 秒有效。NaN/Infinity/bool 不接受。 |
| `taoli_stalls_last_hour` | integer，最近滚动 3600 秒的 taoli1 卡顿次数 | 必填；缺失/负数/小数/bool/字符串 → `guard_metrics_unavailable` | 0–4 可继续；>4 → `taoli_stalls` 停止。卡顿定义与基线采集方法由 PM 沿用生产监控。 |
| `taoli_stalls_not_worse` | boolean | 资源验收需要；缺失允许运行，但对应资源证据 UNKNOWN；存在但不是 bool → `guard_metrics_unavailable` | true 表示不劣于 PM 选定的对照基线；false 作为资源验收不通过证据，不单独触发立即停止。 |
| `no_429_increase` | boolean | 同上 | true 表示共享出口/taoli1 的限频情况较基线未增加；false 使资源验收不通过，不单独触发立即停止。本服务自身限频计数还会独立参与验收。 |

结构示例（**不是实测证据**，必须替换时间戳和测量值）：

```json
{"timestamp":1794206000,"taoli_stalls_last_hour":2,"taoli_stalls_not_worse":true,"no_429_increase":true}
```

启动时在任何网络请求之前检查，之后主循环每 1 秒检查一次，状态仍每 30 秒采样。路径未配置 → `guard_file_required`；文件不存在/无权限/非对象/格式错误/缺必填字段 → `guard_metrics_unavailable`。以上停止条件写入 status.stop_reason，退出码 **78**，systemd 的 `RestartPreventExitStatus=78` 阻止自动重启。同步统计/轮转耗时可能延后检测；检测后跳过完整日报与在途结果入账，主线程不等待在途请求，daemon 工作线程不参与解释器退出 join，测试要求检测后 <=10 秒写完状态并退出。停止状态保留最近成功统计，`statistics_timestamp` 标记采样时间，启动即停止时为 null。

Linux `MemAvailable <200 MiB` 和本服务内存 `>=120 MiB` 由 fill-sim 自己读取，分别记 `low_memory`、`memory_limit`，无需 PM 在 JSON 中填写。服务内存的 cgroup 读取和 RSS 回退见末节。`taoli_stalls_not_worse` / `no_429_increase` 的基线、统计区间须由 PM 真实维护，不能固定填 true。未接入生产的守护与七天资源验收仍未完成。

## 代码入口

| 文件 | 职责 |
| --- | --- |
| `core.py` | 身份/规格/盘口模型、Decimal VWAP、取整、退出及资金费计算 |
| `exchanges.py` | 六家 exact symbol 公开规格/深度请求与解析 |
| `funding.py` | 六家公开资金费端点、周期与时间单位解析 |
| `default-fees.json` | 官方核对后的默认费用，未确认项为 null |
| `network.py` | 同进程 GET、逐 recv 截止检查、唯一发送限流器、API 退避 |
| `scheduler.py` | daemon 线程池、公开请求优先队列、开仓保底份额 |
| `engine.py` | 机会组上限、三变体、持仓优先调度及恢复 |
| `storage.py` | SQLite、事务、日归档、进程锁、运行证据 |
| `reporting.py` | 聚合、bootstrap、通过线 |
| `__main__.py` | run/report/smoke CLI、配置、守护及状态/日报 |
| `smoke.py` | 手动联网录制夹具及四档演示 |
| `tests/` | 离线测试、十二份公开录制夹具、验收原始输出 |
| `deploy/` | 待审阅的 systemd 与 env 样例 |

## 审查修复后的资源与统计口径

- 同进程 `http.client`，HTTPS 默认验证证书，不跟随重定向；已删除 `http_worker.py`，不再每请求创建进程。单调时钟的 8 秒总截止覆盖 DNS、TCP、多地址重试、TLS、发送及读取；socket 操作最多等待 3 秒，并按剩余预算缩短。慢头、chunked 分隔符和慢体均逐 recv 检查；finally 关闭响应和连接，超时计入交易所 `timeouts`/`errors`（API 计失败退避）。DNS 隔离及看门狗见下节。
- 无调度预留。工作线程从同一优先队列选任务时原子执行唯一限流器的准入；无额度时不占用线程等待。为覆盖准入后 DNS/线程调度造成的实际发送延迟，该层额度在传输中占用、完成后保留一秒，比仅按开始时间计窗更保守，仍满足滚动秒窗同所最多 2、全局最多 8。`reserved` 仅是旧手动调用的兼容参数，不能绕过准入；smoke 也经过此 gate。
- 同优先级按入队顺序服务。已有三个非开仓发送且仍有开仓等待时，下个名额必须服务开仓；若该交易所暂时无额度，保留此次机会，不让其他持仓请求抢走。第二个资金费 GET 也入队；预热规格成为开仓依赖时提升优先级。过期/失去依赖的排队任务取消，规格任务集限制在 256 个市场。
- 持仓按腿记录本地队列等待，从真实失败计时中扣除；单腿成功立即更新健康状态，不等另一腿。等待导致超过 60 秒未更新时持久化 `tracking_status=rate_limited_delay` 和等待秒数，只豁免 stale_exit；恢复健康后标为 `healthy`。真实网络/解析错误或远端暂停连续 60 秒仍触发 stale_exit。48h 上限每次 tick 独立检查，不受本地排队、在途请求或下次跟踪时间影响。已持久化本地等待标记在重启恢复时保留。
- 规格有效期及闲置 TTL 均为 6 小时；缓存和待取队列各最多 256 个市场，优先保留活动依赖。失败至少间隔 60 秒重试，最后一个持仓/待处理事件依赖结束后清除失败任务及陈旧缓存，避免重新生成重试。
- `filter_passed` 独立表示业务资格。合格但 stale/future、重复、容量、费率、规格或预算不足等执行跳过继续进覆盖率分母。
- 无法定价退出使用 `exit_pricing=unpriced`，保留真实 `funding_known`；可定价退出为 priced，收益统计独立要求 priced。
- 日报/report 流式读取主库和归档，窄字段落到临时 SQLite；去重、分组、精确分位数在 SQL 中完成。Python 保留输出分组及每日 bootstrap 合计，不保留事件/仓位列表。临时库使用 1 MiB 页缓存并自动清理；分组输出大小取决于不同币种/交易对数。status 使用独立标量 SQL。
- `runtime_rate_limits` 持久化累计计数基线，`rate_limit_samples(ts,delta)` 保存采样区间增量，同事务写入窗口证据。重启沿用基线，旧库从 limiter 快照初始化。新增限频才让本采样失败；窗口按采样结束时间计算，缺外部比较仍为 UNKNOWN；采样与 runtime 同保留 30 天。
- 报告 RSS 测试衡量报告进程增量。公开请求使用 4 个 daemon 线程，Shadow API 使用 1 个 daemon 线程，DNS 缓存未命中时创建独立 daemon 解析线程，无每请求子进程。目标 systemd 的 120 MiB cgroup 总预算仍需在目标机验证。

## DNS、worker 看门狗与 48h 上限（H1/H2）

- `network.Resolver` 为公开请求和 Shadow API 共享。DNS 最多等 2 秒，剩余总预算不足时按剩余时间等待；超时的请求立即失败，`PublicClient` 的 finally 释放在途发送计数，既有保守滚动窗口仍保留。系统 `getaddrinfo` 无法强制取消，遗留解析线程只能返回地址，不能连接或发 HTTP，也不能把超时后的结果写入缓存。
- 按主机名缓存成功解析结果 300 秒（不按端口重复解析）；失败不缓存，过期按需清理。TCP 直接使用解析得到的 IPv4/IPv6 sockaddr，不再做第二次 DNS；`HTTPConnection.host` 保持原主机名，因此 Host、TLS SNI 和证书校验均针对原主机名，仍使用默认可信证书及 hostname 校验。
- status 的 `dns.abandoned_active` 是尚未返回的已放弃解析数，`abandoned_total` 是当前进程累计放弃数，`cached_hosts` 是缓存条目数。并存放弃数达到 8 时锁存 `dns_abandoned_limit`，拒绝继续解析；即使线程随后返回，停止原因也不撤销。主循环检测后按既有受控停止路径写 status、提交/关闭 SQLite，不等待 daemon，以 **78** 退出。
- 每个主循环检查公开池和 API 池实际执行中的 worker。`workers.public` / `workers.api` 包含 `busy`、`capacity`、`all_busy_seconds`；只有全池连续占用 **>30 秒** 才触发 `public_workers_stalled` / `api_workers_stalled`，任一 worker 完成就重置连续计时。使用单调时钟，排队长度不冒充占用时间。同步日报/轮转仍可能延后主循环检测；没有改变既有检测后 <=10 秒退出的验收边界。
- 48h 到期先使用 `last_books` 的最后可用两腿盘口、按原数量吃单；深度不足则尝试 `last_executable_exit`。定价成功为 `timeout_48h`，记录盘口原始时间；旧可执行快照回退额外标 `exit_price_fallback=true`，沿用保守的 `main_eligible=false`。均不可定价则为 `unpriced_exit`，`forced_exit_reason=timeout_48h`，收益为 null，不进主收益。清除无存续持仓的跟踪任务并取消未发送腿；已在途的结果不能回写已关闭持仓。

## 服务内存口径（G3）

每秒守护检查和每 30 秒 status 使用同一个 `service_memory()`：读取 `/proc/self/cgroup` 的 `0::` 路径，在 `/sys/fs/cgroup/<本服务路径>/memory.current` 读取 cgroup v2 字节数并除以 1024²。不会优先读取宿主机根 cgroup 代替本服务。cgroup namespace 中路径为 `/` 时读取该 namespace 根的 `memory.current`。

- `memory_mb`：本次采样，单位 MiB；`memory_source=cgroup_memory.current`；`memory_path`：实际文件路径。
- cgroup 文件缺失、不可读、非法或不是 v2 时回退本进程 RSS，标明 `memory_source=process_rss_fallback`、`memory_path=null`；RSS 也不可读时数值为 null。Windows 实测属于回退情况。
- 为兼容既有报告，`rss_mb` 是 `memory_mb` 的同值别名，`max_rss_mb` 和 runtime 中的同名指标沿用该采样口径；名字不再表示只测主进程。历史峰值可能混有升级前的 RSS，不把旧证据自动认定为 cgroup 验收。
- 守护以本次采样 `>=120 MiB` 触发 `memory_limit`，同时写明来源。cgroup 包含服务进程/线程及计费的页缓存等；RSS 回退不能证明整个服务满足 systemd 总预算。

本地回归覆盖真实 PublicClient、线程池、虚拟时钟、loopback 慢速逐字节返回和实际进程退出。目标 Ubuntu Python 3.10 / cgroup / systemd 行为与七天观察尚须单独验收。
