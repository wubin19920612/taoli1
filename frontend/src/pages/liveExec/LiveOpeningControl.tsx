import { Alert, Button, Modal, Space, Tag, Typography } from "antd";
import { useRef, useState } from "react";
import { LiveAdminConflictError, writeLiveExecAdmin, type LiveSummary } from "../../api/liveExec";

type Pending = { paused: boolean; mode: LiveSummary["mode"] };
type Notice = { type: "success" | "warning"; message: string };

function openingPaused(data: LiveSummary | null): boolean | null {
  const value = data?.opening_paused === undefined ? data?.stop_file : data.opening_paused;
  return typeof value === "boolean" ? value : null;
}

export function LiveOpeningControl({ data, hasAdminSession, refreshSummary }: {
  data: LiveSummary | null;
  hasAdminSession: boolean;
  refreshSummary: () => Promise<LiveSummary | null>;
}) {
  const paused = openingPaused(data);
  const mode = data?.mode ?? null;
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const writing = useRef(false);
  const disabled = !hasAdminSession || paused === null || (paused && mode !== "live" && mode !== "shadow");
  const actionLabel = paused ? "恢复开仓" : "暂停开仓";
  const pendingLabel = pending?.paused ? "恢复开仓" : "暂停开仓";

  const conflict = async () => {
    setPending(null); setModalError(null);
    const refreshed = await refreshSummary();
    setNotice({ type: "warning", message: refreshed ? "状态已变化，已刷新" : "状态已变化，刷新失败，请稍后核实" });
  };
  const submit = async () => {
    if (!pending || disabled || writing.current) return;
    writing.current = true; setBusy(true); setModalError(null); setNotice(null);
    try {
      // Read the server again: polling may not yet reflect a mode/state change.
      // Compare the response itself instead of the render's cached summary.
      const latest = await refreshSummary();
      if (!latest) throw new Error("无法获取最新开仓状态，请刷新后重新确认");
      if (pending.paused !== openingPaused(latest) || pending.mode !== (latest.mode ?? null)) {
        setPending(null);
        setNotice({ type: "warning", message: "状态已变化，请重新确认" });
        return;
      }
      await writeLiveExecAdmin({ resource: "opening", action: pending.paused ? "resume" : "pause",
        expected_paused: pending.paused });
      setPending(null);
      const refreshed = await refreshSummary();
      setNotice({ type: refreshed ? "success" : "warning",
        message: refreshed ? "操作成功，已刷新开仓状态" : "操作成功，汇总刷新失败，请稍后核实" });
    } catch (failure) {
      if (failure instanceof LiveAdminConflictError) await conflict();
      else setModalError(failure instanceof Error ? failure.message : "操作失败，请刷新核实操作结果");
    } finally {
      writing.current = false; setBusy(false);
    }
  };

  return <Space direction="vertical" style={{ width: "100%" }}>
    <Space wrap>
      <Tag color={paused === true ? "orange" : paused === false ? "green" : undefined}>
        {paused === true ? "开仓：已暂停" : paused === false ? "开仓：运行中" : "开仓：状态未知"}
      </Tag>
      <Tag color={mode === "live" ? "volcano" : "blue"}>
        {mode === "live" ? "实盘模式" : mode === "shadow" ? "影子模式" : "模式未知"}
      </Tag>
      <Button aria-label={actionLabel} disabled={disabled || busy} loading={busy} onClick={() => {
        if (disabled || writing.current || paused === null) return;
        setModalError(null); setNotice(null); setPending({ paused, mode });
      }}>{actionLabel}</Button>
      {!hasAdminSession && <Typography.Text type="secondary">管理员会话未验证，开仓操作不可用</Typography.Text>}
      {paused && mode !== "live" && mode !== "shadow" && <Typography.Text type="secondary">执行模式未知，暂不可恢复开仓</Typography.Text>}
    </Space>
    {data?.opening_paused_reason && <Typography.Text type="secondary">开仓限制：{data.opening_paused_reason}</Typography.Text>}
    <Typography.Text type="secondary">仅控制新开仓，已有持仓继续管理和平仓。</Typography.Text>
    {notice && <Alert type={notice.type} showIcon message={notice.message} />}
    <Modal open={pending !== null} title={`确认${pendingLabel}`} okText={`确认${pendingLabel}`} cancelText="取消"
      confirmLoading={busy} okButtonProps={{ disabled: disabled || busy, "aria-label": `确认${pendingLabel}` }}
      cancelButtonProps={{ disabled: busy }}
      closable={!busy} maskClosable={!busy} keyboard={!busy} onOk={() => void submit()}
      onCancel={() => { if (!writing.current) { setPending(null); setModalError(null); } }}>
      <Typography.Paragraph>{pending?.paused ? "确认恢复新开仓？" : "确认暂停新开仓？"}已有持仓继续管理和平仓。</Typography.Paragraph>
      {pending?.paused && pending.mode === "live" && <Alert type="warning" showIcon message="恢复后程序会用真实资金自动开仓" />}
      {modalError && <Alert type="error" showIcon message={modalError} />}
    </Modal>
  </Space>;
}
