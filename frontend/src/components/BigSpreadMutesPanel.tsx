import { Button, Space, Table, Typography, message } from "antd";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useEffect, useState } from "react";

import { deleteBigSpreadMute, listBigSpreadMutes } from "../api/client";
import type { BigSpreadMute } from "../api/types";
import { BigSpreadMuteDialog } from "./BigSpreadMuteDialog";

dayjs.extend(utc);
const exchangeLabel = (value: string) => value === "*" ? "任意" : value;

export function BigSpreadMutesPanel({ active }: { active: boolean }) {
  const [mutes, setMutes] = useState<BigSpreadMute[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true);
    try { setMutes(await listBigSpreadMutes()); }
    catch (exc) { message.error(exc instanceof Error ? exc.message : String(exc)); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (active) void load(); }, [active]);
  const remove = async (mute: BigSpreadMute) => {
    setBusy(true);
    try {
      setMutes(await deleteBigSpreadMute({ symbol: mute.symbol, buy_exchange: mute.buy_exchange, sell_exchange: mute.sell_exchange }));
      message.success("已删除路线屏蔽");
    } catch (exc) { message.error(exc instanceof Error ? exc.message : String(exc)); }
    finally { setBusy(false); }
  };
  return <section aria-label="大价差路线屏蔽">
    <Space wrap>
      <Typography.Title level={5}>大价差路线屏蔽</Typography.Title>
      <Button onClick={() => setOpen(true)} disabled={busy}>新增路线屏蔽</Button>
      <Button onClick={() => void load()} disabled={busy}>刷新屏蔽列表</Button>
    </Space>
    <Table<BigSpreadMute> size="small" loading={busy} dataSource={mutes}
      rowKey={(mute) => JSON.stringify([mute.symbol, mute.buy_exchange, mute.sell_exchange])}
      scroll={{ x: 650 }} columns={[
        { title: "标的", dataIndex: "symbol" },
        { title: "方向", render: (_, mute) => `${exchangeLabel(mute.buy_exchange)} ${mute.both_directions ? "↔" : "→"} ${exchangeLabel(mute.sell_exchange)}` },
        { title: "到期（UTC+8）", dataIndex: "until", render: (value: string | null) => value ? dayjs.utc(value).utcOffset(8).format("YYYY-MM-DD HH:mm") : "永久" },
        { title: "备注", dataIndex: "note" },
        { title: "操作", render: (_, mute) => <Button danger disabled={busy} onClick={() => void remove(mute)}>删除屏蔽</Button> }
      ]} />
    <BigSpreadMuteDialog open={open} onCancel={() => setOpen(false)} onSaved={(next) => { setMutes(next); setOpen(false); }} />
  </section>;
}
