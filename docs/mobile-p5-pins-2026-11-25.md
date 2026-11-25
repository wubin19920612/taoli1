# 手机 App P5（后端）：卡片置顶（2026-11-25）

背景：Astro 网页的置顶只在网页登录态接口里，SDK 拿不到。用户决定 App 自己置顶，存在 taoli1 服务器上（单用户，全局共享，不按会话区分，重新登录后置顶仍在）。

## 允许修改
- `backend/app/db/schema.py`（新表）
- `backend/app/db/repositories.py`（新 repo，或新文件 `backend/app/services/mobile_pins.py`）
- `backend/app/main.py`（挂到 `app.state.mobile_pins_repo`，仿 `mobile_push_repo`）
- `backend/app/api/routes_mobile.py`、`backend/app/models/mobile.py`、`backend/app/services/mobile_summary.py`
- `backend/app/core/security.py`：只允许在 `_mobile_route` 白名单里加一行 `("POST", "/api/mobile/pins")`
- 新测试文件 `backend/tests/test_mobile_pins.py`
- 不得修改其他文件；不得放宽已有测试。

## 需求
1. 表 `mobile_card_pins(card_id TEXT PRIMARY KEY NOT NULL, pinned_at REAL NOT NULL)`，`CREATE TABLE IF NOT EXISTS`。
2. `GET /api/mobile/pins` → `{"pins": {"<card_id>": <pinned_at 秒级浮点>, ...}}`。
3. `POST /api/mobile/pins`，body `{"card_id": str, "pinned": bool}`：
   - `card_id` 校验 `^[A-Za-z0-9_-]{1,64}$`，不合格 422（错误信息中文，**不回显输入**）。
   - `pinned=true` → upsert，`pinned_at=time.time()`（重复置顶刷新时间，排到最前）；`pinned=false` → 删除（不存在也返回成功）。
   - 置顶总数上限 200，超过且是新增 → 409「置顶数量已达上限」。
   - 返回 `{"pins": {...}}`（最新全量）。
   - 依赖 `require_session`（同 push 路由）；full 与 mobile_readonly 会话都可用。
4. `MobileCard` 加 `pinned_at: float | None = None`；`summary()` 读取置顶表，按 `card.id` 填入。**不改 `_card_order` 的排序**（App 端负责排序）。读置顶表失败时 summary 仍正常返回（pinned_at 全为 None，记 warning 日志）。
5. 不自动清理已不存在卡片的置顶（卡片可能短暂从缓存消失）。

## 验收命令
- `pytest backend/tests/test_mobile_pins.py backend/tests/test_api.py -q --basetemp=<scratchpad>/pt`（用锁定 venv）
- 测试至少覆盖：置顶/取消/重复置顶刷新时间/非法 id 422 且不回显/上限 409/只读会话能 POST pins 但仍不能 POST 其他非白名单路径/summary 带 pinned_at/置顶表异常时 summary 不报错。
