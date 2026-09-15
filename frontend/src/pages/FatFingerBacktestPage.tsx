import {
  ExperimentOutlined,
  ReloadOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
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
import { useState } from "react";

import { runFatFingerBacktest } from "../api/client";
import type {
  FatFingerBacktestRequest,
  FatFingerBacktestResult,
  FatFingerBacktestRouteSummary,
  FatFingerBacktestTrade,
  FatFingerMakerSide,
  MarketType
} from "../api/types";

dayjs.extend(utc);

const defaultRequest: FatFingerBacktestRequest = {
  symbol: "DEXEUSDT",
  market_mode: "SF",
  hours: 6,
  sample_limit: 150_000,
  entry_spread_pct: 1,
  ladder_levels: 3,
  ladder_step_pct: 0.5,
  order_notional_usdt: 100,
  maker_fill_assumption_pct: 25,
  maker_fee_pct: 0.02,
  taker_fee_pct: 0.06,
  taker_slippage_pct: 0.05,
  hedge_delay_seconds: 1,
  order_expiry_seconds: 30,
  take_profit_pct: 0.15,
  max_hold_seconds: 120,
  min_hedge_depth_usdt: 100,
  max_quote_age_seconds: 2,
  require_known_hedge_depth: true,
  cooldown_seconds: 10
};

function normalizeSymbol(value: string): string {
  const normalized = value.trim().toUpperCase().replace(/[-_/]/g, "");
  return normalized.endsWith("USDT") ? normalized : `${normalized}USDT`;
}

function shortSymbol(value: string): string {
  return value.replace(/(?:USDT|USDC|USD)$/iu, "");
}

function exchangeText(value: string): string {
  const names: Record<string, string> = {
    aster: "Aster",
    binance: "Binance",
    bitget: "Bitget",
    bybit: "Bybit",
    gate: "Gate",
    hyperliquid: "Hyperliquid",
    okx: "OKX"
  };
  return names[value] ?? value;
}

function marketText(value: MarketType): string {
  return value === "spot" ? "现货" : "合约";
}

function timeText(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function numberText(value: number | null | undefined, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "")
    : "-";
}

function pctText(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value >= 0 ? "+" : ""}${numberText(value, digits)}%`
    : "-";
}

function moneyText(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toFixed(2)} USDT`
    : "-";
}

function signedMoneyText(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value >= 0 ? "+" : ""}${value.toFixed(2)} USDT`
    : "-";
}

function sideText(side: FatFingerMakerSide): string {
  return side === "buy" ? "挂买 / 对冲卖" : "挂卖 / 对冲买";
}

function routeText(
  makerExchange: string,
  makerMarketType: MarketType,
  hedgeExchange: string,
  hedgeMarketType: MarketType,
  makerSide: FatFingerMakerSide
): string {
  const makerAction = makerSide === "buy" ? "买" : "卖";
  const hedgeAction = makerSide === "buy" ? "卖" : "买";
  return `${exchangeText(makerExchange)} ${marketText(makerMarketType)} ${makerAction} / ${exchangeText(hedgeExchange)} ${marketText(hedgeMarketType)} ${hedgeAction}`;
}

function pnlClass(value: number | null | undefined): string {
  return typeof value === "number" && value < 0 ? "fat-finger-negative" : "fat-finger-positive";
}

export function FatFingerBacktestPage() {
  const [form] = Form.useForm<FatFingerBacktestRequest>();
  const [result, setResult] = useState<FatFingerBacktestResult | null>(null);
  const [loading, setLoading] = useState(false);

  const runBacktest = async () => {
    setLoading(true);
    try {
      const values = await form.validateFields();
      const payload: FatFingerBacktestRequest = {
        ...defaultRequest,
        ...values,
        symbol: normalizeSymbol(values.symbol)
      };
      setResult(await runFatFingerBacktest(payload));
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  const routeColumns: ColumnsType<FatFingerBacktestRouteSummary> = [
    {
      title: "预挂路线",
      width: 270,
      render: (_, row) => (
        <Typography.Text strong>
          {routeText(
            row.maker_exchange,
            row.maker_market_type,
            row.hedge_exchange,
            row.hedge_market_type,
            row.maker_side
          )}
        </Typography.Text>
      )
    },
    {
      title: "触价",
      dataIndex: "touch_count",
      width: 78,
      align: "right"
    },
    {
      title: "完成对冲",
      dataIndex: "hedge_count",
      width: 98,
      align: "right"
    },
    {
      title: "未对冲",
      dataIndex: "unhedged_count",
      width: 90,
      align: "right",
      render: (value: number) => <Typography.Text type={value > 0 ? "danger" : undefined}>{value}</Typography.Text>
    },
    {
      title: "已平仓",
      dataIndex: "closed_trade_count",
      width: 84,
      align: "right"
    },
    {
      title: "胜率",
      width: 78,
      align: "right",
      render: (_, row) => (row.closed_trade_count ? pctText((row.win_count / row.closed_trade_count) * 100, 1) : "-")
    },
    {
      title: "总净收益",
      dataIndex: "total_net_pnl_usdt",
      width: 112,
      align: "right",
      render: (value: number) => <Typography.Text className={pnlClass(value)} strong>{signedMoneyText(value)}</Typography.Text>
    },
    {
      title: "均值",
      dataIndex: "average_net_pnl_pct",
      width: 88,
      align: "right",
      render: (value: number | null) => <Typography.Text className={pnlClass(value)}>{pctText(value)}</Typography.Text>
    },
    {
      title: "最差",
      dataIndex: "worst_net_pnl_pct",
      width: 88,
      align: "right",
      render: (value: number | null) => <Typography.Text className={pnlClass(value)}>{pctText(value)}</Typography.Text>
    },
    {
      title: "平均持仓",
      dataIndex: "average_hold_seconds",
      width: 100,
      align: "right",
      render: (value: number | null) => (value === null ? "-" : `${numberText(value, 1)}s`)
    }
  ];

  const tradeColumns: ColumnsType<FatFingerBacktestTrade> = [
    { title: "平仓时间", dataIndex: "closed_at", width: 128, render: timeText },
    {
      title: "路线",
      width: 244,
      render: (_, row) =>
        routeText(
          row.maker_exchange,
          row.maker_market_type,
          row.hedge_exchange,
          row.hedge_market_type,
          row.maker_side
        )
    },
    {
      title: "档位",
      width: 80,
      align: "right",
      render: (_, row) => `${row.tier} / ${pctText(row.entry_target_spread_pct, 2)}`
    },
    {
      title: "触价 / 对冲",
      width: 136,
      render: (_, row) => `${timeText(row.maker_filled_at).slice(-8)} / ${timeText(row.hedge_filled_at).slice(-8)}`
    },
    { title: "额度", dataIndex: "notional_usdt", width: 90, align: "right", render: moneyText },
    {
      title: "入场边际",
      dataIndex: "entry_hedge_edge_pct",
      width: 94,
      align: "right",
      render: (value: number) => pctText(value)
    },
    {
      title: "净收益",
      dataIndex: "net_pnl_usdt",
      width: 104,
      align: "right",
      render: (value: number) => <Typography.Text className={pnlClass(value)} strong>{signedMoneyText(value)}</Typography.Text>
    },
    {
      title: "净收益%",
      dataIndex: "net_pnl_pct",
      width: 94,
      align: "right",
      render: (value: number) => <Typography.Text className={pnlClass(value)}>{pctText(value)}</Typography.Text>
    },
    {
      title: "最大不利",
      dataIndex: "max_adverse_pnl_pct",
      width: 94,
      align: "right",
      render: (value: number) => <Typography.Text className={pnlClass(value)}>{pctText(value)}</Typography.Text>
    },
    {
      title: "退出",
      dataIndex: "exit_reason",
      width: 84,
      render: (value: string) => <Tag color={value === "target" ? "green" : "orange"}>{value === "target" ? "回归止盈" : "超时退出"}</Tag>
    },
    {
      title: "持仓",
      dataIndex: "hold_seconds",
      width: 76,
      align: "right",
      render: (value: number) => `${numberText(value, 1)}s`
    }
  ];

  return (
    <div className="page fat-finger-page">
      <div className="toolbar">
        <div>
          <Typography.Title level={4}>乌龙回测</Typography.Title>
          <Typography.Text type="secondary">
            用 1 秒买一卖一历史模拟预挂 maker 单、延迟 taker 对冲和价差回归平仓，不触发任何真实下单。
          </Typography.Text>
        </div>
        <Button type="primary" icon={<ExperimentOutlined />} loading={loading} onClick={() => void runBacktest()}>
          开始回测
        </Button>
      </div>

      <Alert
        type="info"
        showIcon
        message="触价不等于成交：当前历史只保存买一卖一和数量，没有逐笔成交、队列位置或撤单反馈。结果用于筛选值得继续验证的路线，不能直接外推为真实成交率。"
      />

      <Card size="small" title="回测参数">
        <Form form={form} layout="vertical" initialValues={defaultRequest}>
          <div className="fat-finger-form-grid">
            <Form.Item label="标的" name="symbol" rules={[{ required: true, message: "请输入标的" }]}>
              <Input placeholder="DEXE" />
            </Form.Item>
            <Form.Item label="路线模式" name="market_mode">
              <Segmented
                options={[
                  { label: "现货 - 合约", value: "SF" },
                  { label: "合约 - 合约", value: "FF" }
                ]}
              />
            </Form.Item>
            <Form.Item label="历史小时" name="hours">
              <InputNumber min={0.25} max={168} step={1} />
            </Form.Item>
            <Form.Item label="最大样本" name="sample_limit">
              <InputNumber min={1000} max={300000} step={10000} />
            </Form.Item>

            <Form.Item label="首档触价价差%" name="entry_spread_pct">
              <InputNumber min={0.01} max={100} step={0.1} />
            </Form.Item>
            <Form.Item label="挂单档数" name="ladder_levels">
              <InputNumber min={1} max={10} step={1} />
            </Form.Item>
            <Form.Item label="档位间隔%" name="ladder_step_pct">
              <InputNumber min={0} max={50} step={0.1} />
            </Form.Item>
            <Form.Item label="每档额度 USDT" name="order_notional_usdt">
              <InputNumber min={1} max={1000000} step={10} />
            </Form.Item>
            <Form.Item label="触价成交比例%" name="maker_fill_assumption_pct">
              <InputNumber min={1} max={100} step={5} />
            </Form.Item>
            <Form.Item label="maker 手续费%" name="maker_fee_pct">
              <InputNumber min={0} max={10} step={0.01} />
            </Form.Item>
            <Form.Item label="taker 手续费%" name="taker_fee_pct">
              <InputNumber min={0} max={10} step={0.01} />
            </Form.Item>
            <Form.Item label="taker 滑点%" name="taker_slippage_pct">
              <InputNumber min={0} max={10} step={0.01} />
            </Form.Item>

            <Form.Item label="对冲延迟秒" name="hedge_delay_seconds">
              <InputNumber min={0} max={60} step={0.1} />
            </Form.Item>
            <Form.Item label="挂单有效秒" name="order_expiry_seconds">
              <InputNumber min={1} max={3600} step={1} />
            </Form.Item>
            <Form.Item label="回归止盈%" name="take_profit_pct">
              <InputNumber min={0} max={100} step={0.05} />
            </Form.Item>
            <Form.Item label="最长持仓秒" name="max_hold_seconds">
              <InputNumber min={1} max={86400} step={10} />
            </Form.Item>
            <Form.Item label="最低对冲深度 USDT" name="min_hedge_depth_usdt">
              <InputNumber min={0} max={1000000} step={10} />
            </Form.Item>
            <Form.Item label="允许行情年龄秒" name="max_quote_age_seconds">
              <InputNumber min={0} max={60} step={0.5} />
            </Form.Item>
            <Form.Item label="严格已知深度" name="require_known_hedge_depth" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="路线冷却秒" name="cooldown_seconds">
              <InputNumber min={0} max={3600} step={1} />
            </Form.Item>
          </div>
          <div className="fat-finger-config-note">
            <Typography.Text type="secondary">
              每档实际模拟额度 = 每档额度 × 触价成交比例。严格已知深度开启后，对冲腿没有返回数量或顶层可成交金额不足，都会被跳过或记为未完成对冲。
            </Typography.Text>
          </div>
          <Space>
            <Button type="primary" icon={<ExperimentOutlined />} loading={loading} onClick={() => void runBacktest()}>
              开始回测
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                form.resetFields();
                setResult(null);
              }}
            >
              恢复默认
            </Button>
          </Space>
        </Form>
      </Card>

      {result ? (
        <>
          {result.warnings.map((warning) => (
            <Alert
              key={warning}
              type={warning.includes("未能") || warning.includes("未到退出") ? "warning" : "info"}
              showIcon
              message={warning}
            />
          ))}
          <Card
            size="small"
            title={`回测结果 · ${shortSymbol(result.request.symbol)} · ${result.request.market_mode === "SF" ? "现货-合约" : "合约-合约"}`}
            extra={
              <Space size={6} wrap>
                <Tag>{timeText(result.start_at)} 至 {timeText(result.end_at)}</Tag>
                <Tag color={result.samples_truncated ? "orange" : "green"}>
                  {result.raw_sample_count.toLocaleString()} 条样本
                </Tag>
              </Space>
            }
          >
            <div className="fat-finger-metrics">
              <Statistic title="触价" value={result.quote_touch_count} />
              <Statistic title="完成对冲" value={result.hedge_completed_count} />
              <Statistic title="未对冲" value={result.unhedged_touch_count} valueStyle={{ color: result.unhedged_touch_count ? "#b42318" : undefined }} />
              <Statistic title="已平仓" value={result.closed_trade_count} />
              <Statistic title="胜率" value={result.win_rate_pct ?? 0} precision={1} suffix="%" />
              <Statistic title="总净收益" value={result.total_net_pnl_usdt} precision={2} suffix=" USDT" valueStyle={{ color: result.total_net_pnl_usdt < 0 ? "#b42318" : "#14803c" }} />
              <Statistic title="最差单次" value={result.worst_net_pnl_pct ?? 0} precision={3} suffix="%" valueStyle={{ color: (result.worst_net_pnl_pct ?? 0) < 0 ? "#b42318" : "#14803c" }} />
              <Statistic title="平均持仓" value={result.average_hold_seconds ?? 0} precision={1} suffix="s" />
            </div>
            <div className="fat-finger-detail-strip">
              <Typography.Text type="secondary">
                下单 {result.order_placed_count} · 过期 {result.order_expired_count} · 开仓深度跳过 {result.order_skipped_depth_count} · 平仓深度不足 {result.exit_skipped_depth_count} · 目标退出 {result.target_exit_count} · 超时退出 {result.timeout_exit_count} · 回测结束未平 {result.open_position_count}
              </Typography.Text>
            </div>
          </Card>

          <Card size="small" title="路线表现">
            <Table
              rowKey={(row) => `${row.maker_exchange}-${row.maker_market_type}-${row.hedge_exchange}-${row.hedge_market_type}-${row.maker_side}`}
              size="small"
              columns={routeColumns}
              dataSource={result.route_summaries}
              pagination={{ pageSize: 12 }}
              scroll={{ x: 1320 }}
            />
          </Card>

          <Card size="small" title="已平仓纸面交易">
            <Table
              rowKey="id"
              size="small"
              columns={tradeColumns}
              dataSource={result.trades}
              pagination={{ pageSize: 12 }}
              scroll={{ x: 1540 }}
            />
          </Card>
        </>
      ) : null}
    </div>
  );
}
