import { useState, type FormEvent } from "react";
import { Alert, Button, Input, Typography } from "antd";
import { login } from "../api/client";
import { AUTH_REQUIRED_EVENT, loginReturnPath } from "../api/auth";

export function LoginPage() {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await login(password, code);
      setPassword("");
      setCode("");
      window.history.replaceState(null, "", loginReturnPath());
      window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
    } catch {
      setError("密码或验证码错误，或登录暂时不可用。多次失败请等待 15 分钟后重试。");
      setPassword("");
      setCode("");
    } finally {
      setBusy(false);
    }
  };
  return (
    <main style={{ maxWidth: 360, margin: "15vh auto", padding: 20 }}>
      <Typography.Title level={2}>登录 taoli1</Typography.Title>
      <form onSubmit={submit}>
        <label htmlFor="login-password">密码</label>
        <Input.Password id="login-password" autoComplete="current-password" value={password}
          required onChange={(event) => setPassword(event.target.value)} />
        <label htmlFor="login-code">6 位验证码</label>
        <Input id="login-code" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}"
          maxLength={6} required value={code} onChange={(event) => setCode(event.target.value)} />
        {error && <Alert type="error" message={error} />}
        <Button type="primary" htmlType="submit" loading={busy} block style={{ marginTop: 16 }}>登录</Button>
      </form>
    </main>
  );
}
