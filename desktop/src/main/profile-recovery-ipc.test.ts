import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BackendClient } from "../shared/contracts";

const electronState = vi.hoisted(() => {
  const handlers = new Map<string, unknown>();
  return {
    handlers,
    showOpenDialog: vi.fn(),
    relaunch: vi.fn(),
    quit: vi.fn(),
    handle: vi.fn((channel: string, handler: unknown) => handlers.set(channel, handler)),
    removeHandler: vi.fn((channel: string) => handlers.delete(channel)),
  };
});

vi.mock("electron", () => ({
  app: { relaunch: electronState.relaunch, quit: electronState.quit },
  dialog: { showOpenDialog: electronState.showOpenDialog },
  ipcMain: { handle: electronState.handle, removeHandler: electronState.removeHandler },
}));

const { registerIpcHandlers } = await import("./ipc-handlers");
let temporaryRoot: string | null = null;

afterEach(() => {
  electronState.handlers.clear();
  vi.clearAllMocks();
  if (temporaryRoot) rmSync(temporaryRoot, { recursive: true, force: true });
  temporaryRoot = null;
});

describe("profile recovery IPC", () => {
  it("keeps filesystem paths in the main process and requires confirmation", async () => {
    temporaryRoot = mkdtempSync(path.join(tmpdir(), "kre-profile-restore-"));
    const canonicalTemporaryRoot = realpathSync.native(temporaryRoot);
    electronState.showOpenDialog.mockResolvedValue({
      canceled: false,
      filePaths: [temporaryRoot],
    });
    const requests: Array<{ operation: string; payload: Record<string, unknown> }> = [];
    const client: BackendClient = {
      request: async (operation, payload) => {
        requests.push({ operation, payload });
        if (operation === "profile-recovery.preview") {
          return {
            source_fingerprint: "a".repeat(64),
            schema_version: 3,
            row_counts: { reading_events: 2 },
            configured_calibre: true,
            configured_restic: true,
            configured_remote_backup: false,
          } as never;
        }
        if (operation === "profile-recovery.apply") {
          return {
            checkpoint_id: "b".repeat(32),
            schema_version: 3,
            requires_restart: true,
          } as never;
        }
        throw new Error("unexpected_operation");
      },
    };
    const webContents = { mainFrame: { origin: "koreader://bundle" } };
    const window = { webContents };
    const event = { sender: webContents, senderFrame: webContents.mainFrame };
    const cleanup = registerIpcHandlers(client, window as never, "koreader://bundle", new Map());

    const invoke = async (channel: string, payload?: unknown): Promise<unknown> => {
      const handler = electronState.handlers.get(channel);
      if (typeof handler !== "function") throw new Error("missing_handler");
      return (handler as (event: unknown, payload?: unknown) => Promise<unknown>)(event, payload);
    };

    try {
      const source = await invoke("profile-recovery:select-source") as {
        source_id: string;
        source_name: string;
      };
      expect(source.source_name).toBe(path.basename(temporaryRoot));
      expect(source.source_id).not.toContain(temporaryRoot);

      const preview = await invoke("profile-recovery:preview", { source_id: source.source_id }) as {
        preview_id: string;
        source_name: string;
      };
      expect(preview.source_name).toBe(path.basename(temporaryRoot));
      expect(preview.preview_id).not.toContain(temporaryRoot);
      expect(requests[0]).toEqual({
        operation: "profile-recovery.preview",
        payload: { restore_root: canonicalTemporaryRoot },
      });

      await expect(
        invoke("profile-recovery:preview", { restore_root: temporaryRoot }),
      ).rejects.toMatchObject({ code: "invalid_request" });
      await expect(
        invoke("profile-recovery:apply", { preview_id: preview.preview_id, confirmed: false }),
      ).rejects.toMatchObject({ code: "invalid_request" });
      expect(requests).toHaveLength(1);

      await expect(
        invoke("profile-recovery:apply", { preview_id: preview.preview_id, confirmed: true }),
      ).resolves.toMatchObject({ requires_restart: true });
      expect(requests[1]).toEqual({
        operation: "profile-recovery.apply",
        payload: {
          restore_root: canonicalTemporaryRoot,
          source_fingerprint: "a".repeat(64),
          confirmed: true,
        },
      });
    } finally {
      cleanup();
    }
  });
});
