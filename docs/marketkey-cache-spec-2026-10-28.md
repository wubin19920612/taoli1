# MarketKey 编码与构造缓存：任务书（Claude，2026-10-28）

基线：`8779d40`（当前生产版本）。worktree `.worktrees/marketkey-cache`，分支 `codex/marketkey-cache`。调查报告见主检出目录 `output/cpu-hotspots-investigation-20261028.log`。

## 1 现状
- `backend/app/models/market_identity.py`：`MarketKey.key` 每次读取都会执行 `json.dumps([...], separators=(",", ":"))`；`from_snapshot` 每次都新建一个带 model_validator 的 pydantic 对象。
- py-spy（生产环境，90 秒）：`key` 占 4.8% 核，其中 json.dumps 占 4.1%；`from_snapshot` 占 2.1%。每轮大约 4.7 万次编码、5.2 万次构造，但不同的 key 只有大约 1.24 万个。

## 2 设计（只改 market_identity.py）
1. 新增模块级函数 `_encode_key(exchange, market_type, dex, raw_symbol) -> str`，用 `functools.lru_cache(maxsize=MARKET_KEY_CACHE_SIZE)` 包装（常量 `MARKET_KEY_CACHE_SIZE = 32768`）。函数内容与现在的 json.dumps 完全相同。`key` 属性改为调用它。
2. ~~`from_snapshot` 构造缓存~~：**取消**（第一版实测两个缓存合计 12.5MB；共享实例还会破坏已有的注册表隔离和释放测试）。`from_snapshot` 保持旧实现不变。
3. 其他构造方式（`MarketKey(...)`、`model_validate`）不变。
4. 不做：修改 `.key` 的格式、DB 编码、MarketKey 字段和校验规则，以及其他文件。

## 3 约束
- 对任意输入，新 `key` 和旧的 json.dumps 结果逐字节一致，覆盖：HL main、HL 不同 DEX（`xyz:ABC`）、raw 里带冒号、大小写、首尾空白、Unicode、`dex=None`。
- 编码缓存在 1.24 万个不同 key 时的保留内存（tracemalloc，只统计缓存新增的部分，不含 MarketKey 本身）要在测试里打印出来，上限 4MB。

## 4 文件与测试
- 修改：`backend/app/models/market_identity.py`
- 新建：`backend/tests/test_market_key_cache.py`、`docs/task-handoff-2026-10-28-marketkey-cache.md`
- 禁止修改其他文件。
测试要先写：上面的逐字节等价测试（新旧实现对照，测试文件里保留旧实现）、缓存命中测试（同样的四元组返回同一个 str 对象）、内存测试；再做一个微基准：1.24 万行 × 4 次 key 读取，打印新旧实现的耗时。
回归：`tests/test_market_identity*.py`、`tests/test_market_validation_*.py`、`tests/test_pair_validation*.py`，以及后端全量（在 backend 下 `mkdir -p ../.pytest-tmp`，`python -m pytest -q --basetemp=../.pytest-tmp/x`）。

## 5 派发
- Codex：gpt-6.1-sol xhigh。不要跑 WSL，不要联网。上线需另行申请批准。

## 6 修订（Claude，10-28 第二版）
第一版的结论见 `docs/task-handoff-2026-10-28-marketkey-cache.md`。第二版只保留 §2-1 的编码缓存，内存上限是 4MB，测试的期望值不能放宽。
