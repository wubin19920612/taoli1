import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountConnectionsPage } from "../src/pages/AccountConnectionsPage";


const exchanges = [
  {
    exchange: "binance",
    label: "Binance",
    supports_spot: true,
    supports_futures: true,
    requires_passphrase: false,
    uses_public_address: false,
    note: "只读 API"
  },
  {
    exchange: "okx",
    label: "OKX",
    supports_spot: true,
    supports_futures: true,
    requires_passphrase: true,
    uses_public_address: false,
    note: "需要 Passphrase"
  },
  {
    exchange: "hyperliquid",
    label: "Hyperliquid",
    supports_spot: false,
    supports_futures: true,
    requires_passphrase: false,
    uses_public_address: true,
    note: "公开地址"
  }
];

function connection(overrides: Record<string, unknown> = {}) {
  return {
    id: "account_1",
    exchange: "binance",
    account_label: "Binance 主账户",
    enabled: true,
    include_spot: true,
    include_futures: true,
    dex: null,
    credential_hint: "****ABCD",
    last_test_state: "ok",
    last_test_message: "读取成功",
    last_tested_at: "2026-09-22T08:00:00Z",
    created_at: "2026-09-22T07:00:00Z",
    updated_at: "2026-09-22T08:00:00Z",
    ...overrides
  };
}

function overview(connections = [connection()]) {
  return {
    storage_ready: true,
    storage_message: "账户凭据加密存储已就绪",
    supported_exchanges: exchanges,
    connections
  };
}

describe("AccountConnectionsPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.localStorage.setItem("dashboard_password", "dashboard-test");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("lists multiple same-exchange accounts with masked credentials only", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json(overview([
      connection(),
      connection({ id: "account_2", account_label: "Binance 子账户", credential_hint: "****WXYZ" })
    ])));
    vi.stubGlobal("fetch", fetchMock);

    render(<AccountConnectionsPage />);

    expect(await screen.findByText("Binance 主账户")).toBeTruthy();
    expect(screen.getByText("Binance 子账户")).toBeTruthy();
    expect(screen.getByText("****ABCD")).toBeTruthy();
    expect(screen.getByText("****WXYZ")).toBeTruthy();
    expect(document.body.textContent).not.toContain("secret-value");
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({
      "X-Dashboard-Password": "dashboard-test"
    });
  });

  it("shows passphrase for OKX and public address plus DEX for Hyperliquid", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(overview([]))));
    render(<AccountConnectionsPage />);
    await screen.findByText("尚未配置账户连接");

    await userEvent.click(screen.getByRole("button", { name: "添加账户" }));
    const dialog = screen.getByRole("dialog", { name: "添加账户连接" });
    await userEvent.click(within(dialog).getByLabelText("交易所"));
    await userEvent.click(await screen.findByText("OKX"));
    expect(within(dialog).getByLabelText("Passphrase")).toBeTruthy();

    await userEvent.click(within(dialog).getByLabelText("交易所"));
    await userEvent.click(await screen.findByText("Hyperliquid"));
    expect(within(dialog).getByLabelText("公开地址")).toBeTruthy();
    expect((within(dialog).getByLabelText("DEX") as HTMLInputElement).value).toBe("main");
    expect(within(dialog).queryByLabelText("API Key")).toBeNull();
  });

  it("keeps edit credential inputs blank and omits them from update payload", async () => {
    const requests: Array<{ url: string; body?: Record<string, unknown> }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") {
        requests.push({ url, body: JSON.parse(String(init.body)) });
        return Response.json(connection({ account_label: "更新账户" }));
      }
      return Response.json(overview());
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AccountConnectionsPage />);
    await screen.findByText("Binance 主账户");

    await userEvent.click(screen.getByRole("button", { name: "编辑 Binance 主账户" }));
    const dialog = screen.getByRole("dialog", { name: "编辑 Binance 主账户" });
    expect((within(dialog).getByLabelText("API Key") as HTMLInputElement).value).toBe("");
    expect((within(dialog).getByLabelText("API Secret") as HTMLInputElement).value).toBe("");
    const name = within(dialog).getByLabelText("账户名称");
    await userEvent.clear(name);
    await userEvent.type(name, "更新账户");
    const saveButton = within(dialog).getAllByRole("button").find(
      (button) => button.textContent?.replace(/\s/g, "") === "保存"
    );
    if (!saveButton) throw new Error("Save button not found");
    await userEvent.click(saveButton);

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].body).not.toHaveProperty("api_key");
    expect(requests[0].body).not.toHaveProperty("api_secret");
    expect(requests[0].body).not.toHaveProperty("passphrase");
    expect(requests[0].body).not.toHaveProperty("public_address");
  });

  it("tests, disables and deletes one connection without exposing credentials", async () => {
    const calls: string[] = [];
    let connections = [connection()];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/account_1/test")) {
        calls.push("test");
        return Response.json({
          connection_id: "account_1",
          exchange: "binance",
          account_label: "Binance 主账户",
          success: true,
          scopes: [{ market_type: "future", dex: null, state: "ok", message: "读取成功", position_count: 2 }],
          tested_at: "2026-09-22T09:00:00Z"
        });
      }
      if (init?.method === "PUT") {
        calls.push("disable");
        connections = [connection({ enabled: false })];
        return Response.json(connections[0]);
      }
      if (init?.method === "DELETE") {
        calls.push("delete");
        connections = [];
        return new Response(null, { status: 204 });
      }
      return Response.json(overview(connections));
    }));
    render(<AccountConnectionsPage />);
    await screen.findByText("Binance 主账户");

    await userEvent.click(screen.getByRole("button", { name: "测试 Binance 主账户" }));
    expect(await screen.findByText("Binance 主账户：测试通过")).toBeTruthy();

    await userEvent.click(screen.getByRole("switch", { name: "Binance 主账户账户连接" }));
    await waitFor(() => expect(calls).toContain("disable"));

    await userEvent.click(screen.getByRole("button", { name: "删除 Binance 主账户" }));
    const confirmation = await screen.findByText("删除账户连接？");
    const popover = confirmation.closest(".ant-popover") ?? document.body;
    await userEvent.click(within(popover as HTMLElement).getByRole("button", { name: /删\s*除/ }));
    await waitFor(() => expect(calls).toContain("delete"));
  });
});
