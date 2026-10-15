# 标的查询最大/最小价差交接

## 目标与范围

在标的查询的跨交易所价差表中，为每个买卖方向展示近 24 小时同分钟收盘价计算出的最大价差和最小价差，并解释无共同历史样本的状态。本任务只涉及标的查询价差统计与展示。

## 分支和基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`4fb8533 docs: record instrument lookup deployment`

## 已完成功能

- 后端 `InstrumentSpread24hStats` 增加 `min_spread_pct` 和 `min_at`。
- `InstrumentStatisticsService` 对同一组对齐分钟 K 线计算最大值、最小值及各自时间。
- 前端将原“24h 最大价差”列改为“24h 最大/最小价差”，最大和最小两行放在同一列。
- 无对齐分钟时显示“暂无共同样本”；统计接口整体不可用时仍显示“暂无历史样本”，具体原因通过 Tooltip 展示。

## 关键入口与业务规则

- `backend/app/models/instrument.py`：统计响应模型。
- `backend/app/services/instrument_statistics.py`：按原始市场身份和同一分钟收盘价计算方向性极值；不使用历史买一卖一，不扣手续费和滑点。
- `frontend/src/pages/InstrumentLookupPage.tsx`：价差表列渲染和 Tooltip。
- `frontend/src/api/types.ts`：前端统计响应类型。

“暂无共同样本”表示买卖两边近 24 小时没有可对齐的相同分钟历史 K 线，常见原因是上游历史数据缺失、市场身份/原始交易对不一致或一侧历史请求失败；这不等于价差为 0。

## 验证结果

- `python -m pytest backend/tests/test_instrument_statistics.py backend/tests/test_instrument_statistics_equivalence.py -q`：41 passed。
- `npm test -- --run tests/InstrumentLookupPage.test.tsx`：38 passed。
- `npm run build`：通过。

## 线上状态

- 提交：`5ff1b8f0d1d8730767dcfc6d502520f2b3915fed`，已推送到 `origin/codex/frontend-localization-polish`。
- GitHub Actions：构建 workflow `38334571400` 成功，backend/frontend 两个 job 均成功。
- 部署：生产 Compose 已更新到 backend/frontend `sha-5ff1b8f0d1d8730767dcfc6d502520f2b3915fed`，两个容器均 healthy。
- 数据库备份：
  - `backups/radar-before-5ff1b8f0d1d8-20261013T020155Z.db`，`integrity_check=ok`，SHA-256 `c453ab0861da3e20739810f6c8ee9fbeca58f2aee1b85a2dfaa36e11391321dd`。
  - `backups/squeeze-route-before-5ff1b8f0d1d8-20261013T020155Z.db`，`integrity_check=ok`，SHA-256 `bc957e00711952f3b5ddc6b8b27d9196773c4a2b9c7cf85521aef5da95003d3a`。
- 线上验证：`/api/health` 返回 `status=ok`；`/api/instruments/BTCUSDT/statistics` 返回 `min_spread_pct/min_at` 与 `max_spread_pct/max_at`；前端 `:3000/` 返回 HTTP 200。
- 观察到 OKX 历史 K 线接口限流时，相关方向会保留 `point_count=0` 和错误信息，前端显示无共同样本/统计不可用，不会写成 0。

## 已知问题与下一步

- 最大/最小统计仍基于同分钟收盘价估算，不代表历史盘口可成交价差。
- 若继续扩展标的查询统计，应在新任务中处理，并先复用本文件中的测试和部署记录。
- 工作区中已有其他任务修改和未跟踪产物，交付时只能暂存本任务文件。
