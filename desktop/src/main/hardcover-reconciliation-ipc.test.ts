import { afterEach, describe, expect, it, vi } from "vitest";
import type { BackendClient } from "../shared/contracts";

const electronState = vi.hoisted(() => {
  const handlers = new Map<string, unknown>();
  return {
    handlers,
    handle: vi.fn((channel: string, handler: unknown) => handlers.set(channel, handler)),
    removeHandler: vi.fn((channel: string) => handlers.delete(channel)),
  };
});

vi.mock("electron", () => ({
  app: { relaunch: vi.fn(), quit: vi.fn() },
  dialog: { showOpenDialog: vi.fn() },
  ipcMain: {
    handle: electronState.handle,
    removeHandler: electronState.removeHandler,
  },
}));

const { registerIpcHandlers } = await import("./ipc-handlers");

afterEach(() => {
  electronState.handlers.clear();
  vi.clearAllMocks();
});

describe("Hardcover reconciliation IPC", () => {
  it("requires a trusted frame and explicit confirmation before sending the operation", async () => {
    const requests: Array<{ operation: string; payload: Record<string, unknown> }> = [];
    const client: BackendClient = {
      request: async (operation, payload) => {
        requests.push({ operation, payload });
        return {
          attempted: 1,
          completed: 1,
          failed: 0,
          reconciliation_required: 0,
          safe_to_retry: 0,
          manual_review_required: 1,
        } as never;
      },
    };
    const webContents = { mainFrame: { origin: "koreader://bundle" } };
    const window = { webContents };
    const event = { sender: webContents, senderFrame: webContents.mainFrame };
    const cleanup = registerIpcHandlers(client, window as never, "koreader://bundle", new Map());
    const handler = electronState.handlers.get("hardcover:reconcile");
    if (typeof handler !== "function") throw new Error("missing_handler");
    const invoke = async (source: unknown, payload?: unknown): Promise<unknown> =>
      (handler as (event: unknown, payload?: unknown) => Promise<unknown>)(source, payload);

    try {
      await expect(invoke(event, { confirmed: false })).rejects.toMatchObject({
        code: "invalid_request",
      });
      await expect(invoke(event, { confirmed: true, extra: 1 })).rejects.toMatchObject({
        code: "invalid_request",
      });
      expect(requests).toHaveLength(0);

      await expect(invoke(event, { confirmed: true })).resolves.toMatchObject({
        completed: 1,
        manual_review_required: 1,
      });
      expect(requests).toEqual([
        { operation: "hardcover.reconcile", payload: { confirmed: true } },
      ]);

      await expect(
        invoke({ sender: webContents, senderFrame: { origin: "https://example.invalid" } }, {
          confirmed: true,
        }),
      ).rejects.toMatchObject({ code: "untrusted_sender" });
      expect(requests).toHaveLength(1);
    } finally {
      cleanup();
    }
  });

  it("keeps outbox review local, confirmed, and restricted to the trusted frame", async () => {
    const requests: Array<{ operation: string; payload: Record<string, unknown> | undefined }> = [];
    const client: BackendClient = {
      request: async (operation, payload) => {
        requests.push({ operation, payload });
        return operation === "hardcover.outbox.list"
          ? ([{ operation_id: "12345678-1234-1234-1234-123456789abc" }] as never)
          : {
              operation_id: "12345678-1234-1234-1234-123456789abc",
              resolution: "closed_locally",
              reviewed_at: "2026-10-26T16:00:00Z",
            } as never;
      },
    };
    const webContents = { mainFrame: { origin: "koreader://bundle" } };
    const window = { webContents };
    const event = { sender: webContents, senderFrame: webContents.mainFrame };
    const cleanup = registerIpcHandlers(client, window as never, "koreader://bundle", new Map());
    const listHandler = electronState.handlers.get("hardcover:outbox-list");
    const resolveHandler = electronState.handlers.get("hardcover:outbox-resolve");
    if (typeof listHandler !== "function" || typeof resolveHandler !== "function") {
      throw new Error("missing_outbox_review_handler");
    }
    const invokeList = async (source: unknown, payload?: unknown): Promise<unknown> =>
      await (listHandler as (event: unknown, payload?: unknown) => Promise<unknown>)(source, payload);
    const invokeResolve = async (source: unknown, payload?: unknown): Promise<unknown> =>
      await (resolveHandler as (event: unknown, payload?: unknown) => Promise<unknown>)(source, payload);

    try {
      await expect(invokeList(event, {})).resolves.toEqual([
        { operation_id: "12345678-1234-1234-1234-123456789abc" },
      ]);
      await expect(invokeList(event, { extra: true })).rejects.toMatchObject({ code: "invalid_request" });
      await expect(invokeResolve(event, {
        operation_id: "12345678-1234-1234-1234-123456789abc",
        resolution: "closed_locally",
        confirmed: true,
      })).resolves.toMatchObject({ resolution: "closed_locally" });
      await expect(invokeResolve(event, {
        operation_id: "12345678-1234-1234-1234-123456789abc",
        resolution: "closed_locally",
        confirmed: false,
      })).rejects.toMatchObject({ code: "invalid_request" });
      expect(requests).toEqual([
        { operation: "hardcover.outbox.list", payload: {} },
        {
          operation: "hardcover.outbox.resolve",
          payload: {
            operation_id: "12345678-1234-1234-1234-123456789abc",
            resolution: "closed_locally",
            confirmed: true,
          },
        },
      ]);
      await expect(invokeList(
        { sender: webContents, senderFrame: { origin: "https://example.invalid" } },
        {},
      )).rejects.toMatchObject({ code: "untrusted_sender" });
    } finally {
      cleanup();
    }
  });
});
