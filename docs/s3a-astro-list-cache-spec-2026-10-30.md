# S3-A Astro 卡片列表展示缓存（2026-10-30）

## 背景
浮窗每 10 秒左右一轮，每轮调 `/api/astro/pairs`（routes_astro.py:494 直接 `client.list_pairs()`），symbols/astro 模式再调 `/api/instruments/{symbol}`（routes_instruments.py:190，已有 app 级 10 秒缓存但无锁，冷缓存并发会重复请求）。两条展示路径各打一次 Astro，生产约 30–60 次 Astro/小时。

## 方案
1. 在 `backend/app/services/astro_client.py` 的 `AstroSdkClient` 上增加**展示用**读取方法 `list_pairs_cached(max_age_seconds: float = 10.0)`：进程内保存上次结果和取得时间（monotonic），未过期直接返回副本（调用方不能改到缓存本体）；过期时用 asyncio.Lock + 单个进行中 future 合并并发：同一冷窗口内多个调用只发 1 次 `list_pairs()`。失败不缓存，异常原样传给所有等待者。
2. `add_pair`、`update/enable/disable/delete` 等所有写方法成功**或抛异常**后都清掉缓存（超时可能已在远端生效）。
3. 只有两个展示路由改用缓存：routes_astro.py `/astro/pairs`、routes_instruments.py 的卡片证据（可把它现有的 app 级 10 秒缓存换成 list_pairs_cached，去掉重复的缓存层）。
4. **不改**下列路径，它们必须继续拿实时列表：astro_alerts.py:422/435（建卡前 list-before-create）、fs_astro.py:75、index_components.py:133、routes_market_identities.py:180。在 list_pairs_cached 的 docstring 里写明“仅用于展示，建卡/对账前必须用 list_pairs()”。
5. 401/鉴权失败行为不变（浮窗依赖现有 401 处理，FloatingWatchPanel.tsx:666）。

## 测试
- 10 秒内两次调用只打 1 次上游；过期后再打 1 次。
- 冷缓存下 20 个并发调用只打 1 次上游；上游抛错时 20 个都收到异常，下一次调用重新请求。
- 写方法成功和抛异常后缓存失效。
- 返回值被调用方修改后不影响下一次读取。
- 建卡路径（astro_alerts、fs_astro）仍调用 list_pairs（不是缓存版）。
- 现有 Astro/instruments/浮窗相关测试不放宽。

## 范围
只改 astro_client.py、routes_astro.py、routes_instruments.py、相关测试、本任务书。commit 信息 `perf: share a short-lived astro pair list for display routes`。

## 测试环境（必须）
CI 用 backend/requirements.lock 的锁定依赖（FastAPI 0.162、Starlette 1.10、pydantic 2.14.1），本机全局 Python 是旧版本。全量测试必须用锁定依赖的虚拟环境跑：`C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe -m pytest -q -p no:cacheprovider`（在 backend 目录下）。日志里保留命令和汇总行。不要运行 WSL，不要访问生产服务器或真实 Astro/交易所。
