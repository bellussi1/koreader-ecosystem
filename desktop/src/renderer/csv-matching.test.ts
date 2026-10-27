import { describe, expect, it } from "vitest";
import type { CalibreBook, HardcoverBook, PlatformCsvRecord } from "../shared/contracts";
import { findPlatformCsvMatches } from "./csv-matching";

function csvRecord(overrides: Partial<PlatformCsvRecord> = {}): PlatformCsvRecord {
  return {
    row_number: 2,
    external_id: "",
    title: "Dune",
    authors: ["Frank Herbert"],
    isbn_10: null,
    isbn_13: null,
    rating: null,
    exclusive_shelf: null,
    reading_status: null,
    shelves: [],
    read_count: null,
    date_added: null,
    date_read: null,
    warnings: [],
    ...overrides,
  };
}

function calibreBook(overrides: Partial<CalibreBook> = {}): CalibreBook {
  return {
    book_id: 1,
    uuid: "book-1",
    title: "Dune",
    authors: ["Frank Herbert"],
    identifiers: {},
    custom_values: {},
    ...overrides,
  };
}

function hardcoverBook(overrides: Partial<HardcoverBook> = {}): HardcoverBook {
  return {
    user_book_id: 20,
    book_id: 200,
    title: "Dune",
    authors: ["Frank Herbert"],
    status_id: 1,
    rating: null,
    edition_id: 300,
    edition_pages: 500,
    reads: [],
    cover_url: null,
    edition_format: "Physical Book",
    ...overrides,
  };
}

describe("CSV local match suggestions", () => {
  it("marks a unique ISBN match as exact without linking the Calibre record", () => {
    const result = findPlatformCsvMatches(
      csvRecord({ isbn_13: "9780441172719" }),
      "goodreads",
      [calibreBook({ identifiers: { isbn: "978-0-441-17271-9" } })],
      [],
    );

    expect(result.calibre.status).toBe("exact");
    expect(result.calibre.candidates).toEqual([
      expect.objectContaining({ id: "book-1", reasons: ["isbn"] }),
    ]);
    expect(result.hardcover.status).toBe("none");
  });

  it("flags conflicting provider ID and ISBN candidates instead of choosing one", () => {
    const result = findPlatformCsvMatches(
      csvRecord({ external_id: "1234", isbn_13: "9780441172719" }),
      "goodreads",
      [
        calibreBook({ uuid: "id-book", identifiers: { goodreads: "1234" } }),
        calibreBook({ book_id: 2, uuid: "isbn-book", identifiers: { isbn13: "9780441172719" } }),
      ],
      [],
    );

    expect(result.calibre.status).toBe("conflict");
    expect(result.calibre.candidates.map((candidate) => candidate.id)).toEqual([
      "id-book",
      "isbn-book",
    ]);
    expect(result.calibre.candidates[0]?.reasons).toEqual(["provider_id"]);
    expect(result.calibre.candidates[1]?.reasons).toEqual(["isbn"]);
  });

  it("offers title-and-author suggestions for both local catalogs", () => {
    const result = findPlatformCsvMatches(
      csvRecord({ title: "DÚNE!", authors: ["Frank Herbert"] }),
      "storygraph",
      [calibreBook({ title: "Dune" })],
      [hardcoverBook({ title: "Dune" })],
    );

    expect(result.calibre.status).toBe("candidate");
    expect(result.hardcover.status).toBe("candidate");
    expect(result.calibre.candidates[0]?.reasons).toEqual(["title_author"]);
    expect(result.hardcover.candidates[0]?.id).toBe("20");
  });

  it("does not suggest a match from title alone", () => {
    const result = findPlatformCsvMatches(
      csvRecord({ authors: ["Someone else"] }),
      "goodreads",
      [calibreBook()],
      [hardcoverBook()],
    );

    expect(result.calibre.status).toBe("none");
    expect(result.hardcover.status).toBe("none");
  });

  it("reports duplicate exact ISBNs as a conflict", () => {
    const result = findPlatformCsvMatches(
      csvRecord({ isbn_13: "9780441172719" }),
      "goodreads",
      [
        calibreBook({ uuid: "book-1", identifiers: { isbn: "9780441172719" } }),
        calibreBook({ book_id: 2, uuid: "book-2", identifiers: { isbn: "9780441172719" } }),
      ],
      [],
    );

    expect(result.calibre.status).toBe("conflict");
    expect(result.calibre.candidates).toHaveLength(2);
  });

  it("matches only the selected provider's external ID namespace", () => {
    const result = findPlatformCsvMatches(
      csvRecord({ external_id: "1234", title: "Different title" }),
      "storygraph",
      [calibreBook({ identifiers: { goodreads: "1234" } })],
      [],
    );

    expect(result.calibre.status).toBe("none");
  });

  it("marks a unique identifier from the selected provider as an exact candidate", () => {
    const result = findPlatformCsvMatches(
      csvRecord({ external_id: "gr-1234", title: "Unrelated title" }),
      "goodreads",
      [calibreBook({ identifiers: { goodreads_book_id: "GR-1234" } })],
      [],
    );

    expect(result.calibre.status).toBe("exact");
    expect(result.calibre.candidates).toEqual([
      expect.objectContaining({ id: "book-1", reasons: ["provider_id"] }),
    ]);
  });
});
