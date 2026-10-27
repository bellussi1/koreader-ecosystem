// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CalibreBook, PlatformCsvColumnMappingDraft, PlatformCsvImportBatch, PlatformCsvImportPreview, PlatformCsvPreviewPage } from "../shared/contracts";
import { PlatformCsvDialog } from "./PlatformCsvDialog";

afterEach(cleanup);

function fixtures(status: PlatformCsvImportBatch["status"] = "preview") {
  const batch: PlatformCsvImportBatch = {
    import_id: "a".repeat(64),
    source: "goodreads",
    source_digest: "b".repeat(64),
    status,
    created_at: "2026-10-26T10:00:00Z",
    completed_at: null,
    record_count: 1,
    reason_code: null,
  };
  const preview: PlatformCsvImportPreview = {
    batch,
    record_count: 1,
    issue_count: 0,
    warning_counts: {},
  };
  const mapping = {
    external_id: null,
    title: "Book name",
    author: "Creator",
    isbn_10: null,
    isbn_13: null,
    rating: null,
    status: null,
    shelves: null,
    read_count: null,
    date_added: null,
    date_read: "Finished",
    review: null,
  } as const;
  const draft: PlatformCsvColumnMappingDraft = {
    selection_id: "abcdefab-cdef-abcd-efab-cdefabcdefab",
    source: "goodreads",
    source_digest: batch.source_digest,
    headers: ["Book name", "Creator", "Alternative title", "Finished", "Review text", "Private note"],
    suggested_mapping: mapping,
  };
  const page: PlatformCsvPreviewPage = {
    entries: [{
      row_number: 2,
      row_type: "record",
      record: {
        row_number: 2,
        external_id: "123",
        title: "Fixture book",
        authors: ["Fixture author"],
        isbn_10: null,
        isbn_13: null,
        rating: 4.5,
        exclusive_shelf: "read",
        reading_status: "read",
        shelves: [],
        read_count: 1,
        date_added: null,
        date_read: "2026-01-01",
        warnings: [],
      },
      reason_code: null,
    }],
    has_more: false,
  };
  return { batch, preview, page, mapping, draft };
}

async function previewSelectedCsv() {
  fireEvent.click(screen.getByRole("button", { name: "Choose CSV to import" }));
  expect(await screen.findByLabelText(/Book title.*column/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Preview using these columns" }));
}

describe("platform CSV dialog", () => {
  it("reviews a redacted preview, confirms local import, and exports reviews only by opt-in", async () => {
    const { batch, preview, page, draft, mapping } = fixtures();
    const applied = { ...batch, status: "applied" as const };
    const api = {
      selectImport: vi.fn().mockResolvedValue(draft),
      previewMapped: vi.fn().mockResolvedValue(preview),
      list: vi.fn().mockResolvedValue({ batches: [batch], has_more: false }),
      page: vi.fn().mockResolvedValue(page),
      confirm: vi.fn().mockResolvedValue(applied),
      export: vi.fn().mockResolvedValue({
        file_name: "reading-history-goodreads.csv",
        bytes_written: 450,
        rows_exported: 1,
        formula_escaped_cells: 0,
        reviews_included: false,
        unexported_fields: [],
        warnings: [],
      }),
    };
    render(<PlatformCsvDialog language="en" api={api} onClose={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Source platform"), { target: { value: "storygraph" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose CSV to import" }));
    const titleColumn = await screen.findByLabelText(/Book title.*column/);
    expect(screen.queryByText("Fixture book")).toBeNull();
    expect(api.previewMapped).not.toHaveBeenCalled();
    fireEvent.change(titleColumn, { target: { value: "Alternative title" } });
    fireEvent.click(screen.getByText(/Optional fields/));
    fireEvent.change(screen.getByLabelText(/Review \(private\).*column/), { target: { value: "Review text" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview using these columns" }));
    await waitFor(() => expect(api.selectImport).toHaveBeenCalledWith("storygraph", "en"));
    await waitFor(() => expect(api.previewMapped).toHaveBeenCalledWith(draft.selection_id, {
      ...mapping,
      title: "Alternative title",
      review: "Review text",
    }));
    expect(await screen.findByText("Fixture book")).toBeTruthy();
    expect(screen.queryByText(/review text/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Confirm and save locally" }));
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith(batch.import_id, batch.source_digest));
    expect(await screen.findByText(/does not update Hardcover or Calibre yet/i)).toBeTruthy();
    expect(api.export).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Include reviews in this file" }));
    fireEvent.click(screen.getByRole("button", { name: "Export Goodreads-compatible CSV" }));
    await waitFor(() => expect(api.export).toHaveBeenCalledWith(batch.import_id, true, "en"));
  });

  it("keeps the current preview when the file picker is canceled", async () => {
    const { batch, preview, page, draft } = fixtures();
    const api = {
      selectImport: vi.fn().mockResolvedValueOnce(draft).mockResolvedValueOnce(null),
      previewMapped: vi.fn().mockResolvedValue(preview),
      list: vi.fn().mockResolvedValue({ batches: [batch], has_more: false }),
      page: vi.fn().mockResolvedValue(page),
      confirm: vi.fn(),
      export: vi.fn(),
    };
    render(<PlatformCsvDialog language="en" api={api} onClose={vi.fn()} />);

    await previewSelectedCsv();
    expect(await screen.findByText("Fixture book")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Choose CSV to import" }));
    await waitFor(() => expect(api.selectImport).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Fixture book")).toBeTruthy();
  });

  it("can resume a pending local preview after reopening the app", async () => {
    const { batch, page } = fixtures();
    const api = {
      selectImport: vi.fn(),
      previewMapped: vi.fn(),
      list: vi.fn().mockResolvedValue({ batches: [batch], has_more: false }),
      page: vi.fn().mockResolvedValue(page),
      confirm: vi.fn().mockResolvedValue({ ...batch, status: "applied" }),
      export: vi.fn().mockResolvedValue(null),
    };
    render(<PlatformCsvDialog language="pt-BR" api={api} onClose={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: /Goodreads/ }));
    expect(await screen.findByText("Fixture book")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar e guardar localmente" }));
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith(batch.import_id, batch.source_digest));
  });

  it("shows an exact local ISBN candidate without linking or changing either catalog", async () => {
    const { batch, preview, page, draft } = fixtures();
    const matchingPage: PlatformCsvPreviewPage = {
      ...page,
      entries: page.entries.map((entry) => entry.record
        ? { ...entry, record: { ...entry.record, title: "Dune", authors: ["Frank Herbert"], isbn_13: "9780441172719" } }
        : entry),
    };
    const api = {
      selectImport: vi.fn().mockResolvedValue(draft),
      previewMapped: vi.fn().mockResolvedValue(preview),
      list: vi.fn().mockResolvedValue({ batches: [batch], has_more: false }),
      page: vi.fn().mockResolvedValue(matchingPage),
      confirm: vi.fn().mockResolvedValue({ ...batch, status: "applied" as const }),
      export: vi.fn().mockResolvedValue(null),
    };
    const localCalibre: CalibreBook[] = [{
      book_id: 9,
      uuid: "calibre-dune",
      title: "Dune",
      authors: ["Frank Herbert"],
      identifiers: { isbn: "978-0-441-17271-9" },
      custom_values: {},
    }];
    render(<PlatformCsvDialog language="en" api={api} onClose={vi.fn()} calibreBooks={localCalibre} hardcoverBooks={[]} />);

    await previewSelectedCsv();
    const matchDetails = (await screen.findByText(/Local suggestions/)).closest("details");
    expect(matchDetails).not.toBeNull();
    fireEvent.click(matchDetails!.querySelector("summary")!);
    expect(matchDetails!.textContent).toContain("Exact identifier match");
    expect(within(matchDetails!).getByText("Dune", { selector: "strong" })).toBeTruthy();
    expect(screen.getByText(/Candidates only; nothing is linked or changed/i)).toBeTruthy();
    expect(api.confirm).not.toHaveBeenCalled();
  });

  it("shows conflicting identifiers as review candidates instead of selecting one", async () => {
    const { batch, preview, page, draft } = fixtures();
    const matchingPage: PlatformCsvPreviewPage = {
      ...page,
      entries: page.entries.map((entry) => entry.record
        ? { ...entry, record: { ...entry.record, external_id: "123", isbn_13: "9780441172719" } }
        : entry),
    };
    const api = {
      selectImport: vi.fn().mockResolvedValue(draft),
      previewMapped: vi.fn().mockResolvedValue(preview),
      list: vi.fn().mockResolvedValue({ batches: [batch], has_more: false }),
      page: vi.fn().mockResolvedValue(matchingPage),
      confirm: vi.fn().mockResolvedValue({ ...batch, status: "applied" as const }),
      export: vi.fn().mockResolvedValue(null),
    };
    const localCalibre: CalibreBook[] = [
      { book_id: 10, uuid: "provider-id", title: "Candidate by ID", authors: ["Author"], identifiers: { goodreads: "123" }, custom_values: {} },
      { book_id: 11, uuid: "isbn", title: "Candidate by ISBN", authors: ["Author"], identifiers: { isbn13: "9780441172719" }, custom_values: {} },
    ];
    render(<PlatformCsvDialog language="en" api={api} onClose={vi.fn()} calibreBooks={localCalibre} hardcoverBooks={[]} />);

    await previewSelectedCsv();
    const matchDetails = (await screen.findByText(/Local suggestions/)).closest("details");
    expect(matchDetails).not.toBeNull();
    fireEvent.click(matchDetails!.querySelector("summary")!);
    expect(matchDetails!.textContent).toContain("Conflicting candidates");
    expect(within(matchDetails!).getByText("Candidate by ID")).toBeTruthy();
    expect(within(matchDetails!).getByText("Candidate by ISBN")).toBeTruthy();
    expect(api.confirm).not.toHaveBeenCalled();
  });
});
