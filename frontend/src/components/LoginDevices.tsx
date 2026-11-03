import { useEffect, useState } from "react";
import { Alert, Button, List, Space, Typography } from "antd";
import { listSessions, logout, revokeAllSessions, type LoginSession } from "../api/client";
import { redirectToLogin } from "../api/auth";

export function LoginDevices() {
  const [sessions, setSessions] = useState<LoginSession[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => {
    try { setSessions(await listSessions()); setError(""); }
    catch { setError("无法加载登录设备"); }
  };
  useEffect(() => { void load(); }, []);
  const exit = async (all: boolean) => {
    setBusy(true);
    try {
      await (all ? revokeAllSessions() : logout());
      redirectToLogin();
    } catch { setError("退出失败，请重试"); }
    finally { setBusy(false); }
  };
  return <section aria-label="登录设备">
    <Typography.Title level={4}>登录设备</Typography.Title>
    {error && <Alert type="error" message={error} />}
    <List dataSource={sessions} renderItem={(session) => <List.Item>
      <div>
        <strong>{session.user_agent}{session.current ? "（当前设备）" : ""}</strong>
        <div>IP：{session.ip}</div>
        <div>最近使用：{new Date(session.last_seen_at * 1000).toLocaleString()}</div>
        <div>到期：{new Date(session.expires_at * 1000).toLocaleString()}</div>
      </div>
    </List.Item>} />
    <Space wrap>
      <Button onClick={() => void load()}>刷新设备</Button>
      <Button disabled={busy} onClick={() => void exit(false)}>退出登录</Button>
      <Button danger disabled={busy} onClick={() => void exit(true)}>退出所有设备</Button>
    </Space>
  </section>;
}
