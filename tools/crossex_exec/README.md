# crossex-exec

独立的 Python 3.10+ 标准库服务，CrossEx 下单覆盖 Gate / Binance / Bybit / OKX / Aster，Bitget 使用 Classic v2 mix。默认影子模式。此交付仅本地验证，没有部署、账户实测或实盘授权。

## 入口

在仓库根目录运行：

```text
python -m unittest discover -s tools/crossex_exec/tests -v
python -m unittest discover -s tools/fill_sim/tests -v
python -m tools.crossex_exec report --db /var/lib/crossex-exec/crossex-exec.db
```

未来经独立验收后，Linux 服务入口是：

```text
python3 -m tools.crossex_exec run --env-file /etc/crossex-exec/credentials.env
```

`run` 会读账户鉴权接口，因此不属于本次会话的允许运行范围。没有真实费率或账户核对资料时拒绝开仓；不会采用公开默认手续费替代真实手续费。`report` 只读业务数据，不访问网络，但 SQLite 打开时会检查已有表结构。

## 模块

| 文件 | 责任 |
|---|---|
| `market.py` | 公开目录、精确映射、50 档盘口、成交额、当前/24h 历史资金费 |
| `policy.py` | Decimal 步长公倍数、100 USDT 复核、15bp 门槛、退出与熔断规则 |
| `adapters.py` / `transport.py` | 两家签名、IOC、查单、撤单、分页成交、账户只读接口、限频与总超时 |
| `execution.py` | 意图落库、发送、ACK、终态、未知结果、恢复；两腿并发 |
| `accounts.py` | 持仓模式、3 倍杠杆、真实费率、保证金率及持仓归一化 |
| `engine.py` | 触发、持仓管理、最多三次补平、60 秒对账、风险停开/全平 |
| `storage.py` / `security.py` | SQLite WAL/FULL、事务、文件权限与脱敏 |
| `reporting.py` / `__main__.py` | 模拟与真实成交比较、CLI、300 秒 JSON 状态、即时 JSON 告警 |

复用 `fill_sim` 的 `Market/Spec/Book`、`opening/closing/walk/common_step/track_interval`、原始盘口/规格/资金费解析、`ShadowAPI`、`PublicClient/Limiter/RequestQueue/DaemonPool` 及有总时限的 DNS/HTTP 基础设施。唯一的 fill-sim 修改是允许校验 Gate 的列表响应（目录、ticker、资金费历史）。

## 配置

只读取显式传入的 env 文件，不继承进程环境中的密钥。文件必须为服务用户所有、普通文件、POSIX 权限 **0600**；Linux 禁止符号链接。Windows 不能提供这组 POSIX 保证，因此只用于离线测试，不运行服务。

| 变量 | 默认值/要求 |
|---|---|
| `CROSSEX_LIVE_ENABLED` | `0`；只有用户本人可在后续验收后设为 `1` |
| `CROSSEX_DB` | CLI：`var/crossex-exec.db`；模板：`/var/lib/crossex-exec/crossex-exec.db` |
| `CROSSEX_STOP_FILE` | CLI：`var/STOP`；模板：`/var/lib/crossex-exec/STOP`；**存在即停开** |
| `CROSSEX_POSITION_MODE` | `SINGLE`，另支持 `DUAL`，必须与账户一致 |
| `BITGET_POSITION_MODE` | `one_way_mode`，另支持 `hedge_mode`，必须与账户一致 |
| `BITGET_RISK_RATE_CONFIRMED` | `0`；核实 Classic `crossedRiskRate` 为维持保证金/权益口径后方可设 `1` |
| `CROSSEX_BASES` | 空表示所有可精确映射的基础币；可用逗号分隔的精确基础币收窄范围 |
| `TAOLI_BASE_URL` | `http://127.0.0.1:8000`，按实际服务端口配置 |
| `TAOLI_API_TOKEN` | 空；如 taoli1 需要认证则由用户在私有文件中配置 |
| `GATE_API_KEY` / `GATE_API_SECRET` | 空；仅 CrossEx 所需权限 |
| `BITGET_API_KEY` / `BITGET_API_SECRET` / `BITGET_API_PASSPHRASE` | 空；仅合约所需权限 |

应用不修改账户模式、杠杆、资产模式或权限。CrossEx 必须为 `CROSS_EXCHANGE`，各目标合约杠杆必须已经是 3；Bitget 必须为 Classic v2、crossed、配置的持仓模式和 3 倍杠杆。

## 安全语义与运营

- CrossEx v1.0.8 数量统一为基础币。废弃的 `contract_size` 不参与 CrossEx 下单量计算；Gate/OKX 原生盘口张数由 fill-sim 换算为基础币。
- 开仓手续费扣除完整往返两腿费用；资金费按每腿一个已知周期计算差额，不以预期收款改善不合格机会。保存周期和预估标记，不统一假设 8 小时。
- 不足 24h 资金费历史覆盖、历史有缺口、资料过期或倍率/代码不一致都不交易。OKX 成交额使用基础币成交量 × 24h 最低价的保守下界。
- 意图及两腿 client ID 先持久化，限频与建连后、实际请求前写 `SENT`。超时/5xx/重复 ID 先查原单；**不会自动重发未知结果**。CrossEx text 查不到已终结订单不是未下单的证据。
- 影子模式从协调器和传输层双重禁止下单/撤单。影子意图不伪造成已成交持仓，不产生实盘收益；报告分开展示。
- 订单确认默认每 200ms 查询，最多 10s（限频可延迟查询）；超时保留 `UNKNOWN` 和敞口。成交记录延迟则保留 `accounting_pending`，60s 再补拉，不用 ACK 价格代替成交价。
- 单腿补平最多三次，额外容差依次为 13/23/33bp，进度持久化。价格量化到真实 tick。正常平仓为并发 reduce-only IOC，部分平仓只继续减仓。
- 盘口丢失时采用**该腿自己的最后盘口**，不把另一所价格冒充该腿可成交价；扩大限价仍不保证能成交。剩余仓位一直保留并告警。
- 风险计算同时使用逐笔真实费用/已实现损益/浮动损益和账户权益变化的较差结果。需要专用账户；账户间转账/提现会改变权益基线，不能在运行时随意操作。
- `HALTING` 表示累计亏损触发、仍在处理剩余或未知敞口；确认本地和远端均为空后才写 `HALTED` 并退出。`HALTED` 阻止自动重启。不得删除整个数据库“复位”。
- `stop_reason` 为持久化停开原因。未知单、对账差异、补平失败需人工只读核查后清理；文件 STOP 和日亏停开不阻止减仓。服务不会自动解除这些异常锁存。
- 资金费跨期、非 USDT 手续费或优惠券扣费不会被猜成零成本。跨期成交报告保留 `trading_net_usd`，完整 `net_usd=null`，停开等待账单核对。
- 告警写本地事件表，并由 CLI 输出即时 JSON 到 journal；没有发送飞书、邮件等外部通知。状态 JSON 每 300 秒输出。
- `flock` 防止两个服务实例共享同一数据库；停机尝试减仓，无法清完则以失败退出供恢复处理。进程崩溃、OOM 或交易所不可用时仍可能留下真实敞口。

## 部署模板（本次未安装）

`deploy/crossex-exec.service` 使用专用用户 `crossex-exec`、代码目录 `/opt/taoli1`、私有配置 `/etc/crossex-exec/credentials.env`、状态目录 `/var/lib/crossex-exec`，设置 `MemoryMax=80M`、`Restart=on-failure`、`UMask=0077`。配置目录和文件应仅由服务用户读取，文件模式必须 0600。没有 systemd `EnvironmentFile`，由应用自己打开文件验证权限。

实际安装、Linux 80M 内存验收、账户核对、24h 影子验收及开实盘开关均留待新任务。详细证据、接口 URL 和未确认事项见 [交接文档](../../docs/task-handoff-2026-11-11-crossex-exec.md)。
