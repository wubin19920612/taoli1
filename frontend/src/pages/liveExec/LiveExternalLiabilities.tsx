import { PollingStatus } from "./PollingStatus";
import type { PollingHealth } from "./usePollingHealth";
import { Alert, Button, Card, Descriptions, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { useRef, useState } from "react";
import { LiveAdminConflictError, LiveAdminSessionError, liveAdminReadError, validLiabilityCap, writeLiveExecAdmin,
  type LiveAdminWrite, type LiveExternalLiabilities as Registry, type LiveExternalLiability, type LiveSummary } from "../../api/liveExec";

type Update = Extract<LiveAdminWrite, { resource: "external-liabilities" }>;
type Notice = "registered" | "unregistered" | "conflict";
const exchangeValid = (value: string) => /^[A-Z][A-Z0-9_]{0,39}$/.test(value) && !/\s/.test(value);
const coinValid = (value: string) => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,39}$/.test(value) && !/\s/.test(value);
const responsibility = "登记后，该负债在额度内不会再阻止程序开仓；负债由你自行负责，超出额度程序仍会停开。";

export function LiveExternalLiabilities({ health, writesBlocked = false, registry, summary, error, hasAdminSession, refresh }: {
  health?: PollingHealth; writesBlocked?: boolean; registry: Registry | null; summary: LiveSummary | null; error?: string; hasAdminSession: boolean;
  refresh: () => Promise<void>;
}) {
  const [selectedExchange, setSelectedExchange] = useState<string>();
  const [coin, setCoin] = useState("USDC");
  const [cap, setCap] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<{ update: Update; cap: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [sessionRejected, setSessionRejected] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const writing = useRef(false);
  const liabilities = (Array.isArray(summary?.liabilities) ? summary.liabilities : [])
    .filter(row => row && typeof row.exchange_type === "string" && exchangeValid(row.exchange_type));
  const exchanges = [...new Set([...liabilities.map(row => row.exchange_type),
    ...(registry?.items ?? []).map(row => row.exchange_type)])].filter(exchangeValid);
  const defaultExchange = liabilities.find(row => (typeof row.liability === "string" || typeof row.liability === "number")
    && Number.isFinite(Number(row.liability)) && Number(row.liability) > 0)?.exchange_type ?? exchanges[0];
  const exchange = selectedExchange ?? defaultExchange;
  // Bind the draft to its displayed exchange, including an empty selection until the user chooses one.
  const lockExchange = () => setSelectedExchange(current => current ?? exchange ?? "");
  const unavailable = writesBlocked || !hasAdminSession || sessionRejected || !registry || !!error;
  const disabled = unavailable || busy || pending !== null;

  const register = () => {
    if (disabled || writing.current || !registry) return;
    setFailure(null); setNotice(null); setModalError(null);
    if (!exchange || !exchangeValid(exchange) || !exchanges.includes(exchange)) {
      setFailure("请选择快照或登记记录中的交易所"); return;
    }
    if (!coinValid(coin)) { setFailure("请输入有效币种标识（最多 40 字符，不含空格）"); return; }
    if (!validLiabilityCap(cap)) { setFailure("额度须大于 0 且不超过 1000，按原币数量填写"); return; }
    if (!reason.trim() || [...reason].length > 200 || /[\u0000-\u001f]/.test(reason)) {
      setFailure("备注须为 1 至 200 字符且不含控制字符"); return;
    }
    setPending({ update: { resource: "external-liabilities", action: "register", exchange_type: exchange,
      coin, cap, reason, expected_revision: registry.revision }, cap });
  };
  const unregister = (row: LiveExternalLiability) => {
    if (disabled || writing.current || !registry) return;
    setFailure(null); setNotice(null); setModalError(null);
    setPending({ update: { resource: "external-liabilities", action: "unregister", exchange_type: row.exchange_type,
      coin: row.coin, expected_revision: registry.revision }, cap: row.cap ?? "—" });
  };
  const submit = async () => {
    if (!pending || unavailable || writing.current) return;
    writing.current = true; setBusy(true); setModalError(null);
    try {
      await writeLiveExecAdmin(pending.update);
      setPending(null);
      if (pending.update.action === "register") { setCap(""); setReason(""); }
      await refresh();
      setNotice(pending.update.action === "register" ? "registered" : "unregistered");
    } catch (cause) {
      if (cause instanceof LiveAdminConflictError) {
        setPending(null);
        await refresh();
        setNotice("conflict");
      } else if (cause instanceof LiveAdminSessionError) {
        setSessionRejected(true); setPending(null); setFailure(cause.message);
      } else setModalError(cause instanceof Error ? cause.message : "操作失败，请刷新核实操作结果");
    } finally { writing.current = false; setBusy(false); }
  };
  const refreshed = registry !== null && !error;
  const noticeText = notice === "conflict" ? (refreshed ? "已被修改，已刷新" : "已被修改，刷新失败，请稍后重试")
    : `${notice === "registered" ? "额度已登记" : "额度已注销"}，${refreshed ? "已刷新" : "刷新失败，请稍后核实"}`;

  return <Card title={<><span>外部负债额度</span><PollingStatus health={health} /></>}>
    <Space direction="vertical" style={{ width: "100%" }}>
      <Typography.Paragraph type="secondary">额度和当前负债均为对应币种的原生数量。额度内负债由你自行负责；超额仍会停开，其他开仓限制继续生效。</Typography.Paragraph>
      <div className="live-notice-slot">{error && <Alert type="warning" showIcon message={`负债额度读取失败：${liveAdminReadError(error)}`} />}</div>
      {(!hasAdminSession || sessionRejected) && <Typography.Text type="secondary">管理员会话未验证，负债额度写操作不可用</Typography.Text>}
      {failure && <Alert type="error" showIcon message={failure} />}
      {notice && <Alert type={notice === "conflict" || !refreshed ? "warning" : "success"} showIcon message={noticeText} />}
      <Table<LiveExternalLiability> tableLayout="fixed" size="small" rowKey={row => JSON.stringify([row.exchange_type, row.coin])}
        pagination={false} dataSource={registry?.items ?? []} scroll={{ x: 1050 }}
        locale={{ emptyText: registry ? "暂无负债额度登记" : "负债额度数据不可用" }} columns={[
          { title: "交易所", dataIndex: "exchange_type" },
          { title: "币种", dataIndex: "coin" },
          { title: "当前负债", dataIndex: "liability", render: value => value ?? "—" },
          { title: "登记额度", dataIndex: "cap", render: value => value ?? "—" },
          { title: "状态", render: (_, row) => <Tag color={row.within_cap === true ? "green" : row.within_cap === false ? "red" : undefined}>
            {row.within_cap === true ? "额度内" : row.within_cap === false ? "超额度" : "未知"}</Tag> },
          { title: "备注", dataIndex: "reason" },
          { title: "登记时间", dataIndex: "registered_at", render: (value: number | string) => {
            const at = new Date(typeof value === "number" ? value * 1000 : value);
            return Number.isFinite(at.getTime()) ? at.toLocaleString("zh-CN", { hour12: false }) : "—";
          } },
          { title: "操作", render: (_, row) => <Button disabled={disabled} aria-label={`注销额度 ${row.exchange_type} ${row.coin}`}
            onClick={() => unregister(row)}>注销</Button> },
        ]} />
      <Typography.Text strong>登记额度</Typography.Text>
      <Space wrap align="end">
        <label>交易所<Select aria-label="负债交易所" value={exchange} disabled={disabled} style={{ width: 180 }}
          placeholder="请选择交易所" notFoundContent="暂无可用交易所" options={exchanges.map(value => ({ value, label: value }))}
          onChange={setSelectedExchange} /></label>
        <label>币种<Input aria-label="负债币种" value={coin} disabled={disabled} style={{ width: 120 }}
          onChange={event => { lockExchange(); setCoin(event.target.value); }} /></label>
        <label>额度（原币）<Input aria-label="负债额度" inputMode="decimal" value={cap} disabled={disabled} style={{ width: 190 }}
          placeholder="大于 0 且不超过 1000" onChange={event => { lockExchange(); setCap(event.target.value); }} /></label>
        <label>备注（必填）<Input aria-label="负债额度备注" value={reason} disabled={disabled} style={{ width: 240 }}
          onChange={event => { lockExchange(); setReason(event.target.value); }} /></label>
        <Button type="primary" disabled={disabled} onClick={register}>登记额度</Button>
        <Button disabled={busy || pending !== null} onClick={() => void refresh()}>刷新额度</Button>
      </Space>
    </Space>
    <Modal open={pending !== null} title={pending?.update.action === "unregister" ? "确认注销额度" : "确认登记额度"}
      okText="确认提交" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: unavailable || busy }}
      cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy} keyboard={!busy}
      onOk={() => void submit()} onCancel={() => { if (!writing.current) { setPending(null); setModalError(null); } }}>
      {pending && <>
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="交易所">{pending.update.exchange_type}</Descriptions.Item>
          <Descriptions.Item label="币种">{pending.update.coin}</Descriptions.Item>
          <Descriptions.Item label="登记额度">{pending.cap} {pending.update.coin}</Descriptions.Item>
          {pending.update.action === "register" && <Descriptions.Item label="备注">{pending.update.reason}</Descriptions.Item>}
        </Descriptions>
        <Alert type="warning" showIcon message={pending.update.action === "register" ? responsibility
          : "注销后，该交易所和币种的负债不再享有此额度，可能再次阻止程序开仓。确认注销？"} />
      </>}
      {modalError && <Alert type="error" showIcon message={modalError} />}
    </Modal>
  </Card>;
}
