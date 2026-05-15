import { Alert, Col, Row, Statistic } from "antd";
import { useState } from "react";

import type { OpportunityFilters } from "../api/types";
import { OpportunityTable } from "../components/OpportunityTable";
import { TopFilters } from "../components/TopFilters";
import { useRadarStore } from "../state/useRadarStore";

export function DashboardPage() {
  const [filters, setFilters] = useState<OpportunityFilters>({ include_risky: true });
  const { opportunities, health, loading, error, refresh } = useRadarStore(filters);
  const errors = health?.exchange_errors ?? {};

  return (
    <div className="page">
      <TopFilters filters={filters} loading={loading} onChange={setFilters} onRefresh={refresh} />
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
      <OpportunityTable opportunities={opportunities} loading={loading} />
    </div>
  );
}
