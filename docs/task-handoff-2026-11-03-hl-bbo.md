# Hyperliquid 永续 WS BBO 本地交接

## 目标、范围与基线

- 工作目录：`taoli1/.worktrees/hl-bbo`；分支：`codex/hl-bbo`。
- 本轮修复基线：`1932ce11833944b2837597685dbbe2c3643b5bdf`；上一轮实现基线：`22cae34e7240dec10ac625ae65c94273075b3437`。
- 按 `docs/hl-bbo-spec-2026-11-03.md` 为永续采集接入单条常驻 WebSocket BBO；不增加 REST 请求。
- 本次用户明确要求离线：不联网、不安装依赖、不使用 WSL、不推送。因此不执行线上备份、部署或验收。
- 不改价差引擎、告警规则/引擎、前端、数据库结构及其他交易所适配器。

## 实现与代码入口

1. `backend/app/exchanges/hyperliquid_bbo.py`：`HyperliquidBboStream`。
   - 一个 `wss://api.hyperliquid.xyz/ws` 连接，复用 Lighter 的 `websockets.asyncio.client.connect` 导入方式。
   - `set_coins` 接收各 DEX 最近成功采集的原始 coin 合集，去重后优先保留 `_subscribed` 中仍被需要的 coin，其余按原始 coin 名排序，再截取上限；输入顺序变化不会引发退订/订阅。新增订阅、移除退订，移除时删除该 coin 的缓存；不接受现货 `@...` 或含 `/` 的 coin。
   - `handle_message` 解析并存储 `(bid, bid_sz, ask, ask_sz, exchange_time_ms, local_monotonic)`；保持原始 coin 大小写及 DEX 前缀。毫秒时间必须是整数，且在 `2000-01-01..2100-01-01`（含边界，`946684800000..4102444800000`）。缺侧、交叉、无效价格/数量/时间戳均不能产出可成交盘口；已知 coin 的坏更新立即使旧盘口失效。无锁，无逐消息日志。
   - 所有订阅/退订发送间隔至少 0.05 秒，使用单调时钟循环检查，防止 Windows 定时器提前唤醒；无发送 50 秒发应用层 `{"method":"ping"}`，给 60 秒上限留余量。
   - 保留并纳入 Claude 原有未提交改动：HL 仅在 BBO 变化时推送，因此新鲜度同时要求 `_connected`、最后一条有效 BBO 接收年龄 <= `max_age_seconds`（默认 10 秒），以及该 coin 报价接收年龄 <= `quote_max_age_seconds=120`。其他 coin 的消息只证明连接活跃，不会重置该报价年龄；pong 不作为 BBO 活跃证据。
   - 断线按 1/2/5/10/30 秒退避，30 秒封顶；稳定连接至少 60 秒后重置退避；重连按最新集合全量订阅。断线立即让缓存不可执行；每次成功建立连接时清空旧报价，并把 `_last_message_at` 重置为负无穷，直到新连接收到 BBO。
   - 每 300 秒一行 JSON：`hl_bbo=1, connected, subscribed, fresh_count, msgs_last_5m, reconnects`。`subscribed` 是本连接成功发送的订阅数，不代表服务器 ACK；`fresh_count` 是有效且新鲜的盘口数；消息数是已解析的所选 coin BBO，使用最多 301 个秒桶统计最近五分钟，约一秒边界精度。
2. `backend/app/exchanges/hyperliquid.py`：注入可选 `bbo_stream`，`fetch_future_tickers` 通过 `_bbo_coins_by_dex` 保存各 DEX 最近成功的 coin 集合（main 单列），合并后更新订阅，在 `_attach_priority_order_books` 读取内存。
   - 仅更新请求成功的 DEX 部分；请求或 DEX 发现失败时保留其上次集合。成功返回空集合会移除该 DEX 的旧 coin；失败 DEX 的旧市场行不会重发到本轮结果。
   - 满足上述连接及报价年龄要求、双侧价格 `0 < bid < ask`、数量有效且为正，才覆盖 bid/ask/size。
   - 设置 `quote_kind="book"`, `is_estimated=False`, `estimated_fields=[]`，来源精确为 `Hyperliquid metaAndAssetCtxs + WS bbo`。
   - 两个盘口时间戳均来自 BBO 毫秒交易所时间；`quote_time_source="upstream"` 与现有 `order_book_snapshot` 解析 l2Book 时间的取值一致。
   - BBO 时间转换按行捕获 `OSError/OverflowError/ValueError`（也拒绝缺失转换结果）；该行原样保留 reference，继续后续行，不使整个采集批次失败，也不增加 REST 请求。
   - 缺失/过期/无效 BBO 保留 reference；`io:ANTH` 有有效 BBO 跳过原 l2Book，其他情况继续原 l2Book 回退及错误隔离。
   - 资金费、成交额、mark/index、合约倍率、quote currency、原始市场及 DEX 字段不变。
3. `backend/app/main.py`：lifespan 中通过 `_start_background_task` 注册 `hyperliquid-bbo`，绑定 collector 使用的 Hyperliquid 适配器。
   - 仅 collector、后台 workers 和 BBO 配置均启用时启动。
   - 构造函数和 `set_coins` 不连接网络；没有 coin 时等待。离线/API-only 模式不创建该任务。
   - 停机沿用 `stop_event` 和 supervisor cancellation；连接任务、接收/发送、统计任务全部取消并等待，WebSocket 上下文退出关闭连接。
4. `backend/app/core/config.py`：沿用 dataclass `Settings` 和 `get_settings` 环境读取。
   - `HL_BBO_ENABLED=true`；`0`/`false` 关闭，不绑定 stream，完全沿用既有 reference/l2Book 行为。
   - `HL_BBO_MAX_AGE_SECONDS=10`，要求有限正数；本轮起表示连接最近一条有效 BBO 的接收年龄上限，单币另有固定 120 秒上限。
   - `HL_BBO_MAX_COINS=450`，要求正整数。

## 消息协议依据与验证边界

- 本次未联网读取官方文档，也未捕获实盘消息；仓库中没有找到既有 HL BBO 原始消息样本。
- BBO 订阅请求和响应格式直接依据用户 spec：订阅 `{"method":"subscribe","subscription":{"type":"bbo","coin":"xyz:TSLA"}}`；响应 `channel="bbo"`，`data.coin`、`data.time`、`data.bbo=[bid_level|null, ask_level|null]`，level 的 `px`、`sz` 为字符串。新增测试是按该合同构造的 mock，不是实盘证据。BBO 仅在变化时推送的结论来自用户提供的 Claude 实测，本轮没有联网复测。
- HIP-3 coin 写法由现有 `_parse_perp_rows` 保留 metadata `name`、`_l2_book_coin` 原样传递 raw_symbol，以及 `test_exchange_resilience.py` 中 `xyz:TSLA`/`cash:TSLA`/`io:ANTH` 测试推断。WS 也直接使用该 raw_symbol，绝不根据规范化 ticker 猜测 DEX。
- `quote_time_source` 的依据是 `backend/app/exchanges/base.py::order_book_snapshot`：存在交易所时间时取 `upstream`。

## 测试与检查

- `backend/.venv` 不存在；使用系统 `C:\Users\wubin\AppData\Local\Programs\Python\Python312\python.exe`，Python 3.12.10、pytest 8.4.2、websockets 16.0。
- 仓库 `backend/requirements.lock` 为 websockets 17.3；没有安装/创建锁定环境，锁定版兼容性需要后续用既有 venv-lock 复跑。
- 所有 pytest 命令工作目录为 `backend`；现有 autouse fixture 阻止外部网络，默认排除 network/benchmark 标记。
- 新增 `backend/tests/test_hyperliquid_bbo.py` 覆盖解析、空侧/交叉/坏 JSON、时间戳和非有限数、原始 coin 隔离、字段覆盖/回退、无额外 REST、集合更新/上限/删除、限速/提前唤醒、心跳/统计、退避/全量重订阅及取消、配置与 lifespan 门控。
- 本轮测试显式设置连接状态，BBO 消息由 `handle_message` 更新 `_last_message_at`。新增覆盖：冷门币 60 秒但其他 coin 活跃仍为 book；连接超过 10 秒无 BBO 时全部 reference；单币恰好 120 秒仍 book、超过则 reference；断线 reference；重连清缓存且收到其他 coin 消息后旧报价也不能复活；DEX 请求/发现失败保留订阅、成功刷新/空集合更新；三种时间异常对普通及优先市场逐行隔离；时间范围边界；超额订阅稳定排序和已订阅优先。
- 上一轮已修复 Windows 单次 sleep 可能早醒导致限速间隔不足的问题，本轮保留截止时间循环检查及其回归测试。其他 HL 测试文件未修改或放宽。
- 最终 pytest 命令与原样汇总（退出码均为 0）：

```text
python -m pytest tests -q -k "hyperliquid or bbo"
158 passed, 2275 deselected in 17.22s

python -m pytest tests -q
2427 passed, 6 deselected, 12 warnings in 144.14s (0:02:24)
```

- 全量 12 条 warning 均来自既有 API/指数成分测试中的 `datetime.utcnow()` 弃用警告。两组最终测试均在代码及测试修改完成后运行；随后仅更新本交接文档。
- 本轮先运行原 BBO 测试，得到 `15 failed, 38 passed in 6.92s`，失败源于旧测试未设置连接状态及仍期待断线缓存可执行；适配并新增测试后，在修实现前复现了时间范围、逐行时间异常、订阅上限排序、DEX 失败丢订阅及重连消息时钟未清空的问题，最终均通过。
- `python -m ruff check backend/app/exchanges/hyperliquid.py backend/app/exchanges/hyperliquid_bbo.py backend/tests/test_hyperliquid_bbo.py` 与 `git diff --check` 通过。本轮未改 `main.py`、配置或相邻告警模块。

## 交付、线上与下一步

- 本轮本地提交消息：`fix(backend): hl bbo freshness by connection liveness, per-dex coin sets, row isolation`；包含用户要求保留的 Claude 新鲜度改动。
- 不推送；线上版本、数据库、服务和网络路由均未操作，无本次线上备份或验收结果。
- 初始唯一未跟踪文件为用户提供的 `docs/hl-bbo-spec-2026-11-03.md`，保持内容及未跟踪状态；本次只提交实现、测试及本交接。
- 下一任务可进行锁定环境复跑与获授权后的协议/生产验收：确认 main/HIP-3 BBO 真实响应、订阅成功数、断线/新鲜度、CPU/内存与 REST 请求量，再按生产交付流程处理备份和部署。该阶段不属于本次离线验收。
