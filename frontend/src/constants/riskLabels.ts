export const riskLabelOptions = [
  {
    value: "LOW_VOLUME",
    label: "低成交额",
    description: "买入侧和卖出侧取较小的 24h 成交额，低于你设置的低成交额阈值"
  },
  {
    value: "STALE_DATA",
    label: "数据过期",
    description: "当前时间减去行情最后更新时间，超过你设置的数据过期秒数"
  },
  {
    value: "HUGE_SPREAD_VERIFY",
    label: "异常大价差",
    description: "开仓价差高于你设置的异常大价差阈值，需要人工复核盘口、同名币、停充提等问题"
  },
  {
    value: "WIDE_SPREAD",
    label: "开平价差宽",
    description: "|平仓价差 - 开仓价差| 高于你设置的开平价差宽度阈值，说明进出场估算差异大"
  },
  {
    value: "SAME_TICKER_RISK",
    label: "同名币风险",
    description: "标的在你维护的同名风险标的列表中，可能不是同一个资产"
  },
  {
    value: "FUNDING_AGAINST",
    label: "资金费率逆风",
    description: "净资金费率按两侧结算周期折算到小时口径后比较，低于负的逆风阈值"
  },
  {
    value: "MARK_INDEX_DEVIATION",
    label: "标记/指数偏离",
    description: "合约标记价与指数价的偏离绝对值，高于你设置的偏离阈值"
  },
  {
    value: "MISSING_FUNDING",
    label: "缺资金费率",
    description: "至少一侧永续合约缺少资金费率数据"
  },
  {
    value: "THIN_ORDER_BOOK",
    label: "盘口深度薄",
    description: "买入侧 ask 和卖出侧 bid 的顶层盘口深度低于配置的验证金额和安全倍数"
  },
  {
    value: "EDGE_AFTER_SLIPPAGE_TOO_SMALL",
    label: "有效收益偏小",
    description: "扣除手续费、资金费率和额外信号滑点缓冲后，综合开仓收益低于最低有效开仓收益阈值"
  },
  {
    value: "TRANSIENT_SIGNAL",
    label: "信号不稳定",
    description: "近期观测显示开仓价差衰减过快，信号稳定性不足"
  }
];

export const riskLabelName = new Map(riskLabelOptions.map((item) => [item.value, item.label]));
export const riskLabelDescription = new Map(
  riskLabelOptions.map((item) => [item.value, item.description])
);

export const defaultHiddenRiskLabels = [
  "LOW_VOLUME",
  "STALE_DATA",
  "HUGE_SPREAD_VERIFY",
  "WIDE_SPREAD",
  "SAME_TICKER_RISK",
  "MISSING_FUNDING",
  "THIN_ORDER_BOOK",
  "EDGE_AFTER_SLIPPAGE_TOO_SMALL",
  "TRANSIENT_SIGNAL"
];
