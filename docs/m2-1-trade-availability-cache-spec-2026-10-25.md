# M2-1 交易可用性缓存治理：任务书（Claude，2026-10-25，待用户确认）

规划来源：`docs/m2-memory-slimming-plan-2026-10-25.md` §3 M2-1。

## 1 现状（已核实）

`TradeAvailabilityService`（`backend/app/services/trade_availability.py`）：

- `self._cache: dict[str, tuple[datetime, Any]] = {}`（约 413 行），`_get_json(url, cache_key=None, cache_seconds=20)`（约 424–450 行）：
  - 读取时只检查 `now - ts < cache_seconds`；**过期条目从不删除**，整份原始 JSON 一直留在内存里。
  - 不传 `cache_key` 时用完整 URL 作键。共 21 处调用，大部分不传 `cache_key`。按币种查询的接口（例如 `exchangeInfo?symbol=…`、`futures/usdt/contracts/{raw}`、`spot/public/symbols?symbol=…`）**每查一个新币种就多一个永久条目**，条目数没有上限。
  - 共享大接口解析后体积（本机实测）：Binance 合约 `exchangeInfo` 4.5MB、全币种充提网络 `getNetworkCoinAll` 4.6MB、Bitget 现货全量 4.0MB、OKX 现货 3.5MB、Gate 合约全量 3.2MB、Bybit 线性 2.6MB。
- 调用方：`trade-availability-monitor`（每 20 秒一轮，按观察列表逐项查询）和前端的交易可用性接口。
- 另外，线上日志每分钟约 4 次 `RuntimeError: 聚合行情中未找到指定原始市场`（约 1577 行）：监控在 `fetch_status` 的结果里找不到观察项对应的原始市场。

## 2 目标

1. 缓存总量有界：过期条目及时清理，条目数有上限，单条超大 payload 不会长期驻留。
2. **缓存语义不变**：有效期内命中返回同一个对象；过期后重新请求；`cache_seconds` 由调用方决定，含义不变。
3. 查清 `RuntimeError` 的原因并报告（只读，不改业务逻辑）。

## 3 设计

- 条目改为保存过期时间：`self._cache: OrderedDict[str, tuple[datetime, datetime, Any]]`，即 `(stored_at, expires_at, payload)`，其中 `expires_at = stored_at + cache_seconds`。
  - 读取：`now < expires_at` 才算命中，并 `move_to_end(key)`；否则视为未命中（可以顺手删除该条目）。
  - 写入：写入后执行 `_prune(now)`：(a) 删除所有 `expires_at <= now` 的条目；(b) 条目数超过 `max_entries`（默认 128）时，从最久未使用的一端淘汰。
  - 注意：调用方可能用不同的 `cache_seconds` 读取同一个键（例如同一个 URL 先用 20 秒读，又用 60 秒读）。原实现按“读取方传入的 `cache_seconds`”判断是否命中。新实现必须保持这个行为：**命中条件仍然是 `now - stored_at < 读取时传入的 cache_seconds`**；`expires_at` 只用于清理，取写入时的 `cache_seconds` 和一个全局下限（如 120 秒）中较大的那个，避免把别的调用方还在用的条目提前清掉。实现前先确认所有调用点的 `cache_seconds` 取值，写进交接。
- `max_entries` 和全局下限做成构造参数，并给出默认值，不新增环境变量。
- 新增只读方法 `cache_stats() -> {"entries": int, "keys_sample": [...]}`。`keys_sample` 只能给出 cache_key 的前缀分类计数（例如 `binance:`、`okx:`、`url` 三类各有多少条），**不得输出完整 URL 或币种**。M2-2 普查会用到它。
- 加锁方式沿用现在的 `self._cache_lock`。不要在持锁期间发请求（现在也没有，保持不变）。

### 3.1 RuntimeError 调查（只读）

- 找出 `trade-availability-monitor` 触发这个错误的条件：观察项（`watch.exchange/market_type/raw_symbol/dex`）为什么在 `fetch_status(...)` 的结果里找不到。可能的原因有：已下架、别名或倍率、dex 字段为空和 None 的比较、大小写、`fetch_status` 的过滤条件。
- 在本机用 fake 数据构造能复现的输入，写进交接；**不要修改监控逻辑**，也不要改日志级别。如果确认是代码 bug（而不是数据问题），在交接里给出最小修改建议，由 Claude 决定是否另开任务。

## 4 文件范围与测试

- 修改：`backend/app/services/trade_availability.py`（只改 `_cache`、`_get_json`，新增 `_prune`、`cache_stats`）
- 新建：`backend/tests/test_trade_availability_cache.py`、`docs/task-handoff-2026-10-25-trade-availability-cache.md`
- 禁止修改：`trade_availability.py` 里的其他函数与业务判断、其他服务、前端、compose、部署脚本。

测试（先写测试；用 fake httpx transport，不联网）：
1. 有效期内重复请求同一个 URL 只发一次网络请求，返回同一个对象（`is`）。
2. 过期后重新请求；过期条目在下一次写入后被清理（`cache_stats()["entries"]` 下降）。
3. 不同的 `cache_seconds` 读同一个键时，命中判断与旧实现一致（参照旧实现写对照测试）。
4. 条目数达到 `max_entries` 后按最久未使用淘汰；最近使用过的条目保留。
5. `cache_stats()` 不包含完整 URL 或币种字符串。
6. 失败重试路径（第一次失败、第二次成功）行为不变，失败的请求不写入缓存。
7. 回归：`tests/test_trade_availability*.py`、`tests/test_api.py -k trade_availability`、`tests/test_memory_probe.py`。

## 5 上线验收（Claude 执行，部署需用户批准）

- 部署流程同前，探针保持开启，1024m 限额不变。
- 采 1 小时，和 1024m 观察期（12:50–13:23，平台期约 740MB）对比：平台期是否下降、增长是否进一步放缓、OOM 次数。
- 预期降幅 30–70MB（Python 层）。如果降幅明显偏小，说明大头在别处，由 M2-2 普查继续定位。
- 回滚：revert 提交。
