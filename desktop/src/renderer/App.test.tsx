// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReadingApi } from "../shared/contracts";
import { App } from "./App";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("guided profile recovery", () => {
  it("previews, asks for confirmation, applies, and offers restart", async () => {
    let reviewItems = [{
      operation_id: "12345678-1234-1234-1234-123456789abc",
      operation: "add_journal_entry" as const,
      status: "reconcile" as const,
      reason_code: "remote_unavailable",
      attempts: 1,
      created_at: "2026-10-26T16:00:00Z",
      updated_at: "2026-10-26T16:01:00Z",
      book_title: "Fixture Novel",
      authors: ["Fixture Author"],
      book_reference: "book_id:28",
      rating: null,
      page: null,
    }];
    const profileRecovery = {
      selectSource: vi.fn().mockResolvedValue({
        source_id: "opaque-source-id",
        source_name: "Restauracao verificada",
      }),
      preview: vi.fn().mockResolvedValue({
        preview_id: "opaque-preview-id",
        source_name: "Restauracao verificada",
        schema_version: 5,
        row_counts: { reading_events: 4, sync_outbox: 1 },
        configured_calibre: true,
        configured_restic: true,
        configured_remote_backup: false,
      }),
      apply: vi.fn().mockResolvedValue({
        checkpoint_id: "c".repeat(32),
        schema_version: 5,
        requires_restart: true,
      }),
      restart: vi.fn().mockResolvedValue(undefined),
    };
    const reading: ReadingApi = {
      app: { status: vi.fn().mockResolvedValue({ status: "ready", protocol_version: 1 }) },
      calibre: {
        list: vi.fn().mockResolvedValue({ books: [], total: 0 }),
        selectImport: vi.fn().mockResolvedValue(null),
        addImport: vi.fn(),
        selectCover: vi.fn().mockResolvedValue(null),
        edit: vi.fn(),
        editTagsBatch: vi.fn().mockResolvedValue([]),
        remove: vi.fn(),
        readings: { preview: vi.fn(), apply: vi.fn() },
      },
      koreader: {
        readings: { preview: vi.fn(), events: vi.fn(), confirm: vi.fn(), map: vi.fn() },
      },
      kindle: {
        status: vi.fn().mockResolvedValue({ connected: false, reason_code: "device_not_found" }),
        send: vi.fn(),
      },
      hardcover: {
        status: vi.fn().mockResolvedValue({
          configured: true,
          requests_used: 0,
          request_limit: 1000,
          requests_remaining: 1000,
          window_seconds: 86400,
          pending_mutations: 2,
          reconciliation_required: 2,
          safe_to_retry: 1,
          manual_review_required: 1,
        }),
        list: vi.fn().mockResolvedValue({
          books: [],
          has_more: false,
          source: "live",
          fetched_at: "2026-10-26T12:00:00Z",
        }),
        currentReading: vi.fn().mockResolvedValue({
          book: null,
          source: "live",
          fetched_at: "2026-10-26T12:01:00Z",
        }),
        configure: vi.fn(),
        reconcile: vi.fn().mockResolvedValue({
          attempted: 1,
          completed: 1,
          failed: 0,
          reconciliation_required: 1,
          safe_to_retry: 0,
          manual_review_required: 1,
        }),
        manualReviews: vi.fn().mockImplementation(async () => reviewItems),
        resolveReview: vi.fn().mockImplementation(async (operationId, resolution) => {
          reviewItems = [];
          return {
            operation_id: operationId,
            resolution,
            reviewed_at: "2026-10-26T16:02:00Z",
          };
        }),
      },
      backup: {
        list: vi.fn().mockResolvedValue([]),
        create: vi.fn(),
        automationStatus: vi.fn().mockResolvedValue({
          daily_backup_enabled: false,
          daily_backup_time: "20:00",
          backup_on_kindle_connect: false,
          last_backup: null,
        }),
        configureAutomation: vi.fn().mockImplementation(async (preferences) => ({
          ...preferences,
          last_backup: null,
        })),
        selectRestoreTarget: vi.fn(),
        restore: vi.fn(),
        credentials: vi.fn().mockResolvedValue({ password_configured: false }),
        configurePassword: vi.fn(),
      },
      profileRecovery,
      platformCsv: {
        selectImport: vi.fn().mockResolvedValue(null),
        previewMapped: vi.fn(),
        list: vi.fn().mockResolvedValue({ batches: [], has_more: false }),
        page: vi.fn().mockResolvedValue({ entries: [], has_more: false }),
        confirm: vi.fn(),
        export: vi.fn().mockResolvedValue(null),
      },
    };
    Object.defineProperty(window, "reading", { configurable: true, value: reading });
    vi.stubGlobal("__APP_VERSION__", "test");
    window.localStorage.setItem("kre.ui-language", "pt-BR");

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Kindle e sincronização" }));
    fireEvent.click(await screen.findByRole("button", { name: "Escolher pasta restaurada" }));

    expect(await screen.findByText(/Schema SQLite v5/)).toBeTruthy();
    expect(screen.getByText(/4 eventos de leitura/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Revisar e recuperar perfil" }));
    expect(profileRecovery.apply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar recuperação" }));

    await waitFor(() => expect(profileRecovery.apply).toHaveBeenCalledWith("opaque-preview-id"));
    fireEvent.click(screen.getByRole("button", { name: "Reiniciar agora" }));
    await waitFor(() => expect(profileRecovery.restart).toHaveBeenCalledOnce());

    fireEvent.click(screen.getByRole("button", { name: "Configuração" }));
    expect(await screen.findByRole("heading", { name: "Automação local" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Backup diário" })).toHaveProperty("checked", false);
    fireEvent.click(screen.getByRole("checkbox", { name: "Backup diário" }));
    await waitFor(() => expect(reading.backup.configureAutomation).toHaveBeenCalledWith({
      daily_backup_enabled: true,
      daily_backup_time: "20:00",
      backup_on_kindle_connect: false,
    }));
    fireEvent.change(screen.getByLabelText("Idioma da interface"), { target: { value: "en" } });
    expect(window.localStorage.getItem("kre.ui-language")).toBe("en");
    expect(screen.getByRole("button", { name: "Library" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    expect((screen.getByLabelText("Interface language") as HTMLSelectElement).value).toBe("en");
    expect(screen.getByText("Ready", { selector: ".setting-status" })).toBeTruthy();
    expect(await screen.findByText("Fixture Novel")).toBeTruthy();
    expect(screen.getByText("Journal entry")).toBeTruthy();
    expect(screen.getByText("Journal text is hidden for privacy.")).toBeTruthy();
    expect(screen.queryByText("A private journal sentence")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Review action" }));
    fireEvent.click(screen.getByRole("button", { name: "Already appears in Hardcover" }));
    expect(screen.getByText("You checked Hardcover. This only closes the local item and does not change your account.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm review" }));
    await waitFor(() => expect(reading.hardcover.resolveReview).toHaveBeenCalledWith(
      "12345678-1234-1234-1234-123456789abc",
      "confirmed_remote",
    ));
    await waitFor(() => expect(screen.getByText("No actions need manual review.")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Process up to four safe actions" }));
    expect(screen.getByText(/New reads, journal notes, and actions with an uncertain submission require manual review/)).toBeTruthy();
    expect(reading.hardcover.reconcile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm and reconcile" }));
    await waitFor(() => expect(reading.hardcover.reconcile).toHaveBeenCalledOnce());
    await waitFor(() => expect(reading.hardcover.currentReading).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Library" }));
    expect(screen.getByRole("heading", { name: "Library" })).toBeTruthy();
    expect(screen.getByRole("searchbox", { name: "Search this shelf" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "KOReader & sync" }));
    expect(screen.getByRole("heading", { name: "KOReader & sync" })).toBeTruthy();
    expect(screen.getByText("Connect the Kindle over USB and leave its USB connected screen open.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Backup & restore" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create and verify backup" })).toBeTruthy();
  });
});
