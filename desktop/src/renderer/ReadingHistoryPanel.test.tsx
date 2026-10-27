// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  CalibreBook,
  CalibreReadingProjection,
  KoreaderReadingPreview,
} from "../shared/contracts";
import { ReadingHistoryPanel } from "./ReadingHistoryPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function createPreview(status: "preview" | "applied" = "preview"): KoreaderReadingPreview {
  return {
    batch: {
      import_id: "a".repeat(64),
      source: "koreader",
      source_digest: "b".repeat(64),
      status,
      created_at: "2026-10-25T12:00:00Z",
      completed_at: status === "applied" ? "2026-10-25T12:00:10Z" : null,
      record_count: 4,
      reason_code: null,
    },
    source_digest: "b".repeat(64),
    observed_at: "2026-10-25T12:00:00Z",
    summary: {
      book_count: 1,
      page_event_count: 2,
      estimated_session_count: 1,
      sidecar_count: 1,
      warnings: ["progress_conflict", "sidecar_unmatched"],
    },
    books: [
      {
        koreader_book_id: 42,
        title: "Synthetic Fixture Book",
        authors: "Fixture Author",
        series: "Fixture Series",
        language: "eng",
        pages: 100,
        last_opened_at: "2026-10-25T11:55:00Z",
        statistics_progress: 0.2,
        warnings: ["progress_conflict"],
        sidecars: [{
          progress: 0.25,
          status: "reading",
          rating: 4.5,
          has_note: true,
          annotation_count: 1,
          warnings: [],
        }],
      },
    ],
    unmatched_sidecars: [{
      sidecar_id: "d".repeat(64),
      title: "Unlinked fixture sidecar",
      title_source: "folder",
      authors: "Fixture Author",
      progress: 0.6,
      status: "reading",
      rating: null,
      has_note: true,
      annotation_count: 2,
      warnings: ["sidecar_unmatched"],
    }],
    estimated_sessions: [{
      session_id: "c".repeat(64),
      koreader_book_id: 42,
      book_title: "Synthetic Fixture Book",
      started_at: "2026-10-25T11:50:00Z",
      ended_at: "2026-10-25T11:55:00Z",
      page_event_count: 2,
      duration_seconds: 300,
      first_page: 1,
      last_page: 2,
      estimated: true,
    }],
    sessions_truncated: false,
  };
}

function createProjection(): CalibreReadingProjection {
  return {
    projection_id: "d".repeat(64),
    import_id: "a".repeat(64),
    source_digest: "b".repeat(64),
    items: [{
      book_id: 7,
      calibre_uuid: "book-7",
      title: "Synthetic Fixture Book",
      authors: ["Fixture Author"],
      old_values: { koreader_progress: 0 },
      new_values: { koreader_progress: 0.25, koreader_rating: 4.5 },
      reason_code: "confirmed_identity",
      warnings: [],
    }],
    issues: [],
  };
}

const calibreBook: CalibreBook = {
  book_id: 7,
  uuid: "book-7",
  title: "Synthetic Fixture Book",
  authors: ["Fixture Author"],
  identifiers: {},
  custom_values: {},
};

describe("ReadingHistoryPanel", () => {
  it("localizes the KOReader history entry point in English", () => {
    render(<ReadingHistoryPanel language="en" books={[]} deviceConnected={false} />);

    expect(screen.getByRole("heading", { name: "KOReader → local history → Calibre" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Read Kindle history" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Connect the Kindle over USB to read KOReader history.")).toBeTruthy();
  });

  it("localizes the imported history review in English", async () => {
    const preview = createPreview();
    const api = {
      koreader: {
        readings: {
          preview: vi.fn(async () => preview),
          events: vi.fn(async () => ({ events: [], has_more: false })),
        },
      },
      calibre: { readings: { preview: vi.fn(), apply: vi.fn() } },
    };
    Object.defineProperty(window, "reading", { configurable: true, value: api });

    render(<ReadingHistoryPanel language="en" books={[calibreBook]} deviceConnected />);
    fireEvent.click(screen.getByRole("button", { name: "Read Kindle history" }));

    expect(await screen.findByText("estimated sessions")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import locally" })).toBeTruthy();
    expect(screen.getByLabelText("Search KOReader history")).toBeTruthy();
  });

  it("requires a connected Kindle before staging a history preview", () => {
    const api = {
      koreader: { readings: { preview: vi.fn() } },
      calibre: { readings: { preview: vi.fn(), apply: vi.fn() } },
    };
    Object.defineProperty(window, "reading", { configurable: true, value: api });

    render(<ReadingHistoryPanel books={[calibreBook]} deviceConnected={false} />);

    expect(screen.getByRole("button", { name: "Ler histórico do Kindle" }).hasAttribute("disabled"))
      .toBe(true);
    expect(screen.getByText(/mantenha aberta a tela/i)).toBeTruthy();
  });

  it("shows progress conflicts by book and details for unmatched sidecars", async () => {
    const preview = createPreview();
    const api = {
      koreader: {
        readings: {
          preview: vi.fn(async () => preview),
          events: vi.fn(async () => ({ events: [], has_more: false })),
        },
      },
      calibre: { readings: { preview: vi.fn(), apply: vi.fn() } },
    };
    Object.defineProperty(window, "reading", { configurable: true, value: api });

    render(<ReadingHistoryPanel books={[calibreBook]} deviceConnected />);
    fireEvent.click(screen.getByRole("button", { name: "Ler histórico do Kindle" }));

    expect(await screen.findByText(/estatísticas 20%.*sidecar 25%/i)).toBeTruthy();
    const unmatched = screen.getByText(/arquivos de leitura sem vínculo exato/i);
    fireEvent.click(unmatched);
    expect(screen.getByText("Unlinked fixture sidecar")).toBeTruthy();
    expect(screen.getByText(/nome da pasta/i)).toBeTruthy();
    expect(screen.getByText(/2 anotações/i)).toBeTruthy();
    expect(screen.getByText(/sem associação automática/i)).toBeTruthy();
  });

  it("keeps local import, edition mapping, and Calibre projection as separate confirmations", async () => {
    const preview = createPreview();
    const projection = createProjection();
    const api = {
      koreader: {
        readings: {
          preview: vi.fn(async () => preview),
          events: vi.fn(async () => ({ events: [], has_more: false })),
          confirm: vi.fn(async () => ({ ...preview.batch, status: "applied" as const })),
          map: vi.fn(async () => undefined),
        },
      },
      calibre: {
        readings: {
          preview: vi.fn(async () => projection),
          apply: vi.fn(async () => ({
            snapshot_id: "e".repeat(64),
            applied_book_count: 1,
            applied_field_count: 2,
          })),
        },
      },
    };
    Object.defineProperty(window, "reading", { configurable: true, value: api });

    render(<ReadingHistoryPanel books={[calibreBook]} deviceConnected />);

    fireEvent.click(screen.getByRole("button", { name: "Ler histórico do Kindle" }));
    expect(await screen.findByText("Synthetic Fixture Book")).toBeTruthy();
    expect(screen.getByText("sessões estimadas")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar importação local" }));
    await waitFor(() => expect(api.koreader.readings.confirm).toHaveBeenCalledWith(
      "a".repeat(64),
      "b".repeat(64),
    ));
    expect(screen.getByRole("status").textContent).toContain("Histórico importado neste computador");

    const mappingSelect = screen.getByRole("combobox", {
      name: "Edição do Calibre para Synthetic Fixture Book",
    });
    fireEvent.change(mappingSelect, { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar vínculo da edição" }));
    await waitFor(() => expect(api.koreader.readings.map).toHaveBeenCalledWith(
      "a".repeat(64),
      42,
      7,
    ));

    fireEvent.click(screen.getByRole("button", { name: "Preparar prévia para o Calibre" }));
    expect(await screen.findByText(/2 campos preparados para revisão/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Revisar escrita no Calibre" }));
    const confirmation = screen.getByRole("group", { name: "Confirmar gravação no Calibre" });
    fireEvent.click(within(confirmation).getByRole("button", {
      name: "Criar backup verificado e aplicar",
    }));

    await waitFor(() => expect(api.calibre.readings.apply).toHaveBeenCalledWith(
      "a".repeat(64),
      "d".repeat(64),
    ));
    expect(screen.getByText(/1 livro atualizado no Calibre/i)).toBeTruthy();
  });
});
