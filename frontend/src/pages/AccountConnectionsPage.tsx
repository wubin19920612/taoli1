import {
  ApiOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeInvisibleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createAccountConnection,
  deleteAccountConnection,
  listAccountConnections,
  saveDashboardPassword,
  testDraftAccountConnection,
  testSavedAccountConnection,
  updateAccountConnection
} from "../api/client";
import type {
  AccountConnection,
  AccountConnectionExchange,
  AccountConnectionOverview,
  AccountConnectionTestResult,
  AccountConnectionUpdate,
  AccountConnectionWrite
} from "../api/types";
import "./AccountConnectionsPage.css";


type ConnectionForm = AccountConnectionWrite;

const exchangeLabels: Record<AccountConnectionExchange, string> = {
  binance: "Binance",
  okx: "OKX",
  bybit: "Bybit",
  gate: "Gate",
  bitget: "Bitget",
  hyperliquid: "Hyperliquid"
};

const stateLabels: Record<string, { label: string; color: string }> = {
  ok: { label: "读取成功", color: "green" },
  empty: { label: "已核验无持仓", color: "default" },
  permission_denied: { label: "权限不足", color: "red" },
  error: { label: "测试失败", color: "red" },
  stale: { label: "数据过期", color: "orange" },
  not_configured: { label: "未配置", color: "default" }
};

function dateTime(value: string | null): string {
  if (!value) return "尚未测试";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function scopes(connection: AccountConnection): string[] {
  if (connection.exchange === "hyperliquid") {
    return [`永续 · ${connection.dex || "main"}`];
  }
  return [
    ...(connection.include_spot ? ["现货"] : []),
    ...(connection.include_futures ? ["永续"] : [])
  ];
}

function sanitizedUpdate(values: ConnectionForm): AccountConnectionUpdate {
  const update: AccountConnectionUpdate = {
    account_label: values.account_label,
    enabled: values.enabled,
    include_spot: values.include_spot,
    include_futures: values.include_futures,
    dex: values.dex || null
  };
  for (const key of ["api_key", "api_secret", "passphrase", "public_address"] as const) {
    const value = values[key]?.trim();
    if (value) update[key] = value;
  }
  return update;
}

export function AccountConnectionsPage() {
  const [form] = Form.useForm<ConnectionForm>();
  const exchange = Form.useWatch("exchange", form) ?? "binance";
  const [overview, setOverview] = useState<AccountConnectionOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [password, setPassword] = useState(
    () => window.localStorage.getItem("dashboard_password") ?? ""
  );
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<AccountConnection | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<AccountConnectionTestResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setOverview(await listAccountConnections());
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setTestResult(null);
    form.setFieldsValue({
      exchange: "binance",
      account_label: "",
      enabled: true,
      include_spot: true,
      include_futures: true,
      dex: null,
      api_key: "",
      api_secret: "",
      passphrase: "",
      public_address: ""
    });
    setModalOpen(true);
  };

  const openEdit = (connection: AccountConnection) => {
    setEditing(connection);
    setTestResult(null);
    form.setFieldsValue({
      exchange: connection.exchange,
      account_label: connection.account_label,
      enabled: connection.enabled,
      include_spot: connection.include_spot,
      include_futures: connection.include_futures,
      dex: connection.dex,
      api_key: "",
      api_secret: "",
      passphrase: "",
      public_address: ""
    });
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditing(null);
    setTestResult(null);
    form.resetFields();
  };

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      if (editing) {
        await updateAccountConnection(editing.id, sanitizedUpdate(values));
        message.success("账户连接已更新");
      } else {
        await createAccountConnection(values);
        message.success("账户连接已添加");
      }
      closeModal();
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  };

  const testSaved = async (connection: AccountConnection) => {
    setTesting(connection.id);
    try {
      const result = await testSavedAccountConnection(connection.id);
      setTestResult(result);
      message[result.success ? "success" : "warning"](
        result.success ? "账户读取测试通过" : "部分读取范围测试失败"
      );
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTesting(null);
    }
  };

  const testDraft = async () => {
    const values = await form.validateFields();
    setTesting("draft");
    try {
      const result = await testDraftAccountConnection(values);
      setTestResult(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTesting(null);
    }
  };

  const toggleEnabled = async (connection: AccountConnection, enabled: boolean) => {
    setTesting(connection.id);
    try {
      await updateAccountConnection(connection.id, { enabled });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTesting(null);
    }
  };

  const remove = async (connection: AccountConnection) => {
    setTesting(connection.id);
    try {
      await deleteAccountConnection(connection.id);
      message.success("账户连接已删除");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTesting(null);
    }
  };

  const summary = useMemo(() => {
    const connections = overview?.connections ?? [];
    return {
      configured: connections.length,
      enabled: connections.filter((item) => item.enabled).length,
      passed: connections.filter((item) => ["ok", "empty"].includes(item.last_test_state ?? "")).length,
      failed: connections.filter((item) => ["error", "permission_denied"].includes(item.last_test_state ?? "")).length
    };
  }, [overview]);

  const columns: ColumnsType<AccountConnection> = [
    {
      title: "交易所 / 账户",
      key: "account",
      width: 190,
      render: (_, connection) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{exchangeLabels[connection.exchange]}</Typography.Text>
          <Typography.Text type="secondary">{connection.account_label}</Typography.Text>
        </Space>
      )
    },
    {
      title: "读取范围",
      key: "scopes",
      width: 150,
      render: (_, connection) => scopes(connection).map((scope) => <Tag key={scope}>{scope}</Tag>)
    },
    {
      title: "凭据",
      dataIndex: "credential_hint",
      width: 145,
      render: (hint: string) => <Typography.Text code>{hint}</Typography.Text>
    },
    {
      title: "最近测试",
      key: "test",
      width: 220,
      render: (_, connection) => {
        const state = stateLabels[connection.last_test_state ?? "not_configured"];
        return (
          <Space direction="vertical" size={0}>
            <Tag color={state.color}>{state.label}</Tag>
            <Typography.Text type="secondary" title={connection.last_test_message ?? undefined}>
              {dateTime(connection.last_tested_at)}
            </Typography.Text>
          </Space>
        );
      }
    },
    {
      title: "启用",
      key: "enabled",
      width: 72,
      render: (_, connection) => (
        <Switch
          size="small"
          checked={connection.enabled}
          aria-label={`${connection.account_label}账户连接`}
          loading={testing === connection.id}
          onChange={(enabled) => void toggleEnabled(connection, enabled)}
        />
      )
    },
    {
      title: "操作",
      key: "actions",
      width: 128,
      fixed: "right",
      render: (_, connection) => (
        <Space size={2}>
          <Tooltip title="测试读取">
            <Button
              type="text"
              aria-label={`测试 ${connection.account_label}`}
              icon={<ApiOutlined />}
              loading={testing === connection.id}
              onClick={() => void testSaved(connection)}
            />
          </Tooltip>
          <Tooltip title="编辑">
            <Button
              type="text"
              aria-label={`编辑 ${connection.account_label}`}
              icon={<EditOutlined />}
              onClick={() => openEdit(connection)}
            />
          </Tooltip>
          <Popconfirm
            title="删除账户连接？"
            description="只删除本系统的连接配置，不会修改交易所账户或仓位。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => void remove(connection)}
          >
            <Tooltip title="删除">
              <Button
                type="text"
                danger
                aria-label={`删除 ${connection.account_label}`}
                icon={<DeleteOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      )
    }
  ];

  const selectedMetadata = overview?.supported_exchanges.find((item) => item.exchange === exchange);
  const isHyperliquid = exchange === "hyperliquid";
  const needsPassphrase = exchange === "okx" || exchange === "bitget";

  return (
    <div className="account-connections-page">
      <header className="account-connections-header">
        <div>
          <Typography.Title level={4}>账户持仓连接</Typography.Title>
          <Typography.Text type="secondary">
            Hyperliquid 使用公开地址；其他交易所仅使用具备读取权限、无交易和提现权限的独立 API。
          </Typography.Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          aria-label="添加账户"
          onClick={openCreate}
          disabled={!overview?.storage_ready}
        >
          添加账户
        </Button>
      </header>

      <section className="account-connections-auth" aria-label="面板访问密码">
        <Input.Password
          value={password}
          prefix={<SafetyCertificateOutlined />}
          placeholder="仪表盘密码"
          aria-label="仪表盘密码"
          onChange={(event) => setPassword(event.target.value)}
          onPressEnter={() => {
            saveDashboardPassword(password);
            void load();
          }}
        />
        <Button
          icon={<ReloadOutlined />}
          onClick={() => {
            saveDashboardPassword(password);
            void load();
          }}
        >
          保存并刷新
        </Button>
      </section>

      {error ? <Alert type="error" showIcon closable message={error} onClose={() => setError("")} /> : null}
      {overview && !overview.storage_ready ? (
        <Alert type="warning" showIcon message={overview.storage_message} />
      ) : null}

      <section className="account-connections-summary" aria-label="账户连接概览">
        <div><span>已配置</span><strong>{summary.configured}</strong></div>
        <div><span>已启用</span><strong>{summary.enabled}</strong></div>
        <div><span>已核验</span><strong>{summary.passed}</strong></div>
        <div><span>需处理</span><strong>{summary.failed}</strong></div>
      </section>

      <section className="account-connections-table-shell" aria-label="账户连接列表">
        <Table<AccountConnection>
          rowKey="id"
          columns={columns}
          dataSource={overview?.connections ?? []}
          loading={loading}
          pagination={false}
          scroll={{ x: 905 }}
          locale={{ emptyText: "尚未配置账户连接" }}
          size="middle"
        />
      </section>

      {testResult ? (
        <Alert
          className="account-connections-test-result"
          type={testResult.success ? "success" : "warning"}
          showIcon
          closable
          onClose={() => setTestResult(null)}
          message={`${testResult.account_label}：${testResult.success ? "测试通过" : "测试未全部通过"}`}
          description={testResult.scopes.map((scope) => (
            <div key={`${scope.market_type}-${scope.dex ?? ""}`}>
              {scope.market_type === "spot" ? "现货" : `永续${scope.dex ? ` · ${scope.dex}` : ""}`}：
              {scope.message}（{scope.position_count} 个持仓）
            </div>
          ))}
        />
      ) : null}

      <Modal
        title={editing ? `编辑 ${editing.account_label}` : "添加账户连接"}
        open={modalOpen}
        onCancel={closeModal}
        width={620}
        footer={[
          <Button key="cancel" onClick={closeModal}>取消</Button>,
          !editing ? (
            <Button key="test" icon={<ApiOutlined />} loading={testing === "draft"} onClick={() => void testDraft()}>
              测试连接
            </Button>
          ) : null,
          <Button key="save" type="primary" loading={saving} onClick={() => void submit()}>
            保存
          </Button>
        ]}
      >
        <Form<ConnectionForm>
          form={form}
          layout="vertical"
          requiredMark={false}
          className="account-connections-form"
        >
          <div className="account-connections-form-grid">
            <Form.Item label="交易所" name="exchange" rules={[{ required: true }]}>
              <Select
                disabled={Boolean(editing)}
                options={(overview?.supported_exchanges ?? []).map((item) => ({
                  value: item.exchange,
                  label: item.label
                }))}
                onChange={(value: AccountConnectionExchange) => {
                  if (value === "hyperliquid") {
                    form.setFieldsValue({ include_spot: false, include_futures: true, dex: "main" });
                  } else {
                    form.setFieldsValue({ include_spot: true, include_futures: true, dex: null });
                  }
                }}
              />
            </Form.Item>
            <Form.Item label="账户名称" name="account_label" rules={[{ required: true, message: "请输入账户名称" }]}>
              <Input maxLength={80} placeholder="例如：主账户" />
            </Form.Item>
          </div>

          <Typography.Paragraph type="secondary" className="account-connections-exchange-note">
            {selectedMetadata?.note}
          </Typography.Paragraph>

          <div className="account-connections-scope-row">
            <Form.Item label="现货余额" name="include_spot" valuePropName="checked">
              <Switch disabled={isHyperliquid} />
            </Form.Item>
            <Form.Item label="永续持仓" name="include_futures" valuePropName="checked">
              <Switch disabled={isHyperliquid} />
            </Form.Item>
            <Form.Item label="启用连接" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </div>

          {isHyperliquid ? (
            <>
              <Form.Item
                label="公开地址"
                name="public_address"
                rules={editing ? [] : [{ required: true, message: "请输入 Hyperliquid 公开地址" }]}
                extra={editing ? "留空表示保留当前地址" : undefined}
              >
                <Input.Password prefix={<EyeInvisibleOutlined />} autoComplete="off" placeholder="0x..." />
              </Form.Item>
              <Form.Item label="DEX" name="dex" rules={[{ required: true, message: "请输入 main 或具体 DEX" }]}>
                <Input placeholder="main" />
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item
                label="API Key"
                name="api_key"
                rules={editing ? [] : [{ required: true, message: "请输入 API Key" }]}
                extra={editing ? "留空表示保留当前 API Key" : undefined}
              >
                <Input.Password autoComplete="off" />
              </Form.Item>
              <Form.Item
                label="API Secret"
                name="api_secret"
                rules={editing ? [] : [{ required: true, message: "请输入 API Secret" }]}
                extra={editing ? "留空表示保留当前 Secret" : undefined}
              >
                <Input.Password autoComplete="new-password" />
              </Form.Item>
              {needsPassphrase ? (
                <Form.Item
                  label="Passphrase"
                  name="passphrase"
                  rules={editing ? [] : [{ required: true, message: "请输入 Passphrase" }]}
                  extra={editing ? "留空表示保留当前 Passphrase" : undefined}
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
              ) : null}
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
