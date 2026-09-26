import {
  AreaChartOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SearchOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getFundingResearchLegacyBacktest,
  getFundingResearchPaperTradeSummary,
  listFundingResearchCandidateSnapshots,
  listFundingResearchCandidates,
  listFundingResearchPaperTrades,
  runFundingResearch
} from "../api/client";
import type {
  FundingResearchBasisAlignment,
  FundingResearchCandidate,
  FundingResearchCandidateSnapshot,
  FundingResearchDecision,
  FundingResearchFormulaConfidence,
  FundingResearchLegacyBacktestQuery,
  FundingResearchLegacyBacktestSummary,
  FundingResearchPaperTrade,
  FundingResearchPaperTradeStatus,
  FundingResearchPaperTradeSummary,
  FundingResearchRunResult
} from "../api/types";

dayjs.extend(utc);

const defaultBacktestQuery: Required<Omit<FundingResearchLegacyBacktestQuery, "symbol">> = {
  hours: 168,
  limit: 10000,
  min_entry_edge_pct: 1,
  min_next_funding_pct: 0.8,
  cost_pct: 0.35,
  max_hold_observations: 2
};

const decisionColor: Record<FundingResearchDecision, string> = {
  TRADE: "green",
  SMALL_TRADE: "cyan",
  WATCH: "gold",
  NO_TRADE: "default"
};

const decisionText: Record<FundingResearchDecision, string> = {
  TRADE: "可交易",
  SMALL_TRADE: "小仓试单",
  WATCH: "观察",
  NO_TRADE: "不做"
};

const basisAlignmentText: Record<FundingResearchBasisAlignment, string> = {
  aligned: "价差顺向",
  neutral: "价差中性",
  conflicted: "价差逆向"
};

const fundingSourceText: Record<FundingResearchFormulaConfidence, string> = {
  formula: "公式估算",
  predicted: "下一期预测",
  fallback_current: "当前费率回退",
  missing: "缺失",
  uncertain: "不确定"
};

const paperTradeStatusText: Record<FundingResearchPaperTradeStatus, string> = {
  OPEN: "持仓中",
  CLOSED: "已平仓"
};

const riskLabelText: Record<string, string> = {
  LOW_VOLUME: "成交量偏低",
  THIN_DEPTH: "入场深度不足",
  FUNDING_UNCERTAIN: "资金费率不确定",
  MISSING_SETTLEMENT: "缺少结算时间",
  SETTLEMENT_CROWDING: "临近结算拥挤"
};

const reasonText: Record<string, string> = {
  "missing funding estimate": "缺少资金费率估计",
  "missing settlement time": "缺少结算时间",
  "outside settlement window": "不在结算窗口",
  "weak leg volume too low": "弱边成交量过低",
  "entry depth too thin": "目标仓位入场深度不足",
  "entry depth below full safety multiple": "低于完整安全深度倍数"
};

const exitReasonText: Record<string, string> = {
  settlement_reached: "已到结算时间",
  candidate_missing: "候选信号消失",
  funding_edge_gone: "资金费率优势消失",
  ev_deteriorated: "期望收益恶化",
  basis_conflicted: "价差转为逆向"
};

function pct(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "-";
}

function signedPct(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function usdt(value: number | null | undefined, digits = 0): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)} USDT` : "-";
}

function timeText(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm") : "-";
}

function candidateKey(candidate: FundingResearchCandidate): string {
  return [
    candidate.symbol,
    candidate.long_exchange,
    candidate.short_exchange,
    candidate.next_settlement_time ?? "none"
  ].join(":");
}

function routeText(candidate: FundingResearchCandidate): string {
  return `${candidate.long_exchange} 做多 / ${candidate.short_exchange} 做空`;
}

function depthSourceText(value: string | null | undefined): string {
  if (value === "orderbook") {
    return "订单簿";
  }
  if (value === "ticker_top_of_book") {
    return "一档ticker";
  }
  return value ?? "-";
}

function labelList(values: string[], dictionary: Record<string, string> = {}): string {
  return values.length > 0 ? values.map((value) => dictionary[value] ?? value).join("，") : "-";
}

function friendlyErrorMessage(exc: unknown): string {
  const raw = exc instanceof Error ? exc.message : String(exc);
  if (raw.includes("404") || raw.includes("Not Found") || raw.includes('"detail"')) {
    return "资金研究接口未找到。请确认页面连接的是当前后端服务，例如 3010 前端代理到 8010 后端；旧 8000 后端没有这个接口。";
  }
  return raw;
}

function TrendChart({ snapshots }: { snapshots: FundingResearchCandidateSnapshot[] }) {
  const width = 920;
  const height = 280;
  const padding = { top: 18, right: 56, bottom: 34, left: 52 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const fields = [
    { key: "ev_pct" as const, label: "期望值", className: "research-line-ev" },
    {
      key: "expected_net_funding_pct" as const,
      label: "资金费",
      className: "research-line-funding"
    },
    { key: "basis_diff_pct" as const, label: "价差", className: "research-line-basis" }
  ];
  const rows = snapshots.filter((item) =>
    fields.some(({ key }) => typeof item.candidate[key] === "number")
  );
  if (rows.length === 0) {
    return <div className="research-empty">暂无趋势样本</div>;
  }
  const values = rows.flatMap((item) =>
    fields
      .map(({ key }) => item.candidate[key])
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value))
  );
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = rawMax - rawMin || 1;
  const min = rawMin - span * 0.14;
  const max = rawMax + span * 0.14;
  const xAt = (index: number) =>
    padding.left + (rows.length === 1 ? chartWidth / 2 : (chartWidth * index) / (rows.length - 1));
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const pathFor = (key: (typeof fields)[number]["key"]) =>
    rows
      .map((item, index) => {
        const value = item.candidate[key];
        if (typeof value !== "number" || !Number.isFinite(value)) {
          return "";
        }
        return `${index === 0 ? "M" : "L"} ${xAt(index).toFixed(1)} ${yAt(value).toFixed(1)}`;
      })
      .filter(Boolean)
      .join(" ");

  return (
    <div className="research-chart-wrap">
      <svg className="research-chart" role="img" aria-label="资金研究趋势图" viewBox={`0 0 ${width} ${height}`}>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="research-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="research-axis-label" x={padding.left - 8} y={y + 4} textAnchor="end">
                {pct(value)}
              </text>
            </g>
          );
        })}
        {fields.map((field) => (
          <path key={field.key} className={`research-line ${field.className}`} d={pathFor(field.key)} />
        ))}
        <text className="research-axis-label" x={padding.left} y={height - 10}>
          {dayjs.utc(rows[0].observed_at).format("MM-DD HH:mm")}
        </text>
        <text className="research-axis-label" x={padding.left + chartWidth} y={height - 10} textAnchor="end">
          {dayjs.utc(rows[rows.length - 1].observed_at).format("MM-DD HH:mm")}
        </text>
      </svg>
      <div className="research-chart-legend">
        {fields.map((field) => (
          <span key={field.key} className={field.className}>
            {field.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function buildCandidateColumns(onSelect: (candidate: FundingResearchCandidate) => void): ColumnsType<FundingResearchCandidate> {
  return [
    {
      title: "结论",
      dataIndex: "decision",
      fixed: "left",
      width: 112,
      render: (value: FundingResearchDecision) => <Tag color={decisionColor[value]}>{decisionText[value]}</Tag>
    },
    {
      title: "标的",
      dataIndex: "symbol",
      fixed: "left",
      width: 128,
      render: (value: string, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text strong>{value}</Typography.Text>
          <Typography.Text type="secondary">{fundingSourceText[row.funding_source]}</Typography.Text>
        </Space>
      )
    },
    {
      title: "路线",
      width: 220,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text>{row.long_exchange} 做多</Typography.Text>
          <Typography.Text>{row.short_exchange} 做空</Typography.Text>
        </Space>
      )
    },
    {
      title: "期望值",
      dataIndex: "ev_pct",
      width: 96,
      align: "right",
      sorter: (a, b) => (a.ev_pct ?? -999) - (b.ev_pct ?? -999),
      defaultSortOrder: "descend",
      render: (value: number | null) => (
        <Typography.Text strong type={(value ?? 0) >= 0 ? "success" : "danger"}>
          {signedPct(value)}
        </Typography.Text>
      )
    },
    {
      title: "评分",
      dataIndex: "score",
      width: 86,
      align: "right",
      sorter: (a, b) => a.score - b.score,
      render: (value: number) => value.toFixed(1)
    },
    {
      title: "资金费",
      width: 156,
      align: "right",
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text>{signedPct(row.expected_net_funding_pct)}</Typography.Text>
          <Typography.Text type="secondary">
            {pct(row.long_funding_pct)} / {pct(row.short_funding_pct)}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "价差",
      width: 144,
      align: "right",
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Tag color={row.basis_alignment === "aligned" ? "green" : row.basis_alignment === "conflicted" ? "red" : "blue"}>
            {basisAlignmentText[row.basis_alignment]}
          </Tag>
          <Typography.Text type="secondary">{signedPct(row.basis_diff_pct)}</Typography.Text>
        </Space>
      )
    },
    {
      title: "成本/风险/深度",
      width: 170,
      align: "right",
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text>
            {pct(row.estimated_cost_pct)} / {pct(row.risk_buffer_pct)}
          </Typography.Text>
          <Typography.Text type="secondary">{usdt(row.depth_stats?.min_entry_depth_usdt)}</Typography.Text>
          <Typography.Text type="secondary">
            {depthSourceText(row.depth_stats?.source)}
            {row.depth_stats ? ` ${row.depth_stats.levels}档` : ""}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "结算",
      width: 136,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text>{timeText(row.next_settlement_time)}</Typography.Text>
          <Typography.Text type="secondary">
            {typeof row.minutes_to_settlement === "number" ? `${row.minutes_to_settlement.toFixed(0)} 分钟` : "-"}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "趋势",
      width: 74,
      render: (_, row) => (
        <Button type="text" icon={<AreaChartOutlined />} onClick={() => onSelect(row)} aria-label={`查看趋势 ${row.symbol}`} />
      )
    }
  ];
}

const tradeColumns: ColumnsType<FundingResearchPaperTrade> = [
  {
    title: "状态",
    dataIndex: "status",
    width: 86,
    render: (value: FundingResearchPaperTradeStatus) => (
      <Tag color={value === "OPEN" ? "blue" : "default"}>{paperTradeStatusText[value]}</Tag>
    )
  },
  { title: "标的", dataIndex: "symbol", width: 120 },
  {
    title: "路线",
    width: 210,
    render: (_, row) => `${row.long_exchange} 做多 / ${row.short_exchange} 做空`
  },
  {
    title: "预期收益",
    dataIndex: "expected_ev_pct",
    align: "right",
    width: 112,
    render: (value: number | null) => signedPct(value)
  },
  {
    title: "已实现盈亏",
    dataIndex: "realized_pnl_pct",
    align: "right",
    width: 112,
    render: (value: number | null) => signedPct(value)
  },
  {
    title: "开仓时间",
    dataIndex: "opened_at",
    width: 124,
    render: (value: string) => timeText(value)
  },
  {
    title: "退出原因",
    dataIndex: "exit_reason",
    render: (value: string | null) => (value ? exitReasonText[value] ?? value : "-")
  }
];

type BacktestForm = Required<FundingResearchLegacyBacktestQuery>;

export function FundingResearchPage() {
  const [form] = Form.useForm<BacktestForm>();
  const [candidates, setCandidates] = useState<FundingResearchCandidate[]>([]);
  const [selected, setSelected] = useState<FundingResearchCandidate | null>(null);
  const [snapshots, setSnapshots] = useState<FundingResearchCandidateSnapshot[]>([]);
  const [paperTrades, setPaperTrades] = useState<FundingResearchPaperTrade[]>([]);
  const [paperSummary, setPaperSummary] = useState<FundingResearchPaperTradeSummary | null>(null);
  const [backtest, setBacktest] = useState<FundingResearchLegacyBacktestSummary | null>(null);
  const [lastRun, setLastRun] = useState<FundingResearchRunResult | null>(null);
  const [symbolFilter, setSymbolFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [error, setError] = useState("");

  const loadCore = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextCandidates, nextTrades, nextSummary] = await Promise.all([
        listFundingResearchCandidates({ symbol: symbolFilter, limit: 250 }),
        listFundingResearchPaperTrades({ limit: 500 }),
        getFundingResearchPaperTradeSummary(1000)
      ]);
      setCandidates(nextCandidates);
      setPaperTrades(nextTrades);
      setPaperSummary(nextSummary);
      setSelected((current) =>
        current && nextCandidates.find((item) => candidateKey(item) === candidateKey(current))
          ? current
          : nextCandidates[0] ?? null
      );
    } catch (exc) {
      setError(friendlyErrorMessage(exc));
    } finally {
      setLoading(false);
    }
  }, [symbolFilter]);

  const loadSnapshots = useCallback(async (candidate: FundingResearchCandidate | null) => {
    if (!candidate) {
      setSnapshots([]);
      return;
    }
    setSnapshots(
      await listFundingResearchCandidateSnapshots({
        symbol: candidate.symbol,
        long_exchange: candidate.long_exchange,
        short_exchange: candidate.short_exchange,
        limit: 240
      })
    );
  }, []);

  const loadBacktest = useCallback(async () => {
    setBacktestLoading(true);
    try {
      const values = form.getFieldsValue(true);
      setBacktest(await getFundingResearchLegacyBacktest(values));
    } catch (exc) {
      setError(friendlyErrorMessage(exc));
    } finally {
      setBacktestLoading(false);
    }
  }, [form]);

  useEffect(() => {
    form.setFieldsValue(defaultBacktestQuery);
    void loadCore();
  }, [form, loadCore]);

  useEffect(() => {
    void loadSnapshots(selected);
  }, [loadSnapshots, selected]);

  const runScan = async () => {
    setRunning(true);
    setError("");
    try {
      const result = await runFundingResearch({ manage_paper_trades: true, snapshot_retention_hours: 72 });
      setLastRun(result);
      message.success(`已记录 ${result.candidate_snapshot_count} 个候选快照`);
      await loadCore();
    } catch (exc) {
      setError(friendlyErrorMessage(exc));
    } finally {
      setRunning(false);
    }
  };

  const columns = useMemo(() => buildCandidateColumns(setSelected), []);
  const tradeWinRate = paperSummary?.win_rate_pct ?? null;
  const tradeAvg = paperSummary?.average_realized_pnl_pct ?? null;

  return (
    <div className="page research-page">
      {error ? <Alert type="error" showIcon message={error} /> : null}

      <section className="toolbar">
        <div className="toolbar-controls research-toolbar-grid">
          <Typography.Title level={4}>资金费率研究</Typography.Title>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="输入标的"
            value={symbolFilter}
            onChange={(event) => setSymbolFilter(event.target.value.toUpperCase())}
            onPressEnter={() => void loadCore()}
          />
        </div>
        <div className="toolbar-actions">
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void loadCore()} aria-label="刷新" />
            <Button type="primary" icon={<PlayCircleOutlined />} loading={running} onClick={() => void runScan()}>
              执行扫描
            </Button>
          </Space>
        </div>
      </section>

      <section className="metric-row research-metrics">
        <Statistic title="候选数" value={candidates.length} />
        <Statistic title="可交易" value={candidates.filter((item) => item.decision === "TRADE").length} />
        <Statistic title="观察中" value={candidates.filter((item) => item.decision === "WATCH").length} />
        <Statistic title="模拟持仓" value={paperSummary?.open_trades ?? 0} />
        <Statistic title="胜率" value={tradeWinRate ?? 0} precision={2} suffix="%" />
        <Statistic title="平均收益" value={tradeAvg ?? 0} precision={3} suffix="%" />
      </section>

      {lastRun ? (
        <section className="blocked-strip">
          <span className="blocked-strip-title">最近扫描</span>
          <Space wrap>
            <Tag>{timeText(lastRun.observed_at)}</Tag>
            <Tag color="blue">市场快照 {lastRun.market_snapshot_count}</Tag>
            <Tag color="purple">候选快照 {lastRun.candidate_snapshot_count}</Tag>
            <Tag color="default">清理 {lastRun.pruned_snapshot_count}</Tag>
          </Space>
        </section>
      ) : null}

      <section className="research-grid">
        <div className="panel research-chart-panel">
          <div className="research-panel-head">
            <Space size={8} wrap>
              <Typography.Title level={5}>{selected?.symbol ?? "暂无候选"}</Typography.Title>
              {selected ? <Tag>{routeText(selected)}</Tag> : null}
              {selected ? <Tag color={decisionColor[selected.decision]}>{decisionText[selected.decision]}</Tag> : null}
            </Space>
            {selected ? <Typography.Text type="secondary">评分 {selected.score.toFixed(1)}</Typography.Text> : null}
          </div>
          <TrendChart snapshots={snapshots} />
        </div>

        <div className="panel research-backtest-panel">
          <div className="research-panel-head">
            <Typography.Title level={5}>历史回测</Typography.Title>
            <Button size="small" icon={<SearchOutlined />} loading={backtestLoading} onClick={() => void loadBacktest()}>
              执行回测
            </Button>
          </div>
          <Form form={form} layout="vertical" initialValues={defaultBacktestQuery}>
            <div className="research-backtest-grid">
              <Form.Item label="回测窗口（小时）" name="hours">
                <InputNumber min={1} className="wide-input" />
              </Form.Item>
              <Form.Item label="样本上限" name="limit">
                <InputNumber min={1} className="wide-input" />
              </Form.Item>
              <Form.Item label="入场边际" name="min_entry_edge_pct">
                <InputNumber step={0.1} suffix="%" className="wide-input" />
              </Form.Item>
              <Form.Item label="下一期资金费" name="min_next_funding_pct">
                <InputNumber step={0.1} suffix="%" className="wide-input" />
              </Form.Item>
              <Form.Item label="交易成本" name="cost_pct">
                <InputNumber step={0.05} suffix="%" className="wide-input" />
              </Form.Item>
              <Form.Item label="持仓观测数" name="max_hold_observations">
                <InputNumber min={1} className="wide-input" />
              </Form.Item>
            </div>
          </Form>
          <div className="research-backtest-stats">
            <Statistic title="交易数" value={backtest?.trades ?? 0} />
            <Statistic title="胜率" value={backtest?.win_rate_pct ?? 0} precision={2} suffix="%" />
            <Statistic title="平均收益" value={backtest?.average_pnl_pct ?? 0} precision={3} suffix="%" />
            <Statistic title="最大亏损" value={backtest?.max_loss_pct ?? 0} precision={3} suffix="%" />
          </div>
        </div>
      </section>

      <Table<FundingResearchCandidate>
        className="opportunity-table research-table"
        columns={columns}
        dataSource={candidates}
        loading={loading}
        rowKey={candidateKey}
        pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
        scroll={{ x: 1274 }}
        size="small"
        tableLayout="fixed"
        onRow={(record) => ({
          onClick: () => setSelected(record)
        })}
        expandable={{
          expandedRowRender: (row) => (
            <div className="research-expanded-row">
              <div>
                <Typography.Text type="secondary">风险标签</Typography.Text>
                <Typography.Text>{labelList(row.risk_labels, riskLabelText)}</Typography.Text>
              </div>
              <div>
                <Typography.Text type="secondary">判断原因</Typography.Text>
                <Typography.Text>{labelList(row.reasons, reasonText)}</Typography.Text>
              </div>
              <div>
                <Typography.Text type="secondary">深度统计</Typography.Text>
                <Typography.Text>
                  来源 {depthSourceText(row.depth_stats?.source)}；
                  档位 {row.depth_stats?.levels ?? "-"}；
                  多头入场 {usdt(row.depth_stats?.long_entry_depth_usdt)}；
                  空头入场 {usdt(row.depth_stats?.short_entry_depth_usdt)}；
                  目标 {usdt(row.depth_stats?.target_notional_usdt)}；
                  VWAP滑点 {signedPct(row.depth_stats?.slippage_loss_pct)}
                </Typography.Text>
              </div>
            </div>
          )
        }}
      />

      <section className="panel panel-wide">
        <div className="research-panel-head">
          <Typography.Title level={5}>模拟交易</Typography.Title>
          <Space wrap>
            <Tag color="blue">持仓 {paperSummary?.open_trades ?? 0}</Tag>
            <Tag>平仓 {paperSummary?.closed_trades ?? 0}</Tag>
            <Tag color="green">累计 {signedPct(paperSummary?.total_realized_pnl_pct)}</Tag>
          </Space>
        </div>
        <Table<FundingResearchPaperTrade>
          className="research-paper-table"
          columns={tradeColumns}
          dataSource={paperTrades}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 8, showTotal: (total) => `共 ${total} 条` }}
          tableLayout="fixed"
          scroll={{ x: 980 }}
        />
      </section>
    </div>
  );
}
