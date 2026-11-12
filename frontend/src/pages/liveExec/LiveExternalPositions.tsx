import { Alert, Button, Card, Descriptions, Input, Modal, Space, Table, Tag, Typography } from "antd";
import { useRef, useState } from "react";
import { LiveAdminConflictError, liveAdminReadError, writeLiveExecAdmin,
  type LiveExternalIdentity, type LiveExternalPositions as Registry, type LiveRemotePositions } from "../../api/liveExec";
import { isPageHidden } from "../../utils/pageVisibility";
import { identityKey, remoteSnapshotFresh } from "./remotePositionView";

// Display model: the API adapter must explicitly establish ownership and action eligibility.
// Missing observations must never become an empty account or a zero USD valuation.
export interface ExternalPositionRow {
  key: string; identity: LiveExternalIdentity | null; route: string | null; exchange: string | null;
  symbol: string | null; dex: string | null; side: string | null; quantity: string | number | null;
  ownership: "program" | "external" | "unregistered" | "unknown";
  nativeUpnl: string | number | null; pnlCurrency: string | null; usdUpnl: string | number | null;
  conversion: string | null; action?: "register" | "unregister";
}
const direction = (row: ExternalPositionRow) => row.side === "long" ? "多" : row.side === "short" ? "空"
  : row.action === "unregister" ? "多、空均无仓位" : "—";
const dexLabel = (value: string | null) => value == null ? "—" : value === "" ? "无 DEX（原始值为空）" : value;
const labels = { program: "程序", external: "已登记外仓", unregistered: "未登记——导致停开", unknown: "归属未核实——导致停开" };
const actionLabel = (action: "register" | "unregister") => action === "register" ? "登记为外部持仓" : "取消登记";
const hasStatus = (value: string | undefined): value is string => typeof value === "string"
  && value.length === 67 && /^x1:[0-9a-f]{64}$/.test(value);

export function LiveExternalPositions({ registry, rows, error, remoteError, notice, refresh, snapshot, now = Date.now(), cancellable = [] }: {
  registry: Registry | null; rows: ExternalPositionRow[] | null; error?: string; notice?: string;
  remoteError?: string; snapshot: LiveRemotePositions | null; now?: number; cancellable?: ExternalPositionRow[];
  refresh: () => Promise<void>;
}) {
  const [pending, setPending] = useState<{ row: ExternalPositionRow; identity: LiveExternalIdentity;
    action: "register" | "unregister"; status: string } | null>(null);
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);
  const writing = useRef(false);
  const fresh = remoteSnapshotFresh(snapshot, now);
  const actionCurrent = (identity: LiveExternalIdentity, action: "register" | "unregister") => !error && !remoteError
    && hasStatus(registry?.status) && !isPageHidden() && remoteSnapshotFresh(snapshot, Date.now())
    && [...(rows ?? []), ...cancellable].some(row => row.action === action && row.identity && identityKey(row.identity) === identityKey(identity));
  const prepare = (row: ExternalPositionRow) => {
    if (!row.action || !row.identity || !hasStatus(registry?.status) || !actionCurrent(row.identity, row.action)) return;
    setPending({ row: { ...row }, identity: { ...row.identity }, action: row.action, status: registry.status });
    setReason(""); setFailure(null); setModalError(null); setSuccess(false);
  };
  const submit = async () => {
    if (!pending || writing.current) return;
    if (!actionCurrent(pending.identity, pending.action)) {
      setModalError("快照或持仓状态已变化，请刷新后重新确认"); return;
    }
    if (!reason.trim() || [...reason].length > 200 || /[\u0000-\u001f]/.test(reason)) {
      setModalError("请输入 1 至 200 字符的操作说明，不得含控制字符"); return;
    }
    writing.current = true; setBusy(true); setModalError(null);
    try {
      await writeLiveExecAdmin({ resource: "external-positions", action: pending.action,
        identity: pending.identity, expected_status: pending.status, reason });
      setPending(null); setReason(""); setSuccess(true);
      await refresh();
    } catch (cause) {
      if (cause instanceof LiveAdminConflictError) {
        setPending(null); setReason(""); setFailure(cause.message); setSuccess(false);
        await refresh();
      } else setModalError(cause instanceof Error ? cause.message : "操作失败，请刷新核实操作结果");
    } finally { writing.current = false; setBusy(false); }
  };
  return <Card title="外部持仓">
    <Space direction="vertical" style={{ width: "100%" }}>
      {error && <Alert type="warning" showIcon message={`外部持仓读取失败：${liveAdminReadError(error)}`} />}
      {remoteError && <Alert type="warning" showIcon message={`远端持仓读取失败：${liveAdminReadError(remoteError)}`} />}
      {notice && <Alert type="warning" showIcon message={notice} />}
      <>
        <Descriptions size="small" column={2}>
          <Descriptions.Item label="快照时间（UTC）">{snapshot?.snapshot_at ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="快照版本">{snapshot?.snapshot_version ?? "—"}</Descriptions.Item>
        </Descriptions>
        {!snapshot || !snapshot.available
          ? <Alert type="warning" showIcon message="尚无完整远端快照，不能确认账户为空；登记操作不可用" />
          : !fresh ? <Alert type="warning" showIcon message="快照已过期或无法核实新鲜度；以下为历史观测，登记操作不可用" />
            : <Typography.Text type="secondary">完整快照，10 秒内有效；显示的是快照时点的持仓。</Typography.Text>}
      </>
      {failure && <Alert type="error" showIcon message={failure} />}
      {success && <Alert type="success" showIcon message="外仓登记状态已更新，已请求刷新账户与管理数据" />}
      <Typography.Paragraph type="secondary">以下保留交易所、原始合约代码、方向和 DEX。登记按精确合约覆盖多空两个方向，永久禁止程序交易。归属、风险和可开仓状态以执行服务核实结果为准；USD 浮盈亏使用上游保守下界，缺失显示“—”。</Typography.Paragraph>
      <Table<ExternalPositionRow> size="small" rowKey="key" pagination={false} dataSource={rows ?? []} scroll={{ x: 1400 }}
        locale={{ emptyText: rows === null ? "远端持仓数据不可用，无法确认账户是否为空"
          : !fresh ? "快照已过期，无法确认当前是否空仓" : "账户无远端持仓" }} columns={[
          { title: "交易所 / 通道", render: (_, row) => <>{row.exchange ?? "—"}<br />{row.route ?? "—"}</> },
          { title: "原始合约代码", dataIndex: "symbol", render: value => value ?? "—" },
          { title: "方向", render: (_, row) => direction(row) },
          { title: "DEX", dataIndex: "dex", render: dexLabel },
          { title: "归属", render: (_, row) => <Tag color={row.ownership === "unregistered" || row.ownership === "unknown" ? "red" : undefined}>{labels[row.ownership]}</Tag> },
          { title: "原生数量", dataIndex: "quantity", render: value => value ?? "—" },
          { title: "当前浮盈亏（原币）", render: (_, row) => row.nativeUpnl == null || !row.pnlCurrency ? "—" : `${row.nativeUpnl} ${row.pnlCurrency}` },
          { title: "浮盈亏（USD）", render: (_, row) => typeof row.usdUpnl === "string" ? `${row.usdUpnl} USD`
            : row.usdUpnl == null || !Number.isFinite(row.usdUpnl) ? "—" : `${row.usdUpnl.toFixed(2)} USD` },
          { title: "折算状态", dataIndex: "conversion", render: value => value ?? "—" },
          { title: "操作", render: (_, row) => row.action ? <Button disabled={busy || !fresh || !!error || !!remoteError || !row.identity || !hasStatus(registry?.status)}
            aria-label={`${actionLabel(row.action)} ${row.symbol} ${direction(row)}`} onClick={() => prepare(row)}>{actionLabel(row.action)}</Button> : "—" },
        ]} />
      <Typography.Text>登记记录（不代表当前远端仓位）</Typography.Text>
      <Table size="small" rowKey="key" pagination={false} scroll={{ x: 900 }}
        dataSource={(registry?.contracts ?? []).map((record, index) => ({ ...record, key: `${JSON.stringify(record.identity)}:${index}` }))}
        locale={{ emptyText: registry ? "暂无登记记录" : "登记记录不可用" }} columns={[
          { title: "交易所 / 通道", render: (_, row) => <>{row.identity.exchange_type}<br />{row.identity.route}</> },
          { title: "原始合约代码", render: (_, row) => row.identity.symbol },
          { title: "DEX", render: (_, row) => dexLabel(row.identity.dex) },
          { title: "登记状态", render: (_, row) => row.registered ? "已登记" : "已取消登记" },
          { title: "程序交易限制", render: (_, row) => row.forbidden === true ? "永久禁单（多、空）" : "—" },
          { title: "操作", render: (_, record) => {
            const row = cancellable.find(item => item.identity && identityKey(item.identity) === identityKey(record.identity));
            return row ? <Button disabled={busy || !fresh || !!error || !!remoteError || !hasStatus(registry?.status)}
              aria-label={`取消登记 ${row.symbol} 多、空均无仓位`} onClick={() => prepare(row)}>取消登记</Button> : "—";
          } },
        ]} />
    </Space>
    <Modal open={!!pending} title={pending ? `确认${actionLabel(pending.action)}` : "确认外仓操作"}
      okText="确认提交" cancelText="取消" confirmLoading={busy} closable={!busy} maskClosable={!busy}
      cancelButtonProps={{ disabled: busy }} onOk={() => void submit()} onCancel={() => { if (!busy) { setPending(null); setReason(""); } }}>
      {pending && <>
        <Descriptions column={1} size="small">
          <Descriptions.Item label="通道">{pending.identity.route}</Descriptions.Item>
          <Descriptions.Item label="交易所">{pending.identity.exchange_type}</Descriptions.Item>
          <Descriptions.Item label="业务">{pending.identity.business}</Descriptions.Item>
          <Descriptions.Item label="原始合约代码">{pending.identity.symbol}</Descriptions.Item>
          <Descriptions.Item label="方向">{direction(pending.row)}</Descriptions.Item>
          <Descriptions.Item label="报价币">{pending.identity.quote}</Descriptions.Item>
          <Descriptions.Item label="DEX">{dexLabel(pending.identity.dex)}</Descriptions.Item>
        </Descriptions>
        <p>首次登记后，此精确合约的两个方向永久禁止本程序下单（含减仓单）；取消登记仍保留永久禁单记录。</p>
        <p>登记或取消后需显式重新建立收益基线，旧收益证明失效；操作本身不会解除停开或亏损锁。</p>
        {pending.action === "unregister" && <p>提交时执行服务会再次核对该合约多空均无远端仓位、且没有活动订单。</p>}
        <label>操作说明<Input.TextArea aria-label="操作说明" value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
      </>}
      {modalError && <Alert type="error" showIcon message={modalError} />}
    </Modal>
  </Card>;
}
