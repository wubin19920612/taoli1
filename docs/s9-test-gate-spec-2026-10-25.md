# S9 测试离线化与 CI 门禁：设计与任务书（Claude，2026-10-25，待用户确认）

设计来源：`docs/architecture-review-2026-10-23-claude.md` §4 P2-11（CI 无测试门禁、后端依赖未锁定）。

## 1 现状（已核实）

- `.github/workflows/build-production-images.yml`：推送部署分支后**直接构建并推送镜像**，不跑任何测试。部署脚本只检查镜像是否存在，所以未通过测试的代码也能上线。
- `backend/pyproject.toml` 依赖全部是 `>=` 下限（fastapi、pydantic、ccxt、httpx、aiosqlite 等），Dockerfile 用 `pip install .`。**每次构建都可能装到新版本**，线上行为可能在代码没变的情况下改变。
- 测试依赖真实网络：S1 验收时 `test_api.py` 离线运行有 5 failed + 1 error，与基线 `045ed8e` 一致：
  - ERROR `test_collector_startup_uses_multi_exchange_index_component_provider`（`test_api.py:569`）：启动时尝试真实外网；teardown 时 `FakeExchangeClient` 缺少 `is_closed`，`TradeAvailabilityService.aclose` 报错。
  - FAILED（409 ≠ 200）`test_astro_preview_endpoint_uses_saved_card_defaults`（`:3225`）、`..._uses_env_defaults_before_settings_are_saved`、`..._uses_saved_defaults_even_when_they_match_model_defaults`、`test_astro_manual_card_create_endpoint_uses_saved_defaults_with_real_service`、`test_live_pilot_auto_create_service_uses_pilot_settings_for_real_service`（`:3584`）。原因：路由在预览/建卡前做 fresh 市场身份复核（`routes_astro*.py:282` 抛 409 “市场身份无法确认”），离线时复核失败。
- 全量离线结果：见 §5（本机正在运行，完成后补充）。
- 已有离线护栏：`backend/tests/s1_offline_guard.py`（opt-in pytest 插件，拦截 socket/httpx 外连，只放行 loopback）。

## 2 目标

1. 全量后端测试**默认离线**、在 CI 上稳定通过（零真实外连）。
2. CI 先跑测试，测试通过才构建、推送镜像。
3. 后端依赖锁定到当前生产正在运行的版本，构建可复现。

非目标：不改任何业务逻辑；不重写现有测试的断言；前端测试只接入 CI，不修改前端测试内容（前端 WIP 文件不碰）。

## 3 设计

### 3.1 测试离线化

- 把 `tests/s1_offline_guard.py` 的拦截逻辑提升为默认生效：放进 `backend/tests/conftest.py` 的 autouse fixture（或在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 中 `addopts = "-p s1_offline_guard"`，并把 tests 目录加入 `pythonpath`，二选一，选改动最少的）。
- 允许个别**明确标记**的测试联网：新增 marker `network`（`@pytest.mark.network`），默认 `-m "not network"` 跳过，CI 不跑。只有确实要测真实交易所的测试才能打这个标记，并在交接里逐条列出理由。**优先修测试，不要用打标记来逃避修复。**
- 修复 §1 列出的 6 个测试，以及 §5 全量结果里的其他失败：
  - 409 类：在测试里注入 fake 的身份复核结果（monkeypatch `MarketValidationService` 的复核入口，或使用已有的 fake），让复核返回“已确认”。不改路由行为。
  - 启动外连类：给 `create_app` 测试注入 fake adapters / `start_collector=False`，或 monkeypatch `default_exchange_adapters`；补全 `FakeExchangeClient.is_closed`。
- 测试运行时间：`test_api.py` 整文件约 15 分钟。在交接里记录全量耗时；如果超过 20 分钟，再列出最慢的 15 个测试（`--durations=15`），**本次不做性能优化**。

### 3.2 依赖锁定

- `backend/requirements.lock`：**Claude 已放入仓库**（未跟踪），内容是生产镜像 `sha-f38ced1…` 的 `pip freeze`，共 43 个包，例如 fastapi==0.162.0、pydantic==2.14.1、ccxt==4.5.90、httpx==0.28.1、aiosqlite==0.22.1、uvicorn==0.54.0。Codex 直接使用，不要修改版本号。
- Dockerfile 改为 `pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .`。
- `pyproject.toml` 的 `>=` 范围保持不变（作为兼容声明）；锁文件决定实际安装的版本。
- 新增 `backend/requirements-dev.lock`（pytest、pytest-asyncio、ruff 的固定版本），供 CI 安装测试依赖。

### 3.3 CI 门禁（`.github/workflows/build-production-images.yml`）

- 新增 job `test`：
  - backend：`python 3.12`，`pip install -r backend/requirements.lock -r backend/requirements-dev.lock && pip install --no-deps -e backend`，然后 `cd backend && python -m pytest -q`（离线护栏默认生效）。
  - frontend：`npm ci && npm test`（`package.json` 中为 `vitest run`）以及 `npm run build`（`tsc -b && vite build`）。如果前端测试因为用户 WIP 文件（`PairMonitorPage*`）失败，就在交接里说明，不要修改这些文件。WIP 未提交，CI 上用的是已提交的版本，理论上不受影响。
- `build` job 加 `needs: test`。
- `workflow_dispatch` 路径同样先跑 test。
- 不改镜像标签格式、不改触发分支、不改部署脚本。

## 4 文件范围与测试

- 修改：`backend/tests/conftest.py`（或新建）、`backend/pyproject.toml`（只改 pytest 配置）、`backend/tests/test_api.py`（只改 fixture/fake 注入，不改断言）、§5 列出的其他失败测试文件（同样只改 fake 注入）、`backend/Dockerfile`、`.github/workflows/build-production-images.yml`
- 新建：`backend/requirements.lock`（Claude 提供）、`backend/requirements-dev.lock`、`docs/task-handoff-2026-10-25-test-gate.md`
- 禁止修改：`backend/app/**` 业务代码、前端源码与测试、`deploy/`、compose。

验收：
1. 本机：`cd backend && python -m pytest -q` 全量通过，离线护栏默认生效（构造一个故意联网的临时测试，确认会失败，然后删掉它）。
2. 本机：`docker build backend` 成功，镜像内 `pip freeze` 与 `requirements.lock` 一致（Codex 若无法在本机跑 docker，就在报告里说明，由 Claude 验证）。
3. CI：推送到功能分支后由 Claude 用 `workflow_dispatch` 触发，test job 通过后 build job 才运行。

## 5 全量离线测试结果（2026-10-25，本机，分支 codex/memory-fix @ f38ced1）

命令：`cd backend && PYTHONPATH=tests python -m pytest -q -p no:cacheprovider -p s1_offline_guard tests -rfE --durations=15`

结果：**1740 passed，5 failed，4 errors，耗时 45 分 31 秒。**

| 类别 | 测试 | 原因 | 修法 |
|---|---|---|---|
| 5 failed | `test_api.py` 中 §1 列出的 5 个 409 测试 | fresh 身份复核离线失败 | 注入 fake 复核结果 |
| 4 errors | `test_pair_spread_query.py::test_pair_spread_query_uses_large_intervals_as_historical_klines[3600-60/14400-240/86400-1440]`、`::test_pair_spread_query_falls_back_to_available_window` | 测试尝试真实外网，被护栏拦截（teardown 报 `S1 offline test attempted external network`） | 为该路径注入 fake HTTP 客户端或 monkeypatch 拉取 K 线的函数 |

S1 验收时的 `test_collector_startup_uses_multi_exchange_index_component_provider` 这次已通过，但仍要检查它的 teardown 是否有 `is_closed` 报错日志，有就补全 fake。

### 5.1 耗时异常（必须调查）

最慢的测试耗时集中在约 **30 秒**和约 **60 秒**（例如 `test_account_connections.py` 多个约 59 秒，`test_api.py`、`test_fs_monitor.py`、`test_squeeze_routes.py` 多个约 30 秒，`test_task_supervisor.py::test_shutdown_failed_task_still_closes_clients_and_database` 30.7 秒）。这么整齐的数字，说明很可能是 lifespan 关闭时在等某个 30 秒的超时（每次 `TestClient` 退出一次，或进出两次就是 60 秒）。

要求：
1. 找出这 30 秒具体在等什么（哪个任务、哪个 `wait_for`/`timeout`、哪个客户端关闭），写进交接。
2. 如果只是测试造成的（例如 fake 不响应取消、测试没有设置 `stop_event`），就在测试侧修复。
3. 如果是应用关闭流程本身真的会卡 30 秒（这意味着线上每次重启也会多等 30 秒），**不要改业务代码**，在报告里给出位置和建议，由 Claude 另行决定。
4. 目标：全量测试控制在 15 分钟以内；做不到也不阻断本任务，在报告里说明原因即可。

## 6 上线

- 本任务不改运行时代码；镜像内依赖版本与当前生产一致。部署按原流程，部署后只需确认 live/ready 200、旧 `/api/health` 正常。
- 回滚：revert 提交即可（Dockerfile 回到 `pip install .`）。
