import { Alert, Button, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography, message } from "antd";
import { useEffect, useState } from "react";
import { getMarketIdentities, previewMarketIdentity, saveMarketIdentity, saveMarketValidationSettings, getMarketCardAudit } from "../api/client";
import type { MarketBinding, MarketIdentityOverview } from "../api/types";

const statusNames: Record<string, string> = { pending: "待核验", verified: "已核验", conflict: "身份冲突", expired: "已过期" };

export function MarketIdentityPanel() {
  const [data, setData] = useState<MarketIdentityOverview | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<MarketBinding | null>(null);
  const [impact, setImpact] = useState<number | null>(null);
  const [audit, setAudit] = useState<Array<{ card_id?: string; card_name: string; errors: string[]; validation?: { blockers: Array<{ message: string }> } }>>([]);
  const [form] = Form.useForm();

  async function load(nextPage = page, term = search) {
    setLoading(true);
    try {
      const next = await getMarketIdentities(term, (nextPage - 1) * 50, 50);
      if (!next.coverage || !Array.isArray(next.items)) throw new Error("市场核验接口尚未就绪");
      setData(next);
      setError("");
    } catch (reason) { setError(String(reason)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(1, ""); }, []);

  async function edit(row: MarketBinding) {
    setEditing(row);
    setImpact(null);
    form.setFieldsValue({
      asset_id: row.asset_id ?? "", display_name: row.display_name,
      asset_kind: row.asset_kind, contract_kind: row.contract_kind, tracking_definition: row.tracking_definition ?? "",
      identity_status: row.identity_status, unit_status: row.unit_status,
      quote_currency: row.quote_currency, settlement_currency: row.settlement_currency,
      price_multiplier: row.price_multiplier, contract_size: row.contract_size,
      valid_until: (row.valid_until ?? new Date(Date.now() + 30 * 86400000).toISOString()).slice(0, 10),
      url: row.evidence[0]?.url ?? "", summary: row.evidence[0]?.summary ?? "", reason: ""
    });
    try {
      const preview = await previewMarketIdentity({ binding: row, expected_revision: row.revision, reason: "预览市场核验影响" });
      setImpact(preview.affected_opportunities);
    } catch (reason) { setError(String(reason)); }
  }

  async function save() {
    if (!editing) return;
    try {
      const values = await form.validateFields();
      const now = new Date().toISOString();
      const verified = values.identity_status === "verified";
      const binding: MarketBinding = {
        ...editing, asset_id: values.asset_id || null, display_name: values.display_name,
        asset_kind: values.asset_kind, contract_kind: values.contract_kind, tracking_definition: values.tracking_definition,
        identity_status: values.identity_status, unit_status: verified ? values.unit_status : "pending",
        quote_currency: values.quote_currency, settlement_currency: values.settlement_currency || null,
        price_multiplier: values.price_multiplier, contract_size: values.contract_size ?? null,
        verified_at: verified ? now : editing.verified_at,
        valid_until: new Date(values.valid_until + "T23:59:59").toISOString(),
        evidence: values.url && values.summary ? [{ source_type: "official_specification", url: values.url, summary: values.summary, fetched_at: now }] : []
      };
      await saveMarketIdentity({ binding, expected_revision: editing.revision, reason: values.reason });
      setEditing(null);
      message.success("市场核验已保存，旧资格已失效");
      await load();
    } catch (reason) {
      if (reason instanceof Error) message.error(reason.message);
    }
  }

  return <section className="panel panel-wide">
    <Typography.Title level={4}>市场核验</Typography.Title>
    <Typography.Paragraph type="secondary">按原始市场确认底层资产和合约单位。同名资产分别展示；观察模式保留未知市场流程并拦截确认错配，严格模式要求身份核验通过。</Typography.Paragraph>
    {error ? <Alert type="warning" message="市场核验信息读取失败" description={error} showIcon /> : null}
    {data ? <Space wrap style={{ marginBottom: 12 }}>
      <Tag>已核验 {data.coverage.verified} / {data.coverage.markets}</Tag>
      <Tag color="orange">待核验 {data.coverage.pending}</Tag>
      <Tag>参考报价 {data.coverage.reference_quotes}</Tag>
      <Select aria-label="核验运行模式" value={data.settings.mode} style={{ width: 160 }}
        options={[{ value: "shadow", label: "观察模式" }, { value: "enforce", label: "严格校验" }]}
        onChange={async (mode) => {
          try { await saveMarketValidationSettings({ ...data.settings, mode }); await load(); }
          catch (reason) { message.error(String(reason)); }
        }} />
    </Space> : null}
    <Space wrap style={{ marginBottom: 12 }}>
      <Input.Search aria-label="搜索核验市场" placeholder="资产、原始代码或交易所" value={search} onChange={(e) => setSearch(e.target.value)}
        onSearch={() => { setPage(1); void load(1); }} style={{ width: 280, maxWidth: "100%" }} />
      <Button onClick={() => void load()}>刷新核验</Button>
      <Button onClick={async () => {
        try { setAudit(await getMarketCardAudit()); }
        catch (reason) { message.error(String(reason)); }
      }}>审计已有卡片</Button>
    </Space>
    {data?.settings.mode === "shadow" ? <Alert type="info" showIcon message="观察模式：未知身份仅记录；已确认的不同资产和身份冲突仍禁止同币配对。" /> : null}
    <Table<MarketBinding> dataSource={data?.items ?? []} loading={loading} rowKey={(row) => JSON.stringify(row.market)} size="small" scroll={{ x: 850 }}
      pagination={{ current: page, pageSize: 50, total: data?.total ?? 0, showSizeChanger: false, onChange: (value) => { setPage(value); void load(value); } }}
      columns={[
        { title: "原始市场", render: (_, row) => <Space direction="vertical" size={0}><span>{row.market.exchange} · {row.market.market_type === "future" ? "永续" : "现货"}</span><span>{row.market.raw_symbol}{row.market.dex ? ` · ${row.market.dex}` : ""}</span></Space> },
        { title: "底层资产", render: (_, row) => <Space direction="vertical" size={0}><span>{row.display_name}</span><Typography.Text type="secondary">{row.asset_id ?? "身份未知"}</Typography.Text></Space> },
        { title: "状态", render: (_, row) => <Tag color={row.identity_status === "verified" ? "green" : "orange"}>{statusNames[row.identity_status]}</Tag> },
        { title: "单位", render: (_, row) => row.unit_status === "verified" ? `价格 × ${row.price_multiplier} · ${row.quote_currency}` : "待核验" },
        { title: "证据", render: (_, row) => row.evidence.length ? <a href={row.evidence[0].url} target="_blank" rel="noreferrer">官方来源</a> : "待补充" },
        { title: "操作", render: (_, row) => <Button size="small" onClick={() => void edit(row)}>核验市场</Button> }
      ]} />
    {audit.length ? <Alert type="info" message={`已读取 ${audit.length} 张卡片，仅展示审计结果；仓位状态未知。`}
      description={<ul>{audit.filter((row) => row.errors.length || row.validation?.blockers.length).slice(0, 30).map((row, index) =>
        <li key={row.card_id ?? index}>{row.card_name}：{[...row.errors, ...(row.validation?.blockers.map((item) => item.message) ?? [])].join("；")}</li>)}</ul>} /> : null}
    <Modal title="核验精确市场" open={editing !== null} onCancel={() => setEditing(null)} onOk={() => void save()} okText="保存核验" cancelText="取消" width={640}>
      <Typography.Paragraph>{editing?.market.exchange} · {editing?.market.raw_symbol} · {editing?.market.dex ?? ""}</Typography.Paragraph>
      <Alert type="info" message={impact === null ? "正在计算影响范围" : `影响当前 ${impact} 条机会；保存后重新校验。已有外部卡片保持原状。`} />
      <Form form={form} layout="vertical">
        <Form.Item name="asset_id" label="资产标识" extra="同一底层资产使用同一标识；不同项目分别登记。"><Input /></Form.Item>
        <Form.Item name="display_name" label="资产名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="asset_kind" label="资产类别"><Select options={[{ value: "crypto", label: "加密资产" }, { value: "equity", label: "股票" }, { value: "index", label: "指数" }, { value: "commodity", label: "商品" }, { value: "valuation", label: "估值" }, { value: "other", label: "其他" }]} /></Form.Item>
        <Form.Item name="contract_kind" label="合约口径"><Select options={[{ value: "spot", label: "现货" }, { value: "linear", label: "线性合约" }, { value: "inverse", label: "反向合约" }, { value: "valuation", label: "估值合约" }]} /></Form.Item>
        <Form.Item name="tracking_definition" label="跟踪对象与计价单位" extra="估值合约需写明单位，例如公司总估值，以十亿美元计。"><Input.TextArea rows={2} /></Form.Item>
        <Form.Item name="identity_status" label="身份结论"><Select options={Object.entries(statusNames).map(([value, label]) => ({ value, label }))} /></Form.Item>
        <Form.Item name="unit_status" label="单位结论"><Select options={[{ value: "pending", label: "待核验" }, { value: "verified", label: "已核验价格与数量单位" }]} /></Form.Item>
        <Space align="start" wrap>
          <Form.Item name="quote_currency" label="原始报价币种" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="settlement_currency" label="结算币种"><Input /></Form.Item>
          <Form.Item name="price_multiplier" label="标准价格乘数" rules={[{ required: true }]}><InputNumber min={0.000000001} /></Form.Item>
          <Form.Item name="contract_size" label="每张合约数量"><InputNumber min={0.000000001} placeholder="未知" /></Form.Item>
        </Space>
        <Form.Item name="valid_until" label="有效截止日期" rules={[{ required: true }]}><Input type="date" /></Form.Item>
        <Form.Item name="url" label="官方证据链接" rules={[{ type: "url" }]}><Input placeholder="https://" /></Form.Item>
        <Form.Item name="summary" label="证据与单位说明"><Input.TextArea rows={3} /></Form.Item>
        <Form.Item name="reason" label="本次修改原因" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={2} /></Form.Item>
      </Form>
    </Modal>
  </section>;
}
