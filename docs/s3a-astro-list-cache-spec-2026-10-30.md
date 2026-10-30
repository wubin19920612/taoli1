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

## 实现与验收记录（2026-10-30）

- 分支：`codex/s3a-astro-cache`；实现前基线：`8c1df65`，工作区干净。
- `AstroSdkClient.list_pairs_cached` 使用 monotonic 完成时间、深拷贝和 Lock + 单个共享 Task（Future）实现默认 10 秒展示缓存。失败不缓存，等待者收到原异常；单个等待者取消不会取消共享读取。
- 现有写入口只有 `add_pair`、`update_pair`，均通过 `_mutate_pair` 的 `finally` 失效缓存，覆盖成功、SDK 拒绝、超时和取消。写操作也脱离此前的进行中读取，防止旧结果重新填充缓存。
- 缓存调用点：`routes_astro.list_astro_pairs`、`routes_instruments._astro_route_statuses`；移除 instruments 的 app 级重复缓存，保留其 5 秒等待超时和错误展示行为。
- 实时调用点保持不变：`astro_alerts.py` 两处建卡前读取、`fs_astro.py`、`index_components.py`、`routes_market_identities.py`。缓存方法自身刷新仍调用 `list_pairs()`。
- 新增 `tests/test_astro_list_cache.py`，覆盖 TTL/空列表/深拷贝、20 并发成功与异常、失败重试、写后失效、读写交错、取消隔离、两个展示接口共享缓存、401 透传。已有 instruments 测试只调整替身方法名；Astro/FS 建卡测试增加禁止使用缓存的断言，未放宽原断言。
- 指定虚拟环境版本实测：FastAPI `0.162.0`、Starlette `1.10.0`、pydantic `2.14.1`。
- 相关测试：`307 passed, 1 warning in 14.32s`。
- 全量测试工作目录：本 worktree 的 `backend`；PowerShell 命令：

```powershell
& 'C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe' -m pytest -q -p no:cacheprovider
```

```text
2320 passed, 6 deselected, 13 warnings in 105.76s (0:01:45)
```

- 完整命令及输出日志：`%TEMP%\s3a-astro-cache-full-tests-2026-10-30.log`。6 项按项目默认配置排除 network/benchmark；13 条警告为 Starlette TestClient 和 `datetime.utcnow()` 弃用提示。
- 范围检查：仅三个指定生产文件、五个相关测试文件及本任务书；无前端修改。未运行 WSL，未访问生产或真实 Astro/交易所，未部署；线上验收留给独立发布任务。本记录作为本模块交接，不新增范围外文档。
