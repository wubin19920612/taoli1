export const alertTypeOptions = [
  { label: "SF", value: "SF" },
  { label: "FF", value: "FF" },
  { label: "SS", value: "SS" }
];

export const alertSeverityOptions = [
  { label: "info", value: "info" },
  { label: "warning", value: "warning" },
  { label: "critical", value: "critical" }
];

export const alertRuleGuide =
  "SF=现货买入 / 永续卖出，FF=永续买入 / 永续卖出，SS=现货买入 / 现货卖出。info=仅记录，warning=普通告警，critical=强提醒。连续命中：同一机会需要连续满足多少轮才触发。冷却秒数：同一机会触发后，多少秒内不重复发送。";

export const alertRuleFieldHelp = {
  name: "只是给自己识别这条规则的名字。",
  enabled: "关闭后这条规则不会参与评估，也不会发告警。",
  types: "选择要监控的套利类型。",
  include_exchanges: "只匹配这些交易所，留空表示不限制。",
  exclude_exchanges: "这些交易所会被排除。",
  include_symbols: "只匹配这些标的，留空表示不限制。",
  exclude_symbols: "这些标的会被排除。",
  min_open_spread_pct: "开仓价差达到这个值才算命中。",
  min_fee_adjusted_open_pct: "扣除手续费和滑点后的净开仓价差阈值。",
  min_volume_24h_usdt: "买卖两侧较小的 24h 成交额必须达到这个值。",
  consecutive_hits: "同一机会需要连续满足多少轮才触发。",
  cooldown_seconds: "同一机会触发后，多少秒内不重复发送。",
  severity: "info=仅记录，warning=普通告警，critical=强提醒。"
};
