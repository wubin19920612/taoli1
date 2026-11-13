# crossex-exec 第二十轮任务书（2026-11-13）

背景：e2d39db（第十九轮，终审无 P0/P1）11-13 09:47 部署影子，rebaseline 后外部收益核实通过。10:11 用户在同一 CrossEx 账户用 Astro 新开两笔仓位：
- `HYPERLIQUID_FUTURE_UNITREE_USDC` LONG，**margin_mode=ISOLATED**，5x
- `ASTER_FUTURE_UNITREE_USDT` SHORT，CROSS，2x

此后线上：
1. `accounts.remote_positions`（accounts.py:76-77）遇到任意 ISOLATED 行抛 `isolated_position` → 每次账户刷新失败（近 1 小时 `ACCOUNT_REFRESH_FAILED` 88 次，事件值为 `{}`，无任何诊断）。
2. 同一原因下 **Engine.startup 抛 ValueError 失败**（服务器副本已复现）：此时服务一旦重启就崩溃循环。
3. 登记外部持仓（CLI/ADMIN）被拒 `external_read_unavailable`：登记需要读账户，读账户又被 ISOLATED 卡住，**死循环，无法通过登记解除**。
4. 外部收益核实随用户交易抖动：`history_coverage`（9 次/h）、`settlement_changed_during_read`、`funding_collection_counter_conflict`（3 次/h）。实测诊断见文末“线上诊断”。

用户裁定：登记外部持仓并做本轮；用户会继续在同一账户用 Astro 交易（可能逐仓）。

## P1-A 逐仓外部持仓
- `remote_positions` 不再对所有 ISOLATED 行直接抛异常；把 margin_mode 作为持仓身份/属性带出。
- **已登记的外部持仓**允许 ISOLATED。**本程序自己的仓位**出现 ISOLATED、或**未登记**的 ISOLATED，仍然 fail-closed 停开（停开锁，不是抛异常），且不能让账户快照整体失败。
- 外部登记流程（CLI 和 ADMIN API）在账户里有未登记/逐仓外部持仓时必须能完成（这正是登记要解决的状态）；登记身份证明里包含 margin_mode。
- 整户保证金覆盖倍数：逐仓仓位的保证金/浮亏只影响该逐仓，不应被当作全仓保证金的可用部分；按保守口径处理（宁可低估覆盖倍数），在交接写明口径。
- 只减本程序仓位的物理强平保护逻辑不变。

## P1-B 启动不因外部状态失败
- 账户里有未登记外部持仓（含逐仓）时，Engine.startup 必须能完成：进入停开状态 + 告警 + 后台线程与 API 正常，以便管理员登记。只有本程序自身状态无法证明安全（如非终态订单无法恢复）才允许启动失败。
- 用现有全生命周期离线冒烟（round18 lifecycle）补“启动时存在未登记逐仓外部持仓 → 启动成功、停开、登记后解除”的用例。

## P1-C 账户刷新失败带诊断
- `ACCOUNT_REFRESH_FAILED` 事件写入安全诊断：异常类型、来自本地常量的原因（不可回显交易所响应/密钥）、文件:行号（复用第十八轮 fatal 诊断的做法）。告警按原因去重。

## P2 核实抖动
按文末线上诊断定位 `history_coverage` / `funding_collection_counter_conflict` / `settlement_changed_during_read` 在用户频繁交易时的真实原因；属于读取窗口/新鲜度时序问题的，在不降低证明强度的前提下修（例如核实与快照同一读取周期内完成、失败后下一轮自动重试而不是长时间挂锁）；属于证明本身不成立的，保持 fail-closed 并在交接写明。不得放宽外仓/本程序隔离和 150/600/日亏/累计亏损。

## 验收
- 新增测试使用上面的真实形态（ISOLATED 行字段：symbol、margin_mode=ISOLATED、position_side=LONG、leverage=5）。
- 3.12 与 3.10 两条全量：`python -m unittest discover -s tools/crossex_exec/tests -v`、`python -m unittest discover -s tools/fill_sim/tests -v`；3.10 解释器 `C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe`。模拟器三种子照旧全过。
- 不改 limits.py 硬上限、不改下单路径；不放宽已有测试（与真实形态矛盾的旧断言在交接写明原因）。
- 交接文档 docs/task-handoff-2026-11-12-crossex-exec-r8.md 追加“第二十轮”一节。小步提交。

## 线上诊断（PM，11-13 10:45–10:55，服务器库副本 + e2d39db，临时把 ISOLATED 当 CROSS 绕过）
- HL 子市场：目录行 `{"symbol":"HYPERLIQUID_FUTURE_UNITREE_USDC","exchange_type":"HYPERLIQUID","tag":"xyz","support_cross":"false",...}`——symbol 不带 `xyz:` 前缀但 tag=xyz；另有 `HYPERLIQUID_FUTURE_xyz:UNITREE_USDC`、`HYPERLIQUID_FUTURE_para:UNITREE_USDC`（tag para，support_cross false）。HIP-3 子市场 support_cross=false ⇒ **只能逐仓**，用户在 HL 子市场的仓位必然 ISOLATED。用 dex='' 登记 → `external_identity_unverifiable`；身份应为 dex=tag。登记入口要能清楚提示 dex 与目录 tag 不符（安全常量即可）。
- ASTER_FUTURE_UNITREE_USDT（CROSS）登记成功。
- 未登记 HL 仓位时 rebaseline 报 `remote_positions`。
- 10 分钟内 47 次核实失败（均因副本里锚点版本未更新的 external_baseline_unverifiable，非本轮目标），但同时观测到：**activity.complete=False 约占 40%**；快照到核实的延迟多为 4–6s，偶有 18–25s（`_evaluate` 要求 ≤10s ⇒ history_coverage）。线上也有 `settlement_changed_during_read`。请据此分析：读取不完整的具体原因（哪个接口/条件；为此可在诊断里带 activity failure 的 condition）、10s 窗口在高频外部交易账户下是否可达，以及能否在不降低证明强度的前提下提高成功率（例如失败快速重试、缩短读取路径、只在必要时重读全量历史）。
