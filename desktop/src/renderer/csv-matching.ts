import type { CalibreBook, HardcoverBook, PlatformCsvRecord, PlatformCsvSource } from "../shared/contracts";

export type CsvMatchReason = "provider_id" | "isbn" | "title_author";
export type CsvMatchStatus = "exact" | "conflict" | "candidate" | "none";

export interface CsvMatchCandidate {
  id: string;
  title: string;
  authors: string[];
  reasons: CsvMatchReason[];
}

export interface CsvMatchGroup {
  status: CsvMatchStatus;
  candidates: CsvMatchCandidate[];
}

export interface PlatformCsvMatches {
  calibre: CsvMatchGroup;
  hardcover: CsvMatchGroup;
}

const MAX_CANDIDATES = 3;

const PROVIDER_IDENTIFIER_KEYS: Record<PlatformCsvSource, ReadonlySet<string>> = {
  goodreads: new Set(["goodreads", "goodreadsid", "goodreadsbookid"]),
  storygraph: new Set(["storygraph", "storygraphid", "storygraphbookid"]),
  maratona: new Set(["maratona", "maratonaid", "maratonabookid"]),
};

const ISBN_IDENTIFIER_KEYS = new Set(["isbn", "isbn10", "isbn13"]);

function normalizeText(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLocaleLowerCase("en-US")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function normalizeIdentifier(value: string): string {
  return value.toLocaleLowerCase("en-US").replace(/[^\p{L}\p{N}]+/gu, "");
}

function normalizeIsbn(value: string): string {
  return value.toLocaleUpperCase("en-US").replace(/[^0-9X]/g, "");
}

function titleAndAuthorMatch(record: PlatformCsvRecord, title: string, authors: string[]): boolean {
  const normalizedTitle = normalizeText(record.title);
  if (!normalizedTitle || normalizedTitle !== normalizeText(title)) return false;
  const recordAuthors = new Set(record.authors.map(normalizeText).filter(Boolean));
  return authors.some((author) => recordAuthors.has(normalizeText(author)));
}

function emptyGroup(): CsvMatchGroup {
  return { status: "none", candidates: [] };
}

function collectCalibreMatches(
  record: PlatformCsvRecord,
  source: PlatformCsvSource,
  books: CalibreBook[],
): CsvMatchGroup {
  const exact = new Map<string, { book: CalibreBook; reasons: Set<CsvMatchReason> }>();
  const sourceIds = PROVIDER_IDENTIFIER_KEYS[source];
  const externalId = record.external_id ? normalizeIdentifier(record.external_id) : "";
  const isbns = new Set([record.isbn_10, record.isbn_13].filter((value): value is string => Boolean(value)).map(normalizeIsbn));

  for (const book of books) {
    const addReason = (reason: CsvMatchReason) => {
      const current = exact.get(book.uuid) ?? { book, reasons: new Set<CsvMatchReason>() };
      current.reasons.add(reason);
      exact.set(book.uuid, current);
    };

    if (externalId && Object.entries(book.identifiers).some(([key, value]) =>
      sourceIds.has(normalizeIdentifier(key)) && normalizeIdentifier(value) === externalId,
    )) {
      addReason("provider_id");
    }

    const identifiers = Object.entries(book.identifiers).filter(([key]) =>
      ISBN_IDENTIFIER_KEYS.has(normalizeIdentifier(key)),
    );
    if (identifiers.some(([, value]) => isbns.has(normalizeIsbn(value)))) {
      addReason("isbn");
    }
  }

  if (exact.size > 0) {
    const candidates = Array.from(exact.values())
      .sort((left, right) => left.book.title.localeCompare(right.book.title, "en", { sensitivity: "base" }))
      .slice(0, MAX_CANDIDATES)
      .map(({ book, reasons }) => ({
        id: book.uuid,
        title: book.title,
        authors: book.authors,
        reasons: ["provider_id", "isbn"].filter((reason) => reasons.has(reason as CsvMatchReason)) as CsvMatchReason[],
      }));
    return { status: exact.size > 1 ? "conflict" : "exact", candidates };
  }

  const candidates = books
    .filter((book) => titleAndAuthorMatch(record, book.title, book.authors))
    .sort((left, right) => left.title.localeCompare(right.title, "en", { sensitivity: "base" }))
    .slice(0, MAX_CANDIDATES)
    .map((book) => ({
      id: book.uuid,
      title: book.title,
      authors: book.authors,
      reasons: ["title_author"] as CsvMatchReason[],
    }));
  if (!candidates.length) return emptyGroup();
  return { status: candidates.length > 1 ? "conflict" : "candidate", candidates };
}

function collectHardcoverMatches(record: PlatformCsvRecord, books: HardcoverBook[]): CsvMatchGroup {
  const candidates = books
    .filter((book) => titleAndAuthorMatch(record, book.title, book.authors))
    .sort((left, right) => left.title.localeCompare(right.title, "en", { sensitivity: "base" }))
    .slice(0, MAX_CANDIDATES)
    .map((book) => ({
      id: String(book.user_book_id),
      title: book.title,
      authors: book.authors,
      reasons: ["title_author"] as CsvMatchReason[],
    }));
  if (!candidates.length) return emptyGroup();
  return { status: candidates.length > 1 ? "conflict" : "candidate", candidates };
}

export function findPlatformCsvMatches(
  record: PlatformCsvRecord,
  source: PlatformCsvSource,
  calibreBooks: CalibreBook[],
  hardcoverBooks: HardcoverBook[],
): PlatformCsvMatches {
  return {
    calibre: collectCalibreMatches(record, source, calibreBooks),
    hardcover: collectHardcoverMatches(record, hardcoverBooks),
  };
}
