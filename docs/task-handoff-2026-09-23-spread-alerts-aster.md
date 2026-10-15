# 价差告警：Gate 现货 / Aster 永续 TAKEUSDT 排查与修复交接

日期：2026-09-23。模块：价差与资金费率告警。

## 目标、范围与基线

- 目标：解释截图中 Gate 现货买入、Aster 永续卖出 TAKEUSDT 未通知的原因；修复已确认的采集与限流缺口，并验证不绕过风险条件。
- 分支：`codex/frontend-localization-polish`；起始提交：`9385e21249665d556393c02bf564d7bf6fca0975`。
- 本次仅涉及 Aster 永续元数据采集、价差告警限流、对应测试；未调整告警阈值、交易方向、通知模板或用户设置。

## 已完成与关键入口

- `backend/app/exchanges/aster.py`：原先只取 `bookTicker` 和资金周期，未取资金费率与 Aster 合约 24h 成交额；默认规则因 `MISSING_FUNDING` 排除。现在并行取公开 `premiumIndex`、`ticker/24hr`、`fundingInfo`，按实际标的匹配；资金费原始比例乘 100 后记录为每结算周期百分比。`lastFundingRate` 是当前公开费率，不冒充预测费率；无法获取时保留空值，由既有风险规则挡住。24h 合约成交额取 USDT 计价的 `quoteVolume`。
- `backend/app/services/alert_engine.py`：每轮最多 10 条、每标的最多 3 条；原逻辑会把超出限额而未送出的机会也登记冷却。现在只对实际入选的候选开启冷却，以便下一轮有机会通知。
- `backend/tests/test_exchange_resilience.py`、`backend/tests/test_alert_engine.py`：正向连续命中、部分接口失败、未知资金周期及限流不吞机会的回归测试。
- `backend/app/services/risk_labels.py`、`backend/app/models/alert.py`：默认排除 `MISSING_FUNDING`、`LOW_VOLUME` 等；未改变此安全边界。
- `backend/app/services/pair_spread_diagnostics.py`：历史分钟 K 线诊断不是实时盘口/阈值完全复盘；页面显示的同标的事件可能属于其他交易方向或交易所。

## 业务口径与现场核对

- 截图中的 `+5.35%` 是历史分钟 K 线收盘价差；截图时按 Gate ask `0.158922` 买入、Aster bid `0.160060` 卖出，实时盘口开仓价差约 `0.71%`。另一个 `1.85%` 是反向操作，不属于现货买入／永续卖出的 SF 路线。
- 2026-09-23 使用官方公开接口只读核对：TAKEUSDT Aster `premiumIndex` 存在资金费率与下次结算时间，`fundingInfo` 显示 4 小时周期，`ticker/24hr` 存在合约 USDT 成交额；Gate 现货也有 quote volume。报价和费率随时间变化，不能将核对时的值回填为截图峰值时的数据。
- 修复后的本地适配器对实时公开接口可取得双边报价、Aster 当前费率与周期及双方 24h 成交额；有正价差时能产生 SF 候选。一次本地只读核对时价差约 `0.25%`，以 `0.9%` 示例开仓门槛评估未命中。2026-09-23 17:30:27（北京时间）再次核对时 Gate ask `0.194047` 已高于 Aster bid `0.19361`，没有正向 SF 候选；Aster 当前费率约 `+0.270299% / 4 小时`，Gate/Aster 24h 成交额分别约 `509万 / 672万 USDT`。这些都不是截图峰值时的数据，不能据此补发历史通知。
- 发通知还受线上规则开关、SF 类型、现货/合约方向、原始开仓与扣费后门槛、真实盘口深度、数据新鲜度、资金费是否为负、成交额、连续命中（默认 3 轮）、风险标签、冷却、Astro/最新快照校验、飞书发送状态限制。生产配置未取得，不能保证某时点通知。
- `lastFundingRate` 在此链路作为当前周期费率；`funding_next_rate_pct` 保持空，后续计算按当前费率回退，属于预估而不是已验证的下一结算费率。Aster 合约成交额接口故障时成交额仍可能为空，既有双腿成交额检查只使用可得侧，需要结合盘口复核，不能因此推断另一腿有足够成交量。

## 验证、线上状态与后续

- 本地：定向采集/引擎/告警循环/采集器/诊断测试 108 项通过；Aster 6 项、Astro 交易路线 16 项分别通过；`ruff check` 修改文件通过。全后端套件曾运行到约 36% 出现一项失败，中止后单独重跑 Astro 交易路线文件通过；没有保留完整失败栈，因此不能确认该失败归因，交付前须继续追踪或说明限制。
- 线上：**尚未部署、未备份 `/data/radar.db`、未读取生产规则或事件数据库、未校验飞书实际投递**。当前工作站无已配置生产 SSH 目标，本地 `127.0.0.1:8000` 也未启动；不要声称线上已修复。
- 代码交付时仅暂存本任务文件；工作区已有大量与其他任务相关的 `output/` 未跟踪截图/脚本和 `script/dexe_bybit_bitget_chain.py`，须原样保留。排查期间另有 `frontend/src/pages/InstrumentLookupPage.tsx` 出现并行任务的已跟踪修改，同样不能暂存或覆盖。
- 下一步：取得受控生产连接后，先备份 `/data/radar.db` 并校验，按 `docs/linux-deployment.md` 使用 `git pull --ff-only` 和 `docker compose up -d --build` 部署，检查前后端容器及 `/api/health`；只读核验 `/api/alerts/rules`、`/api/alerts/events`、机会数据及 Gate/Aster 两侧盘口，并在满足线上实际规则连续数轮时确认事件为 `sent`（或明确记录 `muted` / `failed` 的原因）。不要发送人为虚构的真实交易信号。
