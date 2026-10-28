import { describe, expect, it, vi } from "vitest";
import { createReadingApi } from "./contracts";

describe("reading API", () => {
  it("validates and sends local reading records over the bridge", async () => {
    const book = {
      book_id: "a".repeat(32), title: "Fixture", author: "Writer", status: "reading",
      progress: 25, rating: null, started_on: null, finished_on: null, notes: "Private fixture",
    };
    const transport = { invoke: vi.fn(async (channel: string) => channel === "local-library:list" ? { books: [book] } : book) };
    const api = createReadingApi(transport);
    expect(await api.localLibrary!.list(100)).toEqual([book]);
    expect(transport.invoke).toHaveBeenCalledWith("local-library:list", { offset: 100 });
    await api.localLibrary!.save(book as import("./contracts").LocalBook);
    expect(transport.invoke).toHaveBeenCalledWith("local-library:save", {
      book_id: book.book_id, status: "reading", progress: 25, rating: null,
      started_on: null, finished_on: null, notes: "Private fixture",
    });
    const invalid = createReadingApi({ invoke: async () => ({ books: [{ ...book, progress: 101 }] }) });
    await expect(invalid.localLibrary!.list()).rejects.toThrow("invalid_response");
  });
  it("loads and saves validated local backup automation settings", async () => {
    const timestamp = "2026-10-26T20:00:00Z";
    const status = {
      daily_backup_enabled: false,
      daily_backup_time: "20:00",
      backup_on_kindle_connect: false,
      last_backup: {
        status: "success",
        updated_at: timestamp,
        last_success_at: timestamp,
        reason_code: null,
      },
    };
    const transport = {
      invoke: vi.fn(async (channel: string) => {
        if (channel === "backup:automation-status") return status;
        if (channel === "backup:automation-configure") return status;
        throw new Error("unexpected channel");
      }),
    };
    const api = createReadingApi(transport);

    await expect(api.backup.automationStatus()).resolves.toEqual(status);
    await expect(api.backup.configureAutomation({
      daily_backup_enabled: false,
      daily_backup_time: "20:00",
      backup_on_kindle_connect: false,
    })).resolves.toEqual(status);
    expect(transport.invoke).toHaveBeenCalledWith("backup:automation-configure", {
      daily_backup_enabled: false,
      daily_backup_time: "20:00",
      backup_on_kindle_connect: false,
    });

    const invalidApi = createReadingApi({
      invoke: vi.fn(async () => ({ ...status, daily_backup_time: "25:70" })),
    });
    await expect(invalidApi.backup.automationStatus()).rejects.toThrow("invalid_response");
  });

  it("preserves Hardcover cache provenance and synchronization reconciliation counts", async () => {
    const book = {
      user_book_id: 11,
      book_id: 21,
      title: "Fixture Book",
      authors: ["Fixture Author"],
      status_id: 2,
      rating: null,
      edition_id: 31,
      edition_pages: 200,
      reads: [],
      cover_url: null,
      edition_format: "eBook",
    };
    const timestamp = "2026-10-25T16:00:00Z";
    const transport = {
      invoke: vi.fn(async (channel: string) => {
        if (channel === "hardcover:status") {
          return {
            configured: true,
            requests_used: 8,
            request_limit: 1000,
            requests_remaining: 992,
            window_seconds: 86400,
            pending_mutations: 2,
            reconciliation_required: 1,
            safe_to_retry: 1,
            manual_review_required: 1,
          };
        }
        if (channel === "hardcover:list") {
          return { books: [book], has_more: false, source: "cache", fetched_at: timestamp };
        }
        if (channel === "hardcover:current-reading") {
          return { book, source: "cache", fetched_at: timestamp };
        }
        throw new Error("unexpected channel");
      }),
    };
    const api = createReadingApi(transport);

    await expect(api.hardcover.status()).resolves.toMatchObject({ pending_mutations: 2, reconciliation_required: 1 });
    await expect(api.hardcover.list()).resolves.toMatchObject({ source: "cache", fetched_at: timestamp });
    await expect(api.hardcover.currentReading()).resolves.toEqual({ book, source: "cache", fetched_at: timestamp });
  });

  it("sends an explicit confirmation and validates the Hardcover reconciliation result", async () => {
    const result = {
      attempted: 2,
      completed: 1,
      failed: 0,
      reconciliation_required: 1,
      safe_to_retry: 1,
      manual_review_required: 1,
    };
    const transport = {
      invoke: vi.fn(async (channel: string) => {
        if (channel === "hardcover:reconcile") return result;
        throw new Error("unexpected channel");
      }),
    };
    const api = createReadingApi(transport);

    await expect(api.hardcover.reconcile()).resolves.toEqual(result);
    expect(transport.invoke).toHaveBeenCalledWith("hardcover:reconcile", { confirmed: true });

    const invalidApi = createReadingApi({
      invoke: vi.fn(async () => ({ ...result, completed: 3 })),
    });
    await expect(invalidApi.hardcover.reconcile()).rejects.toThrow("invalid_response");
  });

  it("lists only sanitized Hardcover review items and confirms local resolution", async () => {
    const item = {
      operation_id: "12345678-1234-1234-1234-123456789abc",
      operation: "add_journal_entry",
      status: "reconcile",
      reason_code: "remote_unavailable",
      attempts: 1,
      created_at: "2026-10-26T16:00:00Z",
      updated_at: "2026-10-26T16:01:00Z",
      book_title: "Fixture Novel",
      authors: ["Fixture Author"],
      book_reference: "book_id:28",
      rating: null,
      page: null,
    };
    const transport = {
      invoke: vi.fn(async (channel: string) => {
        if (channel === "hardcover:outbox-list") return [item];
        if (channel === "hardcover:outbox-resolve") {
          return {
            operation_id: item.operation_id,
            resolution: "confirmed_remote",
            reviewed_at: "2026-10-26T16:02:00Z",
          };
        }
        throw new Error("unexpected channel");
      }),
    };
    const api = createReadingApi(transport);

    await expect(api.hardcover.manualReviews()).resolves.toEqual([item]);
    await expect(
      api.hardcover.resolveReview(item.operation_id, "confirmed_remote"),
    ).resolves.toMatchObject({ resolution: "confirmed_remote" });
    expect(transport.invoke).toHaveBeenCalledWith("hardcover:outbox-resolve", {
      operation_id: item.operation_id,
      resolution: "confirmed_remote",
      confirmed: true,
    });

    const invalidApi = createReadingApi({
      invoke: vi.fn(async () => ({ ...item, entry: "must never reach the UI" })),
    });
    await expect(invalidApi.hardcover.manualReviews()).rejects.toThrow("invalid_response");
  });

  it("exposes profile recovery only through opaque identifiers and validates the restart result", async () => {
    const sourceId = "12345678-1234-1234-1234-123456789abc";
    const previewId = "abcdefab-cdef-abcd-efab-cdefabcdefab";
    const transport = {
      invoke: vi.fn(async (channel: string) => {
        if (channel === "profile-recovery:select-source") {
          return { source_id: sourceId, source_name: "KOReader-Ecosystem-restore" };
        }
        if (channel === "profile-recovery:preview") {
          return {
            preview_id: previewId,
            source_name: "KOReader-Ecosystem-restore",
            schema_version: 3,
            row_counts: { reading_events: 12, sync_outbox: 1 },
            configured_calibre: true,
            configured_restic: true,
            configured_remote_backup: false,
          };
        }
        if (channel === "profile-recovery:apply") {
          return { checkpoint_id: "a".repeat(32), schema_version: 3, requires_restart: true };
        }
        if (channel === "app:restart") return { scheduled: true };
        throw new Error("unexpected channel");
      }),
    };
    const api = createReadingApi(transport);

    await expect(api.profileRecovery.selectSource()).resolves.toMatchObject({ source_id: sourceId });
    await expect(api.profileRecovery.preview(sourceId)).resolves.toMatchObject({ preview_id: previewId });
    await expect(api.profileRecovery.apply(previewId)).resolves.toMatchObject({ requires_restart: true });
    await expect(api.profileRecovery.restart()).resolves.toBeUndefined();
    expect(transport.invoke).toHaveBeenCalledWith("profile-recovery:preview", { source_id: sourceId });
    expect(transport.invoke).toHaveBeenCalledWith("profile-recovery:apply", {
      preview_id: previewId,
      confirmed: true,
    });

    const invalidApi = createReadingApi({ invoke: async () => ({ ...{
      preview_id: previewId,
      source_name: "Restore",
      schema_version: 3,
      row_counts: { reading_events: -1 },
      configured_calibre: true,
      configured_restic: true,
      configured_remote_backup: false,
    } }) });
    await expect(invalidApi.profileRecovery.preview(sourceId)).rejects.toThrow("invalid_response");
  });

  it("keeps CSV import paths private and validates paged records and explicit export choices", async () => {
    const importId = "a".repeat(64);
    const digest = "b".repeat(64);
    const batch = {
      import_id: importId,
      source: "goodreads",
      source_digest: digest,
      status: "preview",
      created_at: "2026-10-26T10:00:00Z",
      completed_at: null,
      record_count: 1,
      reason_code: null,
    };
    const selectionId = "abcdefab-cdef-abcd-efab-cdefabcdefab";
    const mapping = {
      external_id: null,
      title: "Book Title",
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
    const record = {
      row_number: 2,
      external_id: "123",
      title: "Fixture title",
      authors: ["Fixture author"],
      isbn_10: null,
      isbn_13: "9780000000000",
      rating: 4.5,
      exclusive_shelf: "read",
      reading_status: "read",
      shelves: ["fantasy"],
      read_count: 1,
      date_added: "2026-01-01",
      date_read: "2026-02-01",
      warnings: [],
    };
    const transport = {
      invoke: vi.fn(async (channel: string) => {
        if (channel === "platform-csv:select-import") {
          return {
            selection_id: selectionId,
            source: "goodreads",
            source_digest: digest,
            headers: ["Book Title", "Creator", "Private Notes"],
            suggested_mapping: mapping,
          };
        }
        if (channel === "platform-csv:preview-mapped") {
          return { batch, record_count: 1, issue_count: 0, warning_counts: {} };
        }
        if (channel === "platform-csv:list") return { batches: [batch], has_more: false };
        if (channel === "platform-csv:page") {
          return { entries: [{ row_number: 2, row_type: "record", record, reason_code: null }], has_more: false };
        }
        if (channel === "platform-csv:confirm") return { ...batch, status: "applied" };
        if (channel === "platform-csv:export") {
          return {
            file_name: "reading-history-goodreads.csv",
            bytes_written: 450,
            rows_exported: 1,
            formula_escaped_cells: 0,
            reviews_included: false,
            unexported_fields: ["sessions"],
            warnings: [],
          };
        }
        throw new Error("unexpected channel");
      }),
    };
    const api = createReadingApi(transport);

    await expect(api.platformCsv.selectImport("goodreads", "en")).resolves.toMatchObject({ selection_id: selectionId });
    await expect(api.platformCsv.previewMapped(selectionId, mapping)).resolves.toMatchObject({ record_count: 1 });
    await expect(api.platformCsv.list()).resolves.toMatchObject({ batches: [batch] });
    await expect(api.platformCsv.page(importId)).resolves.toMatchObject({ entries: [{ record }] });
    await expect(api.platformCsv.confirm(importId, digest)).resolves.toMatchObject({ status: "applied" });
    await expect(api.platformCsv.export(importId, false, "en")).resolves.toMatchObject({ reviews_included: false });
    expect(transport.invoke).toHaveBeenCalledWith("platform-csv:select-import", { source: "goodreads", language: "en" });
    expect(transport.invoke).toHaveBeenCalledWith("platform-csv:preview-mapped", {
      selection_id: selectionId,
      column_mapping: mapping,
    });
    expect(transport.invoke).toHaveBeenCalledWith("platform-csv:export", {
      import_id: importId,
      include_reviews: false,
      language: "en",
      confirmed: true,
    });

    const leakingApi = createReadingApi({
      invoke: async () => ({
        entries: [{ row_number: 2, row_type: "record", record: { ...record, review: "private review" }, reason_code: null }],
        has_more: false,
      }),
    });
    await expect(leakingApi.platformCsv.page(importId)).rejects.toThrow("invalid_response");
  });

  it("accepts the local library schema for preview and recovery", async () => {
    const previewId = "abcdefab-cdef-abcd-efab-cdefabcdefab";
    const api = createReadingApi({
      invoke: async (channel) => channel === "profile-recovery:preview"
        ? {
          preview_id: previewId,
          source_name: "Restore",
          schema_version: 7,
          row_counts: { local_books: 2 },
          configured_calibre: false,
          configured_restic: true,
          configured_remote_backup: false,
        }
        : { checkpoint_id: "a".repeat(32), schema_version: 7, requires_restart: true },
    });
    await expect(api.profileRecovery.preview("12345678-1234-1234-1234-123456789abc"))
      .resolves.toMatchObject({ schema_version: 7 });
    await expect(api.profileRecovery.apply(previewId))
      .resolves.toMatchObject({ schema_version: 7 });
  });

  it("validates review details for KOReader progress conflicts and unmatched sidecars", async () => {
    const batch = {
      import_id: "a".repeat(64),
      source: "koreader",
      source_digest: "b".repeat(64),
      status: "preview",
      created_at: "2026-10-25T00:00:00Z",
      completed_at: null,
      record_count: 1,
      reason_code: null,
    };
    const preview = {
      batch,
      source_digest: batch.source_digest,
      observed_at: "2026-10-25T00:00:00Z",
      summary: {
        book_count: 1,
        page_event_count: 0,
        estimated_session_count: 0,
        sidecar_count: 1,
        warnings: ["progress_conflict", "sidecar_unmatched"],
      },
      books: [{
        koreader_book_id: 42,
        title: "Fixture Book",
        authors: "Fixture Author",
        series: null,
        language: "eng",
        pages: 100,
        last_opened_at: null,
        statistics_progress: 0.2,
        warnings: ["progress_conflict"],
        sidecars: [{
          progress: 0.5,
          status: "reading",
          rating: null,
          has_note: false,
          annotation_count: 0,
          warnings: [],
        }],
      }],
      unmatched_sidecars: [{
        sidecar_id: "c".repeat(64),
        title: "Unlinked sidecar",
        title_source: "metadata",
        authors: "Fixture Author",
        progress: null,
        status: null,
        rating: null,
        has_note: false,
        annotation_count: 0,
        warnings: ["sidecar_unmatched"],
      }],
      estimated_sessions: [],
      sessions_truncated: false,
    };
    const validApi = createReadingApi({ invoke: async () => preview });
    await expect(validApi.koreader.readings.preview()).resolves.toEqual(preview);

    const invalidApi = createReadingApi({
      invoke: async () => ({
        ...preview,
        unmatched_sidecars: [{ ...preview.unmatched_sidecars[0], title_source: "private_path" }],
      }),
    });
    await expect(invalidApi.koreader.readings.preview()).rejects.toThrow("invalid_response");
  });

  it("exposes typed application, Calibre, Kindle, Hardcover, and backup methods", async () => {
    const transport = {
      invoke: vi.fn(async (channel: string) => {
        if (channel === "app:status") {
          return { status: "ready", protocol_version: 1 };
        }
        if (channel === "calibre:list") {
          return { books: [], total: 0 };
        }
        throw new Error("unexpected channel");
      }),
    };

    const api = createReadingApi(transport);

    await expect(api.app.status()).resolves.toEqual({
      status: "ready",
      protocol_version: 1,
    });
    await expect(api.calibre.list()).resolves.toEqual({ books: [], total: 0 });
    expect(transport.invoke.mock.calls.map(([channel]) => channel)).toEqual([
      "app:status",
      "calibre:list",
    ]);
    expect(Object.keys(api)).toEqual([
      "localLibrary",
      "app",
      "calibre",
      "koreader",
      "kindle",
      "hardcover",
      "backup",
      "profileRecovery",
      "platformCsv",
    ]);
    expect("invoke" in api).toBe(false);
  });

  it("rejects backend results that do not match the public response shape", async () => {
    const api = createReadingApi({
      invoke: async () => ({ status: "unexpected", private_detail: "must not leak" }),
    });

    await expect(api.app.status()).rejects.toThrow("invalid_response");
  });

  it("requires a confirmed Calibre removal and accepts only a removal result", async () => {
    const transport = { invoke: vi.fn(async () => ({ removed: true })) };
    const api = createReadingApi(transport);

    await expect(api.calibre.remove(7)).resolves.toBeUndefined();
    expect(transport.invoke).toHaveBeenCalledWith("calibre:remove", {
      book_id: 7,
      confirmed: true,
      recovery_copy_confirmed: true,
    });

    const invalidApi = createReadingApi({ invoke: async () => ({ removed: false }) });
    await expect(invalidApi.calibre.remove(7)).rejects.toThrow("invalid_response");
  });

  it("exposes paginated KOReader history and the separate confirmed Calibre projection", async () => {
    const batch = {
      import_id: "a".repeat(64),
      source: "koreader",
      source_digest: "b".repeat(64),
      status: "applied",
      created_at: "2026-10-25T00:00:00Z",
      completed_at: "2026-10-25T00:01:00Z",
      record_count: 4,
      reason_code: null,
    };
    const transport = {
      invoke: vi.fn(async (channel: string) => {
        if (channel === "koreader:readings-events") return { events: [], has_more: false };
        if (channel === "koreader:readings-confirm") return batch;
        if (channel === "koreader:readings-map") return { mapped: true };
        if (channel === "calibre:readings-preview") {
          return {
            projection_id: "c".repeat(64),
            import_id: batch.import_id,
            source_digest: batch.source_digest,
            items: [],
            issues: [],
          };
        }
        if (channel === "calibre:readings-apply") {
          return { snapshot_id: "d".repeat(64), applied_book_count: 1, applied_field_count: 2 };
        }
        throw new Error("unexpected channel");
      }),
    };
    const api = createReadingApi(transport);

    await expect(api.koreader.readings.events(100, 200)).resolves.toEqual({
      events: [],
      has_more: false,
    });
    await expect(api.koreader.readings.confirm(batch.import_id, batch.source_digest))
      .resolves.toMatchObject({ status: "applied" });
    await expect(api.koreader.readings.map(batch.import_id, 42, 7)).resolves.toBeUndefined();
    await expect(api.calibre.readings.preview(batch.import_id)).resolves.toMatchObject({
      projection_id: "c".repeat(64),
      issues: [],
    });
    await expect(api.calibre.readings.apply(batch.import_id, "c".repeat(64))).resolves.toEqual({
      snapshot_id: "d".repeat(64),
      applied_book_count: 1,
      applied_field_count: 2,
    });

    expect(transport.invoke).toHaveBeenNthCalledWith(1, "koreader:readings-events", {
      limit: 100,
      offset: 200,
    });
    expect(transport.invoke).toHaveBeenNthCalledWith(2, "koreader:readings-confirm", {
      import_id: batch.import_id,
      source_digest: batch.source_digest,
      confirmed: true,
    });
    expect(transport.invoke).toHaveBeenNthCalledWith(3, "koreader:readings-map", {
      import_id: batch.import_id,
      koreader_book_id: 42,
      calibre_book_id: 7,
      confirmed: true,
    });
    expect(transport.invoke).toHaveBeenNthCalledWith(5, "calibre:readings-apply", {
      import_id: batch.import_id,
      projection_id: "c".repeat(64),
      confirmed: true,
    });
  });

  it("rejects malformed KOReader history and Calibre projection responses", async () => {
    const invalidApi = createReadingApi({
      invoke: vi.fn(async (channel: string) =>
        channel === "koreader:readings-events"
          ? { events: [{ source_event_id: "invalid" }], has_more: false }
          : { projection_id: "bad", items: [], issues: [] },
      ),
    });

    await expect(invalidApi.koreader.readings.preview()).rejects.toThrow("invalid_response");
    await expect(invalidApi.koreader.readings.events()).rejects.toThrow("invalid_response");
    await expect(invalidApi.calibre.readings.preview("a".repeat(64)))
      .rejects.toThrow("invalid_response");
  });

  it("rejects oversized or ambiguous CSV mapping drafts", async () => {
    const baseDraft = {
      selection_id: "abcdefab-cdef-abcd-efab-cdefabcdefab",
      source: "goodreads",
      source_digest: "a".repeat(64),
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
    };
    const oversizedApi = createReadingApi({
      invoke: async () => ({ ...baseDraft, headers: Array.from({ length: 257 }, (_, index) => `Column ${index}`) }),
    });
    await expect(oversizedApi.platformCsv.selectImport("goodreads", "en"))
      .rejects.toThrow("invalid_response");

    const ambiguousApi = createReadingApi({
      invoke: async () => ({
        ...baseDraft,
        suggested_mapping: { ...baseDraft.suggested_mapping, author: "Title" },
      }),
    });
    await expect(ambiguousApi.platformCsv.selectImport("goodreads", "en"))
      .rejects.toThrow("invalid_response");
  });
});
