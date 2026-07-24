import {
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SaveOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  InputNumber,
  Row,
  Select,
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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getSecondLevelSamplingConfig,
  getSecondLevelSamplingStatus,
  listSecondLevelIndexComponentSamples,
  listSecondLevelSamples,
  listSecondLevelSamplingExchanges,
  stopSecondLevelSampling,
  updateSecondLevelSamplingConfig
} from "../api/client";
import type {
  SecondLevelIndexComponentSample,
  SecondLevelIndexComponentSignal,
  SecondLevelMarketSample,
  SecondLevelPairSpreadSnapshot,
  SecondLevelSamplingConfig,
  SecondLevelSamplingStatus
} from "../api/types";

dayjs.extend(utc);

const defaultConfig: SecondLevelSamplingConfig = {
  enabled: false,
  interval_seconds: 1,
  retention_hours: 48,
  exchanges: ["bybit", "bitget"],
  symbols: ["DEXEUSDT"],
  max_concurrent_requests: 8,
  capture_index_components: true,
  component_signal_window_seconds: 10
};

function numberText(value: number | null | undefined, digits = 6): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "-";
}

function pctText(value: number | null | undefined, digits = 4): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "-";
}

function timeText(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function statusTag(status: SecondLevelMarketSample["status"]) {
  const color = status === "ok" ? "green" : status === "partial" ? "gold" : "red";
  const text = status === "ok" ? "正常" : status === "partial" ? "部分" : "失败";
  return <Tag color={color}>{text}</Tag>;
}

function signalLevelTag(level: SecondLevelIndexComponentSignal["signal_level"]) {
  const color = level === "high" ? "red" : level === "medium" ? "gold" : "blue";
  const text = level === "high" ? "强痕迹" : level === "medium" ? "观察" : "轻微";
  return <Tag color={color}>{text}</Tag>;
}

function normalizeSymbolInput(values: string[]): string[] {
  return values
    .map((value) => value.trim().toUpperCase().replace(/[-_/]/g, ""))
    .filter(Boolean)
    .map((value) => (value.endsWith("USDT") ? value : `${value}USDT`));
}

function componentDetailFingerprint(samples: SecondLevelIndexComponentSample[]): string {
  const latestByComponent = new Map<string, SecondLevelIndexComponentSample>();
  [...samples]
    .sort((left, right) => dayjs(right.observed_at).valueOf() - dayjs(left.observed_at).valueOf())
    .forEach((sample) => {
      const key = [
        sample.target_exchange,
        sample.symbol,
        sample.component_source,
        sample.component_symbol
      ].join(":");
      if (!latestByComponent.has(key)) {
        latestByComponent.set(key, sample);
      }
    });
  return Array.from(latestByComponent.values())
    .map((sample) =>
      [
        sample.target_exchange,
        sample.symbol,
        sample.component_source,
        sample.component_symbol,
        sample.weight_pct?.toFixed(8) ?? "",
        sample.error ? "error" : "ok"
      ].join(":")
    )
    .sort()
    .join("|");
}

function filterComponentSamples(
  samples: SecondLevelIndexComponentSample[],
  symbol: string,
  exchange?: string
): SecondLevelIndexComponentSample[] {
  return samples.filter(
    (sample) => sample.symbol === symbol && (!exchange || sample.target_exchange === exchange)
  );
}

export function SecondLevelSamplingPage() {
  const [exchangeOptions, setExchangeOptions] = useState<string[]>([]);
  const [draft, setDraft] = useState<SecondLevelSamplingConfig>(defaultConfig);
  const [status, setStatus] = useState<SecondLevelSamplingStatus | null>(null);
  const [samples, setSamples] = useState<SecondLevelMarketSample[]>([]);
  const [componentSamples, setComponentSamples] = useState<SecondLevelIndexComponentSample[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string>("DEXEUSDT");
  const [selectedExchange, setSelectedExchange] = useState<string | undefined>();
  const [minutes, setMinutes] = useState<number>(30);
  const [loading, setLoading] = useState(false);
  const [componentLoading, setComponentLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const componentFingerprintRef = useRef<string | null>(null);

  const refreshComponentSamples = useCallback(
    async (knownFingerprint?: string) => {
      setComponentLoading(true);
      try {
        const nextComponentSamples = await listSecondLevelIndexComponentSamples({
          symbol: selectedSymbol,
          target_exchange: selectedExchange,
          minutes,
          limit: 1000
        });
        setComponentSamples(nextComponentSamples);
        componentFingerprintRef.current = knownFingerprint ?? componentDetailFingerprint(nextComponentSamples);
      } catch (exc) {
        message.error(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setComponentLoading(false);
      }
    },
    [minutes, selectedExchange, selectedSymbol]
  );

  const refresh = useCallback(async (options: { forceComponentDetails?: boolean } = {}) => {
    setLoading(true);
    try {
      const [nextStatus, nextSamples] = await Promise.all([
        getSecondLevelSamplingStatus(),
        listSecondLevelSamples({
          symbol: selectedSymbol,
          exchange: selectedExchange,
          minutes,
          limit: 1000
        })
      ]);
      setStatus(nextStatus);
      setSamples(nextSamples);
      const nextComponentFingerprint = componentDetailFingerprint(
        filterComponentSamples(nextStatus.latest_component_samples ?? [], selectedSymbol, selectedExchange)
      );
      if (
        options.forceComponentDetails ||
        componentFingerprintRef.current === null ||
        nextComponentFingerprint !== componentFingerprintRef.current
      ) {
        await refreshComponentSamples(nextComponentFingerprint);
      }
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [minutes, refreshComponentSamples, selectedExchange, selectedSymbol]);

  useEffect(() => {
    let alive = true;
    Promise.all([
      listSecondLevelSamplingExchanges(),
      getSecondLevelSamplingConfig(),
      getSecondLevelSamplingStatus()
    ])
      .then(([exchanges, config, nextStatus]) => {
        if (!alive) {
          return;
        }
        setExchangeOptions(exchanges);
        setDraft(config);
        setStatus(nextStatus);
        setSelectedSymbol(config.symbols[0] ?? "DEXEUSDT");
      })
      .catch((exc) => {
        message.error(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      alive = false;
    };
  }, []);

  const isRunning = status?.running ?? false;
  const pollingMs = Math.max(1000, Math.round((status?.config.interval_seconds ?? draft.interval_seconds) * 1000));

  useEffect(() => {
    void refresh({ forceComponentDetails: true });
  }, [refresh]);

  useEffect(() => {
    if (!isRunning) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void refresh();
    }, pollingMs);
    return () => window.clearInterval(timer);
  }, [isRunning, pollingMs, refresh]);

  const saveConfig = useCallback(
    async (enabled = draft.enabled) => {
      setSaving(true);
      try {
        const payload: SecondLevelSamplingConfig = {
          ...draft,
          enabled,
          symbols: normalizeSymbolInput(draft.symbols)
        };
        const saved = await updateSecondLevelSamplingConfig(payload);
        setDraft(saved);
        setSelectedSymbol(saved.symbols[0] ?? selectedSymbol);
        message.success(enabled ? "采样已启动" : "配置已保存");
        await refresh({ forceComponentDetails: true });
      } catch (exc) {
        message.error(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setSaving(false);
      }
    },
    [draft, refresh, selectedSymbol]
  );

  const stopSampling = useCallback(async () => {
    setSaving(true);
    try {
      const nextStatus = await stopSecondLevelSampling();
      setStatus(nextStatus);
      setDraft(nextStatus.config);
      message.success("采样已暂停");
      await refresh({ forceComponentDetails: true });
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }, [refresh]);

  const latestSpreads = status?.latest_spreads ?? [];
  const componentSignals = status?.latest_component_signals ?? [];
  const symbolOptions = useMemo(
    () =>
      Array.from(
        new Set([
          ...draft.symbols,
          ...samples.map((item) => item.symbol),
          ...componentSamples.map((item) => item.symbol)
        ])
      ).filter(Boolean),
    [draft.symbols, samples, componentSamples]
  );

  const spreadColumns: ColumnsType<SecondLevelPairSpreadSnapshot> = [
    {
      title: "标的",
      dataIndex: "symbol",
      key: "symbol",
      width: 110
    },
    {
      title: "路径",
      key: "route",
      render: (_, row) => `${row.left_exchange} / ${row.right_exchange}`
    },
    {
      title: "合约价差",
      dataIndex: "future_spread_pct",
      key: "future_spread_pct",
      align: "right",
      render: (value) => pctText(value)
    },
    {
      title: "溢价差",
      dataIndex: "premium_gap_pct",
      key: "premium_gap_pct",
      align: "right",
      render: (value) => pctText(value)
    },
    {
      title: "左侧合约",
      dataIndex: "left_future_mid",
      key: "left_future_mid",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "右侧合约",
      dataIndex: "right_future_mid",
      key: "right_future_mid",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "时间",
      dataIndex: "observed_at",
      key: "observed_at",
      width: 140,
      render: timeText
    }
  ];

  const componentSignalColumns: ColumnsType<SecondLevelIndexComponentSignal> = [
    {
      title: "时间",
      dataIndex: "observed_at",
      key: "observed_at",
      width: 140,
      render: timeText
    },
    {
      title: "目标",
      key: "target",
      width: 150,
      render: (_, row) => `${row.target_exchange} ${row.symbol}`
    },
    {
      title: "成分源",
      key: "component",
      width: 150,
      render: (_, row) => `${row.component_source} ${row.component_symbol}`
    },
    {
      title: "等级",
      dataIndex: "signal_level",
      key: "signal_level",
      width: 92,
      render: signalLevelTag
    },
    {
      title: "权重",
      dataIndex: "weight_pct",
      key: "weight_pct",
      align: "right",
      render: (value) => pctText(value, 2)
    },
    {
      title: "成分价",
      dataIndex: "component_price",
      key: "component_price",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "成分变化",
      dataIndex: "component_price_change_pct",
      key: "component_price_change_pct",
      align: "right",
      render: (value) => pctText(value, 4)
    },
    {
      title: "预计推指数",
      dataIndex: "estimated_index_impact_pct",
      key: "estimated_index_impact_pct",
      align: "right",
      render: (value) => pctText(value, 4)
    },
    {
      title: "官方指数变化",
      dataIndex: "official_index_change_pct",
      key: "official_index_change_pct",
      align: "right",
      render: (value) => pctText(value, 4)
    },
    {
      title: "mark 溢价变化",
      dataIndex: "mark_premium_change_pct",
      key: "mark_premium_change_pct",
      align: "right",
      render: (value) => (typeof value === "number" ? `${value.toFixed(4)}pct` : "-")
    },
    {
      title: "说明",
      dataIndex: "reason",
      key: "reason",
      ellipsis: true
    }
  ];

  const componentSampleColumns: ColumnsType<SecondLevelIndexComponentSample> = [
    {
      title: "时间",
      dataIndex: "observed_at",
      key: "observed_at",
      width: 140,
      render: timeText
    },
    {
      title: "目标所",
      dataIndex: "target_exchange",
      key: "target_exchange",
      width: 86
    },
    {
      title: "标的",
      dataIndex: "symbol",
      key: "symbol",
      width: 108
    },
    {
      title: "成分源",
      dataIndex: "component_source",
      key: "component_source",
      width: 96
    },
    {
      title: "成分标的",
      dataIndex: "component_symbol",
      key: "component_symbol",
      width: 120
    },
    {
      title: "权重",
      dataIndex: "weight_pct",
      key: "weight_pct",
      align: "right",
      render: (value) => pctText(value, 2)
    },
    {
      title: "成分价",
      dataIndex: "component_price",
      key: "component_price",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "贡献价",
      dataIndex: "contribution_price",
      key: "contribution_price",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "官方指数",
      dataIndex: "official_index_price",
      key: "official_index_price",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "重建指数",
      dataIndex: "reconstructed_index_price",
      key: "reconstructed_index_price",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "mark 溢价",
      dataIndex: "mark_premium_pct",
      key: "mark_premium_pct",
      align: "right",
      render: (value) => pctText(value)
    },
    {
      title: "提示",
      dataIndex: "error",
      key: "error",
      ellipsis: true,
      render: (value) => value || "-"
    }
  ];

  const sampleColumns: ColumnsType<SecondLevelMarketSample> = [
    {
      title: "时间",
      dataIndex: "observed_at",
      key: "observed_at",
      width: 140,
      render: timeText
    },
    {
      title: "交易所",
      dataIndex: "exchange",
      key: "exchange",
      width: 96
    },
    {
      title: "标的",
      dataIndex: "symbol",
      key: "symbol",
      width: 108
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 82,
      render: statusTag
    },
    {
      title: "现货 mid",
      dataIndex: "spot_mid",
      key: "spot_mid",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "合约 mid",
      dataIndex: "future_mid",
      key: "future_mid",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "标记价",
      dataIndex: "mark_price",
      key: "mark_price",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "指数价",
      dataIndex: "index_price",
      key: "index_price",
      align: "right",
      render: (value) => numberText(value)
    },
    {
      title: "mark 溢价",
      dataIndex: "mark_premium_pct",
      key: "mark_premium_pct",
      align: "right",
      render: (value) => pctText(value)
    },
    {
      title: "延迟",
      dataIndex: "latency_ms",
      key: "latency_ms",
      align: "right",
      width: 90,
      render: (value) => (typeof value === "number" ? `${value.toFixed(0)}ms` : "-")
    },
    {
      title: "提示",
      dataIndex: "error",
      key: "error",
      ellipsis: true,
      render: (value) => value || "-"
    }
  ];

  return (
    <div className="second-sampling-page">
      <Space direction="vertical" size={18} className="page-stack">
        <div className="page-heading-row">
          <div>
            <Typography.Title level={2}>1s 采样</Typography.Title>
            <Typography.Text type="secondary">秒级采集现货、合约、标记价、指数价和溢价</Typography.Text>
          </div>
          <Space>
            {isRunning ? <Tag color="green">运行中 / 自动刷新</Tag> : <Tag>已暂停</Tag>}
            <Button icon={<ReloadOutlined />} onClick={() => void refresh({ forceComponentDetails: true })} loading={loading}>
              刷新
            </Button>
          </Space>
        </div>

        {status?.latest_error ? <Alert type="warning" showIcon message={status.latest_error} /> : null}
        {status && !isRunning ? (
          <Alert type="info" showIcon message="采样已暂停，自动刷新已停止；点击右上角“刷新”可手动查看历史样本。" />
        ) : null}

        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="样本数" value={status?.sample_count ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="成分样本" value={status?.component_sample_count ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="最新时间" value={timeText(status?.latest_observed_at)} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="交易所" value={draft.exchanges.length} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="标的" value={draft.symbols.length} />
            </Card>
          </Col>
        </Row>

        <Card className="sampling-control-card" size="small">
          <Form layout="vertical">
            <Row gutter={[12, 12]}>
              <Col xs={24} lg={8}>
                <Form.Item label="交易所">
                  <Select
                    mode="multiple"
                    value={draft.exchanges}
                    options={exchangeOptions.map((value) => ({ value, label: value }))}
                    onChange={(exchanges) => setDraft((current) => ({ ...current, exchanges }))}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} lg={8}>
                <Form.Item label="标的">
                  <Select
                    mode="tags"
                    value={draft.symbols}
                    tokenSeparators={[",", "，", " "]}
                    onChange={(symbols) => setDraft((current) => ({ ...current, symbols }))}
                  />
                </Form.Item>
              </Col>
              <Col xs={12} lg={3}>
                <Form.Item label="间隔秒">
                  <InputNumber
                    min={1}
                    max={60}
                    value={draft.interval_seconds}
                    onChange={(value) =>
                      setDraft((current) => ({ ...current, interval_seconds: Number(value ?? 1) }))
                    }
                    className="full-width-control"
                  />
                </Form.Item>
              </Col>
              <Col xs={12} lg={3}>
                <Form.Item label="保留小时">
                  <InputNumber
                    min={1}
                    max={720}
                    value={draft.retention_hours}
                    onChange={(value) =>
                      setDraft((current) => ({ ...current, retention_hours: Number(value ?? 48) }))
                    }
                    className="full-width-control"
                  />
                </Form.Item>
              </Col>
              <Col xs={24} lg={2}>
                <Form.Item label="并发">
                  <InputNumber
                    min={1}
                    max={32}
                    value={draft.max_concurrent_requests}
                    onChange={(value) =>
                      setDraft((current) => ({ ...current, max_concurrent_requests: Number(value ?? 8) }))
                    }
                    className="full-width-control"
                  />
                </Form.Item>
              </Col>
              <Col xs={12} lg={3}>
                <Form.Item label="指数组成">
                  <Switch
                    checked={draft.capture_index_components}
                    checkedChildren="采集"
                    unCheckedChildren="关闭"
                    onChange={(capture_index_components) =>
                      setDraft((current) => ({ ...current, capture_index_components }))
                    }
                  />
                </Form.Item>
              </Col>
              <Col xs={12} lg={3}>
                <Form.Item label="信号窗口秒">
                  <InputNumber
                    min={2}
                    max={300}
                    value={draft.component_signal_window_seconds}
                    onChange={(value) =>
                      setDraft((current) => ({
                        ...current,
                        component_signal_window_seconds: Number(value ?? 10)
                      }))
                    }
                    className="full-width-control"
                  />
                </Form.Item>
              </Col>
            </Row>
            <Space wrap>
              <Button icon={<SaveOutlined />} onClick={() => void saveConfig(false)} loading={saving}>
                保存
              </Button>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={() => void saveConfig(true)}
                loading={saving}
              >
                启动
              </Button>
              <Button
                icon={<PauseCircleOutlined />}
                onClick={() => void stopSampling()}
                loading={saving}
                disabled={!isRunning}
              >
                暂停
              </Button>
            </Space>
          </Form>
        </Card>

        <Card title="最新跨所价差" size="small">
          <Table
            rowKey={(row) => `${row.symbol}:${row.left_exchange}:${row.right_exchange}`}
            columns={spreadColumns}
            dataSource={latestSpreads}
            pagination={false}
            size="small"
            scroll={{ x: 860 }}
          />
        </Card>

        <Card
          title="指数组成痕迹"
          size="small"
          extra={<Tag color="blue">窗口 {status?.config.component_signal_window_seconds ?? draft.component_signal_window_seconds}s</Tag>}
        >
          <Alert
            type="info"
            showIcon
            message="这里追踪成分源现货价对目标交易所指数价、mark 溢价的秒级传导；权重大且预计推指数幅度大的行优先看。"
            className="sampling-card-hint"
          />
          <Table
            rowKey={(row) =>
              `${row.target_exchange}:${row.symbol}:${row.component_source}:${row.component_symbol}:${row.observed_at}`
            }
            columns={componentSignalColumns}
            dataSource={componentSignals}
            pagination={false}
            size="small"
            scroll={{ x: 1380 }}
          />
        </Card>

        <Card title="指数组成明细" size="small" extra={<Tag>组成变化时更新</Tag>}>
          <Table
            rowKey={(row) =>
              row.id ?? `${row.target_exchange}:${row.symbol}:${row.component_source}:${row.component_symbol}:${row.observed_at}`
            }
            columns={componentSampleColumns}
            dataSource={componentSamples}
            pagination={{ pageSize: 20, showSizeChanger: true }}
            size="small"
            loading={componentLoading}
            scroll={{ x: 1380 }}
            expandable={{
              expandedRowRender: (row) => <pre className="sampling-error-text">{row.error || "OK"}</pre>,
              rowExpandable: (row) => Boolean(row.error)
            }}
          />
        </Card>

        <Card
          title="最近样本"
          size="small"
          extra={
            <Space wrap>
              <Select
                value={selectedSymbol}
                options={symbolOptions.map((value) => ({ value, label: value }))}
                onChange={setSelectedSymbol}
                className="sampling-filter-select"
              />
              <Select
                allowClear
                placeholder="全部交易所"
                value={selectedExchange}
                options={exchangeOptions.map((value) => ({ value, label: value }))}
                onChange={setSelectedExchange}
                className="sampling-filter-select"
              />
              <InputNumber min={1} max={43200} value={minutes} onChange={(value) => setMinutes(Number(value ?? 30))} />
            </Space>
          }
        >
          <Table
            rowKey={(row) => row.id ?? `${row.exchange}:${row.symbol}:${row.observed_at}`}
            columns={sampleColumns}
            dataSource={samples}
            pagination={{ pageSize: 20, showSizeChanger: true }}
            size="small"
            loading={loading}
            scroll={{ x: 1280 }}
            expandable={{
              expandedRowRender: (row) => (
                <pre className="sampling-error-text">{row.error || "OK"}</pre>
              ),
              rowExpandable: (row) => Boolean(row.error)
            }}
          />
        </Card>
      </Space>
    </div>
  );
}
