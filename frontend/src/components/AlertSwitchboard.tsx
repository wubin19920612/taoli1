import { Alert, Button, Form, Switch, Typography } from "antd";
import type { FormInstance } from "antd";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { getAnnouncementSettings, getRiskSettings, listPhonePriceAlertRules, updateAnnouncementSettings } from "../api/client";
import type { AlertRule, AnnouncementSettings, RiskSettings } from "../api/types";
import "./AlertSwitchboard.css";

type RiskSwitch = "big_spread_alert_enabled" | "big_spread_alert_auto_create"
  | "big_spread_phone_enabled" | "multi_exchange_premium_alert_enabled";
const announcementRows = [
  ["enabled", "交易所公告总开关"],
  ["listing_delisting_alerts_enabled", "上币与下币公告"],
  ["launchpool_alerts_enabled", "新币挖矿公告"],
  ["other_alerts_enabled", "其他公告"],
  ["bootstrap_alerts_enabled", "启动时历史公告"],
  ["event_reminders_enabled", "公告事件提醒"]
] as const;
type AnnouncementSwitch = typeof announcementRows[number][0];

interface Props {
  riskForm: FormInstance<RiskSettings>;
  rules: AlertRule[];
  loading: boolean;
  error?: string;
  saveRisk: (settings: RiskSettings) => Promise<RiskSettings>;
  onNavigate: (tab: "risk" | "rules") => void;
}

function Row({ label, status, control, onConfigure, href }: {
  label: string; status: string; control?: ReactNode; onConfigure?: () => void; href?: string;
}) {
  return (
    <div className="alert-switchboard-row" role="group" aria-label={label}>
      <div className="alert-switchboard-description">
        <Typography.Text strong>{label}</Typography.Text>
        <Typography.Text type="secondary">{status}</Typography.Text>
      </div>
      <div className="alert-switchboard-actions">
        {control}
        <Button size="small" href={href} onClick={onConfigure} aria-label={`${label}去设置`}>去设置</Button>
      </div>
    </div>
  );
}

function summary(rules: { enabled: boolean }[]): string {
  return `已启用 ${rules.filter((rule) => rule.enabled).length} / 共 ${rules.length} 条`;
}
function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function AlertSwitchboard({ riskForm, rules, loading, error, saveRisk, onNavigate }: Props) {
  // Watch the existing form store, including fields that have not been visited yet.
  const risk = Form.useWatch([], { form: riskForm, preserve: true }) as RiskSettings | undefined;
  const [riskSaving, setRiskSaving] = useState<RiskSwitch | null>(null);
  const riskBusy = useRef(false);
  const [announcements, setAnnouncements] = useState<AnnouncementSettings | null>(null);
  const [announcementLoading, setAnnouncementLoading] = useState(true);
  const [announcementError, setAnnouncementError] = useState("");
  const [announcementSaving, setAnnouncementSaving] = useState<AnnouncementSwitch | null>(null);
  const announcementBusy = useRef(false);
  const [phoneSummary, setPhoneSummary] = useState("加载中");
  const [saveError, setSaveError] = useState("");

  const loadAnnouncements = async () => {
    setAnnouncementLoading(true);
    setAnnouncementError("");
    try {
      setAnnouncements(await getAnnouncementSettings());
    } catch (exc) {
      setAnnouncementError(errorText(exc));
    } finally {
      setAnnouncementLoading(false);
    }
  };

  useEffect(() => {
    void loadAnnouncements();
    void listPhonePriceAlertRules().then((next) => setPhoneSummary(summary(next)))
      .catch(() => setPhoneSummary("加载失败"));
  }, []);

  const toggleRisk = async (field: RiskSwitch, checked: boolean) => {
    if (riskBusy.current || loading || error) return;
    const previous = riskForm.getFieldValue(field);
    riskBusy.current = true;
    setRiskSaving(field);
    setSaveError("");
    riskForm.setFieldValue(field, checked);
    try {
      // Never write unrelated stale form values or unsaved edits into the PUT.
      const latest = await getRiskSettings();
      const saved = await saveRisk({ ...latest, [field]: checked });
      riskForm.setFieldValue(field, saved[field] ?? checked);
    } catch (exc) {
      riskForm.setFieldValue(field, previous);
      setSaveError(`保存失败：${errorText(exc)}`);
    } finally {
      riskBusy.current = false;
      setRiskSaving(null);
    }
  };

  const toggleAnnouncement = async (field: AnnouncementSwitch, checked: boolean) => {
    if (announcementBusy.current || !announcements || announcementLoading || announcementError) return;
    const previous = announcements;
    announcementBusy.current = true;
    setAnnouncementSaving(field);
    setSaveError("");
    setAnnouncements({ ...previous, [field]: checked });
    try {
      const latest = await getAnnouncementSettings();
      setAnnouncements(await updateAnnouncementSettings({ ...latest, [field]: checked }));
    } catch (exc) {
      setAnnouncements(previous);
      setSaveError(`保存失败：${errorText(exc)}`);
    } finally {
      announcementBusy.current = false;
      setAnnouncementSaving(null);
    }
  };

  const riskRows: { field: RiskSwitch; label: string; status: string }[] = [
    {
      field: "big_spread_alert_enabled", label: "大价差提醒",
      status: `阈值 ≥${risk?.big_spread_alert_pct ?? "—"}% / 连续 ${risk?.big_spread_alert_hits ?? "—"} 次 / 路线冷却 ${risk?.big_spread_alert_cooldown_seconds ?? "—"} 秒 / 同币种冷却 ${risk?.big_spread_alert_symbol_cooldown_seconds ?? "—"} 秒`
    },
    {
      field: "big_spread_alert_auto_create", label: "大价差自动建卡",
      status: "自动创建卡片，开仓保持关闭；还需启用 Astro 全局自动建卡"
    },
    {
      field: "big_spread_phone_enabled", label: "夜间电话加急",
      status: `${risk?.big_spread_phone_start_hour ?? "—"}:00-${risk?.big_spread_phone_end_hour ?? "—"}:00 / ≥${risk?.big_spread_phone_min_spread_pct ?? "—"}% / 每晚≤${risk?.big_spread_phone_max_per_night ?? "—"} 次（北京时间）`
    },
    {
      field: "multi_exchange_premium_alert_enabled", label: "多交易所溢价告警",
      status: `绝对值 ≥${risk?.multi_exchange_premium_threshold_pct ?? "—"}% / 冷却 ${risk?.multi_exchange_premium_cooldown_seconds ?? "—"} 秒`
    }
  ];

  return (
    <section className="panel alert-switchboard" aria-label="告警总开关">
      <Typography.Title level={4}>告警总开关</Typography.Title>
      {saveError ? <Alert type="error" showIcon message={saveError} /> : null}
      {riskRows.map(({ field, label, status }) => (
        <Row key={field} label={label} status={loading ? "加载中" : error ? "加载失败" : status}
          onConfigure={() => onNavigate("risk")}
          control={<Switch aria-label={label} checked={Boolean(risk?.[field])} loading={riskSaving === field}
            disabled={loading || Boolean(error) || !risk || riskSaving !== null}
            onChange={(checked) => void toggleRisk(field, checked)} />} />
      ))}
      <Row label="普通告警规则" status={loading ? "加载中" : error ? "加载失败" : summary(rules)}
        onConfigure={() => onNavigate("rules")} />
      <section className="alert-switchboard-announcements" aria-label="交易所公告">
        <Typography.Title level={5}>交易所公告</Typography.Title>
        {announcementError ? <Alert type="error" showIcon message="交易所公告加载失败" description={announcementError}
          action={<Button size="small" onClick={() => void loadAnnouncements()}>重试公告设置</Button>} /> : null}
        {announcementRows.map(([field, label]) => (
          <Row key={field} label={label} href="?page=announcements"
            status={announcementLoading ? "加载中" : announcementError ? "加载失败"
              : field !== "enabled" && !announcements?.enabled ? "公告总开关已关闭，分类设置保留"
                : announcements?.[field] ? "已开启" : "已关闭"}
            control={<Switch aria-label={label} checked={Boolean(announcements?.[field])}
              loading={announcementSaving === field}
              disabled={!announcements || announcementLoading || Boolean(announcementError) || announcementSaving !== null}
              onChange={(checked) => void toggleAnnouncement(field, checked)} />} />
        ))}
      </section>
      <Row label="手机价格提醒" status={phoneSummary} onConfigure={() => onNavigate("rules")} />
    </section>
  );
}
