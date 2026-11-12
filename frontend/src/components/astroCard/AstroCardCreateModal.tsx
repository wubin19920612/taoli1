import { Alert, Button, Checkbox, Descriptions, Form, InputNumber, Modal, Radio, Space, Switch, Typography } from "antd";
import type { FormInstance } from "antd";
import type { MarketType } from "../../api/types";
import type { AstroActionResult, AstroCardCreateRequest, AstroCardVariant, AstroPairPlan, ArbMvpStatus, CardTarget, InstrumentSpreadComparison } from "../../api/types";
import { AstroCardVariantSelector, selectedAstroRoutes, supportsAstroCardVariant } from "../AstroCardVariantSelector";
export type AstroSizingFormValues = Required<Omit<AstroCardCreateRequest, "save_as_default" | "target" | "open_spread_pct" | "close_spread_pct">> & { save_as_default: boolean };
export type AstroSpreadDisplay = Pick<InstrumentSpreadComparison, "buy_exchange" | "buy_market_type" | "buy_ask" | "sell_exchange" | "sell_market_type" | "sell_bid" | "opportunity_type">;
interface Props {
  astroSpread: AstroSpreadDisplay | null; astroSymbol: string; astroReversed: boolean;
  astroPlan: AstroPairPlan | null; astroPreviewLoading: boolean; astroPreviewError: string;
  astroSubmitLoading: boolean; astroSubmitResult: AstroActionResult | null; astroSubmitError: string;
  astroSizingForm: FormInstance<AstroSizingFormValues>; cardTarget: CardTarget; arbStatus: ArbMvpStatus | null;
  readyPreviewTarget: CardTarget | null; selectedCardVariant: AstroCardVariant;
  closeAstroPreview: () => void; submitAstroCard: () => void; changeCardTarget: (target: CardTarget) => void;
  astroOnly?: boolean;
  templateThresholds?: { open: number; close: number };
}
const exchangeLabels: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  hyperliquid: "Hyperliquid",
  lighter: "Lighter",
  "rh-lighter": "RH Lighter",
  arcus: "Arcus",
  okx: "OKX"
};
function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const abs = Math.abs(value);
  if (abs >= 10_000) return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (abs >= 100) return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (abs >= 1) return value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  return value.toPrecision(6);
}

function signedPct(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function marketTypeLabel(value: MarketType): string {
  return value === "spot" ? "现货" : "永续";
}

export function AstroCardCreateModal({ astroSpread, astroSymbol, astroReversed, astroPlan, astroPreviewLoading, astroPreviewError, astroSubmitLoading, astroSubmitResult, astroSubmitError, astroSizingForm, cardTarget, arbStatus, readyPreviewTarget, selectedCardVariant, closeAstroPreview, submitAstroCard, changeCardTarget, astroOnly = false, templateThresholds }: Props) {
 return (
      <Modal
        open={astroSpread !== null}
        title={astroReversed ? "反向创建 Astro 卡片" : "创建 Astro 卡片"}
        width={760}
        onCancel={closeAstroPreview}
        closable={!astroSubmitLoading}
        keyboard={!astroSubmitLoading}
        maskClosable={!astroSubmitLoading}
        footer={[
          <Button key="close" disabled={astroSubmitLoading} onClick={closeAstroPreview}>关闭</Button>,
          <Button
            key="submit"
            type="primary"
            loading={astroSubmitLoading}
            disabled={readyPreviewTarget !== cardTarget || astroPreviewLoading || astroSubmitLoading || !astroPlan?.pair || !astroPlan.can_submit
              || (cardTarget === "astro" && !supportsAstroCardVariant(astroPlan.route_variants, selectedCardVariant))}
            onClick={() => void submitAstroCard()}
          >
            确认创建
          </Button>
        ]}
        destroyOnHidden
      >
        {astroSpread && astroSymbol ? (
          <Space direction="vertical" size={12} className="astro-preview-panel">
            <div>
              <Typography.Text>建卡目标</Typography.Text>{" "}
              <Radio.Group value={cardTarget} disabled={astroSubmitLoading} onChange={(event) => void changeCardTarget(event.target.value)}>
                <Radio value="astro">Astro</Radio>
                {!astroOnly ? <Radio value="arb_paper" disabled={!arbStatus?.paper?.configured}>arb-mvp 模拟盘</Radio> : null}
                {!astroOnly ? <Radio value="arb_live" disabled={!arbStatus?.live?.configured}>arb-mvp live</Radio> : null}
              </Radio.Group>
            </div>
            {cardTarget !== "astro" ? <Alert type="info" showIcon message="arb-mvp 卡片建好后是 STOPPED、禁止开仓，需要在 arb-mvp 界面手动开始交易" /> : null}

            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="标的">{astroSymbol}</Descriptions.Item>
              <Descriptions.Item label="卡片名称">{String(astroPlan?.pair?.name ?? "-")}</Descriptions.Item>
              <Descriptions.Item label="卡片类型">{String(astroPlan?.pair?.type ?? astroSpread.opportunity_type ?? "-")}</Descriptions.Item>
              {cardTarget === "astro" ? (<Descriptions.Item label="Astro 路线">
                {selectedAstroRoutes(astroPlan?.route_variants, selectedCardVariant)}
              </Descriptions.Item>) : null}
              <Descriptions.Item label="买入">
                {exchangeLabels[astroSpread.buy_exchange] ?? astroSpread.buy_exchange} · {marketTypeLabel(astroSpread.buy_market_type)} · Ask {price(astroSpread.buy_ask)}
              </Descriptions.Item>
              <Descriptions.Item label="卖出">
                {exchangeLabels[astroSpread.sell_exchange] ?? astroSpread.sell_exchange} · {marketTypeLabel(astroSpread.sell_market_type)} · Bid {price(astroSpread.sell_bid)}
              </Descriptions.Item>
              <Descriptions.Item label="预览可成交差价">{signedPct(astroPlan?.source_open_spread_pct)}</Descriptions.Item>
              {templateThresholds ? <Descriptions.Item label="模板开仓价差">≥ {signedPct(templateThresholds.open, 4)}</Descriptions.Item> : null}
              {templateThresholds ? <Descriptions.Item label="模板平仓价差">≤ {signedPct(templateThresholds.close, 4)}</Descriptions.Item> : null}
              <Descriptions.Item label={cardTarget === "astro" ? "Astro 开仓阈值" : "开仓阈值"}>{String(astroPlan?.pair?.[cardTarget === "astro" ? "openPosition" : "openSpread"] ?? "-")}</Descriptions.Item>
              {cardTarget === "astro" ? <Descriptions.Item label="Astro 平仓阈值">{String(astroPlan?.pair?.closePosition ?? "-")}</Descriptions.Item> : null}
              <Descriptions.Item label="允许提交">{astroPlan?.can_submit ? "是" : "否"}</Descriptions.Item>
            </Descriptions>
            {astroPreviewLoading ? <Alert type="info" showIcon message="正在按最新行情生成卡片预览" /> : null}
            {astroReversed ? (
              <Alert
                type="warning"
                showIcon
                message="当前为反向建卡"
                description={astroPlan?.pair?.type === "FS"
                  ? "将使用 FS 类型买入永续、卖出现货，并主动承受当前可成交价差；请确认现货侧有可卖余额或借币能力。"
                  : "将交换买卖方向并主动承受当前可成交价差；请核对开平仓阈值和资金费率。"}
              />
            ) : null}
            {astroPreviewError ? <Alert type="error" showIcon message={astroPreviewError} /> : null}
            {astroSubmitError ? <Alert type="error" showIcon message={astroSubmitError} /> : null}
            {astroSubmitResult ? (
              <Alert
                type={astroSubmitResult.status === "created" || astroSubmitResult.status === "updated" ? "success" : astroSubmitResult.status === "failed" ? "error" : "warning"}
                showIcon
                message={astroSubmitResult.message}
              />
            ) : null}
            {astroSubmitResult?.warnings?.length ? (
              <Alert type="warning" showIcon message={astroSubmitResult.warnings.join("；")} />
            ) : null}
            {astroPlan?.blockers.length ? <Alert type="error" showIcon message="当前不能创建" description={astroPlan.blockers.join("；")} /> : null}
            {astroPlan?.warnings.length ? <Alert type="warning" showIcon message={astroPlan.warnings.join("；")} /> : null}
            {cardTarget !== "astro" && astroPlan?.pair ? <pre className="astro-preview-json">{JSON.stringify(astroPlan.pair, null, 2)}</pre> : null}
            {astroPlan?.pair ? (
              <Form form={astroSizingForm} layout="vertical" className="astro-sizing-form">
                <div className="form-grid">
                  <Form.Item label={cardTarget === "astro" ? "单笔金额 USDT" : "最大持仓 (USD)"} name="max_trade_usdt" rules={[{ required: true }]}>
                    <InputNumber min={0.01} step={1} className="wide-input" />
                  </Form.Item>
                  <Form.Item label="杠杆" name="leverage" rules={[{ required: true }]}>
                    <InputNumber min={1} step={1} className="wide-input" />
                  </Form.Item>
                  <Form.Item label="最小名义金额 USDT" name="min_notional" rules={[{ required: true }]}>
                    <InputNumber min={0} step={1} className="wide-input" />
                  </Form.Item>
                  <Form.Item label="最大名义金额 USDT" name="max_notional" rules={[{ required: true }]}>
                    <InputNumber min={0.01} step={1} className="wide-input" />
                  </Form.Item>
                  {cardTarget === "astro" ? (<Form.Item label="创建后允许开仓" name="open_enabled" valuePropName="checked">
                    <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                  </Form.Item>) : null}
                </div>
                {cardTarget === "astro" ? (<Form.Item label="建卡路线" name="card_variant" rules={[{ required: true }]}>
                  <AstroCardVariantSelector routes={astroPlan.route_variants} />
                </Form.Item>) : null}
                <Form.Item name="save_as_default" valuePropName="checked">
                  <Checkbox>保存为全局建卡默认值</Checkbox>
                </Form.Item>
              </Form>
            ) : null}
          </Space>
        ) : null}
      </Modal>
 );
}
