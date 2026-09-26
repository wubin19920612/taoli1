# 交接：正差价正费率实盘实验隔离

日期：2026-08-18

## 当前代码状态

- 分支：`codex/frontend-localization-polish`
- 最新已推送提交：`f75bc66 Isolate live pilot card experiment`
- 远端：`origin/codex/frontend-localization-polish`
- 用户未跟踪文件，请勿提交或删除：
  - `output/dexe_bybit_bitget_chain_probe/`
  - `script/dexe_bybit_bitget_chain.py`

## 用户目标

用户希望对“正差价 + 正资金费率边际”的机会做小资金实盘实验，实验候选创建 Astro 卡片后默认启动；但实验的仓位、最大标的数、资金费率筛选等配置不能影响已有的常规告警、飞书通知、普通自动建卡、手动建卡或新币建卡。

## 已完成实现

### 独立实验入口

- `AstroAlertService.handle_alert()` 现在永远使用普通 Astro 卡片默认参数。
- 新增 `AstroAlertService.handle_live_pilot()`，仅实验候选使用：
  - `notional_per_symbol_usdt` 覆盖 `max_trade_usdt` 和 `max_notional`
  - `create_cards_enabled` 决定实验卡片是否默认启动
- 关键文件：
  - `backend/app/services/astro_alerts.py`
  - `backend/app/main.py`

### 告警循环不再被实验截流

- 以前开启 Live Pilot 后，主告警循环会先按 Live Pilot 的 `max_symbols`、资金边际和 Hyper 偏好过滤机会，导致常规告警受到影响。
- 现在先完整执行原有告警匹配；随后只从命中的告警里选择实验候选。
- 非实验候选继续按普通逻辑处理和建卡。
- 同一标的多路线时，实验最优路线会优先尝试建卡，避免普通暂停卡片抢占同名 Astro 卡片；若实验路线未通过最新信号、卡片参数或盘口校验，普通路线仍会继续尝试。
- 新币机会不会被实验通道接管，继续使用原有“新币卡片默认开启”逻辑。

### 页面文案

`frontend/src/pages/SettingsPage.tsx` 中原“实盘灰度”区已更名为“正差价正费率实盘实验”，并明确说明：

- 实验卡片默认启动仅影响实验候选
- 普通卡片仍遵循“Astro 卡片默认参数”
- 资金阈值现在叫“最小下周期资金边际”，填正数可只保留正资金边际

## 当前配置建议

部署更新后，在“参数与告警 -> 正差价正费率实盘实验”中先设置：

```text
启用实盘实验：开启
实验卡片默认启动：开启
屏蔽 SS：开启
最多标的数：1
每标的资金：10 USDT
最小下周期资金边际：0.03%
Hyper 优先：开启（可按实际执行偏好调整）
```

普通“Astro 卡片默认参数 -> 创建后允许开仓”应继续保持关闭。这样只有实验候选自动启动，普通自动建卡仍保持暂停。

部署环境仍须确认：

```env
ASTRO_DRY_RUN_ONLY=false
ASTRO_ALERT_AUTO_CREATE=true
```

## 当前限制 / 后续可做

- 实验候选当前仍以“已有启用的告警规则命中结果”为候选来源。因此“正差价”的门槛由既有告警规则的开仓阈值和综合开仓阈值决定。
- 如果用户希望实验完全独立于现有告警规则，可以新增专属实验规则或专属过滤字段，例如：
  - 最小原始开仓价差
  - 最小扣费后价差净值
  - 实验专属最低 24h 成交额
  - 实验专属连续命中次数 / 冷却时间
- 可以在 Astro 或告警历史中增加“实验卡片”来源标记，便于后续单独统计实盘结果。

## 已验证

- 后端完整测试：`451 passed`
- 前端生产构建：`npm run build` 通过
- Python 编译检查：`python -m compileall -q app` 通过
- `git diff --check` 通过
- 当前环境没有安装 `ruff`，未执行 Ruff 静态检查。

## 子设备更新

确认子设备处于 `codex/frontend-localization-polish` 分支后执行：

```bash
cd ~/wubin/taoli1
git branch --show-current
git pull --ff-only
git log -1 --oneline
sudo docker compose up -d --build
sudo docker compose ps
```

预期最新提交为：

```text
f75bc66 Isolate live pilot card experiment
```
