# crossex-exec 第十八、十九轮只读终审（2026-11-13，gpt-6-astra max，read-only）

范围 4e75e5d..e2d39db（含 b99c8ae）。

已覆盖 **`4e75e5d..e2d39db`，含 `b99c8ae`**；未修改文件，工作区保持干净。

- **P0：未发现。**
- **P1：未发现。** 现金域例外仍要求完整合约身份一致；未知归属、错误 DEX、缺失流水继续停开。CONVERT／Lighter 利息不计程序盈亏，但仍校验余额连续性。未发现误解除日亏锁或破坏外仓、150U／600U 隔离。
- **P2：无新增；第十七轮遗留已修复。** [external_accounting.py:220](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/external_accounting.py:220)：第二、第三历史列表校验失败时保留原缓存；全部成功后统一替换，容量回归通过。建议保留这些失败场景测试。

分页 null 例外、fatal 类型／精确行号及生命周期测试通过。**`limits.py`、下单路径未改**；原 57 个测试／夹具文件中，仅一文件的两处空页预期按裁决调整，未见其他断言放宽。

本次 **88 项回归＋4 项安全探针通过**，数据库测试采用内存适配；另核验双版本 **642＋158** 全量日志及哈希，均匹配。未复验真实账户或 Linux/systemd。

**结论：代码层面可开小额实盘；线上启动与持续运行验收仍须完成。**
