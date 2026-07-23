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
  getSecondLevelSamplingConfig,
  getSecondLevelSamplingStatus,
  listSecondLevelSamples,
  listSecondLevelSamplingExchanges,
  stopSecondLevelSampling,
  updateSecondLevelSamplingConfig
} from "../api/client";
import type {
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
  max_concurrent_requests: 8
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

function normalizeSymbolInput(values: string[]): string[] {
  return values
    .map((value) => value.trim().toUpperCase().replace(/[-_/]/g, ""))
    .filter(Boolean)
    .map((value) => (value.endsWith("USDT") ? value : `${value}USDT`));
}

export function SecondLevelSamplingPage() {
  const [exchangeOptions, setExchangeOptions] = useState<string[]>([]);
  const [draft, setDraft] = useState<SecondLevelSamplingConfig>(defaultConfig);
  const [status, setStatus] = useState<SecondLevelSamplingStatus | null>(null);
  const [samples, setSamples] = useState<SecondLevelMarketSample[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string>("DEXEUSDT");
  const [selectedExchange, setSelectedExchange] = useState<string | undefined>();
  const [minutes, setMinutes] = useState<number>(30);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
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
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [minutes, selectedExchange, selectedSymbol]);

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

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [refresh]);

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
        await refresh();
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
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }, [refresh]);

  const latestSpreads = status?.latest_spreads ?? [];
  const symbolOptions = useMemo(
    () => Array.from(new Set([...draft.symbols, ...samples.map((item) => item.symbol)])).filter(Boolean),
    [draft.symbols, samples]
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
      title: "错误",
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
            {status?.running ? <Tag color="green">运行中</Tag> : <Tag>已暂停</Tag>}
            <Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>
              刷新
            </Button>
          </Space>
        </div>

        {status?.latest_error ? <Alert type="warning" showIcon message={status.latest_error} /> : null}

        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="样本数" value={status?.sample_count ?? 0} />
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
              <Button icon={<PauseCircleOutlined />} onClick={() => void stopSampling()} loading={saving}>
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
