import { Form, Input, Modal, Radio, Select, Switch, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { createBigSpreadMute } from "../api/client";
import type { BigSpreadMute, BigSpreadMuteRoute } from "../api/types";

const exchanges = ["*", "binance", "okx", "bybit", "gate", "bitget", "htx", "aster", "hyperliquid", "lighter", "rh-lighter", "arcus"]
  .map((value) => ({ value, label: value === "*" ? "任意" : value }));

type Values = BigSpreadMuteRoute & { duration: number; note: string; both_directions: boolean };

export function BigSpreadMuteDialog({ open, route, routeDescription, onCancel, onSaved }: {
  open: boolean;
  route?: BigSpreadMuteRoute;
  routeDescription?: string;
  onCancel: () => void;
  onSaved: (mutes: BigSpreadMute[]) => void;
}) {
  const [form] = Form.useForm<Values>();
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue({ symbol: "", buy_exchange: "*", sell_exchange: "*", duration: 24, note: "", both_directions: true, ...route });
    }
  }, [open, route, form]);

  const save = async () => {
    let values: Values;
    try { values = await form.validateFields(); } catch { return; }
    setSaving(true);
    try {
      const { duration, ...mute } = values;
      const mutes = await createBigSpreadMute({ ...mute, ...(duration ? { duration_hours: duration } : { until: null }) });
      message.success("已屏蔽此路线");
      onSaved(mutes);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  return <Modal title="屏蔽大价差路线" open={open} destroyOnHidden onCancel={onCancel} onOk={() => void save()}
    okText="确认屏蔽" cancelText="取消" confirmLoading={saving} cancelButtonProps={{ disabled: saving }} closable={!saving} maskClosable={!saving}>
    <Typography.Paragraph type="secondary">仅影响大价差提醒。按标的和买卖交易所匹配，覆盖对应交易所的市场及 DEX。</Typography.Paragraph>
    {routeDescription && <Typography.Paragraph style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{routeDescription}</Typography.Paragraph>}
    <Form name="big-spread-mute" form={form} layout="vertical" disabled={saving}>
      <Form.Item name="symbol" label="屏蔽标的" rules={[{ required: true, whitespace: true }]}>
        <Input maxLength={128} placeholder="如 IMXUSDT" disabled={Boolean(route)} />
      </Form.Item>
      <Form.Item name="buy_exchange" label="屏蔽买所" rules={[{ required: true }]}>
        <Select options={exchanges} showSearch disabled={Boolean(route)} />
      </Form.Item>
      <Form.Item name="sell_exchange" label="屏蔽卖所" rules={[{ required: true }]}>
        <Select options={exchanges} showSearch disabled={Boolean(route)} />
      </Form.Item>
      <Form.Item name="both_directions" label="同时屏蔽反向" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="duration" label="屏蔽时长">
        <Radio.Group options={[{ label: "1 天", value: 24 }, { label: "7 天", value: 168 }, { label: "永久", value: 0 }]} />
      </Form.Item>
      <Form.Item name="note" label="屏蔽备注"><Input.TextArea maxLength={1000} rows={2} /></Form.Item>
    </Form>
  </Modal>;
}
