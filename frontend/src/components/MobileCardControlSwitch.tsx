import { useEffect, useState } from "react";
import { Alert, Button, Modal, Switch, Typography } from "antd";
import { getMobileCardControl, setMobileCardControl, type MobileCardControlState } from "../api/client";

export function MobileCardControlSwitch() {
  const [state, setState] = useState<MobileCardControlState | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    getMobileCardControl().then(
      (result) => { if (active) setState(result); },
      () => { if (active) setError("读取手机改卡开关失败"); }
    );
    return () => { active = false; };
  }, [reload]);

  async function save(enabled: boolean) {
    setConfirmOpen(false);
    setSaving(true);
    setError("");
    try {
      setState(await setMobileCardControl(enabled));
    } catch {
      setState(null);
      setError("保存手机改卡开关失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel" aria-label="手机改卡">
      <Typography.Title level={4}>手机改卡</Typography.Title>
      {error && <Alert type="error" showIcon message={error} action={
        <Button size="small" onClick={() => { setError(""); setReload(n => n + 1); }}>重新读取</Button>
      } />}
      {!state && !error && <p>正在读取开关…</p>}
      {state && <>
        <p>.env：{state.env_enabled ? "允许" : "禁止"}</p>
        <p>dry-run：{state.dry_run ? "开启" : "关闭"}</p>
        <div>运行时开关　<Switch
          aria-label="运行时开关"
          checked={state.runtime_enabled}
          checkedChildren="开" unCheckedChildren="关"
          loading={saving}
          disabled={saving || !state.env_enabled || state.dry_run}
          onChange={(enabled) => { if (enabled) setConfirmOpen(true); else void save(false); }}
        /></div>
        {!state.env_enabled && <p>.env 未允许手机改卡</p>}
        {state.dry_run && <p>dry-run 已开启</p>}
        <p>最终状态：{state.effective ? "已生效" : "未生效"}</p>
        <p>最后修改：{state.changed_at === null ? "暂无记录" :
          `${state.changed_by}（${new Date(state.changed_at * 1000).toLocaleString()}）`}</p>
      </>}
      <Modal title="打开手机改卡" open={confirmOpen} okText="确认打开" cancelText="取消"
        onCancel={() => setConfirmOpen(false)} onOk={() => void save(true)}>
        打开后，已绑定的手机可以修改 Astro 卡片的暂停/禁开/禁清
      </Modal>
    </section>
  );
}
