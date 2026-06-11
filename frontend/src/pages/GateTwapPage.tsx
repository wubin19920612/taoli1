import {
  ClockCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Statistic,
  Switch,
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
  cancelGateTwapJob,
  getGateTwapMarket,
  listGateTwapJobs,
  previewGateTwap,
  startGateTwapJob
} from "../api/client";
import type {
  GateTwapJobStatus,
  GateTwapMarketSnapshot,
  GateTwapPlan,
  GateTwapPlanSlice,
  GateTwapRequest,
  GateTwapRunRequest,
  GateTwapSide,
  GateTwapSliceMode
} from "../api/types";

dayjs.extend(utc);

type GateTwapFormValues = {
  contract: string;
  settle: string;
  side: GateTwapSide;
  start_at?: string;
  interval_seconds: number;
  duration_seconds: number;
  percent: number;
  slice_mode: GateTwapSliceMode;
  initial_size?: number | null;
  last_order_all: boolean;
  slip_ratio?: number | null;
  client_prefix: string;
  live: boolean;
  confirm_live: boolean;
};

const defaultValues: GateTwapFormValues = {
  contract: "SKHYNIX_USDT",
  settle: "usdt",
  side: "sell",
  start_at: "",
  interval_seconds: 10,
  duration_seconds: 1000,
  percent: 1,
  slice_mode: "initial",
  initial_size: undefined,
  last_order_all: true,
  slip_ratio: undefined,
  client_prefix: "t-twap",
  live: false,
  confirm_live: false
};

const jobStateColor: Record<GateTwapJobStatus["state"], string> = {
  queued: "blue",
  running: "cyan",
  completed: "green",
  failed: "red",
  cancelled: "default"
};

function normalizeContract(value: string): string {
  const raw = value.trim().toUpperCase().replace("-", "_");
  return !raw.includes("_") && raw.endsWith("USDT") ? `${raw.slice(0, -4)}_USDT` : raw;
}

function valueOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pct(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "-";
}

function signedPct(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function price(value: number | null | undefined, digits = 4): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return value >= 100 ? value.toFixed(2) : value.toFixed(digits);
}

function sizeText(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

function compactUsdt(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return value.toFixed(0);
}

function timeText(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function requestFromForm(values: GateTwapFormValues): GateTwapRequest {
  const start = values.start_at?.trim();
  return {
    contract: normalizeContract(values.contract),
    settle: values.settle.trim().toLowerCase() || "usdt",
    side: values.side,
    start_at: start ? dayjs(start).toISOString() : null,
    interval_seconds: values.interval_seconds,
    duration_seconds: values.duration_seconds,
    percent: values.percent,
    slice_mode: values.slice_mode,
    initial_size: valueOrNull(values.initial_size),
    last_order_all: values.last_order_all,
    slip_ratio: valueOrNull(values.slip_ratio),
    client_prefix: values.client_prefix || "t-twap",
    action_mode: "ACK"
  };
}

function MarketMetrics({ market }: { market: GateTwapMarketSnapshot | null }) {
  return (
    <div className="gate-market-grid">
      <div>
        <Statistic title="合约买一 / 卖一" value={`${price(market?.future?.bid)} / ${price(market?.future?.ask)}`} />
        <Typography.Text type="secondary">
          {`数量 ${sizeText(market?.future?.bid_size)} / ${sizeText(market?.future?.ask_size)}`}
        </Typography.Text>
      </div>
      <div>
        <Statistic title="标记 / 指数" value={`${price(market?.mark_price)} / ${price(market?.index_price)}`} />
        <Typography.Text type={market?.mark_index_premium_pct && market.mark_index_premium_pct > 0 ? "success" : "secondary"}>
          {`溢价 ${signedPct(market?.mark_index_premium_pct)}`}
        </Typography.Text>
      </div>
      <div>
        <Statistic title="资金费率" value={`${signedPct(market?.funding_rate_pct)} / ${signedPct(market?.funding_next_rate_pct)}`} />
        <Typography.Text type="secondary">
          {`${market?.funding_interval_hours ?? "-"}h  ${timeText(market?.funding_next_time)} UTC+8`}
        </Typography.Text>
      </div>
      <div>
        <Statistic title="现货买一 / 卖一" value={`${price(market?.spot?.bid)} / ${price(market?.spot?.ask)}`} />
        <Typography.Text type="secondary">
          {market?.spot_available ? `24h ${compactUsdt(market.spot?.volume_24h_usdt)} USDT` : "Gate 现货无该交易对"}
        </Typography.Text>
      </div>
      <div>
        <Statistic title="合约中价 vs 指数" value={signedPct(market?.future_index_premium_pct)} />
        <Typography.Text type="secondary">{`合约 24h ${compactUsdt(market?.future?.volume_24h_usdt)} USDT`}</Typography.Text>
      </div>
      <div>
        <Statistic title="交易规则" value={`min ${sizeText(market?.order_size_min)} / step ${sizeText(market?.order_size_step)}`} />
        <Typography.Text type="secondary">{`市价滑点上限 ${pct((market?.market_order_slip_ratio ?? 0) * 100, 2)}`}</Typography.Text>
      </div>
    </div>
  );
}

const planColumns: ColumnsType<GateTwapPlanSlice> = [
  { title: "#", dataIndex: "index", width: 62 },
  {
    title: "计划时间",
    dataIndex: "scheduled_at",
    width: 150,
    render: (value: string) => timeText(value)
  },
  {
    title: "原始数量",
    dataIndex: "raw_size",
    align: "right",
    render: (value: number) => sizeText(value)
  },
  {
    title: "下单数量",
    dataIndex: "signed_order_size",
    align: "right",
    render: (value: number, row) => (
      <Typography.Text type={row.skipped_reason ? "secondary" : value < 0 ? "danger" : "success"}>
        {sizeText(value)}
      </Typography.Text>
    )
  },
  {
    title: "剩余",
    dataIndex: "remaining_after",
    align: "right",
    render: (value: number) => sizeText(value)
  },
  {
    title: "状态",
    dataIndex: "skipped_reason",
    width: 180,
    render: (value: string | null) => (value ? <Tag color="orange">{value}</Tag> : <Tag color="green">ready</Tag>)
  }
];

function buildJobColumns(onCancel: (job: GateTwapJobStatus) => void): ColumnsType<GateTwapJobStatus> {
  return [
    {
      title: "任务",
      dataIndex: "job_id",
      width: 136,
      render: (value: string, row) => (
        <Space direction="vertical" size={2}>
          <Typography.Text code>{value}</Typography.Text>
          <Tag color={row.live ? "red" : "blue"}>{row.live ? "LIVE" : "DRY"}</Tag>
        </Space>
      )
    },
    {
      title: "状态",
      dataIndex: "state",
      width: 104,
      render: (value: GateTwapJobStatus["state"]) => <Tag color={jobStateColor[value]}>{value}</Tag>
    },
    { title: "合约", dataIndex: ["request", "contract"], width: 130 },
    {
      title: "进度",
      width: 140,
      render: (_, row) => `${row.completed_orders}/${row.plan?.order_count ?? 0}`
    },
    {
      title: "已计划数量",
      dataIndex: "total_order_size",
      align: "right",
      width: 120,
      render: (value: number) => sizeText(value)
    },
    {
      title: "开始",
      dataIndex: "started_at",
      width: 140,
      render: (value: string | null) => timeText(value)
    },
    {
      title: "操作",
      width: 84,
      render: (_, row) =>
        row.state === "queued" || row.state === "running" ? (
          <Button danger size="small" icon={<StopOutlined />} onClick={() => onCancel(row)} />
        ) : null
    }
  ];
}

export function GateTwapPage() {
  const [form] = Form.useForm<GateTwapFormValues>();
  const [market, setMarket] = useState<GateTwapMarketSnapshot | null>(null);
  const [plan, setPlan] = useState<GateTwapPlan | null>(null);
  const [jobs, setJobs] = useState<GateTwapJobStatus[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  const formContract = Form.useWatch("contract", form) ?? defaultValues.contract;
  const formSettle = Form.useWatch("settle", form) ?? defaultValues.settle;
  const formLive = Form.useWatch("live", form) ?? false;

  const loadMarket = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setMarket(
        await getGateTwapMarket({
          contract: normalizeContract(form.getFieldValue("contract") ?? defaultValues.contract),
          settle: form.getFieldValue("settle") ?? defaultValues.settle
        })
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [form]);

  const loadJobs = useCallback(async () => {
    try {
      setJobs(await listGateTwapJobs());
    } catch {
      // Keep the page focused on the market/task form; job refresh failures are non-fatal.
    }
  }, []);

  useEffect(() => {
    form.setFieldsValue(defaultValues);
    void loadMarket();
    void loadJobs();
  }, [form, loadJobs, loadMarket]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadMarket();
      void loadJobs();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadJobs, loadMarket, formContract, formSettle]);

  const preview = async () => {
    setPreviewing(true);
    setError("");
    try {
      const values = await form.validateFields();
      const nextPlan = await previewGateTwap(requestFromForm(values));
      setPlan(nextPlan);
      message.success("预览已生成");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setPreviewing(false);
    }
  };

  const start = async () => {
    setStarting(true);
    setError("");
    try {
      const values = await form.validateFields();
      const payload: GateTwapRunRequest = {
        ...requestFromForm(values),
        live: Boolean(values.live),
        confirm_live: Boolean(values.confirm_live)
      };
      if (payload.live && !payload.confirm_live) {
        throw new Error("Live 任务需要勾选确认开关。");
      }
      const run = async () => {
        const job = await startGateTwapJob(payload);
        setJobs(await listGateTwapJobs());
        message.success(`${payload.live ? "Live" : "Dry-run"} 任务已启动: ${job.job_id}`);
      };
      if (payload.live) {
        Modal.confirm({
          title: "确认启动 Gate 真实减仓任务",
          content: "该任务会向 Gate 发送 reduce-only 市价 IOC 订单。确认前请再次核对合约、方向、仓位和开始时间。",
          okText: "确认启动",
          okButtonProps: { danger: true },
          cancelText: "取消",
          onOk: run
        });
      } else {
        await run();
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setStarting(false);
    }
  };

  const cancelJob = async (job: GateTwapJobStatus) => {
    try {
      await cancelGateTwapJob(job.job_id);
      setJobs(await listGateTwapJobs());
      message.success("任务已取消");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const jobColumns = useMemo(() => buildJobColumns(cancelJob), []);

  return (
    <div className="page gate-twap-page">
      <div className="toolbar">
        <div className="toolbar-controls">
          <Typography.Title level={4}>Gate 定时减仓</Typography.Title>
          <Typography.Text type="secondary">
            参数预览、dry-run 任务和 Gate 标的行情监控在同一页，默认不会发送真实订单。
          </Typography.Text>
        </div>
        <div className="toolbar-actions">
          <Space>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={loadMarket}>
              刷新行情
            </Button>
            <Button icon={<ClockCircleOutlined />} loading={previewing} onClick={preview}>
              预览计划
            </Button>
            <Button type={formLive ? "primary" : "default"} danger={formLive} icon={<PlayCircleOutlined />} loading={starting} onClick={start}>
              {formLive ? "启动 Live" : "启动 Dry-run"}
            </Button>
          </Space>
        </div>
      </div>

      {error ? <Alert className="page-alert" type="error" showIcon message={error} /> : null}
      {formLive ? (
        <Alert
          className="page-alert"
          type="warning"
          showIcon
          message="Live 模式会真实调用 Gate 下单接口。建议先用 dry-run 完整跑一遍。"
        />
      ) : null}

      <div className="panel">
        <MarketMetrics market={market} />
      </div>

      <div className="panel">
        <Form form={form} layout="vertical" className="gate-twap-form" initialValues={defaultValues}>
          <Form.Item name="contract" label="Gate 合约" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="settle" label="结算币种" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="side" label="方向">
            <Segmented
              options={[
                { label: "卖出减多", value: "sell" },
                { label: "买入减空", value: "buy" }
              ]}
            />
          </Form.Item>
          <Form.Item name="start_at" label="开始时间">
            <Input placeholder="留空为立即开始，例如 2026-06-05 15:40:00" />
          </Form.Item>
          <Form.Item name="interval_seconds" label="间隔秒数" rules={[{ required: true }]}>
            <InputNumber min={1} precision={0} />
          </Form.Item>
          <Form.Item name="duration_seconds" label="总时长秒数" rules={[{ required: true }]}>
            <InputNumber min={1} precision={0} />
          </Form.Item>
          <Form.Item name="percent" label="每单比例 %" rules={[{ required: true }]}>
            <InputNumber min={0.0001} max={100} step={0.1} />
          </Form.Item>
          <Form.Item name="slice_mode" label="切片基准">
            <Segmented
              options={[
                { label: "初始仓位", value: "initial" },
                { label: "剩余仓位", value: "remaining" }
              ]}
            />
          </Form.Item>
          <Form.Item name="initial_size" label="手动初始张数">
            <InputNumber min={0} precision={4} placeholder="留空则用 Gate API 仓位" />
          </Form.Item>
          <Form.Item name="slip_ratio" label="市价滑点比例">
            <InputNumber min={0} max={0.2} step={0.001} precision={4} placeholder="留空用 Gate 默认" />
          </Form.Item>
          <Form.Item name="client_prefix" label="订单前缀">
            <Input />
          </Form.Item>
          <Form.Item name="last_order_all" valuePropName="checked" label="尾单处理">
            <Checkbox>最后一单卖出剩余仓位</Checkbox>
          </Form.Item>
          <Form.Item name="live" valuePropName="checked" label="执行模式">
            <Switch checkedChildren="LIVE" unCheckedChildren="DRY" />
          </Form.Item>
          <Form.Item name="confirm_live" valuePropName="checked" label="Live 确认">
            <Checkbox>我确认真实发送 Gate reduce-only 市价单</Checkbox>
          </Form.Item>
        </Form>
      </div>

      {plan ? (
        <div className="panel">
          <Space className="gate-plan-head" align="start">
            <div>
              <Typography.Title level={5}>计划预览</Typography.Title>
              <Typography.Text type="secondary">
                {`${plan.contract} ${plan.side}，${plan.order_count} 单，计划数量 ${sizeText(plan.total_planned_size)} 张`}
              </Typography.Text>
            </div>
            <Space wrap>
              <Tag color={plan.has_credentials ? "green" : "orange"}>
                {plan.has_credentials ? "已配置 API Key" : "未配置 API Key"}
              </Tag>
              <Tag>{`min ${sizeText(plan.rules.order_size_min)} / step ${sizeText(plan.rules.order_size_step)}`}</Tag>
            </Space>
          </Space>
          {plan.warnings.length > 0 ? (
            <Alert type="warning" showIcon className="page-alert" message={plan.warnings.join("; ")} />
          ) : null}
          <Table
            className="gate-plan-table"
            rowKey="index"
            size="small"
            columns={planColumns}
            dataSource={plan.slices}
            pagination={{ pageSize: 20, showSizeChanger: true }}
          />
        </div>
      ) : null}

      <div className="panel">
        <Typography.Title level={5}>任务</Typography.Title>
        <Table
          rowKey="job_id"
          size="small"
          columns={jobColumns}
          dataSource={jobs}
          pagination={{ pageSize: 8 }}
          expandable={{
            expandedRowRender: (job) => (
              <div className="gate-job-events">
                {job.events.length > 0 ? (
                  job.events.slice(-20).map((event) => (
                    <div key={`${event.at}-${event.message}`} className={`gate-job-event gate-job-event-${event.level}`}>
                      <Typography.Text type="secondary">{timeText(event.at)}</Typography.Text>
                      <Typography.Text>{event.message}</Typography.Text>
                      {event.order ? <Typography.Text code>{JSON.stringify(event.order)}</Typography.Text> : null}
                    </div>
                  ))
                ) : (
                  <Typography.Text type="secondary">暂无事件</Typography.Text>
                )}
              </div>
            )
          }}
        />
      </div>
    </div>
  );
}
