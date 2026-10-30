# S3-B 实时序列缓存加上限（2026-10-30）

## 背景
`backend/app/services/pair_spread_query.py:519` 起四个模块级 dict（_REALTIME_PAIR_SPREAD_CACHE、_REALTIME_PAIR_FUNDING_CACHE、_REALTIME_PAIR_OPEN_INTEREST_CACHE、_REALTIME_SYMBOL_SPREAD_CACHE）。键由用户输入组成（交易所/DEX/symbol/倍率/周期），单序列已限 4000 点、访问时按 hours 裁剪当前键，但**键从不删除、没有总量上限**。例如 50 个组合、三类各填满就是 60 万点。生产目前只有 0–1 个键，这是防护性改动，不减少上游请求。

## 方案
1. 新增 `backend/app/services/realtime_series_cache.py`：有界缓存类，按键保存序列，支持：每类最大键数（默认 128）、四类合计最大点数（默认 200000，跨实例共享一个预算对象）、闲置 TTL（默认 1800 秒，按最后访问时间）、超限时按最久未访问淘汰（LRU），再不够就淘汰最旧的键直到满足点数预算。时钟可注入（测试用）。淘汰发生在写入时和读取时顺便清理过期键，不加后台任务。
2. 四个 dict 换成这个类的实例，尽量保持现有读写调用形状（get/set/裁剪）。现有健康检查、memory probe、census 里的 keys/points 统计接口保持字段名不变。
3. 键被淘汰后再次查询：行为等同首次查询（从当前点开始累积），并在该次响应现有的 warnings/notes 字段（先读代码确认有哪个字段）里加一条明确说明“实时序列缓存已过期或被淘汰，历史从现在重新累积”。如果响应模型没有合适字段，只记 debug 日志，不改响应模型，在报告里说明。
4. 键隔离：不同 DEX、倍率、周期、交易所的键不能串线（现有 key 构造不改）。
5. 上限值做成模块常量，不新增环境变量。

## 测试
- 灌入 10000 个不同键后每类键数 ≤128、合计点数 ≤200000。
- 闲置超过 TTL 的键被清理；被访问的键刷新 TTL。
- LRU 顺序正确：最近访问的不被先淘汰。
- 淘汰后再查询有说明（或按第 3 条报告）。
- DEX/倍率/周期不同的两个查询结果不串。
- 现有 pair_spread_query、premium_index、health/memory probe 相关测试不放宽。

## 范围
只改 pair_spread_query.py、新增 realtime_series_cache.py、必要时读取这些缓存统计的那一个模块（只改导入/调用，不改字段）、测试、本任务书。commit 信息 `perf: bound realtime pair spread series caches`。

## 测试环境（必须）
CI 用 backend/requirements.lock 的锁定依赖（FastAPI 0.162、Starlette 1.10、pydantic 2.14.1），本机全局 Python 是旧版本。全量测试必须用锁定依赖的虚拟环境跑：`C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe -m pytest -q -p no:cacheprovider`（在 backend 目录下）。日志里保留命令和汇总行。不要运行 WSL，不要访问生产服务器或真实 Astro/交易所。
