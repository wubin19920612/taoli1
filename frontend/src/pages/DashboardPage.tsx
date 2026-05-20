import { Alert, Button, Col, Row, Space, Statistic, Typography, message } from "antd";
import { useEffect, useState } from "react";
import { EyeOutlined } from "@ant-design/icons";

import { getRiskSettings, updateRiskSettings } from "../api/client";
import type { OpportunityFilters, RiskSettings } from "../api/types";
import { OpportunityTable } from "../components/OpportunityTable";
import { TopFilters } from "../components/TopFilters";
import { defaultHiddenRiskLabels } from "../constants/riskLabels";
import { useRadarStore } from "../state/useRadarStore";

function normalizeSymbol(value: string): string {
  return value.toUpperCase().replace(/[-_]/g, "");
}

function normalizeSymbols(values: string[] | undefined): string[] {
  return Array.from(
    new Set((values ?? []).map((item) => normalizeSymbol(item)).filter((item) => item.length > 0))
  );
}

export function DashboardPage() {
  const [filters, setFilters] = useState<OpportunityFilters>({
    include_risky: false,
    hidden_risk_labels: defaultHiddenRiskLabels
  });
  const [riskSettings, setRiskSettings] = useState<RiskSettings | null>(null);
  const [savingSymbol, setSavingSymbol] = useState<string | null>(null);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const { opportunities, health, loading, error, refresh } = useRadarStore(filters, settingsLoaded);
  const errors = health?.exchange_errors ?? {};
  const blockedSymbols = riskSettings ? normalizeSymbols(riskSettings.excluded_symbols) : [];

  useEffect(() => {
    let cancelled = false;
    void getRiskSettings()
      .then((settings) => {
        if (cancelled) {
          return;
        }
        const normalizedSettings = {
          ...settings,
          excluded_symbols: normalizeSymbols(settings.excluded_symbols)
        };
        setRiskSettings(normalizedSettings);
        if (typeof settings.min_volume_24h_usdt === "number") {
          setFilters((current) => ({
            ...current,
            min_volume_24h_k: Math.round(settings.min_volume_24h_usdt / 1000)
          }));
        }
        setSettingsLoaded(true);
      })
      .catch(() => {
        if (!cancelled) {
          // Keep the local fallback if settings cannot be loaded.
          setSettingsLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleBlockedSymbol = async (symbol: string, block: boolean) => {
    if (!riskSettings) {
      return;
    }
    const normalizedSymbol = normalizeSymbol(symbol);
    if (!normalizedSymbol) {
      return;
    }
    const currentExcluded = normalizeSymbols(riskSettings.excluded_symbols);
    const nextExcluded = block
      ? normalizeSymbols([...currentExcluded, normalizedSymbol])
      : currentExcluded.filter((item) => item !== normalizedSymbol);
    setSavingSymbol(normalizedSymbol);
    try {
      const saved = await updateRiskSettings({
        ...riskSettings,
        excluded_symbols: nextExcluded
      });
      const normalizedSaved = {
        ...saved,
        excluded_symbols: normalizeSymbols(saved.excluded_symbols)
      };
      setRiskSettings(normalizedSaved);
      message.success(block ? `已屏蔽 ${normalizedSymbol}` : `已取消屏蔽 ${normalizedSymbol}`);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSavingSymbol(null);
    }
  };

  return (
    <div className="page">
      <TopFilters filters={filters} loading={loading} onChange={setFilters} onRefresh={refresh} />
      {blockedSymbols.length > 0 ? (
        <div className="blocked-strip">
          <Typography.Text className="blocked-strip-title">已屏蔽标的</Typography.Text>
          <Space size={8} wrap className="blocked-strip-list">
            {blockedSymbols.map((symbol) => (
              <Button
                key={symbol}
                size="small"
                type="text"
                icon={<EyeOutlined />}
                aria-label={`取消屏蔽 ${symbol}`}
                loading={savingSymbol === symbol}
                disabled={savingSymbol !== null && savingSymbol !== symbol}
                onClick={() => void toggleBlockedSymbol(symbol, false)}
              >
                取消屏蔽 {symbol}
              </Button>
            ))}
          </Space>
        </div>
      ) : null}
      <Row gutter={[12, 12]} className="metric-row">
        <Col xs={12} md={6}>
          <Statistic title="机会数" value={health?.opportunities ?? opportunities.length} />
        </Col>
        <Col xs={12} md={6}>
          <Statistic title="市场快照" value={health?.markets ?? 0} />
        </Col>
        <Col xs={12} md={6}>
          <Statistic title="接口异常" value={Object.keys(errors).length} />
        </Col>
        <Col xs={12} md={6}>
          <Statistic
            title="最高开仓"
            value={opportunities[0]?.open_spread_pct ?? 0}
            precision={3}
            suffix="%"
          />
        </Col>
      </Row>
      {error ? <Alert className="page-alert" type="error" message={error} showIcon /> : null}
      {Object.keys(errors).length > 0 ? (
        <Alert
          className="page-alert"
          type="warning"
          message={Object.entries(errors)
            .slice(0, 4)
            .map(([key, value]) => `${key}: ${value}`)
            .join(" | ")}
          showIcon
        />
      ) : null}
      <OpportunityTable
        opportunities={opportunities}
        loading={loading}
        blockedSymbols={blockedSymbols}
        actionLoadingSymbol={savingSymbol}
        onToggleSymbol={(symbol, block) => void toggleBlockedSymbol(symbol, block)}
      />
    </div>
  );
}
