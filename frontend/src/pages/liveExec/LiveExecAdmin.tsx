import { Alert, Button, Card, Checkbox, Input, Modal, Space, Table, Tag, Typography } from "antd";
import { useRef, useState, type ReactNode } from "react";
import { liveAdminReadError, writeLiveExecAdmin, type LiveAdminWrite, type LiveCategory,
  type LiveMapping, type LiveUniverse } from "../../api/liveExec";
import { useLiveExecAdmin } from "./useLiveExecAdmin";

const labels: Record<string, string> = {
  min_quote_volume_usd: "单所 24 小时成交额门槛（USD）", min_venues_with_volume: "达到成交额门槛的交易所数",
  sampling_min_quote_volume_usd: "采样成交额预筛门槛（USD）", order_usd_max: "单腿名义额上限（USD）",
  depth_fraction: "较薄腿容忍带内深度占比", oi_fraction: "有效 OI 占比", max_total_open_usd: "全部未平名义额上限（USD）",
  max_open_pairs: "同时持仓对数上限", max_equity_drop_usd: "权益回撤停机阈值（USD）",
  daily_loss_stop_usd: "日亏停开阈值（USD）", total_loss_stop_usd: "累计亏损停机阈值（USD）",
  min_excess_bps: "最低超额价差（bp）", max_book_skew_ms: "最大盘口时间差（毫秒）",
  max_book_age_seconds: "最大盘口年龄（秒）", max_funding_diff_bps_h: "最大每小时资金费率差（bp）",
  max_funding_rate_8h: "最大八小时资金费率（比例）", max_baseline_bps: "最大基准偏移（bp）",
  max_baseline_mad_bps: "最大基准波动（bp）", max_normal_deviation_bps: "最大正常所偏离（bp）",
  max_reference_age_seconds: "最大参考价格年龄（秒）", min_outlier_share: "最低异常贡献比例", min_venues: "最少参考交易所数",
};
const categories: Record<LiveCategory, string> = {
  crypto: "加密币", listed_equity: "已上市股票 / ETF", pre_ipo: "上市前（不交易）",
  commodity: "商品（不交易）", unconfirmed: "待人工判断",
};
const statuses = { pending: "待确认", confirmed: "已确认", rejected: "已拒绝" };
const num = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : String(value);
const date = (value: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const categoryLabel = (value: LiveCategory) => categories[value] ?? "待人工判断";

function MappingDetails({ mapping }: { mapping: LiveMapping }) {
  return <Space direction="vertical">
    {mapping.legs.map(leg => <div key={leg.market_key}><div>{leg.market_key}</div>
      <small>原生代码：{leg.raw_base}；数量倍率：{num(leg.quantity_multiplier)}</small></div>)}
    <div>发现时原生价格比（上列第一腿 / 第二腿）：{num(mapping.ratio)}；采集时间：{date(mapping.at)}</div>
  </Space>;
}

type Pending = { update: LiveAdminWrite; title: string; details: ReactNode; configVersion?: number };
export function LiveExecAdmin() {
  const { data, errors, refresh } = useLiveExecAdmin();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Pending | null>(null);
  const [classification, setClassification] = useState<LiveUniverse | null>(null);
  const [category, setCategory] = useState<LiveCategory>("unconfirmed");
  const [market, setMarket] = useState<"" | "US" | "KRX">("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);
  const writing = useRef(false);
  const config = data.config;
  const prepare = (value: Pending) => { setError(null); setModalError(null); setSuccess(false); setPending(value); };
  const prepareConfig = () => {
    setError(null); setSuccess(false);
    if (!config) return;
    const changes: Record<string, number> = {};
    for (const [key, raw] of Object.entries(draft)) {
      const value = Number(raw), rule = config.schema[key];
      if (!rule || !raw.trim() || !Number.isFinite(value)) { setError(`${labels[key] ?? key}：请输入有效数字`); return; }
      if (rule.integer && !Number.isInteger(value)) { setError(`${labels[key] ?? key}：必须为整数`); return; }
      if (value < rule.min || (rule.max !== null && value > rule.max)) {
        setError(`${labels[key] ?? key}：超出允许范围 ${rule.min} 至 ${rule.max ?? "无代码上限"}`); return;
      }
      if (value !== config.values[key]) changes[key] = value;
    }
    if (!Object.keys(changes).length) { setError("尚未修改参数"); return; }
    prepare({ update: { resource: "config", changes }, title: "确认参数修改", configVersion: config.version,
      details: <><p>请核对当前值 → 新值（版本 {config.version}）：</p>
        {Object.entries(changes).map(([key, value]) => <p key={key}>{labels[key] ?? key}（{key}）：{config.values[key]} → {value}</p>)}
        <p>新开仓使用新版本；已有持仓保留开仓版本，账户损失门槛按活动持仓取最严格值。最终以后端校验为准。</p></> });
  };
  const submit = async (update: LiveAdminWrite) => {
    if (writing.current) return;
    writing.current = true; setBusy(true); setModalError(null);
    try {
      await writeLiveExecAdmin(update);
      if (update.resource === "config") setDraft({});
      setPending(null); setClassification(null); setSuccess(true);
      await refresh(true);
    } catch (failure) {
      setModalError(failure instanceof Error ? failure.message : "操作失败，请刷新核实操作结果");
    } finally { writing.current = false; setBusy(false); }
  };
  const confirmPending = () => {
    if (!pending) return;
    if (pending.configVersion !== undefined && (!config || config.version !== pending.configVersion)) {
      setModalError("参数版本已变化，请取消并重新核对新旧值"); return;
    }
    void submit(pending.update);
  };
  const openClassification = (row: LiveUniverse) => {
    setError(null); setSuccess(false); setModalError(null); setClassification(row);
    setCategory(row.category); setMarket(row.underlying_market ?? "");
    setSelected(row.markets.filter(item => item.selected).map(item => item.market_key));
  };
  const confirmClassification = () => {
    if (!classification) return;
    if (category === "unconfirmed") { setModalError("请选择类别"); return; }
    if (category === "listed_equity" && !market) { setModalError("请选择标的市场 US 或 KRX"); return; }
    if (!selected.length) { setModalError("请至少选择一个精确合约"); return; }
    void submit({ resource: "universe", base: classification.base, action: "classify",
      classification: { category, markets: selected, ...(category === "listed_equity" && market ? { underlying_market: market } : {}) } });
  };
  const readFailure = (resource: keyof typeof data, label: string) => errors[resource]
    ? <Alert type="warning" showIcon message={`${label}读取失败：${liveAdminReadError(errors[resource]!)}`} /> : null;
  return <Space direction="vertical" size="middle" style={{ width: "100%" }}>
    <Space wrap><Button disabled={busy} onClick={() => void refresh(true)}>刷新管理数据</Button>
      <Typography.Text type="secondary">管理数据每 30 秒刷新；成交额为各精确合约 24 小时 USDT 报价成交额，超过 5 分钟或未知显示“—”。</Typography.Text></Space>
    {error && <Alert type="error" showIcon message={error} />}
    {success && <Alert type="success" showIcon message="操作成功，已请求刷新管理数据" />}
    <Card title="参数">
      {readFailure("config", "参数")}
      {config?.fallback && <Alert type="warning" message="参数存储不可读，当前使用默认值；请先检查实盘服务" />}
      <Typography.Paragraph>配置版本：{config?.version ?? "—"}。比例参数按小数填写；上下限由服务端约束，修改只在确认后提交。</Typography.Paragraph>
      <Table size="small" rowKey="key" pagination={false} scroll={{ x: 900 }} locale={{ emptyText: "暂无参数" }}
        dataSource={config ? Object.entries(config.schema).map(([key, rule]) => ({ key, ...rule })) : []}
        columns={[
          { title: "参数", dataIndex: "key", render: (key: string) => <><div>{labels[key] ?? key}</div><small>{key}</small></> },
          { title: "当前值", render: (_, row) => num(config?.values[row.key]) },
          { title: "默认值", dataIndex: "default", render: num }, { title: "下限", dataIndex: "min", render: num },
          { title: "上限", dataIndex: "max", render: value => value === null ? "无代码上限" : num(value) },
          { title: "修改值", render: (_, row) => <Input aria-label={`修改 ${row.key}`} inputMode="decimal" style={{ minWidth: 130 }}
            disabled={busy} value={draft[row.key] ?? String(config?.values[row.key] ?? "")}
            onChange={event => setDraft(old => ({ ...old, [row.key]: event.target.value }))} /> },
        ]} />
      <Button type="primary" style={{ marginTop: 12 }} disabled={!config || config.fallback || busy} onClick={prepareConfig}>提交参数修改</Button>
    </Card>
    <Card title="交易范围">
      {readFailure("universe", "交易范围")}
      <Typography.Paragraph type="secondary">资格通过仅代表类别与所选择允许参与，仍需满足全部开仓风控。股票须确认同一标的市场及精确合约；取消排除不会自动确认类别或映射。</Typography.Paragraph>
      <Table size="small" rowKey="base" pagination={{ pageSize: 20, showSizeChanger: false }} scroll={{ x: 1100 }}
        dataSource={data.universe ?? []} locale={{ emptyText: "暂无交易范围" }} columns={[
          { title: "基础币 / 类别", render: (_, row) => <><div>{row.base}</div><div>{categoryLabel(row.category)}</div>
            <div>{row.underlying_market ?? "—"}</div><Tag color={row.excluded ? "red" : undefined}>{row.excluded ? "已排除" : "未排除"}</Tag></> },
          { title: "各所精确合约 / 类别 / 实时成交额", render: (_, row) => <Space direction="vertical">{row.markets.map(item => <div key={item.market_key}>
            <div>{item.market_key}</div><div>{item.exchange} · {item.raw_symbol} · {categoryLabel(item.category)} → {categoryLabel(item.effective_category)} · {item.underlying_market ?? "—"}</div>
            <div>24 小时成交额：{num(item.volume_usd)} USD（{item.volume_basis === "base_times_low24h_lower_bound" ? "基础币成交量 × 24 小时最低价，保守下界" : item.volume_basis === "quote_volume" ? "报价成交额" : "口径未知"}）</div>
            <small>采集时间：{date(item.at)}；{item.selected ? "已人工选择" : "未人工选择"}；{item.eligible ? "类别资格通过" : "类别资格未通过"}{item.needs_classification ? "；待人工判断" : ""}</small>
          </div>)}</Space> },
          { title: "操作", render: (_, row) => <Space direction="vertical">
            <Button disabled={busy} aria-label={`${row.excluded ? "取消排除" : "排除"} ${row.base}`} onClick={() => prepare({
              title: `确认${row.excluded ? "取消排除" : "排除"} ${row.base}`,
              update: { resource: "universe", base: row.base, action: row.excluded ? "unexclude" : "exclude" },
              details: <p>{row.excluded ? "取消排除不会自动确认类别或映射。" : "排除后禁止该币新开仓，已有持仓保留减仓能力。"}</p>,
            })}>{row.excluded ? "取消排除" : "排除"}</Button>
            <Button disabled={busy} aria-label={`判断类别 ${row.base}`} onClick={() => openClassification(row)}>判断类别</Button>
          </Space> },
        ]} />
    </Card>
    <Card title="映射确认">
      {readFailure("mappings", "映射")}
      <Typography.Paragraph type="secondary">公共数量 = 原生数量 × 倍率，公共价格 = 原生价格 ÷ 倍率。价格比是发现时快照，采集超过 5 分钟不可确认；服务端开仓前会用新鲜盘口再次复核。</Typography.Paragraph>
      <Table size="small" rowKey="id" pagination={{ pageSize: 20, showSizeChanger: false }} scroll={{ x: 950 }}
        dataSource={data.mappings ?? []} locale={{ emptyText: "暂无映射候选" }} columns={[
          { title: "公共基础币 / 状态", render: (_, row) => <>{row.base}<br /><Tag>{statuses[row.status] ?? "未知状态"}</Tag></> },
          { title: "各所写法 / 数量倍率 / 价格比", render: (_, row) => <MappingDetails mapping={row} /> },
          { title: "操作", render: (_, row) => <Space>{(["confirm", "reject"] as const).map(action => <Button key={action}
            disabled={busy} danger={action === "reject"} aria-label={`${action === "confirm" ? "确认" : "拒绝"}映射 ${row.base}`}
            onClick={() => prepare({ title: `${action === "confirm" ? "确认" : "拒绝"}映射 ${row.base}`,
              update: { resource: "mappings", id: row.id, action }, details: <><MappingDetails mapping={row} />
                <p>{action === "confirm" ? "请确认以上合约确为同一基础币且倍率正确。" : "拒绝后不能用此映射新开仓；已有持仓仍可减仓。"}</p></> })}>
            {action === "confirm" ? "确认" : "拒绝"}</Button>)}</Space> },
        ]} />
    </Card>
    <Card title="参数审计记录">
      {readFailure("config/audit", "参数审计")}
      <Typography.Paragraph type="secondary">最近最多 500 条记录，按时间从旧到新排列。</Typography.Paragraph>
      <Table size="small" rowKey="version" dataSource={data["config/audit"] ?? []} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 650 }}
        locale={{ emptyText: "暂无参数审计记录" }} columns={[
          { title: "版本", dataIndex: "version" }, { title: "时间", dataIndex: "at", render: date },
          { title: "来源", dataIndex: "source", render: value => value === "admin_api" ? "管理接口" : value },
          { title: "旧值 → 新值", render: (_, row) => Object.entries(row.new).map(([key, value]) => <div key={key}>{labels[key] ?? key}：{num(row.old[key])} → {num(value)}</div>) },
        ]} />
    </Card>
    <Modal open={!!pending} title={pending?.title} okText="确认提交" cancelText="取消" confirmLoading={busy}
      closable={!busy} maskClosable={!busy} cancelButtonProps={{ disabled: busy }} onOk={confirmPending} onCancel={() => { if (!busy) setPending(null); }}>
      {pending?.details}{modalError && <Alert type="error" showIcon message={modalError} />}
    </Modal>
    <Modal open={!!classification} title={`人工判断 ${classification?.base ?? ""}`} okText="确认分类" cancelText="取消" confirmLoading={busy}
      closable={!busy} maskClosable={!busy} cancelButtonProps={{ disabled: busy }} onOk={confirmClassification} onCancel={() => { if (!busy) setClassification(null); }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <label>类别 <select aria-label="类别" disabled={busy} value={category} onChange={event => setCategory(event.target.value as LiveCategory)}>
          {Object.entries(categories).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select></label>
        {category === "listed_equity" && <label>标的市场 <select aria-label="标的市场" disabled={busy} value={market} onChange={event => setMarket(event.target.value as typeof market)}>
          <option value="">请选择</option><option value="US">US（NYSE / Nasdaq）</option><option value="KRX">KRX（韩交所）</option>
        </select></label>}
        <Typography.Text>仅选择跟踪同一标的的精确合约：</Typography.Text>
        {classification?.markets.map(item => <Checkbox key={item.market_key} disabled={busy} checked={selected.includes(item.market_key)}
          onChange={event => setSelected(old => event.target.checked ? [...old, item.market_key] : old.filter(key => key !== item.market_key))}>{item.market_key}</Checkbox>)}
        <Typography.Text type="secondary">上市前资产及商品不交易；不能通过改为加密币绕过已识别类别限制。</Typography.Text>
        {modalError && <Alert type="error" showIcon message={modalError} />}
      </Space>
    </Modal>
  </Space>;
}
