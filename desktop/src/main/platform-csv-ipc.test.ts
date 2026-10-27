import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BackendClient } from "../shared/contracts";

const electronState = vi.hoisted(() => {
  const handlers = new Map<string, unknown>();
  return {
    handlers,
    showOpenDialog: vi.fn(),
    showSaveDialog: vi.fn(),
    relaunch: vi.fn(),
    quit: vi.fn(),
    handle: vi.fn((channel: string, handler: unknown) => handlers.set(channel, handler)),
    removeHandler: vi.fn((channel: string) => handlers.delete(channel)),
  };
});

vi.mock("electron", () => ({
  app: { relaunch: electronState.relaunch, quit: electronState.quit },
  dialog: {
    showOpenDialog: electronState.showOpenDialog,
    showSaveDialog: electronState.showSaveDialog,
  },
  ipcMain: { handle: electronState.handle, removeHandler: electronState.removeHandler },
}));

const { registerIpcHandlers } = await import("./ipc-handlers");
let temporaryRoot: string | null = null;

afterEach(() => {
  vi.useRealTimers();
  electronState.handlers.clear();
  vi.clearAllMocks();
  if (temporaryRoot) rmSync(temporaryRoot, { recursive: true, force: true });
  temporaryRoot = null;
});

describe("platform CSV IPC", () => {
  it("rejects an inspection response with too many headers before exposing it to the renderer", async () => {
    temporaryRoot = mkdtempSync(path.join(tmpdir(), "kre-platform-csv-headers-"));
    const csvPath = path.join(temporaryRoot, "history.csv");
    writeFileSync(csvPath, "Title,Author\nFixture,Author\n");
    electronState.showOpenDialog.mockResolvedValue({ canceled: false, filePaths: [csvPath] });
    const client: BackendClient = {
      request: async (operation) => {
        if (operation === "platform-csv.inspect") {
          return {
            source: "goodreads",
            source_digest: "d".repeat(64),
            headers: Array.from({ length: 257 }, (_, index) => `Column ${index}`),
            suggested_mapping: {
              external_id: null,
              title: "Column 0",
              author: "Column 1",
              isbn_10: null,
              isbn_13: null,
              rating: null,
              status: null,
              shelves: null,
              read_count: null,
              date_added: null,
              date_read: null,
              review: null,
            },
          } as never;
        }
        throw new Error("unexpected_operation");
      },
    };
    const webContents = { mainFrame: { origin: "koreader://bundle" } };
    const window = { webContents };
    const event = { sender: webContents, senderFrame: webContents.mainFrame };
    const cleanup = registerIpcHandlers(client, window as never, "koreader://bundle", new Map());
    const handler = electronState.handlers.get("platform-csv:select-import");
    if (typeof handler !== "function") throw new Error("missing_handler");

    await expect((handler as (event: unknown, payload: unknown) => Promise<unknown>)(event, {
      source: "goodreads",
      language: "en",
    })).rejects.toMatchObject({ code: "invalid_protocol" });
    cleanup();
  });

  it("uses native file dialogs and keeps selected paths out of renderer responses", async () => {
    temporaryRoot = mkdtempSync(path.join(tmpdir(), "kre-platform-csv-"));
    const csvPath = path.join(temporaryRoot, "history.csv");
    const exportPath = path.join(temporaryRoot, "export.csv");
    writeFileSync(csvPath, "Book Name,Creator,Private note\nFixture,Author,Local only\n");
    electronState.showOpenDialog.mockResolvedValue({ canceled: false, filePaths: [csvPath] });
    electronState.showSaveDialog.mockResolvedValue({ canceled: false, filePath: exportPath });
    const requests: Array<{ operation: string; payload: Record<string, unknown> }> = [];
    const batch = {
      import_id: "a".repeat(64),
      source: "goodreads",
      source_digest: "b".repeat(64),
      status: "preview",
      created_at: "2026-10-26T10:00:00Z",
      completed_at: null,
      record_count: 1,
      reason_code: null,
    };
    const client: BackendClient = {
      request: async (operation, payload) => {
        requests.push({ operation, payload });
        if (operation === "platform-csv.inspect") {
          return {
            source: "goodreads",
            source_digest: "c".repeat(64),
            headers: ["Book Name", "Creator", "Private note"],
            suggested_mapping: {
              external_id: null,
              title: "Book Name",
              author: "Creator",
              isbn_10: null,
              isbn_13: null,
              rating: null,
              status: null,
              shelves: null,
              read_count: null,
              date_added: null,
              date_read: null,
              review: null,
            },
          } as never;
        }
        if (operation === "platform-csv.preview-mapped") {
          return { batch, record_count: 1, issue_count: 0, warning_counts: {} } as never;
        }
        if (operation === "platform-csv.export") {
          return {
            file_name: "export.csv",
            bytes_written: 500,
            rows_exported: 1,
            formula_escaped_cells: 0,
            reviews_included: false,
            unexported_fields: [],
            warnings: [],
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
      const draft = await invoke("platform-csv:select-import", {
        source: "goodreads",
        language: "en",
      }) as Record<string, unknown>;
      expect(draft).toMatchObject({
        source: "goodreads",
        headers: ["Book Name", "Creator", "Private note"],
      });
      expect(typeof draft.selection_id).toBe("string");
      expect(draft.suggested_mapping).toMatchObject({ title: "Book Name", author: "Creator" });
      expect(JSON.stringify(draft)).not.toContain("Private note value");
      expect(JSON.stringify(draft)).not.toContain(csvPath);

      const mapping = {
        external_id: null,
        title: "Book Name",
        author: "Creator",
        isbn_10: null,
        isbn_13: null,
        rating: null,
        status: null,
        shelves: null,
        read_count: null,
        date_added: null,
        date_read: null,
        review: null,
      };
      const preview = await invoke("platform-csv:preview-mapped", {
        selection_id: draft.selection_id,
        column_mapping: mapping,
      }) as Record<string, unknown>;
      expect(preview).toMatchObject({ record_count: 1 });
      expect(JSON.stringify(preview)).not.toContain(csvPath);
      expect(electronState.showOpenDialog).toHaveBeenCalledWith(window, expect.objectContaining({
        title: "Import Goodreads reading CSV",
        filters: [{ name: "CSV", extensions: ["csv"] }],
      }));
      expect(requests[0]).toEqual({
        operation: "platform-csv.inspect",
        payload: { path: realpathSync(csvPath), source: "goodreads" },
      });
      expect(requests[1]).toEqual({
        operation: "platform-csv.preview-mapped",
        payload: {
          path: realpathSync(csvPath),
          source: "goodreads",
          source_digest: "c".repeat(64),
          column_mapping: mapping,
        },
      });
      await expect(invoke("platform-csv:preview-mapped", {
        selection_id: draft.selection_id,
        column_mapping: mapping,
      })).rejects.toMatchObject({ code: "platform_csv_selection_expired" });

      const exported = await invoke("platform-csv:export", {
        import_id: batch.import_id,
        include_reviews: false,
        language: "en",
      }) as Record<string, unknown>;
      expect(exported).toMatchObject({ file_name: "export.csv", reviews_included: false });
      expect(JSON.stringify(exported)).not.toContain(exportPath);
      expect(requests[2]).toEqual({
        operation: "platform-csv.export",
        payload: {
          import_id: batch.import_id,
          path: exportPath,
          include_reviews: false,
          confirmed: true,
        },
      });

      await expect(invoke("platform-csv:select-import", {
        source: "goodreads",
        language: "en",
        path: csvPath,
      })).rejects.toMatchObject({ code: "invalid_request" });
      expect(requests).toHaveLength(3);

      electronState.showOpenDialog.mockResolvedValueOnce({ canceled: true, filePaths: [] });
      await expect(invoke("platform-csv:select-import", {
        source: "goodreads",
        language: "en",
      })).resolves.toBeNull();
      electronState.showSaveDialog.mockResolvedValueOnce({ canceled: true, filePath: undefined });
      await expect(invoke("platform-csv:export", {
        import_id: batch.import_id,
        include_reviews: false,
        language: "en",
      })).resolves.toBeNull();
      expect(requests).toHaveLength(3);
    } finally {
      cleanup();
    }
  });

  it("expires an unconfirmed header mapping without sending a preview", async () => {
    temporaryRoot = mkdtempSync(path.join(tmpdir(), "kre-platform-csv-expiry-"));
    const csvPath = path.join(temporaryRoot, "history.csv");
    writeFileSync(csvPath, "Title,Author\nFixture,Author\n");
    electronState.showOpenDialog.mockResolvedValue({ canceled: false, filePaths: [csvPath] });
    const requests: string[] = [];
    const client: BackendClient = {
      request: async (operation) => {
        requests.push(operation);
        if (operation === "platform-csv.inspect") {
          return {
            source: "goodreads",
            source_digest: "d".repeat(64),
            headers: ["Title", "Author"],
            suggested_mapping: {
              external_id: null,
              title: "Title",
              author: "Author",
              isbn_10: null,
              isbn_13: null,
              rating: null,
              status: null,
              shelves: null,
              read_count: null,
              date_added: null,
              date_read: null,
              review: null,
            },
          } as never;
        }
        throw new Error("preview must not be requested after expiration");
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
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-10-26T12:00:00Z"));
      const draft = await invoke("platform-csv:select-import", {
        source: "goodreads",
        language: "en",
      }) as Record<string, unknown>;
      vi.advanceTimersByTime(15 * 60 * 1000 + 1);
      await expect(invoke("platform-csv:preview-mapped", {
        selection_id: draft.selection_id,
        column_mapping: {
          external_id: null,
          title: "Title",
          author: "Author",
          isbn_10: null,
          isbn_13: null,
          rating: null,
          status: null,
          shelves: null,
          read_count: null,
          date_added: null,
          date_read: null,
          review: null,
        },
      })).rejects.toMatchObject({ code: "platform_csv_selection_expired" });
      expect(requests).toEqual(["platform-csv.inspect"]);
    } finally {
      cleanup();
    }
  });
});
