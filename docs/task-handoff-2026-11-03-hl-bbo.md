# Hyperliquid 永续 WS BBO 本地交接

## 目标、范围与基线

- 工作目录：`taoli1/.worktrees/hl-bbo`；分支：`codex/hl-bbo`。
- 基线：`22cae34e7240dec10ac625ae65c94273075b3437`。
- 按 `docs/hl-bbo-spec-2026-11-03.md` 为永续采集接入单条常驻 WebSocket BBO；不增加 REST 请求。
- 本次用户明确要求离线：不联网、不安装依赖、不使用 WSL、不推送。因此不执行线上备份、部署或验收。
- 不改价差引擎、告警规则/引擎、前端、数据库结构及其他交易所适配器。

## 实现与代码入口

1. `backend/app/exchanges/hyperliquid_bbo.py`：`HyperliquidBboStream`。
   - 一个 `wss://api.hyperliquid.xyz/ws` 连接，复用 Lighter 的 `websockets.asyncio.client.connect` 导入方式。
   - `set_coins` 接收最近一次永续采集解析出的所有 DEX 原始 coin，按传入顺序去重、截取上限；发送顺序确定化。新增订阅、移除退订，移除时删除该 coin 的缓存；不接受现货 `@...` 或含 `/` 的 coin。
   - `handle_message` 解析并存储 `(bid, bid_sz, ask, ask_sz, exchange_time_ms, local_monotonic)`；保持原始 coin 大小写及 DEX 前缀。缺侧、交叉、无效价格/数量/时间戳均不能产出可成交盘口；已知 coin 的坏更新立即使旧盘口失效。无锁，无逐消息日志。
   - 所有订阅/退订发送间隔至少 0.05 秒，使用单调时钟循环检查，防止 Windows 定时器提前唤醒；无发送 50 秒发应用层 `{"method":"ping"}`，给 60 秒上限留余量。
   - 断线按 1/2/5/10/30 秒退避，30 秒封顶；稳定连接至少 60 秒后重置退避；重连按最新集合全量订阅。断线不清空仍在订阅集合中的缓存，其按接收年龄自然过期。
   - 每 300 秒一行 JSON：`hl_bbo=1, connected, subscribed, fresh_count, msgs_last_5m, reconnects`。`subscribed` 是本连接成功发送的订阅数，不代表服务器 ACK；`fresh_count` 是有效且新鲜的盘口数；消息数是已解析的所选 coin BBO，使用最多 301 个秒桶统计最近五分钟，约一秒边界精度。
2. `backend/app/exchanges/hyperliquid.py`：注入可选 `bbo_stream`，在 `fetch_future_tickers` 汇总所有 DEX 后更新订阅集合，在 `_attach_priority_order_books` 读取内存。
   - 本地接收年龄 <= 阈值、双侧价格 `0 < bid < ask`、数量有效且为正，才覆盖 bid/ask/size。
   - 设置 `quote_kind="book"`, `is_estimated=False`, `estimated_fields=[]`，来源精确为 `Hyperliquid metaAndAssetCtxs + WS bbo`。
   - 两个盘口时间戳均来自 BBO 毫秒交易所时间；`quote_time_source="upstream"` 与现有 `order_book_snapshot` 解析 l2Book 时间的取值一致。
   - 缺失/过期/无效 BBO 保留 reference；`io:ANTH` 有有效 BBO 跳过原 l2Book，其他情况继续原 l2Book 回退及错误隔离。
   - 资金费、成交额、mark/index、合约倍率、quote currency、原始市场及 DEX 字段不变。
3. `backend/app/main.py`：lifespan 中通过 `_start_background_task` 注册 `hyperliquid-bbo`，绑定 collector 使用的 Hyperliquid 适配器。
   - 仅 collector、后台 workers 和 BBO 配置均启用时启动。
   - 构造函数和 `set_coins` 不连接网络；没有 coin 时等待。离线/API-only 模式不创建该任务。
   - 停机沿用 `stop_event` 和 supervisor cancellation；连接任务、接收/发送、统计任务全部取消并等待，WebSocket 上下文退出关闭连接。
4. `backend/app/core/config.py`：沿用 dataclass `Settings` 和 `get_settings` 环境读取。
   - `HL_BBO_ENABLED=true`；`0`/`false` 关闭，不绑定 stream，完全沿用既有 reference/l2Book 行为。
   - `HL_BBO_MAX_AGE_SECONDS=10`，要求有限正数。
   - `HL_BBO_MAX_COINS=450`，要求正整数。

## 消息协议依据与验证边界

- 本次未联网读取官方文档，也未捕获实盘消息；仓库中没有找到既有 HL BBO 原始消息样本。
- BBO 订阅请求和响应格式直接依据用户 spec：订阅 `{"method":"subscribe","subscription":{"type":"bbo","coin":"xyz:TSLA"}}`；响应 `channel="bbo"`，`data.coin`、`data.time`、`data.bbo=[bid_level|null, ask_level|null]`，level 的 `px`、`sz` 为字符串。新增测试是按该合同构造的 mock，不是实盘证据。
- HIP-3 coin 写法由现有 `_parse_perp_rows` 保留 metadata `name`、`_l2_book_coin` 原样传递 raw_symbol，以及 `test_exchange_resilience.py` 中 `xyz:TSLA`/`cash:TSLA`/`io:ANTH` 测试推断。WS 也直接使用该 raw_symbol，绝不根据规范化 ticker 猜测 DEX。
- `quote_time_source` 的依据是 `backend/app/exchanges/base.py::order_book_snapshot`：存在交易所时间时取 `upstream`。

## 测试与检查

- `backend/.venv` 不存在；使用系统 `C:\Users\wubin\AppData\Local\Programs\Python\Python312\python.exe`，Python 3.12.10、pytest 8.4.2、websockets 16.0。
- 仓库 `backend/requirements.lock` 为 websockets 17.3；没有安装/创建锁定环境，锁定版兼容性需要后续用既有 venv-lock 复跑。
- 所有 pytest 命令工作目录为 `backend`；现有 autouse fixture 阻止外部网络，默认排除 network/benchmark 标记。
- 新增 `backend/tests/test_hyperliquid_bbo.py` 覆盖解析、空侧/交叉/坏 JSON、时间戳和非有限数、原始 coin 隔离、字段覆盖/回退、无额外 REST、集合更新/上限/删除、限速/提前唤醒、心跳/统计、退避/全量重订阅及取消、配置与 lifespan 门控。
- 初轮测试发现 Windows 单次 sleep 可能早醒导致间隔不足，已改为截止时间循环检查，并增加回归测试。现有 HL 测试未修改或放宽。
- 最终 pytest 命令与原样汇总（退出码均为 0）：

```text
python -m pytest tests -q -k "hyperliquid or bbo"
138 passed, 2275 deselected in 18.76s

python -m pytest tests -q
2407 passed, 6 deselected, 12 warnings in 145.72s (0:02:25)
```

- 全量 12 条 warning 为既有测试中 `datetime.utcnow()` 的弃用警告。全量运行期间的收尾编辑仅为新增文件 lint 修正（导入间距、异常类型及 pairwise），之后已完成上面的最终相关测试复跑。
- 开发过程的先行 pytest 记录，便于复核失败如何关闭：

```text
python -m pytest tests/test_hyperliquid_bbo.py -q
1 error in 1.05s

python -m pytest tests -q -k "hyperliquid or bbo"
1 failed, 122 passed, 2275 deselected in 19.66s

python -m pytest tests -q -k "hyperliquid or bbo"
138 passed, 2275 deselected in 20.64s
```

- 第一条是在仅写测试、实现模块尚未创建时的预期导入失败；第二条为上述 Windows 早醒问题；第三条及最终复跑已修复通过。
- 新模块、适配器、配置及新测试的 Ruff 检查通过。`main.py` 整文件 Ruff 仍有既有导入/循环闭包等问题；已用 HEAD 内容对照，不修改相邻告警模块。

## 交付、线上与下一步

- 本地提交消息：`feat(backend): stream Hyperliquid perp bbo over websocket for executable quotes`。
- 不推送；线上版本、数据库、服务和网络路由均未操作，无本次线上备份或验收结果。
- 初始唯一未跟踪文件为用户提供的 `docs/hl-bbo-spec-2026-11-03.md`，保持内容及未跟踪状态；本次只提交实现、测试及本交接。
- 下一任务可进行锁定环境复跑与获授权后的协议/生产验收：确认 main/HIP-3 BBO 真实响应、订阅成功数、断线/新鲜度、CPU/内存与 REST 请求量，再按生产交付流程处理备份和部署。该阶段不属于本次离线验收。
