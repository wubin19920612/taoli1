# taoli1 “实盘”页面任务书 2026-11-11

接口约定：`docs/live-exec-contract-2026-11-11.md`（唯一依据）。数据源 crossex-exec 正在另一个分支开发，本任务**只用假数据和契约测试**，不依赖它。

## 后端
- 新文件 `backend/app/api/routes_live_exec.py`，照 `routes_arb_mvp.py` 的写法：
  - `GET /api/live-exec/summary`、`/positions`、`/trades`（透传 `limit`、`before`，`limit` 上限 500）、`/stats`（透传 `days`，范围 1–90）；
  - 认证规则与其他看板路由相同，匿名返回 401。
- 配置写在 `core/config.py`：`CROSSEX_EXEC_URL`（默认空）和 `CROSSEX_EXEC_API_KEY`（默认空）。
  - 未配置时返回 `{"available": false, "error": "not_configured"}`，HTTP 200。
  - 上游不可达、超时（3 秒）、非 200 或 JSON 不合法时，返回 `{"available": false, "error": "<简短原因>"}`，不抛 500。
  - 正常时返回 `{"available": true, "data": <上游原样>}`。
- 只透传上述 4 个路径。参数做白名单校验，不拼接任意路径。上游 token 不得出现在日志和响应里。
- 测试：未配置、401、上游正常、上游超时、上游非 200、参数越界、token 不进日志。

## 前端
- 新页面 `frontend/src/pages/LiveExecPage.tsx`，在 `AppShell` 侧栏加一项“实盘”，紧跟在“影子账本”后面，懒加载写法同 ShadowLedgerPage。
- 刷新频率：summary 和 positions 每 5 秒，trades 和 stats 每 30 秒。页面不可见时暂停刷新。
- 区块：
  1. **顶部汇总卡片**：
     - 模式标签：影子 / 实盘，实盘用醒目颜色；
     - 熔断、暂停开仓状态，有原因就显示原因；
     - 今日净利润、累计净利润、手续费、资金费、笔数、胜率；
     - 上限占用（已开名义额/600、持仓对数/3）；
     - 两边账户权益和较基线的跌幅，跌幅接近 150 时变色；
     - 保证金倍数；
     - 最后一个错误。
  2. **当前持仓表**：币种；两腿（交易所、通道、方向、数量、开仓价、标记价）；开仓价差、当前价差；浮动盈亏（美元和 bp）；已持有时长；离下次资金费结算还有多久；离止损还差多少 bp；离最长持仓到期还有多久；状态（`one_leg` 标红）。
  3. **已平仓表**：开仓、平仓时间；持有时长；两腿均价；手续费；资金费；净利润（美元和 bp）；平仓原因（中文）；模拟净 bp；滑点；两腿成交时间差；单腿事件标记。支持“加载更多”（用 `before` 翻页）。
  4. **利润分布**：用 `lightweight-charts`（已有依赖）画 net_bps 直方图；旁边放 by_symbol、by_pair、by_exit_reason、by_spread_tier 四张小表。
  5. **实盘与模拟对比**：显示 vs_sim 的各项指标。
- `available:false` 时显示“实盘服务未连接（原因）”，其余区块显示为空状态，不报错。
- 全部用中文，风格沿用现有页面（antd）。
- 平仓原因中文对照：converged 收敛止盈、stop_loss 止损、max_hold 超时、pre_funding 结算前平仓、stale 行情中断、one_leg 单腿处理、halt 熔断。

## 约束
- 不改 compose、部署脚本、`.env`；不改其他页面的行为。
- 不 push、不 SSH、不跑 WSL；按 test/feat/docs 分开提交，不 amend。
- 后端测试用锁定 venv 跑全量：
  `C:/Users/wubin/AppData/Local/Temp/claude/1f5c892841cd1e82/435b98f6-0522-4055-9b28-46a94def1380/scratchpad/venv-lock/Scripts/python.exe`，在 backend 目录、`PYTHONPATH=.` 下运行 `-m pytest tests -q -p no:cacheprovider --basetemp=../.pytest-tmp/live-exec-full`。
- 前端运行 `npm run build` 和已有的前端测试命令（看 package.json）。
- 贴原始汇总行，并写交接文档 `docs/task-handoff-2026-11-11-live-exec-page.md`。
