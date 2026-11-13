# crossex-exec 第二十七轮任务书（2026-11-13）：基线统计阻塞账户刷新

## 线上现象（影子 725d025，11-13 15:37 部署，LIVE=0）
- stop_locks 常驻 `account_snapshot_unavailable`，账户快照年龄在 40–72 秒间循环，从不够新鲜，永远不能开仓。
- py-spy 多次采样一致：`exec-baseline` 线程在 `refresh_baselines`（engine.py:~497 持 `_lifecycle_lock`）→ `baseline.record` → `status`（baseline.py:149 持 `store.lock`）→ `storage.baseline_samples`（storage.py:209 列表推导）；`exec-accounts` 卡在 engine.py:307 `with self._lifecycle_lock`；主线程卡在 storage.meta 等 store.lock。
- 规模：baseline_samples 约 612 万行、6899 个 pair_key；每 30 秒一轮，每轮对每个 pair 读 6 小时窗口（最多 720 行）并在 Python 里算中位数/MAD，全程持两把锁。CPU 约 40%。旧版本同代码，属既有性能缺陷，随数据增长恶化。

## 要求
1. 账户刷新、下单前检查、API、主循环不得被基线统计阻塞：基线计算不在 `_lifecycle_lock`、`store.lock` 内做重活。持锁只做快速的读快照/写结果（毫秒级）。
2. 降低每轮成本：例如每个 pair 在内存维护 6h 滑动窗口（启动时一次性从库加载，之后增量追加/淘汰），或只对本轮有新样本的 pair 重算；ready/MAD/中位数语义与现有完全一致（≥300 样本、跨度 ≥10800s、FRESH 90s、RECHECK 600s 等不变）。注意内存：生产机 1.9G 无 swap，进程目前约 100MB，新增内存须有上界并写入交接（估算 6899×720 个样本的占用，必要时用紧凑结构或只保留 ready 判定需要的统计）。
3. 正确性：基线读到的数据与 DB 一致，崩溃重启后能从 DB 恢复；“延迟一轮不与新报价混用”等现有保护不变；`_closed` 关闭语义不变。
4. 测试：新增性能/并发回归——大量 pair（如 7000 个 × 720 样本，可缩放）下，基线一轮进行中，账户刷新能在限定时间内拿到锁并完成；基线结果与旧实现逐 pair 一致（对拍）。
5. 不改 limits.py、不改下单/发送路径；不放宽已有测试。3.12 与 3.10 两条全量（crossex_exec、fill_sim），3.10 解释器 C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/125141d8-f3d5-4518-98a0-6a4761963215/scratchpad/uvpy/cpython-3.10-windows-x86_64-none/python.exe；模拟器三种子全过。
6. 交接追加“第二十七轮”：改法、内存上界估算、每轮耗时前后对比（本机用合成数据测）。小步提交并推送。
