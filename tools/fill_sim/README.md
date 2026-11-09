# fill-sim

独立、纯标准库的公开盘口模拟服务，运行目标 Python 3.10 / SQLite 3.37。代码不导入 backend，不下单，不使用交易所认证接口。业务规则见 `docs/fill-sim-spec-2026-11-09.md` 及其引用设计稿；触发器以任务书的 API 轮询约定为准。

## 本地验证

在仓库根目录执行（单元测试强制阻断 socket connect）：

```powershell
python -m unittest discover -s tools/fill_sim/tests -v
python -m compileall -q tools/fill_sim
```

显式联网冒烟，仅公开 GET，无 taoli1 token：

```powershell
python -m tools.fill_sim.smoke --include-spot --fixtures tools/fill_sim/tests/fixtures
```

冒烟顺序获取六家 BTC 合约/现货规格和 50 档深度，打印转换结果与四档模拟 fills；跨所 BTC 示例是非同步快照的工程演示，不能用于交易判断。费用来自 `deploy/fill-sim.env.example`，仅为设计稿假设。

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
- 令牌桶及滚动秒窗共同控制每所 ≤2/s、全局 ≤8/s（可调低），规格与盘口共用预算；最多 4 个公开 HTTP 请求在途。持仓优先，未获预算的新开/延迟阶段记 `skipped_rate_limit`。429/418/限频码暂停至少 60 秒并指数退避至 3600 秒；尊重更长的数字 Retry-After，Bybit 403 保守暂停至少 600 秒。

## 资金费和手续费边界

普通 taoli1 `/api/shadow/trades` payload 未保证提供两腿开仓资金费率、下次结算时间和周期；不能用影子最终 funding_bps 代替独立模拟仓位的持仓资金费。本期按“只请求深度/规格接口”约束，没有增加资金费或 ticker 列表请求。

纯计算层支持可审计的开仓快照字段 `fill_sim_funding`（两腿列表），每腿为 `{"rate": 0.0001, "next": 1794206000, "interval": 28800}`。rate 单位为比率，next 为 UTC 秒，interval 为秒。现货自动为零；合约在每个跨过的结算时点按开仓快照估算，多头支付、空头收取。该可选字段当前上游没有接通，属于明确的集成缺口。

缺快照的仓位仍可模拟、记录价格损益，但 `funding_known=false`，不计入主收益或盈利通过线。日报提供 `provisional_price_only` 用于观察这类不完整结果，不能作为完整净收益。默认全是合约腿时，此缺口会使主收益样本不足；上线观察前须另行授权并实现公开资金费数据来源或扩展上游契约，不能声称已具备完整盈利验收条件。

`FILL_SIM_FEES_JSON` 按 exchange × future/spot 配置小数费率，无生产代码默认费率。样例合约费仅来自设计稿，尚未逐所核验最新 VIP0/地区/活动条件；现货费默认 null，涉及现货时记录 `fee_unconfigured`。现货卖腿仅模拟已有币库存卖出，不证明可借币做空；借贷利息、库存/保证金/账户权限和实际撮合不在本模型内。

## 持久化、报表、资源

- 单写进程锁、WAL、FULL 同步，事件/成交/持仓更新在事务内完成。重启恢复持仓和 seen ID；未完成的延迟阶段记 `interrupted_restart`，不使用重启后的盘口假冒原延迟。
- UTC 按天将已完成的整个事件族移入 `fill_sim-YYYY-MM-DD.db`。活动族留在主库，结束后下次日切归档。归档迁移暂切 DELETE journal，使用 SQLite 多库原子事务复制/删除，随后恢复 WAL；seen tombstone 保留 30 天。归档和日报保留 30 天；不会操作 radar.db。
- 日报每 5 分钟重算今日和昨日；触发/还在率按发现日，收益按平仓日。报表读取数据库日期窗口，聚合前丢弃盘口大字段。记录事件跳过原因、实测 300 美元还在率、各档均值/胜率/USD、容量曲线、各变体、影子偏差分位数、币/所对/价差档和 top3/其余。
- 90% 下界为单侧按“完整平仓日”重抽样 2000 次、每次样本按交易数加权的均值第 10 百分位；不是逐笔 bootstrap。少于两天返回未知；总观察时长还须达到七天。
- 30 秒写 `out/status.json`：进程运行时间、RSS、历史峰值、各所请求/限频/错误/暂停、API 失败、机会组/子仓位、今日事件/300 档开仓数、守护原因。按时间窗口持久化运行证据，旧运行时长和当前健康快照不能使最近七天自动通过。
- `report --days 7` 逐项输出 PASS / FAIL / UNKNOWN；缺资源基线证据为 UNKNOWN，退出码 1；全部通过退出码 0。
- systemd 模板不会自行安装。准备代码时保留 `tools/fill_sim` 布局，部署根目录为 `/home/ubuntu/wubin/fill-sim`，预先建立可写 `var/`、`out/`，其余只读。资源超限/守护异常退出码 78，`RestartPreventExitStatus=78` 阻止自动重启绕过守护。

现有生产守护需原子写本地 JSON 文件（示例是结构说明，**不是实测证据**）：

```json
{"timestamp":1794206000,"taoli_stalls_last_hour":2,"taoli_stalls_not_worse":true,"no_429_increase":true}
```

每 30 秒检查 Linux MemAvailable <200 MiB、RSS ≥120 MiB、taoli1 卡顿 >4/h，任一满足则停服。守护文件缺失/超过 90 秒/格式错误也停服。布尔字段用于观察期资源通过线，需由生产监控提供对比基线，本任务未修改既有守护脚本、未连接生产机。

## 代码入口

| 文件 | 职责 |
| --- | --- |
| `core.py` | 身份/规格/盘口模型、Decimal VWAP、取整、退出及资金费计算 |
| `exchanges.py` | 六家 exact symbol 公开规格/深度请求与解析 |
| `network.py` | GET、限频预算、暂停、API 轮询退避 |
| `engine.py` | 机会组上限、三变体、持仓优先调度及恢复 |
| `storage.py` | SQLite、事务、日归档、进程锁、运行证据 |
| `reporting.py` | 聚合、bootstrap、通过线 |
| `__main__.py` | run/report/smoke CLI、配置、守护及状态/日报 |
| `smoke.py` | 手动联网录制夹具及四档演示 |
| `tests/` | 离线测试、十二份公开录制夹具、验收原始输出 |
| `deploy/` | 待审阅的 systemd 与 env 样例 |
