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

describe("backup automation IPC", () => {
  it("allows only the trusted frame and forwards validated opt-in preferences", async () => {
    const calls: Array<{ operation: string; payload: Record<string, unknown> }> = [];
    const client: BackendClient = {
      request: async (operation, payload) => {
        calls.push({ operation, payload });
        return {
          daily_backup_enabled: true,
          daily_backup_time: "06:30",
          backup_on_kindle_connect: true,
          last_backup: null,
        } as never;
      },
    };
    const webContents = { mainFrame: { origin: "koreader://bundle" } };
    const event = { sender: webContents, senderFrame: webContents.mainFrame };
    const cleanup = registerIpcHandlers(client, { webContents } as never, "koreader://bundle", new Map());
    const statusHandler = electronState.handlers.get("backup:automation-status");
    const configureHandler = electronState.handlers.get("backup:automation-configure");
    if (typeof statusHandler !== "function" || typeof configureHandler !== "function") {
      throw new Error("missing_backup_automation_handler");
    }
    const invoke = async (handler: unknown, source: unknown, payload?: unknown) =>
      (handler as (event: unknown, payload?: unknown) => Promise<unknown>)(source, payload);

    try {
      await expect(invoke(statusHandler, event)).resolves.toMatchObject({
        daily_backup_enabled: true,
      });
      await expect(invoke(configureHandler, event, {
        daily_backup_enabled: true,
        daily_backup_time: "06:30",
        backup_on_kindle_connect: true,
      })).resolves.toMatchObject({ daily_backup_time: "06:30" });
      await expect(invoke(configureHandler, event, {
        daily_backup_enabled: true,
        daily_backup_time: "25:61",
        backup_on_kindle_connect: true,
      })).rejects.toMatchObject({ code: "invalid_request" });
      await expect(invoke(configureHandler, {
        sender: webContents,
        senderFrame: { origin: "https://example.invalid" },
      }, {
        daily_backup_enabled: true,
        daily_backup_time: "06:30",
        backup_on_kindle_connect: true,
      })).rejects.toMatchObject({ code: "untrusted_sender" });
      expect(calls).toEqual([
        { operation: "backup.automation.status", payload: {} },
        {
          operation: "backup.automation.configure",
          payload: {
            daily_backup_enabled: true,
            daily_backup_time: "06:30",
            backup_on_kindle_connect: true,
          },
        },
      ]);
    } finally {
      cleanup();
    }
  });
});
