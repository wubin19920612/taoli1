# crossex-exec：管理接口暂停/恢复开仓（2026-11-16）

## 目标
前端要有“暂停开仓 / 恢复开仓”按钮，代替每次 SSH 改 CROSSEX_LIVE_ENABLED 再重启。
语义：CROSSEX_LIVE_ENABLED 仍是启动时的硬上限（不在运行时切换 live/shadow）；按钮只控制**是否允许开新仓**。
暂停时已有持仓照常管理、平仓（不能影响平仓/修复/对账路径）。

实现手段：复用已有 STOP 文件闸门（engine.can_open 已检查 `self.stop_file.exists()`，api_data summary 已输出 `stop_file`）。

## 要做
1. management.py `write_route` 增加两个管理写路由：`POST /api/v1/opening/pause`、`POST /api/v1/opening/resume`（仍走现有 admin token、JSON body、长度限制）。
   - body：`{"expected_paused": <bool>}`，必填且必须是 bool；与当前状态不符 → 现有 WriteConflict 模式返回 409（响应含当前 `paused`）。目标状态与当前相同也属于不符（不做静默成功）。
   - pause：原子创建 STOP 文件（写临时文件 + os.replace，内容 JSON `{"at":..., "source":"admin_api"}`，权限 0600）。
   - resume：删除 STOP 文件。
   - STOP 文件路径与引擎一致：config `CROSSEX_STOP_FILE`，默认 `DEFAULT_VAR+'/STOP'`；把路径从 APIService/config 传进 Management，不要硬编码第二份。
   - 每次成功写入记一条审计事件（store events，kind `OPENING_PAUSED` / `OPENING_RESUMED`，含 at、source='admin_api'），并沿用现有管理写接口的审计/日志写法。
   - 文件系统错误 → 返回现有的失败响应形态（5xx/固定错误码），不能让 API 线程崩溃，也不能留下半写文件。
2. summary 输出增加 `opening_paused`（= STOP 文件存在）与 `mode`（live/shadow，已有则复用），字段名保持向后兼容，不删已有字段。
3. 不改：引擎开平仓逻辑、风控、settings、__main__ 的 live 判定、任何下单路径。

## 验收
- 新增测试覆盖：非 admin 403、body 缺失/非 bool 400、状态不符 409、pause→can_open False、resume→can_open 恢复（其余条件满足时）、STOP 文件原子写入与权限、审计事件、summary 字段。
- `python -m unittest discover -s tools/crossex_exec/tests` 与 `python -m unittest discover -s tools/fill_sim/tests -t .` 全部通过。
- 只允许改 tools/crossex_exec/ 下的代码与测试；不要提交 git commit，由 Claude 验收后提交。
