import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  ArrowDown,
  ArrowUp,
  AlertTriangle,
  BookOpen,
  BookOpenCheck,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Filter,
  FilePlus2,
  LoaderCircle,
  LibraryBig,
  LayoutGrid,
  Pencil,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import type {
  CalibreBook,
  CalibreCoverSelection,
  CalibreEditChanges,
  CalibreImportBatch,
  HardcoverBook,
  HardcoverDataSource,
  KindleFormat,
  KindleTransfer,
  ReadingApi,
} from "../shared/contracts";
import { getUiLocale, localizeText, type UiLanguage } from "./i18n";
import { UiButton, UiInput, UiSelect, UiTextarea } from "./ui/controls";
import { PlatformCsvDialog } from "./PlatformCsvDialog";

export type LibraryStatus = "loading" | "ready" | "error";
export type HardcoverUiStatus = "checking" | "loading" | "ready" | "unconfigured" | "error";
type Shelf = "calibre" | "hardcover" | "want" | "reading" | "read" | "other";
type SortOrder = "title" | "author" | "rating" | "publication" | "series" | "size" | "calibreId" | "pages" | "lastRead";
type SortDirection = "asc" | "desc";
type ViewMode = "cards" | "shelf";
type RatingFilter = "all" | "rated" | "unrated";

const ROWS_PER_PAGE = 2;
const CALIBRE_LANGUAGE_OPTIONS = [
  ["por", "PT-BR"], ["eng", "ENG"], ["spa", "SPA"], ["fra", "FRA"], ["deu", "DEU"],
  ["ita", "ITA"], ["nld", "NLD"], ["pol", "POL"], ["rus", "RUS"], ["jpn", "JPN"],
  ["zho", "ZHO"], ["kor", "KOR"], ["ara", "ARA"], ["heb", "HEB"], ["hin", "HIN"],
  ["dan", "DAN"], ["fin", "FIN"], ["nor", "NOR"], ["swe", "SWE"], ["ces", "CES"],
  ["ell", "ELL"], ["hun", "HUN"], ["ron", "RON"], ["ukr", "UKR"], ["vie", "VIE"],
  ["tha", "THA"], ["ind", "IND"], ["fas", "FAS"], ["bul", "BUL"], ["hrv", "HRV"],
  ["srp", "SRP"], ["slv", "SLV"], ["cat", "CAT"], ["eus", "EUS"], ["glg", "GLG"],
] as const;

interface CalibreFilters {
  format: string;
  language: string;
  series: string;
  publisher: string;
  tag: string;
}

interface HardcoverFilters {
  format: string;
  rating: RatingFilter;
  digitalOnly: boolean;
}

interface LibraryPageProps {
  language?: UiLanguage;
  books: CalibreBook[];
  status: LibraryStatus;
  hardcoverBooks?: HardcoverBook[];
  hardcoverCurrent?: HardcoverBook | null;
  hardcoverStatus?: HardcoverUiStatus;
  hardcoverSource?: HardcoverDataSource | null;
  hardcoverFetchedAt?: string | null;
  onSelectImport?: () => Promise<CalibreImportBatch | null>;
  onAddImport?: (
    previewId: string,
    fileIndex: number,
    mode: "new" | "attach",
    targetBookId?: number,
  ) => Promise<CalibreBook>;
  onSelectCover?: (bookId: number) => Promise<CalibreCoverSelection | null>;
  onEditBook?: (
    bookId: number,
    changes: CalibreEditChanges,
    coverSelectionId?: string,
  ) => Promise<CalibreBook>;
  onEditTagsBatch?: (
    bookIds: number[],
    addTags: string[],
    removeTags: string[],
  ) => Promise<CalibreBook[]>;
  onRemoveBook?: (bookId: number) => Promise<void>;
  onSendBook?: (bookId: number, format: KindleFormat, filename: string) => Promise<KindleTransfer>;
  onRetry?: () => void;
  onRetryHardcover?: () => void;
  platformCsv?: ReadingApi["platformCsv"];
}

type CatalogItem =
  | { kind: "calibre"; book: CalibreBook; searchText: string }
  | { kind: "hardcover"; book: HardcoverBook; searchText: string };

const supportedKindleFormats: KindleFormat[] = ["EPUB", "PDF", "AZW3", "MOBI", "AZW", "CBZ", "FB2", "TXT"];

function readableActionError(error: unknown): string {
  const code = error instanceof Error ? error.message : "";
  const messages: Record<string, string> = {
    calibre_open: "Feche o Calibre antes de salvar alterações. A biblioteca não foi modificada.",
    backup_unavailable: "Não foi possível criar ou verificar o backup. Nenhuma alteração foi feita.",
    calibre_duplicate_review: "A duplicata precisa ser revisada na janela de importação.",
    calibre_format_exists: "Esse registro já possui este formato. Nenhum arquivo foi substituído.",
    calibre_write_unconfirmed: "O Calibre não confirmou o resultado. Atualize a biblioteca antes de repetir.",
    calibre_invalid_metadata: "Confira os metadados; algum valor tem formato inválido.",
    calibre_import_invalid: "O arquivo selecionado não é um formato compatível.",
    calibre_import_unavailable: "O arquivo selecionado não está mais disponível.",
    calibre_import_metadata: "Não foi possível ler os metadados do arquivo.",
    calibre_unavailable: "A biblioteca ou as ferramentas Calibre estão indisponíveis.",
    calibre_book_not_found: "Este livro não está mais na biblioteca. Atualize a lista.",
    kindle_unavailable: "Conecte o Kindle e deixe a tela de USB conectado aberta.",
    kindle_multiple_devices: "Há mais de um Kindle conectado. Deixe apenas o dispositivo desejado.",
    kindle_destination_exists: "Já existe um arquivo com esse nome. Altere o nome no campo e tente novamente.",
    kindle_transfer_uncertain: "A cópia começou, mas não foi verificada. Confira o Kindle antes de repetir.",
    kindle_transfer_failed: "A transferência falhou. Nenhum arquivo existente foi substituído.",
    kindle_content_mismatch: "A cópia não passou na verificação SHA-256. Não tente repetir sem conferir o Kindle.",
    kindle_invalid_format: "Escolha um formato compatível disponível para este livro.",
    kindle_invalid_name: "Use um nome simples, sem caracteres reservados do Windows, e sem ponto ou espaço no final.",
    operation_timeout: "A operação ainda pode estar rodando. Confira Calibre/Kindle antes de repetir.",
  };
  return messages[code] ?? "Não foi possível concluir a ação. Confira as conexões e tente novamente.";
}

function cleanFileStem(value: string): string {
  return value
    .replace(/[<>:"/\\|?*]/g, " ")
    .split("")
    .filter((character) => character.charCodeAt(0) >= 32 && character.charCodeAt(0) !== 127)
    .join("")
    .replace(/\s+/g, " ")
    .replace(/[ .]+$/g, "")
    .trim()
    .slice(0, 145);
}

function containsControlCharacters(value: string): boolean {
  return Array.from(value).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
}

function normalizeSearchText(value: string): string {
  return value
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLocaleLowerCase("pt-BR");
}

function calibreSearchText(book: CalibreBook): string {
  return normalizeSearchText(
    [
      book.title,
      ...book.authors,
      book.series ?? "",
      book.publisher ?? "",
      ...(book.languages ?? []),
      ...(book.tags ?? []),
      ...(book.formats ?? []),
      String(book.book_id),
      book.uuid,
      ...Object.entries(book.identifiers).flat(),
    ].join(" "),
  );
}

function hardcoverSearchText(book: HardcoverBook): string {
  return normalizeSearchText(
    [book.title, ...book.authors, String(book.book_id), book.edition_format ?? ""].join(" "),
  );
}

function isDigitalEdition(book: HardcoverBook): boolean {
  const format = book.edition_format?.normalize("NFKD").replace(/\p{Diacritic}/gu, "").toLocaleLowerCase("en-US") ?? "";
  return /\b(ebook|e-book|kindle|digital|epub|mobi|azw)\b/.test(format);
}

function formatCount(count: number, singular: string, plural = `${singular}s`, language: UiLanguage = "pt-BR"): string {
  return `${count.toLocaleString(getUiLocale(language))} ${count === 1 ? singular : plural}`;
}

function formatSourceDate(value: string, language: UiLanguage = "pt-BR"): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "data desconhecida"
    : new Intl.DateTimeFormat(getUiLocale(language), { dateStyle: "short", timeStyle: "short" }).format(date);
}

function displayFormat(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "Formato";
  if (/[\\/]/.test(trimmed) || /^[A-Za-z]:/.test(trimmed)) {
    const filename = trimmed.replaceAll("\\", "/").split("/").at(-1) ?? "";
    return filename.match(/\.([A-Za-z0-9]{1,8})$/)?.[1]?.toLocaleUpperCase("en-US") ?? "Arquivo";
  }
  return trimmed.toLocaleUpperCase("en-US");
}

function displayLanguage(code: string): string {
  const normalized = code.trim().replaceAll("_", "-").toLocaleLowerCase("en-US");
  if (["por", "pt", "pt-br", "pt-pt"].includes(normalized)) return "PT-BR";
  if (["eng", "en", "en-us", "en-gb"].includes(normalized)) return "ENG";
  try {
    return new Intl.DisplayNames(["pt-BR"], { type: "language" }).of(normalized) ?? code.toLocaleUpperCase("en-US");
  } catch {
    return code.toLocaleUpperCase("en-US");
  }
}

function formatReadingDate(value: string | null | undefined, language: UiLanguage = "pt-BR"): string | null {
  if (!value) return null;
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.getTime()) ? null : new Intl.DateTimeFormat(getUiLocale(language), { dateStyle: "medium" }).format(date);
}

function uniqueOptions(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.map((value) => value?.trim()).filter((value): value is string => Boolean(value)))]
    .sort((left, right) => left.localeCompare(right, "pt-BR", { sensitivity: "base" }));
}

function latestReadDate(book: HardcoverBook): string | null {
  return book.reads
    .flatMap((read) => [read.started_at, read.finished_at])
    .filter((value): value is string => value !== null)
    .sort((left, right) => right.localeCompare(left))[0] ?? null;
}

function defaultSortDirection(order: SortOrder): SortDirection {
  return ["title", "author", "series"].includes(order) ? "asc" : "desc";
}

function sortDirectionLabel(order: SortOrder, direction: SortDirection): string {
  if (["title", "author", "series"].includes(order)) return direction === "asc" ? "A–Z" : "Z–A";
  if (["publication", "lastRead"].includes(order)) return direction === "asc" ? "Mais antigas" : "Mais recentes";
  if (["rating", "size", "calibreId", "pages"].includes(order)) return direction === "asc" ? "Menor" : "Maior";
  return direction === "asc" ? "Crescente" : "Decrescente";
}

function sortValue(item: CatalogItem, order: SortOrder): string | number | null {
  if (item.kind === "calibre") {
    const book = item.book;
    switch (order) {
      case "author": return book.authors[0] ?? null;
      case "rating": return book.rating ?? null;
      case "publication": return book.pubdate ?? null;
      case "series": return book.series ? `${book.series} ${book.series_index ?? ""}`.trim() : null;
      case "size": return book.size ?? null;
      case "calibreId": return book.book_id;
      default: return book.title;
    }
  }
  const book = item.book;
  switch (order) {
    case "author": return book.authors[0] ?? null;
    case "rating": return book.rating;
    case "pages": return book.edition_pages;
    case "lastRead": return latestReadDate(book);
    default: return book.title;
  }
}

function sortItems(items: CatalogItem[], order: SortOrder, direction: SortDirection): CatalogItem[] {
  return [...items].sort((left, right) => {
    const leftValue = sortValue(left, order);
    const rightValue = sortValue(right, order);
    if (leftValue === null) return rightValue === null ? 0 : 1;
    if (rightValue === null) return -1;
    const comparison = typeof leftValue === "number" && typeof rightValue === "number"
      ? leftValue - rightValue
      : String(leftValue).localeCompare(String(rightValue), "pt-BR", { numeric: true, sensitivity: "base" });
    return direction === "asc" ? comparison : -comparison;
  });
}

function getActiveRead(book: HardcoverBook) {
  return [...book.reads].reverse().find((read) => read.finished_at === null) ?? book.reads.at(-1) ?? null;
}

function progressFor(book: HardcoverBook): number | null {
  const read = getActiveRead(book);
  if (!read || !book.edition_pages || book.edition_pages < 1) return null;
  return Math.max(0, Math.min(100, Math.round((read.progress_pages / book.edition_pages) * 100)));
}

function BookCover({ title, coverUrl, number, language = "pt-BR" }: { title: string; coverUrl?: string | null; number: string; language?: UiLanguage }) {
  const [failed, setFailed] = useState(false);
  if (coverUrl && !failed) {
    return (
      <img
        className="book-card-cover"
        src={coverUrl}
        alt={`${language === "en" ? "Cover" : "Capa"}: ${title}`}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
      />
    );
  }
  return (
    <div className="book-card-cover book-card-cover-placeholder" aria-hidden="true">
      <BookOpen size={25} strokeWidth={1.6} />
      <span>{number}</span>
    </div>
  );
}

export function LibraryPage({
  language = "pt-BR",
  books,
  status,
  hardcoverBooks = [],
  hardcoverCurrent = null,
  hardcoverStatus = "unconfigured",
  hardcoverSource = null,
  hardcoverFetchedAt = null,
  onSelectImport,
  onAddImport,
  onSelectCover,
  onEditBook,
  onEditTagsBatch,
  onRemoveBook,
  onSendBook,
  onRetry,
  onRetryHardcover,
  platformCsv,
}: LibraryPageProps) {
  const tx = (text: string) => localizeText(language, text);
  const [shelf, setShelf] = useState<Shelf>("calibre");
  const [search, setSearch] = useState("");
  const [sortOrder, setSortOrder] = useState<SortOrder>("title");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [viewMode, setViewMode] = useState<ViewMode>("cards");
  const [pagination, setPagination] = useState({ context: "", page: 0 });
  const [selectingBooks, setSelectingBooks] = useState(false);
  const [selectedBookIds, setSelectedBookIds] = useState<Set<number>>(() => new Set());
  const [calibreFilters, setCalibreFilters] = useState<CalibreFilters>({
    format: "all", language: "all", series: "all", publisher: "all", tag: "all",
  });
  const [hardcoverFilters, setHardcoverFilters] = useState<HardcoverFilters>({
    format: "all", rating: "all", digitalOnly: true,
  });
  const [columns, setColumns] = useState(1);
  const gridRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const filterDetailsRef = useRef<HTMLDetailsElement>(null);
  const [importBatch, setImportBatch] = useState<CalibreImportBatch | null>(null);
  const [bulkTagsDialog, setBulkTagsDialog] = useState(false);
  const [editingBook, setEditingBook] = useState<CalibreBook | null>(null);
  const [removingBook, setRemovingBook] = useState<CalibreBook | null>(null);
  const [sendingBook, setSendingBook] = useState<CalibreBook | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<string | null>(null);
  const [platformCsvOpen, setPlatformCsvOpen] = useState(false);

  useEffect(() => {
    const closeFiltersOnOutsideClick = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !filterDetailsRef.current?.contains(target)) {
        if (filterDetailsRef.current) filterDetailsRef.current.open = false;
      }
    };
    document.addEventListener("pointerdown", closeFiltersOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeFiltersOnOutsideClick);
  }, []);

  useEffect(() => {
    if (!importBatch && !editingBook && !sendingBook && !removingBook && !bulkTagsDialog && !platformCsvOpen) return;
    const previousRootOverflow = document.documentElement.style.overflow;
    const previousOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    return () => {
      document.documentElement.style.overflow = previousRootOverflow;
      document.body.style.overflow = previousOverflow;
    };
  }, [importBatch, editingBook, sendingBook, removingBook, bulkTagsDialog, platformCsvOpen]);

  useEffect(() => {
    if (shelf !== "calibre") {
      setSelectingBooks(false);
      setSelectedBookIds(new Set());
    }
  }, [shelf]);

  const currentReading = useMemo(
    () => hardcoverCurrent ?? hardcoverBooks.find((book) => book.status_id === 2) ?? null,
    [hardcoverBooks, hardcoverCurrent],
  );

  const calibreFacetOptions = useMemo(() => ({
    formats: uniqueOptions(books.flatMap((book) => (book.formats ?? []).map(displayFormat))),
    languages: uniqueOptions(books.flatMap((book) => book.languages ?? [])),
    series: uniqueOptions(books.map((book) => book.series)),
    publishers: uniqueOptions(books.map((book) => book.publisher)),
    tags: uniqueOptions(books.flatMap((book) => book.tags ?? [])),
  }), [books]);

  const hardcoverFormatOptions = useMemo(
    () => uniqueOptions(hardcoverBooks.map((book) => book.edition_format)),
    [hardcoverBooks],
  );

  const matchingHardcoverBooks = useMemo(
    () => hardcoverBooks.filter((book) => {
      if (hardcoverFilters.digitalOnly && !isDigitalEdition(book)) return false;
      if (hardcoverFilters.format !== "all" && book.edition_format !== hardcoverFilters.format) return false;
      if (hardcoverFilters.rating === "rated" && book.rating === null) return false;
      if (hardcoverFilters.rating === "unrated" && book.rating !== null) return false;
      return true;
    }),
    [hardcoverBooks, hardcoverFilters],
  );

  const shelfCounts = useMemo(() => ({
    calibre: books.length,
    hardcover: matchingHardcoverBooks.length,
    want: matchingHardcoverBooks.filter((book) => book.status_id === 1).length,
    reading: matchingHardcoverBooks.filter((book) => book.status_id === 2).length,
    read: matchingHardcoverBooks.filter((book) => book.status_id === 3).length,
    other: matchingHardcoverBooks.filter((book) => ![1, 2, 3].includes(book.status_id)).length,
  }), [books.length, matchingHardcoverBooks]);

  const indexedItems = useMemo<CatalogItem[]>(() => {
    if (shelf === "calibre") {
      return books
        .filter((book) => {
          if (calibreFilters.format !== "all" && !book.formats?.some((format) => displayFormat(format) === calibreFilters.format)) return false;
          if (calibreFilters.language !== "all" && !book.languages?.includes(calibreFilters.language)) return false;
          if (calibreFilters.series !== "all" && book.series !== calibreFilters.series) return false;
          if (calibreFilters.publisher !== "all" && book.publisher !== calibreFilters.publisher) return false;
          if (calibreFilters.tag !== "all" && !book.tags?.includes(calibreFilters.tag)) return false;
          return true;
        })
        .map((book) => ({ kind: "calibre", book, searchText: calibreSearchText(book) }));
    }
    const shelfBooks = matchingHardcoverBooks.filter((book) => {
      if (shelf === "hardcover") return true;
      if (shelf === "want") return book.status_id === 1;
      if (shelf === "reading") return book.status_id === 2;
      if (shelf === "read") return book.status_id === 3;
      return ![1, 2, 3].includes(book.status_id);
    });
    return shelfBooks.map((book) => ({ kind: "hardcover", book, searchText: hardcoverSearchText(book) }));
  }, [books, calibreFilters, matchingHardcoverBooks, shelf]);

  const terms = useMemo(
    () => normalizeSearchText(search).trim().split(/\s+/).filter(Boolean),
    [search],
  );

  const visibleItems = useMemo(() => {
    const filtered = terms.length === 0
      ? indexedItems
      : indexedItems.filter(({ searchText }) => terms.every((term) => searchText.includes(term)));
    return sortItems(filtered, sortOrder, sortDirection);
  }, [indexedItems, sortDirection, sortOrder, terms]);

  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid) return;
    const measure = () => {
      const width = grid.getBoundingClientRect().width;
      const minColumnWidth = viewMode === "shelf" ? 142 : 194;
      setColumns(Math.min(viewMode === "shelf" ? 9 : 6, Math.max(1, Math.floor((width + 14) / minColumnWidth))));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    return () => observer.disconnect();
  }, [shelf, status, hardcoverStatus, viewMode]);

  const gridRows = useMemo(() => {
    const result: CatalogItem[][] = [];
    for (let index = 0; index < visibleItems.length; index += columns) {
      result.push(visibleItems.slice(index, index + columns));
    }
    return result;
  }, [columns, visibleItems]);

  const paginationContext = JSON.stringify([
    shelf, search, sortOrder, sortDirection, viewMode, columns, calibreFilters, hardcoverFilters,
  ]);
  const pageCount = Math.max(1, Math.ceil(gridRows.length / ROWS_PER_PAGE));
  const requestedPage = pagination.context === paginationContext ? pagination.page : 0;
  const currentPage = Math.min(requestedPage, pageCount - 1);
  const pageRows = gridRows.slice(currentPage * ROWS_PER_PAGE, (currentPage + 1) * ROWS_PER_PAGE);
  const pageStart = currentPage * ROWS_PER_PAGE * columns + 1;
  const pageEnd = Math.min((currentPage + 1) * ROWS_PER_PAGE * columns, visibleItems.length);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase("pt-BR") === "k") {
        event.preventDefault();
        searchInputRef.current?.focus();
      } else if (event.key === "Escape" && document.activeElement === searchInputRef.current && search) {
        setSearch("");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [search]);

  const shelfLabels: Array<{ id: Shelf; label: string }> = [
    { id: "calibre", label: "Calibre" },
    { id: "hardcover", label: "Hardcover" },
    { id: "want", label: tx("Quero ler") },
    { id: "reading", label: tx("Lendo") },
    { id: "read", label: tx("Lidos") },
    { id: "other", label: tx("Outros") },
  ];
  const sortOptions: Array<{ value: SortOrder; label: string }> = shelf === "calibre"
    ? [
      { value: "title", label: tx("Título") },
      { value: "author", label: tx("Autor") },
      { value: "publication", label: tx("Data de publicação") },
      { value: "rating", label: tx("Avaliação") },
      { value: "series", label: tx("Série") },
      { value: "size", label: tx("Tamanho do arquivo") },
      { value: "calibreId", label: tx("ID do Calibre") },
    ]
    : [
      { value: "title", label: tx("Título") },
      { value: "author", label: tx("Autor") },
      { value: "rating", label: tx("Minha avaliação") },
      { value: "pages", label: tx("Número de páginas") },
      { value: "lastRead", label: tx("Última leitura") },
    ];
  const activeCount = shelfCounts[shelf];
  const activeFilterCount = shelf === "calibre"
    ? Object.values(calibreFilters).filter((value) => value !== "all").length
    : Number(hardcoverFilters.digitalOnly) + Number(hardcoverFilters.format !== "all") + Number(hardcoverFilters.rating !== "all");
  const sourceShelfCount = shelf === "calibre"
    ? books.length
    : hardcoverBooks.filter((book) => shelf === "hardcover"
      || (shelf === "want" && book.status_id === 1)
      || (shelf === "reading" && book.status_id === 2)
      || (shelf === "read" && book.status_id === 3)
      || (shelf === "other" && ![1, 2, 3].includes(book.status_id))).length;
  const bookCount = search.trim() || activeFilterCount > 0
    ? language === "en" ? `${visibleItems.length.toLocaleString(getUiLocale(language))} ${visibleItems.length === 1 ? "result" : "results"}` : formatCount(visibleItems.length, "resultado", "resultados", language)
    : language === "en" ? `${activeCount.toLocaleString(getUiLocale(language))} ${activeCount === 1 ? "book" : "books"}` : formatCount(activeCount, "livro", "livros", language);

  function clearCurrentFilters() {
    if (shelf === "calibre") {
      setCalibreFilters({ format: "all", language: "all", series: "all", publisher: "all", tag: "all" });
    } else {
      setHardcoverFilters({ format: "all", rating: "all", digitalOnly: false });
    }
    if (filterDetailsRef.current) filterDetailsRef.current.open = false;
  }

  function renderPagination(position: "topo" | "inferior") {
    if (visibleItems.length === 0 || pageCount <= 1) return null;
    return (
      <nav className="library-pagination" aria-label={language === "en" ? `Library pagination (${position === "topo" ? "top" : "bottom"})` : `Paginação ${position} da biblioteca`}>
        <span className="library-pagination-summary">
          {language === "en"
            ? `Books ${pageStart.toLocaleString(getUiLocale(language))}–${pageEnd.toLocaleString(getUiLocale(language))} of ${visibleItems.length.toLocaleString(getUiLocale(language))}`
            : `Livros ${pageStart.toLocaleString(getUiLocale(language))}–${pageEnd.toLocaleString(getUiLocale(language))} de ${visibleItems.length.toLocaleString(getUiLocale(language))}`}
        </span>
        <div className="library-pagination-controls">
          <UiButton
            className="button-secondary pagination-button"
            type="button"
            aria-label={tx("Página anterior")}
            disabled={currentPage === 0}
            onClick={() => setPagination({ context: paginationContext, page: currentPage - 1 })}
          >
            <ChevronLeft size={15} aria-hidden="true" /> {tx("Anterior")}
          </UiButton>
          <span className="library-pagination-page" aria-live="polite">
            {language === "en" ? `Page ${currentPage + 1} of ${pageCount}` : `Página ${currentPage + 1} de ${pageCount}`}
          </span>
          <UiButton
            className="button-secondary pagination-button"
            type="button"
            aria-label={tx("Próxima página")}
            disabled={currentPage >= pageCount - 1}
            onClick={() => setPagination({ context: paginationContext, page: currentPage + 1 })}
          >
            {tx("Próxima")} <ChevronRight size={15} aria-hidden="true" />
          </UiButton>
        </div>
      </nav>
    );
  }

  return (
    <section className="library-page" aria-labelledby="library-title">
      <div className="page-heading">
        <div className="page-heading-copy">
          <span className="eyebrow">{tx("SUA BIBLIOTECA")}</span>
          <h1 id="library-title">{tx("Biblioteca")}</h1>
          <p>{tx("Calibre e Hardcover, com cada informação na sua fonte.")}</p>
        </div>
        <div className="library-heading-actions">
          <div className={`source-chip${hardcoverStatus === "ready" ? " source-chip-ready" : ""}`}>
            <span className="source-indicator" aria-hidden="true" />
            <span>{hardcoverStatus === "ready" ? hardcoverSource === "cache" ? tx("Hardcover · cache local") : tx("Hardcover conectado") : tx("Hardcover não conectado")}</span>
          </div>
          {hardcoverSource === "cache" && hardcoverFetchedAt ? <small className="source-cache-note">{language === "en" ? `Cache saved at ${formatSourceDate(hardcoverFetchedAt, language)} · may be out of date` : `Cache salvo em ${formatSourceDate(hardcoverFetchedAt, language)} · pode estar desatualizado`}</small> : null}
          {shelf === "calibre" && onSelectImport ? (
            <UiButton
              className="button-primary add-book-button"
              type="button"
              onClick={() => void beginImport()}
              disabled={actionBusy}
            >
              <FilePlus2 size={16} aria-hidden="true" /> {tx("Adicionar livro")}
            </UiButton>
          ) : null}
          {platformCsv ? (
            <UiButton
              className="button-secondary platform-csv-open-button"
              type="button"
              onClick={() => setPlatformCsvOpen(true)}
            >
              <BookOpenCheck size={16} aria-hidden="true" /> {tx("Histórico CSV")}
            </UiButton>
          ) : null}
        </div>
      </div>

      <CurrentReadingCard
        language={language}
        book={currentReading}
        status={hardcoverStatus}
        onRetry={onRetryHardcover}
      />

      <div className="shelf-switcher" role="tablist" aria-label={tx("Estantes e biblioteca")}>
        {shelfLabels.map(({ id, label }) => (
          <UiButton
            key={id}
            type="button"
            role="tab"
            aria-selected={shelf === id}
            className={`shelf-tab${shelf === id ? " is-active" : ""}`}
            onClick={() => {
              setShelf(id);
              setSearch("");
              setSortOrder("title");
              setSortDirection("asc");
            }}
          >
            <span>{label}</span>
            <span className="shelf-count">{shelfCounts[id].toLocaleString("pt-BR")}</span>
          </UiButton>
        ))}
      </div>

      <div className="library-toolbar">
        <div className="library-toolbar-actions">
          <div className="sort-field sort-field-primary">
            <SlidersHorizontal size={16} aria-hidden="true" />
            <span className="sort-field-value">
              {sortOptions.find((option) => option.value === sortOrder)?.label ?? tx("Título")}
            </span>
            <ChevronDown className="sort-field-chevron" size={14} aria-hidden="true" />
            <UiSelect
              aria-label={tx("Ordenar estante")}
              className="sort-field-hit-target"
              value={sortOrder}
              onChange={(event) => {
                const nextOrder = event.currentTarget.value as SortOrder;
                setSortOrder(nextOrder);
                setSortDirection(defaultSortDirection(nextOrder));
              }}
            >
              {sortOptions.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
            </UiSelect>
          </div>
          <UiButton
            className="sort-direction-button"
            type="button"
            aria-label={language === "en" ? `${sortDirection === "asc" ? "Ascending" : "Descending"} order; reverse order` : `Ordem ${sortDirection === "asc" ? "crescente" : "decrescente"}; inverter ordem`}
            title={tx(sortDirection === "asc" ? "Ordem crescente" : "Ordem decrescente")}
            onClick={() => setSortDirection((current) => current === "asc" ? "desc" : "asc")}
          >
            {sortDirection === "asc" ? <ArrowUp size={15} aria-hidden="true" /> : <ArrowDown size={15} aria-hidden="true" />}
            <span>{tx(sortDirectionLabel(sortOrder, sortDirection))}</span>
          </UiButton>
          <details className="toolbar-filter" ref={filterDetailsRef}>
            <summary
              aria-label={tx("Abrir filtros da estante")}
              aria-controls="library-filter-panel"
            >
              <Filter size={15} aria-hidden="true" />
              <span>{tx("Filtros")}</span>
              {activeFilterCount > 0 ? <span className="filter-count-badge">{activeFilterCount}</span> : null}
            </summary>
            <div className="toolbar-filter-panel" id="library-filter-panel">
              <div className="filter-panel-heading">
                <div><strong>{tx("Filtrar biblioteca")}</strong><span>{activeFilterCount > 0 ? language === "en" ? `${activeFilterCount} active` : `${activeFilterCount} ativo${activeFilterCount === 1 ? "" : "s"}` : tx("Sem filtros")}</span></div>
                <UiButton type="button" className="filter-clear-button" onClick={clearCurrentFilters} disabled={activeFilterCount === 0}>{tx("Limpar")}</UiButton>
              </div>
              {shelf === "calibre" ? (
                <div className="filter-fields">
                  <label className="filter-field"><span>{tx("Formato")}</span><UiSelect value={calibreFilters.format} onChange={(event) => setCalibreFilters((current) => ({ ...current, format: event.currentTarget.value }))}><option value="all">{tx("Todos")}</option>{calibreFacetOptions.formats.map((format) => <option key={format} value={format}>{format}</option>)}</UiSelect></label>
                  <label className="filter-field"><span>{tx("Idioma")}</span><UiSelect value={calibreFilters.language} onChange={(event) => setCalibreFilters((current) => ({ ...current, language: event.currentTarget.value }))}><option value="all">{tx("Todos")}</option>{calibreFacetOptions.languages.map((language) => <option key={language} value={language}>{displayLanguage(language)}</option>)}</UiSelect></label>
                  <label className="filter-field"><span>{tx("Série")}</span><UiSelect value={calibreFilters.series} onChange={(event) => setCalibreFilters((current) => ({ ...current, series: event.currentTarget.value }))}><option value="all">{tx("Todas")}</option>{calibreFacetOptions.series.map((series) => <option key={series} value={series}>{series}</option>)}</UiSelect></label>
                  <label className="filter-field"><span>{tx("Editora")}</span><UiSelect value={calibreFilters.publisher} onChange={(event) => setCalibreFilters((current) => ({ ...current, publisher: event.currentTarget.value }))}><option value="all">{tx("Todas")}</option>{calibreFacetOptions.publishers.map((publisher) => <option key={publisher} value={publisher}>{publisher}</option>)}</UiSelect></label>
                  <label className="filter-field"><span>{tx("Tag")}</span><UiSelect value={calibreFilters.tag} onChange={(event) => setCalibreFilters((current) => ({ ...current, tag: event.currentTarget.value }))}><option value="all">{tx("Todas")}</option>{calibreFacetOptions.tags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}</UiSelect></label>
                </div>
              ) : (
                <div className="filter-fields">
                  <label className="edition-filter filter-edition-option"><UiInput type="checkbox" checked={hardcoverFilters.digitalOnly} onChange={(event) => setHardcoverFilters((current) => ({ ...current, digitalOnly: event.currentTarget.checked }))} /><span>{tx("Somente eBook / Kindle")}</span></label>
                  <label className="filter-field"><span>{tx("Formato da edição")}</span><UiSelect value={hardcoverFilters.format} onChange={(event) => setHardcoverFilters((current) => ({ ...current, format: event.currentTarget.value }))}><option value="all">{tx("Todos")}</option>{hardcoverFormatOptions.map((format) => <option key={format} value={format}>{format}</option>)}</UiSelect></label>
                  <label className="filter-field"><span>{tx("Avaliação")}</span><UiSelect value={hardcoverFilters.rating} onChange={(event) => setHardcoverFilters((current) => ({ ...current, rating: event.currentTarget.value as RatingFilter }))}><option value="all">{tx("Com ou sem avaliação")}</option><option value="rated">{tx("Avaliados")}</option><option value="unrated">{tx("Sem avaliação")}</option></UiSelect></label>
                  <p className="filter-panel-hint">{tx("As estantes e avaliações são lidas do Hardcover; nenhum status será alterado.")}</p>
                </div>
              )}
            </div>
          </details>
        </div>
        <label className="search-field">
          <Search size={19} strokeWidth={2} aria-hidden="true" />
          <span className="visually-hidden">{tx("Buscar nesta estante")}</span>
          <UiInput
            ref={searchInputRef}
            type="search"
            aria-label={tx("Buscar nesta estante")}
            autoComplete="off"
            spellCheck={false}
            placeholder={tx(shelf === "calibre" ? "Título, autor, série, editora, tag ou ISBN" : "Título ou autor no Hardcover")}
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
          />
          <kbd>Ctrl K</kbd>
        </label>
        <UiButton
          className="view-mode-field"
          type="button"
          aria-label={`${tx("Alternar para visualização em")} ${tx(viewMode === "cards" ? "capas" : "cards")}`}
          aria-pressed={viewMode === "shelf"}
          onClick={() => setViewMode((current) => current === "cards" ? "shelf" : "cards")}
        >
          {viewMode === "cards" ? <LayoutGrid size={16} aria-hidden="true" /> : <BookOpen size={16} aria-hidden="true" />}
          <span>{viewMode === "cards" ? tx("Cards") : tx("Capas")}</span>
        </UiButton>
        <p className="catalog-count" aria-live="polite">{bookCount}</p>
      </div>

      {shelf === "calibre" && onEditTagsBatch ? (
        <div className="bulk-selection-bar">
          <UiButton
            className="button-secondary bulk-selection-toggle"
            type="button"
            aria-pressed={selectingBooks}
            onClick={() => {
              setSelectingBooks((current) => !current);
              setSelectedBookIds(new Set());
            }}
          >
            {tx(selectingBooks ? "Sair da seleção" : "Selecionar vários")}
          </UiButton>
          {selectingBooks ? (
            <>
              <span className="bulk-selection-count">{selectedBookIds.size} {tx("selecionados")}</span>
              <UiButton
                className="button-secondary"
                type="button"
                disabled={visibleItems.length === 0}
                onClick={() => setSelectedBookIds(new Set(pageRows.flat().flatMap((item) =>
                  item.kind === "calibre" ? [item.book.book_id] : [],
                )))}
              >
                {tx("Selecionar página")}
              </UiButton>
              {selectedBookIds.size > 0 ? (
                <>
                  <UiButton className="button-secondary" type="button" onClick={() => setSelectedBookIds(new Set())}>
                    {tx("Limpar")}
                  </UiButton>
                  <UiButton
                    className="button-primary"
                    type="button"
                    onClick={() => { setActionError(null); setBulkTagsDialog(true); }}
                  >
                    {tx("Editar tags")} ({selectedBookIds.size})
                  </UiButton>
                </>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      {shelf === "calibre" && status === "loading" ? (
        <div className="catalog-state" role="status">
          <span className="loading-mark" aria-hidden="true" />
          <div><strong>{tx("Carregando biblioteca")}</strong><p>{tx("Consultando os livros do Calibre.")}</p></div>
        </div>
      ) : null}
      {shelf === "calibre" && status === "error" ? (
        <div className="catalog-state catalog-state-error" role="alert">
          <div className="state-icon"><LibraryBig size={21} aria-hidden="true" /></div>
          <div className="state-copy">
            <strong>{tx("Não foi possível abrir a biblioteca")}</strong>
            <p>{tx("Confira se o Calibre está instalado e se a biblioteca configurada está disponível.")}</p>
          </div>
          {onRetry ? <UiButton className="button-secondary" type="button" onClick={onRetry}>{tx("Tentar novamente")}</UiButton> : null}
        </div>
      ) : null}
      {shelf !== "calibre" && hardcoverStatus === "checking" || shelf !== "calibre" && hardcoverStatus === "loading" ? (
        <div className="catalog-state" role="status">
          <span className="loading-mark" aria-hidden="true" />
          <div><strong>{tx("Atualizando suas estantes")}</strong><p>{tx("O Calibre continua disponível na aba ao lado.")}</p></div>
        </div>
      ) : null}
      {shelf !== "calibre" && (hardcoverStatus === "unconfigured" || hardcoverStatus === "error") ? (
        <div className="catalog-state catalog-state-error" role="status">
          <div className="state-icon"><BookOpenCheck size={21} aria-hidden="true" /></div>
          <div className="state-copy">
            <strong>{tx(hardcoverStatus === "unconfigured" ? "Conecte o Hardcover para ver esta estante" : "Não foi possível atualizar o Hardcover")}</strong>
            <p>{tx(hardcoverStatus === "unconfigured" ? "Configure sua chave em Configuração. A lista do Calibre não depende dela." : "Falhas temporárias são tentadas novamente automaticamente. Se persistir, confira a chave e a conexão; o Calibre continua disponível.")}</p>
          </div>
          {hardcoverStatus === "error" && onRetryHardcover ? <UiButton className="button-secondary" type="button" onClick={onRetryHardcover}>{tx("Atualizar")}</UiButton> : null}
        </div>
      ) : null}
      {status === "ready" && shelf === "calibre" && books.length === 0 ? (
        <div className="empty-library">
          <div className="empty-icon"><BookOpen size={25} aria-hidden="true" /></div>
          <span className="eyebrow">{tx("CATÁLOGO VAZIO")}</span>
          <h2>{tx("Nenhum livro na biblioteca Calibre")}</h2>
          <p>{tx("Quando você adicionar livros, eles aparecerão aqui.")}</p>
        </div>
      ) : null}
      {shelf !== "calibre" && hardcoverStatus === "ready" && sourceShelfCount === 0 ? (
        <div className="empty-library">
          <div className="empty-icon"><BookOpenCheck size={25} aria-hidden="true" /></div>
          <span className="eyebrow">{tx("ESTANTE VAZIA")}</span>
          <h2>{tx("Nenhum livro nesta estante")}</h2>
          <p>{tx("O status é lido diretamente da sua biblioteca Hardcover.")}</p>
        </div>
      ) : null}
      {visibleItems.length === 0 && sourceShelfCount > 0 && (shelf === "calibre" ? status === "ready" : hardcoverStatus === "ready") ? (
        <div className="no-results" role="status">
          <Search size={22} aria-hidden="true" />
          <strong>{tx("Nenhum livro encontrado")}</strong>
          <p>{tx(activeFilterCount > 0 ? "Tente ajustar os filtros ou limpar a busca." : "Tente outro título ou autor.")}</p>
          {activeFilterCount > 0 ? <UiButton className="button-secondary" type="button" onClick={clearCurrentFilters}>{tx("Limpar filtros")}</UiButton> : null}
        </div>
      ) : null}

      {renderPagination("topo")}

      {visibleItems.length > 0 ? (
        <div className="book-grid" ref={gridRef} role="list" aria-label={shelfLabels.find((item) => item.id === shelf)?.label}>
          {pageRows.map((items, rowIndex) => (
            <div
              className="book-grid-row"
              key={`${currentPage}:${rowIndex}`}
              style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
            >
              {items.map((item) => (
                <CatalogBookCard
                  language={language}
                  key={`${item.kind}:${item.kind === "calibre" ? item.book.uuid : item.book.user_book_id}`}
                  item={item}
                  viewMode={viewMode}
                  selectable={selectingBooks}
                  selected={item.kind === "calibre" && selectedBookIds.has(item.book.book_id)}
                  onEdit={setEditingBook}
                  onSend={setSendingBook}
                  onToggleSelect={(bookId) => setSelectedBookIds((current) => {
                    const next = new Set(current);
                    if (next.has(bookId)) next.delete(bookId);
                    else next.add(bookId);
                    return next;
                  })}
                />
              ))}
            </div>
          ))}
        </div>
      ) : null}

      {renderPagination("inferior")}

      {actionResult ? <div className="action-result" role="status"><Check size={17} aria-hidden="true" />{actionResult}<UiButton className="icon-button" type="button" aria-label={tx("Fechar aviso")} onClick={() => setActionResult(null)}><X size={15} /></UiButton></div> : null}
      {actionError && !importBatch && !editingBook && !sendingBook && !bulkTagsDialog ? <div className="action-error" role="alert"><AlertTriangle size={17} aria-hidden="true" />{tx(actionError)}<UiButton className="icon-button" type="button" aria-label={tx("Fechar aviso")} onClick={() => setActionError(null)}><X size={15} /></UiButton></div> : null}
      {importBatch ? (
        <ImportDialog
          language={language}
          batch={importBatch}
          busy={actionBusy}
          error={actionError}
          onClose={() => { if (!actionBusy) { setImportBatch(null); setActionError(null); } }}
          onAdd={(mode, targetBookId) => void confirmImport(mode, targetBookId)}
        />
      ) : null}
      {bulkTagsDialog && onEditTagsBatch ? (
        <BulkTagsDialog
          language={language}
          books={books.filter((book) => selectedBookIds.has(book.book_id))}
          tagOptions={calibreFacetOptions.tags}
          busy={actionBusy}
          error={actionError}
          onClose={() => { if (!actionBusy) { setBulkTagsDialog(false); setActionError(null); } }}
          onConfirm={(addTags, removeTags) => void confirmBulkTags(addTags, removeTags)}
        />
      ) : null}
      {editingBook ? (
        <EditBookDialog
          language={language}
          book={editingBook}
          seriesOptions={calibreFacetOptions.series}
          tagOptions={calibreFacetOptions.tags}
          onSelectCover={onSelectCover ? () => onSelectCover(editingBook.book_id) : undefined}
          busy={actionBusy}
          error={actionError}
          onClose={() => { if (!actionBusy) { setEditingBook(null); setActionError(null); } }}
          onSave={(changes, coverSelectionId) => void confirmEdit(changes, coverSelectionId)}
          onRemove={onRemoveBook ? () => {
            setRemovingBook(editingBook);
            setEditingBook(null);
            setActionError(null);
            setActionResult(null);
          } : undefined}
        />
      ) : null}
      {removingBook ? (
        <RemoveBookDialog
          language={language}
          book={removingBook}
          busy={actionBusy}
          error={actionError}
          onClose={() => { if (!actionBusy) { setRemovingBook(null); setActionError(null); } }}
          onConfirm={() => void confirmRemove()}
        />
      ) : null}
      {sendingBook ? (
        <SendBookDialog
          language={language}
          book={sendingBook}
          busy={actionBusy}
          error={actionError}
          onClose={() => { if (!actionBusy) { setSendingBook(null); setActionError(null); } }}
          onSend={(format, filename) => void confirmSend(format, filename)}
        />
      ) : null}
      {platformCsvOpen && platformCsv ? (
        <PlatformCsvDialog
          language={language}
          api={platformCsv}
          calibreBooks={books}
          hardcoverBooks={hardcoverBooks}
          onClose={() => setPlatformCsvOpen(false)}
        />
      ) : null}
    </section>
  );

  async function beginImport() {
    if (!onSelectImport || actionBusy) return;
    setActionError(null);
    setActionResult(null);
    try {
      const batch = await onSelectImport();
      if (batch) setImportBatch(batch);
    } catch (error) {
      setActionError(readableActionError(error));
    }
  }

  async function confirmImport(mode: "new" | "attach", targetBookId?: number) {
    if (!importBatch || !onAddImport || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    try {
      const imported = await onAddImport(importBatch.preview_id, 0, mode, targetBookId);
      setImportBatch(null);
      setActionResult(language === "en"
        ? `“${imported.title}” was added to the Calibre library after backup.`
        : `“${imported.title}” foi salvo na biblioteca Calibre após o backup.`);
    } catch (error) {
      onRetry?.();
      setActionError(readableActionError(error));
    } finally {
      setActionBusy(false);
    }
  }

  async function confirmEdit(changes: CalibreEditChanges, coverSelectionId?: string) {
    if (!editingBook || !onEditBook || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    try {
      const updated = await onEditBook(editingBook.book_id, changes, coverSelectionId);
      setEditingBook(null);
      setActionResult(language === "en"
        ? `Metadata for “${updated.title}” was saved and read back from Calibre.`
        : `Metadados de “${updated.title}” foram salvos e relidos do Calibre.`);
    } catch (error) {
      onRetry?.();
      setActionError(readableActionError(error));
    } finally {
      setActionBusy(false);
    }
  }

  async function confirmBulkTags(addTags: string[], removeTags: string[]) {
    if (!onEditTagsBatch || selectedBookIds.size === 0 || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    try {
      const updated = await onEditTagsBatch([...selectedBookIds], addTags, removeTags);
      setBulkTagsDialog(false);
      setSelectedBookIds(new Set());
      setSelectingBooks(false);
      setActionResult(language === "en"
        ? `Tags updated for ${updated.length} ${updated.length === 1 ? "book" : "books"} after backup and Calibre read-back.`
        : `Tags atualizadas em ${updated.length} livros após backup e releitura do Calibre.`);
    } catch (error) {
      onRetry?.();
      setActionError(readableActionError(error));
    } finally {
      setActionBusy(false);
    }
  }

  async function confirmSend(format: KindleFormat, filename: string) {
    if (!sendingBook || !onSendBook || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    setActionResult(null);
    try {
      const result = await onSendBook(sendingBook.book_id, format, filename);
      setSendingBook(null);
      setActionResult(result.already_present
        ? language === "en" ? `“${result.filename}” already existed on the Kindle with the same SHA-256; nothing was copied.` : `“${result.filename}” já existia no Kindle com o mesmo SHA-256; nada foi copiado.`
        : language === "en" ? `“${result.filename}” was sent to the Kindle and verified with SHA-256.` : `“${result.filename}” foi enviado e verificado no Kindle por SHA-256.`);
    } catch (error) {
      setActionError(readableActionError(error));
    } finally {
      setActionBusy(false);
    }
  }

  async function confirmRemove() {
    if (!removingBook || !onRemoveBook || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    setActionResult(null);
    try {
      const title = removingBook.title;
      await onRemoveBook(removingBook.book_id);
      setRemovingBook(null);
      setActionResult(language === "en"
        ? `“${title}” was moved to the Calibre Recycle Bin after a verified backup.`
        : `“${title}” foi movido para a lixeira do Calibre após backup verificado.`);
    } catch (error) {
      onRetry?.();
      setActionError(readableActionError(error));
    } finally {
      setActionBusy(false);
    }
  }
}

function CurrentReadingCard({
  language,
  book,
  status,
  onRetry,
}: {
  language: UiLanguage;
  book: HardcoverBook | null;
  status: HardcoverUiStatus;
  onRetry?: () => void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const progress = book ? progressFor(book) : null;
  const read = book ? getActiveRead(book) : null;
  const startDate = formatReadingDate(read?.started_at, language);
  return (
    <section className="current-reading-card" aria-label={tx("Livro atualmente em leitura")}>
      <div className="current-reading-heading">
        <div><span className="eyebrow">{tx("HARDCOVER · LEITURA ATUAL")}</span><h2>{tx("Lendo agora")}</h2></div>
        {book ? <span className="current-reading-badge">{progress === null ? tx("Em andamento") : `${progress}%`}</span> : null}
      </div>
      {book ? (
        <div className="current-reading-content">
          <BookCover title={book.title} coverUrl={book.cover_url} number={String(book.book_id).padStart(3, "0")} language={language} />
          <div className="current-reading-details">
            <h3>{book.title}</h3>
          <p className="book-authors">{book.authors.join(", ") || tx("Autor não informado")}</p>
            <p className="current-reading-pages">
            {read ? language === "en" ? `Page ${read.progress_pages.toLocaleString(getUiLocale(language))}` : `Página ${read.progress_pages.toLocaleString(getUiLocale(language))}` : tx("Progresso não informado")}
            {book.edition_pages ? ` ${language === "en" ? "of" : "de"} ${book.edition_pages.toLocaleString(getUiLocale(language))}` : ""}
            {startDate ? ` · ${tx("Iniciado em")} ${startDate}` : ""}
            </p>
            <div className="progress-track" aria-label={progress === null ? undefined : `${progress}% lido`}>
            <span style={{ width: `${progress ?? 0}%` }} />
            </div>
          </div>
        </div>
      ) : (
        <div className="current-reading-empty">
          <BookOpenCheck size={19} aria-hidden="true" />
          <p>
            {status === "unconfigured"
              ? tx("Conecte o Hardcover em Configuração para mostrar sua leitura atual.")
              : status === "checking" || status === "loading"
                ? tx("Verificando sua leitura atual…")
                : status === "error"
                  ? tx("Não foi possível consultar o Hardcover agora.")
                  : tx("Nenhum livro está marcado como Lendo no Hardcover.")}
          </p>
        {status === "error" && onRetry ? <UiButton className="icon-button" type="button" aria-label={tx("Atualizar leitura atual")} onClick={onRetry}><RefreshCw size={16} /></UiButton> : null}
        </div>
      )}
    </section>
  );
}

function CatalogBookCard({
  language,
  item,
  viewMode,
  selectable = false,
  selected = false,
  onEdit,
  onSend,
  onToggleSelect,
}: {
  language: UiLanguage;
  item: CatalogItem;
  viewMode: ViewMode;
  selectable?: boolean;
  selected?: boolean;
  onEdit(book: CalibreBook): void;
  onSend(book: CalibreBook): void;
  onToggleSelect(bookId: number): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  if (item.kind === "calibre") {
    const { book } = item;
    const isbn = Object.entries(book.identifiers).find(([key]) => key.toLocaleLowerCase("pt-BR").startsWith("isbn"))?.[1];
    const languages = book.languages ?? [];
    const tags = book.tags ?? [];
    const publicationYear = book.pubdate?.match(/^\d{4}/)?.[0];
    const imprint = [book.publisher, publicationYear].filter(Boolean).join(" · ");
    const hasKindleFormat = book.formats?.some((format) => supportedKindleFormats.includes(displayFormat(format) as KindleFormat)) ?? false;
    return (
      <article className={`catalog-book-card${viewMode === "shelf" ? " catalog-book-card-shelf" : ""}${selected ? " is-selected" : ""}`} role="listitem" aria-label={book.title}>
        {selectable ? (
          <label className="book-selection-control">
            <UiInput
              type="checkbox"
              checked={selected}
              aria-label={`${tx("Selecionar")} ${book.title}`}
              onChange={() => onToggleSelect(book.book_id)}
            />
            <span>{tx("Selecionar")}</span>
          </label>
        ) : null}
        <BookCover title={book.title} coverUrl={book.cover_url} number={String(book.book_id).padStart(3, "0")} language={language} />
        <div className="catalog-book-copy">
          {viewMode === "cards" ? <span className="book-id">CALIBRE · #{book.book_id}</span> : null}
          <h3 title={book.title}>{book.title || tx("Sem título")}</h3>
          <p className="book-authors" title={book.authors.join(", ")}>{book.authors.join(", ") || tx("Autor não informado")}</p>
          {viewMode === "cards" ? (
            <>
              {book.series ? <span className="book-series" title={book.series}>{book.series}{book.series_index ? ` · ${book.series_index}` : ""}</span> : null}
              <div className="book-card-metadata" aria-label={tx("Formatos e idiomas")}>
                {(book.formats ?? []).map((format, index) => <span className="metadata-chip metadata-chip-format" key={`${displayFormat(format)}-${index}`}>{displayFormat(format)}</span>)}
              {!hasKindleFormat ? <span className="metadata-chip metadata-chip-warning" title={tx("Adicione um EPUB, PDF, AZW3, MOBI, CBZ, FB2 ou TXT ao Calibre para enviar ao Kindle.")}>{tx("Sem formato Kindle")}</span> : null}
                {languages.slice(0, 1).map((language) => <span className="metadata-chip" key={language} title={languages.map(displayLanguage).join(", ")}>{displayLanguage(language)}</span>)}
                {languages.length > 1 ? <span className="metadata-chip metadata-chip-muted">+{languages.length - 1}</span> : null}
              </div>
              {imprint ? <span className="book-publisher" title={imprint}>{imprint}</span> : null}
              {tags.length > 0 ? (
                <div className="book-tag-list" aria-label={tx("Tags")}>
                  {tags.slice(0, 2).map((tag) => <span className="book-tag" key={tag} title={tag}>{tag}</span>)}
                  {tags.length > 2 ? <span className="book-tag book-tag-more" title={tags.slice(2).join(", ")}>+{tags.length - 2}</span> : null}
                </div>
              ) : null}
              {isbn ? <span className="book-isbn" title={`ISBN ${isbn}`}>ISBN {isbn}</span> : null}
            </>
          ) : null}
          <div className="book-card-actions">
            <UiButton className="book-action-button" type="button" onClick={() => onEdit(book)} aria-label={language === "en" ? `Edit metadata: ${book.title}` : `Editar metadados de ${book.title}`} title={tx("Editar metadados")}>
              <Pencil size={13} aria-hidden="true" />{viewMode === "cards" ? <span>{tx("Editar")}</span> : null}
            </UiButton>
            <UiButton className="book-action-button book-action-primary" type="button" onClick={() => onSend(book)} aria-label={`${tx("Enviar ao Kindle")}: ${book.title}`} title={hasKindleFormat ? tx("Enviar ao Kindle") : tx("Adicione um formato compatível no Calibre")} disabled={!hasKindleFormat}>
              <Send size={13} aria-hidden="true" />{viewMode === "cards" ? <span>{tx("Enviar")}</span> : null}
            </UiButton>
          </div>
        </div>
      </article>
    );
  }

  const { book } = item;
  const progress = progressFor(book);
  const read = getActiveRead(book);
  const startDate = formatReadingDate(read?.started_at, language);
  const finishDate = formatReadingDate(read?.finished_at, language);
  const statusLabel = tx(book.status_id === 1 ? "Quero ler" : book.status_id === 2 ? "Lendo" : book.status_id === 3 ? "Lido" : "Outro status");
  return (
    <article className={`catalog-book-card hardcover-book-card${viewMode === "shelf" ? " catalog-book-card-shelf" : ""}`} role="listitem" aria-label={book.title}>
      <BookCover title={book.title} coverUrl={book.cover_url} number={String(book.book_id).padStart(3, "0")} />
      <div className="catalog-book-copy">
        <span className="hardcover-card-label hardcover-status-label">{statusLabel}</span>
        <h3 title={book.title}>{book.title}</h3>
        <p className="book-authors" title={book.authors.join(", ")}>{book.authors.join(", ") || tx("Autor não informado")}</p>
        {viewMode === "cards" && book.status_id === 2 ? (
          <div className="hardcover-progress">
          <span>{read ? language === "en" ? `Page ${read.progress_pages.toLocaleString(getUiLocale(language))}` : `Página ${read.progress_pages.toLocaleString(getUiLocale(language))}` : tx("Progresso sem páginas")}{book.edition_pages ? ` ${language === "en" ? "of" : "de"} ${book.edition_pages.toLocaleString(getUiLocale(language))}` : ""}</span>
            <div className="progress-track"><span style={{ width: `${progress ?? 0}%` }} /></div>
          {startDate ? <small>{tx("Iniciado em")} {startDate}</small> : null}
          </div>
        ) : null}
      {viewMode === "cards" && book.status_id === 3 && finishDate ? <span className="hardcover-reading-date">{tx("Concluído em")} {finishDate}</span> : null}
      {viewMode === "cards" && book.status_id === 3 && book.rating !== null ? <span className="hardcover-rating" aria-label={`${tx("Avaliação")} ${book.rating} ${language === "en" ? "out of" : "de"} 5`}>★ {book.rating.toLocaleString(getUiLocale(language), { maximumFractionDigits: 1 })}</span> : null}
      {viewMode === "cards" && book.status_id !== 2 && book.edition_pages ? <span className="book-series">{language === "en" ? `${book.edition_pages.toLocaleString(getUiLocale(language))} pages` : `${book.edition_pages.toLocaleString(getUiLocale(language))} páginas`}</span> : null}
      <span className="hardcover-card-label hardcover-edition-label">{book.edition_format ?? tx("Edição Hardcover")}</span>
      </div>
    </article>
  );
}

function ImportDialog({
  language,
  batch,
  busy,
  error,
  onClose,
  onAdd,
}: {
  language: UiLanguage;
  batch: CalibreImportBatch;
  busy: boolean;
  error: string | null;
  onClose(): void;
  onAdd(mode: "new" | "attach", targetBookId?: number): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const file = batch.files[0];
  const hasDuplicates = file.candidates.length > 0;
  return (
    <div className="modal-backdrop">
      <section className="action-dialog import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-dialog-title">
        <div className="dialog-heading">
          <div><span className="eyebrow">{tx("ADICIONAR À BIBLIOTECA")}</span><h2 id="import-dialog-title">{tx("Confira os dados")}</h2></div>
          <UiButton className="icon-button" type="button" aria-label={tx("Fechar")} onClick={onClose} disabled={busy}><X size={17} /></UiButton>
        </div>
        <div className="import-book-summary">
          <span className="import-format-chip">{file.format}</span>
          <div>
            <span className="import-summary-label">{tx("METADADOS LIDOS DO ARQUIVO")}</span>
            <strong>{file.title}</strong>
            <span>{file.authors.join(", ") || tx("Autor não informado")}</span>
            <small className="import-filename" title={file.name}>{file.name}</small>
            {file.isbn ? <small>ISBN {file.isbn}</small> : null}
          </div>
        </div>
        <p className="import-metadata-note">{tx("O Calibre importa estes dados do arquivo. Depois de adicionar ou anexar, revise a ficha em Editar.")}</p>
        {hasDuplicates ? (
          <>
            <div className="dialog-inline-warning"><AlertTriangle size={17} aria-hidden="true" /><p>{tx("Este título pode já estar no Calibre. Confira os candidatos e escolha anexar o arquivo a um deles ou criar uma ficha separada.")}</p></div>
            <h3 className="duplicate-heading">{tx("Possíveis correspondências")} <span>{file.candidates.length}</span></h3>
            <div className="duplicate-list">
              {file.candidates.map((candidate) => {
                const alreadyHasFormat = candidate.formats.some((format) => displayFormat(format) === file.format.toUpperCase());
                return (
                  <article className="duplicate-candidate" key={candidate.book_id}>
                    <div><strong>{candidate.title}</strong><span>{candidate.authors.join(", ") || tx("Autor não informado")} · Calibre #{candidate.book_id}</span><small>{tx(candidate.reason)} · {candidate.formats.map(displayFormat).join(" · ") || tx("sem formato")}</small></div>
                    <UiButton className="button-secondary" type="button" disabled={busy || alreadyHasFormat} onClick={() => onAdd("attach", candidate.book_id)}>
                      {alreadyHasFormat ? tx("Formato já existe") : busy ? <><LoaderCircle className="spin" size={14} /> {tx("Fazendo backup…")}</> : `${tx("Anexar")} ${file.format}`}
                    </UiButton>
                  </article>
                );
              })}
            </div>
          </>
        ) : (
          <p className="dialog-body-copy">{tx("Não encontramos duplicatas exatas por ISBN ou por título e autor.")}</p>
        )}
        {error ? <div className="dialog-error" role="alert"><AlertTriangle size={16} />{tx(error)}</div> : null}
        <div className="dialog-footer">
          <UiButton className="button-quiet" type="button" onClick={onClose} disabled={busy}>{tx("Cancelar")}</UiButton>
          <UiButton className="button-primary" type="button" onClick={() => onAdd("new")} disabled={busy}>
            {busy ? <><LoaderCircle className="spin" size={15} /> {tx("Salvando após backup…")}</> : hasDuplicates ? tx("Criar livro separado") : tx("Adicionar à biblioteca")}
          </UiButton>
        </div>
        <p className="dialog-footnote"><ShieldCheck size={14} aria-hidden="true" /> {tx("O app faz backup antes da gravação, mantém o Calibre fechado e relê os dados após salvar.")}</p>
      </section>
    </div>
  );
}

function BulkTagsDialog({
  language,
  books,
  tagOptions,
  busy,
  error,
  onClose,
  onConfirm,
}: {
  language: UiLanguage;
  books: CalibreBook[];
  tagOptions: string[];
  busy: boolean;
  error: string | null;
  onClose(): void;
  onConfirm(addTags: string[], removeTags: string[]): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const [addTags, setAddTags] = useState<string[]>([]);
  const [removeTags, setRemoveTags] = useState<string[]>([]);
  const overlaps = addTags.some((tag) => removeTags.some(
    (removed) => removed.toLocaleLowerCase("pt-BR") === tag.toLocaleLowerCase("pt-BR"),
  ));

  return (
    <div className="modal-backdrop">
      <section className="action-dialog bulk-tags-dialog" role="dialog" aria-modal="true" aria-labelledby="bulk-tags-title">
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">{tx("CALIBRE · EDIÇÃO EM LOTE")}</span>
            <h2 id="bulk-tags-title">{language === "en" ? `Edit tags for ${books.length} books` : `Editar tags de ${books.length} livros`}</h2>
            <p>{tx("Confira a seleção. O backup terá a integridade conferida antes de salvar.")}</p>
          </div>
          <UiButton className="icon-button" type="button" aria-label={tx("Fechar")} onClick={onClose} disabled={busy}><X size={17} /></UiButton>
        </div>
        <div className="bulk-tags-preview" aria-label={tx("Livros selecionados")}>
          {books.map((book) => <span className="metadata-chip" title={book.title} key={book.book_id}>{book.title}</span>)}
        </div>
        <div className="bulk-tags-fields">
          <label className="metadata-field">
            <span>{tx("Adicionar tags")}</span>
            <MetadataTagEditor language={language} id="bulk-add-tags" values={addTags} options={tagOptions} onChange={setAddTags} />
          </label>
          <label className="metadata-field">
            <span>{tx("Remover tags")}</span>
            <MetadataTagEditor language={language} id="bulk-remove-tags" values={removeTags} options={tagOptions} onChange={setRemoveTags} />
          </label>
        </div>
        {overlaps ? <p className="dialog-error" role="alert">{tx("A mesma tag não pode ser adicionada e removida.")}</p> : null}
        {error ? <div className="dialog-error" role="alert"><AlertTriangle size={16} />{tx(error)}</div> : null}
        <p className="dialog-footnote"><ShieldCheck size={14} aria-hidden="true" /> {tx("O catálogo será relido depois da gravação. Se uma falha ocorrer no meio do lote, atualize a biblioteca antes de repetir.")}</p>
        <div className="dialog-footer">
          <UiButton className="button-quiet" type="button" onClick={onClose} disabled={busy}>{tx("Cancelar")}</UiButton>
          <UiButton
            className="button-primary"
            type="button"
            disabled={busy || books.length === 0 || overlaps || (addTags.length === 0 && removeTags.length === 0)}
            onClick={() => onConfirm(addTags, removeTags)}
          >
            {busy ? <><LoaderCircle className="spin" size={15} /> {tx("Backup e gravação…")}</> : tx("Fazer backup e aplicar tags")}
          </UiButton>
        </div>
      </section>
    </div>
  );
}

function MetadataTagEditor({
  language = "pt-BR",
  id,
  values,
  options,
  onChange,
}: {
  language?: UiLanguage;
  id: string;
  values: string[];
  options: string[];
  onChange(values: string[]): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const [draft, setDraft] = useState("");

  function addDraft() {
    const additions = draft.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
    const nextValues = [...values];
    for (const value of additions) {
      if (!nextValues.some((item) => item.toLocaleLowerCase("pt-BR") === value.toLocaleLowerCase("pt-BR"))) {
        nextValues.push(value);
      }
    }
    if (nextValues.length !== values.length) onChange(nextValues);
    setDraft("");
  }

  return (
    <div className="metadata-token-editor">
      {values.length > 0 ? (
        <div className="metadata-token-list" aria-label={tx("Tags selecionadas")}>
          {values.map((value) => (
            <span className="metadata-token" key={value}>
              {value}
              <UiButton
                type="button"
                aria-label={`${tx("Remover tag")} ${value}`}
                title={`${tx("Remover tag")} ${value}`}
                onClick={() => onChange(values.filter((item) => item !== value))}
              >
                <X size={12} aria-hidden="true" />
              </UiButton>
            </span>
          ))}
        </div>
      ) : null}
      <UiInput
        list={id}
        value={draft}
        maxLength={4096}
        placeholder={tx("Digite uma tag e pressione Enter")}
        aria-label={tx("Adicionar tag")}
        onChange={(event) => setDraft(event.currentTarget.value)}
        onBlur={addDraft}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === ",") {
            event.preventDefault();
            addDraft();
          } else if (event.key === "Backspace" && !draft && values.length > 0) {
            onChange(values.slice(0, -1));
          }
        }}
      />
      <datalist id={id}>{options.filter((option) => !values.includes(option)).map((option) => <option key={option} value={option} />)}</datalist>
    </div>
  );
}

function EditBookDialog({
  language,
  book,
  seriesOptions,
  tagOptions,
  onSelectCover,
  busy,
  error,
  onClose,
  onSave,
  onRemove,
}: {
  language: UiLanguage;
  book: CalibreBook;
  seriesOptions: string[];
  tagOptions: string[];
  onSelectCover?(): Promise<CalibreCoverSelection | null>;
  busy: boolean;
  error: string | null;
  onClose(): void;
  onSave(changes: CalibreEditChanges, coverSelectionId?: string): void;
  onRemove?: () => void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const [title, setTitle] = useState(book.title);
  const [authors, setAuthors] = useState(book.authors.join("\n"));
  const [series, setSeries] = useState(book.series ?? "");
  const [seriesIndex, setSeriesIndex] = useState(book.series_index?.toString() ?? "");
  const [publisher, setPublisher] = useState(book.publisher ?? "");
  const [pubdate, setPubdate] = useState(book.pubdate?.match(/^\d{4}(?:-\d{2}(?:-\d{2})?)?/)?.[0] ?? "");
  const [languages, setLanguages] = useState(book.languages ?? []);
  const [tags, setTags] = useState(book.tags ?? []);
  const [languageToAdd, setLanguageToAdd] = useState("");
  const [rating, setRating] = useState(book.rating === null || book.rating === undefined ? "" : String(book.rating / 2));
  const [identifiers, setIdentifiers] = useState(Object.entries(book.identifiers).map(([key, value]) => `${key}: ${value}`).join("\n"));
  const [customValues, setCustomValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(book.custom_values).map(([label, value]) => [
      label,
      value === null || value === undefined ? "" : typeof value === "string" ? value : String(value),
    ])),
  );
  const [coverSelection, setCoverSelection] = useState<CalibreCoverSelection | null>(null);
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverError, setCoverError] = useState<string | null>(null);
  const writableCustomColumns = (book.custom_column_definitions ?? []).filter(
    (column) =>
      column.editable &&
      !column.is_multiple &&
      column.datatype !== "composite" &&
      (column.datatype !== "enumeration" || column.enum_values.length > 0),
  );
  const extraFieldsCount = Object.keys(book.identifiers).length;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const identifierMap: Record<string, string> = {};
    for (const line of identifiers.split("\n").map((item) => item.trim()).filter(Boolean)) {
      const separator = line.indexOf(":");
      if (separator < 1) continue;
      identifierMap[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
    }
    const customChanges = Object.fromEntries(
      (book.custom_column_definitions ?? [])
        .filter((column) =>
          column.editable &&
          !column.is_multiple &&
          column.datatype !== "composite" &&
          (column.datatype !== "enumeration" || column.enum_values.length > 0),
        )
        .map((column) => {
          const raw = customValues[column.label] ?? "";
          if (column.datatype === "bool") return [column.label, raw === "" ? "" : raw === "true"];
          if (["int", "float", "rating"].includes(column.datatype)) {
            return [column.label, raw.trim() === "" ? "" : Number(raw.replace(",", "."))];
          }
          return [column.label, raw];
        }),
    );
    onSave({
      title: title.trim(),
      authors: authors.split("\n").map((author) => author.trim()).filter(Boolean),
      series: series.trim(),
      series_index: seriesIndex.trim() ? Number(seriesIndex.replace(",", ".")) : "",
      publisher: publisher.trim(),
      pubdate: pubdate.trim(),
      languages,
      tags,
      rating: rating.trim() ? Number(rating.replace(",", ".")) * 2 : "",
      identifiers: identifierMap,
      ...(Object.keys(customChanges).length > 0 ? { custom_values: customChanges } : {}),
    }, coverSelection?.selection_id);
  };

  async function chooseCover() {
    if (!onSelectCover || coverBusy || busy) return;
    setCoverBusy(true);
    setCoverError(null);
    try {
      setCoverSelection(await onSelectCover());
    } catch {
      setCoverError(tx("Não foi possível selecionar a imagem. Use JPG, PNG ou WebP de até 20 MB."));
    } finally {
      setCoverBusy(false);
    }
  }

  function updateCustomValue(label: string, value: string) {
    setCustomValues((current) => ({ ...current, [label]: value }));
  }

  return (
    <div className="modal-backdrop metadata-editor-backdrop">
      <section className="action-dialog edit-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-dialog-title">
        <div className="dialog-heading">
          <div><span className="eyebrow">{tx("CALIBRE · METADADOS")}</span><h2 id="edit-dialog-title">{tx("Editar livro")}</h2><p>{tx("As informações ficam no Calibre, sem alterar o Hardcover.")}</p></div>
        </div>
        <form className="metadata-form" onSubmit={submit}>
          <div className="metadata-editor-content">
            <aside className="metadata-cover-column" aria-label={tx("Capa e arquivos do livro")}>
              <div className="metadata-cover-frame">
                <BookCover title={book.title} coverUrl={book.cover_url} number={String(book.book_id).padStart(3, "0")} language={language} />
              </div>
              <div className="metadata-cover-controls">
                <span className="metadata-cover-caption">
                  {coverSelection ? `${tx("Nova capa:")} ${coverSelection.name}` : tx("Capa cadastrada no Calibre")}
                </span>
                {onSelectCover ? (
                  <UiButton
                    className="button-secondary metadata-cover-button"
                    type="button"
                    onClick={() => void chooseCover()}
                    disabled={busy || coverBusy}
                  >
                    {coverBusy ? <LoaderCircle className="spin" size={14} /> : <BookOpen size={14} aria-hidden="true" />}
                    {tx(coverSelection ? "Trocar imagem" : "Alterar capa")}
                  </UiButton>
                ) : null}
                {coverError ? <small className="metadata-field-hint" role="alert">{coverError}</small> : null}
              </div>
              <section className="metadata-format-panel" aria-label={tx("Arquivos disponíveis no Calibre")}>
                <span className="metadata-panel-label">{tx("ARQUIVOS DO LIVRO")}</span>
                <div className="metadata-format-list">
                  {(book.formats ?? []).length > 0
                    ? (book.formats ?? []).map((format, index) => <span className="metadata-chip metadata-chip-format" key={`${displayFormat(format)}-${index}`}>{displayFormat(format)}</span>)
                    : <span className="metadata-chip metadata-chip-muted">{tx("Sem arquivo")}</span>}
                </div>
              </section>
            </aside>
            <div className="metadata-editor-main">
              <div className="metadata-record-heading">
                <span className="eyebrow">{tx("FICHA DO CALIBRE")} #{book.book_id}</span>
                <p>{tx("Altere os dados bibliográficos usados na sua biblioteca local.")}</p>
              </div>
              <section className="metadata-section">
                <div className="metadata-section-title"><h3>{tx("Identificação")}</h3><span>{tx("Título e autoria")}</span></div>
                <div className="metadata-section-fields">
                  <label className="metadata-field metadata-field-wide"><span>{tx("Título")}</span><UiInput required maxLength={2048} value={title} onChange={(event) => setTitle(event.currentTarget.value)} /></label>
                  <label className="metadata-field metadata-field-wide"><span>{tx("Autor(es)")} <small>{tx("um por linha")}</small></span><UiTextarea required rows={2} value={authors} onChange={(event) => setAuthors(event.currentTarget.value)} /></label>
                </div>
              </section>
              <section className="metadata-section">
                <div className="metadata-section-title"><h3>{tx("Edição")}</h3><span>{tx("Série, publicação e idioma")}</span></div>
                <div className="metadata-section-fields">
                  <label className="metadata-field"><span>{tx("Série")}</span><UiInput list={`series-options-${book.book_id}`} maxLength={4096} value={series} onChange={(event) => setSeries(event.currentTarget.value)} /><datalist id={`series-options-${book.book_id}`}>{seriesOptions.map((option) => <option key={option} value={option} />)}</datalist></label>
                  <label className="metadata-field"><span>{tx("Número na série")}</span><UiInput inputMode="decimal" placeholder={language === "en" ? "e.g. 1 or 2.5" : "Ex.: 1 ou 2,5"} value={seriesIndex} onChange={(event) => setSeriesIndex(event.currentTarget.value)} /></label>
                  <label className="metadata-field"><span>{tx("Editora")}</span><UiInput maxLength={4096} value={publisher} onChange={(event) => setPublisher(event.currentTarget.value)} /></label>
                  <label className="metadata-field"><span>{tx("Data de publicação")}</span><UiInput placeholder={language === "en" ? "YYYY-MM-DD" : "AAAA-MM-DD"} value={pubdate} onChange={(event) => setPubdate(event.currentTarget.value)} /></label>
                  <div className="metadata-field metadata-field-wide">
                    <span>{tx("Idiomas")} <small>{tx("valores Calibre")}</small></span>
                    {languages.length > 0 ? (
                      <div className="metadata-token-list" aria-label={tx("Idiomas selecionados")}>
                        {languages.map((language) => (
                          <span className="metadata-token" key={language}>
                            {displayLanguage(language)}
                            <UiButton type="button" aria-label={`${tx("Remover idioma")} ${displayLanguage(language)}`} title={`${tx("Remover idioma")} ${displayLanguage(language)}`} onClick={() => setLanguages(languages.filter((item) => item !== language))}>
                              <X size={12} aria-hidden="true" />
                            </UiButton>
                          </span>
                        ))}
                      </div>
                    ) : null}
                    <UiSelect value={languageToAdd} onChange={(event) => {
                      const value = event.currentTarget.value;
                      if (value && !languages.includes(value)) setLanguages([...languages, value]);
                      setLanguageToAdd("");
                    }}>
                      <option value="">{tx("Adicionar idioma…")}</option>
                      {CALIBRE_LANGUAGE_OPTIONS.filter(([code]) => !languages.includes(code)).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                    </UiSelect>
                    <small className="metadata-field-hint">{tx("Português: PT-BR · Inglês: ENG")}</small>
                  </div>
                </div>
              </section>
              <section className="metadata-section">
                <div className="metadata-section-title"><h3>{tx("Organização")}</h3><span>{tx("Avaliação e tags")}</span></div>
                <div className="metadata-section-fields">
                  <label className="metadata-field"><span>{tx("Avaliação")} <small>{tx("0 a 5")}</small></span><UiInput type="number" min="0" max="5" step="0.5" placeholder={tx("Sem avaliação")} value={rating} onChange={(event) => setRating(event.currentTarget.value)} /></label>
                  <label className="metadata-field"><span>{tx("Tags")} <small>{tx("escolha uma existente ou crie")}</small></span><MetadataTagEditor language={language} id={`tag-options-${book.book_id}`} values={tags} options={tagOptions} onChange={setTags} /></label>
                </div>
              </section>
              <details className="metadata-extra">
                <summary><span>{tx("Identificadores")}</span><small>{extraFieldsCount > 0 ? `${extraFieldsCount} ${tx(extraFieldsCount === 1 ? "identificador" : "identificadores")}` : tx("Opcional")}</small></summary>
                <div className="metadata-section-fields metadata-extra-fields">
                  <label className="metadata-field metadata-field-wide"><span>{tx("ISBN e outros IDs")} <small>{tx("uma chave: valor por linha")}</small></span><UiTextarea rows={3} placeholder={'isbn: 978...\ngoodreads: 12345'} value={identifiers} onChange={(event) => setIdentifiers(event.currentTarget.value)} /></label>
                </div>
              </details>
              {writableCustomColumns.length > 0 ? (
                <details className="metadata-extra">
                  <summary><span>{tx("Campos personalizados")}</span><small>{writableCustomColumns.length} {tx("editáveis")}</small></summary>
                  <div className="metadata-section-fields metadata-extra-fields">
                    {writableCustomColumns.map((column) => (
                      <label className="metadata-field" key={column.label}>
                        <span>{column.name} <small>{column.datatype}</small></span>
                        {column.datatype === "bool" ? (
                          <UiSelect
                            value={customValues[column.label] ?? ""}
                            onChange={(event) => updateCustomValue(column.label, event.currentTarget.value)}
                          >
                            <option value="">{tx("Sem valor")}</option>
                            <option value="true">{tx("Sim")}</option>
                            <option value="false">{tx("Não")}</option>
                          </UiSelect>
                        ) : column.datatype === "enumeration" && column.enum_values.length > 0 ? (
                          <UiSelect
                            value={customValues[column.label] ?? ""}
                            onChange={(event) => updateCustomValue(column.label, event.currentTarget.value)}
                          >
                            <option value="">{tx("Sem valor")}</option>
                            {column.enum_values.map((value) => <option key={value} value={value}>{value}</option>)}
                          </UiSelect>
                        ) : column.datatype === "comments" ? (
                          <UiTextarea
                            rows={3}
                            value={customValues[column.label] ?? ""}
                            onChange={(event) => updateCustomValue(column.label, event.currentTarget.value)}
                          />
                        ) : (
                          <UiInput
                            type={["int", "float", "rating"].includes(column.datatype) ? "number" : "text"}
                            step={column.datatype === "int" ? "1" : "any"}
                            value={customValues[column.label] ?? ""}
                            onChange={(event) => updateCustomValue(column.label, event.currentTarget.value)}
                          />
                        )}
                      </label>
                    ))}
                  </div>
                </details>
              ) : null}
            </div>
          </div>
          <div className="metadata-form-footer">
            {error ? <div className="dialog-error" role="alert"><AlertTriangle size={16} />{tx(error)}</div> : null}
            <p className="dialog-footnote"><ShieldCheck size={14} aria-hidden="true" /> {tx("Backup antes de salvar. Campos que não aparecem aqui, como a descrição, permanecem intactos.")}</p>
            <div className="dialog-footer">
              {onRemove ? (
                <UiButton className="button-danger" type="button" onClick={onRemove} disabled={busy}>
                  <Trash2 size={15} aria-hidden="true" /> {tx("Mover para lixeira do Calibre")}
                </UiButton>
              ) : null}
              <UiButton className="button-quiet" type="button" onClick={onClose} disabled={busy}>{tx("Cancelar")}</UiButton>
              <UiButton className="button-primary" type="submit" disabled={busy}>
                {busy ? <><LoaderCircle className="spin" size={15} /> {tx("Fazendo backup e salvando…")}</> : tx("Salvar metadados")}
              </UiButton>
            </div>
          </div>
        </form>
      </section>
    </div>
  );
}

function RemoveBookDialog({
  language,
  book,
  busy,
  error,
  onClose,
  onConfirm,
}: {
  language: UiLanguage;
  book: CalibreBook;
  busy: boolean;
  error: string | null;
  onClose(): void;
  onConfirm(): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  return (
    <div className="modal-backdrop">
      <section className="action-dialog remove-dialog" role="dialog" aria-modal="true" aria-labelledby="remove-dialog-title">
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">{tx("CALIBRE · REMOÇÃO PROTEGIDA")}</span>
            <h2 id="remove-dialog-title">{tx("Mover livro para a lixeira do Calibre?")}</h2>
            <p>{book.title} · {book.authors.join(", ") || tx("Autor não informado")}</p>
          </div>
          <UiButton className="icon-button" type="button" aria-label={tx("Fechar")} onClick={onClose} disabled={busy}><X size={17} /></UiButton>
        </div>
        <div className="remove-confirmation-copy">
          <p>{tx("Será criado um backup Restic e a restauração será conferida antes da remoção.")}</p>
          <p>{tx("O Calibre moverá o livro para a própria lixeira. Esta ação não usa remoção permanente.")}</p>
        </div>
        <div className="dialog-inline-warning"><ShieldCheck size={17} /><p>{tx("O Calibre precisa estar fechado. Se o backup ou a verificação falhar, o livro não será removido.")}</p></div>
        {error ? <div className="dialog-error" role="alert"><AlertTriangle size={16} />{tx(error)}</div> : null}
        <div className="dialog-footer">
          <UiButton className="button-quiet" type="button" onClick={onClose} disabled={busy}>{tx("Cancelar")}</UiButton>
          <UiButton className="button-danger button-danger-solid" type="button" onClick={onConfirm} disabled={busy}>
            {busy ? <><LoaderCircle className="spin" size={15} /> {tx("Criando backup verificado…")}</> : <><Trash2 size={15} /> {tx("Fazer backup verificado e mover")}</>}
          </UiButton>
        </div>
      </section>
    </div>
  );
}

function SendBookDialog({
  language,
  book,
  busy,
  error,
  onClose,
  onSend,
}: {
  language: UiLanguage;
  book: CalibreBook;
  busy: boolean;
  error: string | null;
  onClose(): void;
  onSend(format: KindleFormat, filename: string): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const formats = supportedKindleFormats.filter((format) => book.formats?.some((item) => displayFormat(item) === format));
  const [format, setFormat] = useState<KindleFormat>(formats[0] ?? "EPUB");
  const [stem, setStem] = useState(() => cleanFileStem(book.title) || `Livro ${book.book_id}`);
  const isValidStem = stem.trim().length > 0 && stem.length <= 145 && stem.trim() === stem && !/[<>:"/\\|?*]/.test(stem) && !containsControlCharacters(stem) && !/[ .]$/.test(stem) && !/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i.test(stem);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!formats.includes(format) || !isValidStem) return;
    onSend(format, `${stem}.${format.toLocaleLowerCase("en-US")}`);
  };

  return (
    <div className="modal-backdrop">
      <section className="action-dialog send-dialog" role="dialog" aria-modal="true" aria-labelledby="send-dialog-title">
        <div className="dialog-heading">
          <div><span className="eyebrow">{tx("CALIBRE → KINDLE")}</span><h2 id="send-dialog-title">{tx("Enviar livro")}</h2><p>{book.title} · {book.authors.join(", ") || tx("Autor não informado")}</p></div>
          <UiButton className="icon-button" type="button" aria-label={tx("Fechar")} onClick={onClose} disabled={busy}><X size={17} /></UiButton>
        </div>
        {formats.length === 0 ? (
          <div className="dialog-inline-warning"><AlertTriangle size={17} /><p>{tx("Este livro não possui um formato compatível para KOReader/Kindle. Adicione um EPUB, PDF, AZW3, MOBI, CBZ, FB2 ou TXT à ficha Calibre.")}</p></div>
        ) : (
          <form className="send-form" onSubmit={submit}>
            <label className="metadata-field"><span>{tx("Formato do arquivo")}</span><UiSelect value={format} onChange={(event) => setFormat(event.currentTarget.value as KindleFormat)}>{formats.map((item) => <option key={item} value={item}>{item}</option>)}</UiSelect></label>
            <label className="metadata-field"><span>{tx("Nome no Kindle")}</span><div className="filename-input"><UiInput aria-label={tx("Nome no Kindle")} required maxLength={145} value={stem} onChange={(event) => setStem(event.currentTarget.value)} /><span>.{format.toLocaleLowerCase("en-US")}</span></div></label>
            <p className="metadata-hint">{tx("Destino: Kindle / documents / Books · Se já existir, você poderá mudar o nome; nada será sobrescrito.")}</p>
            {!isValidStem ? <p className="field-error">{tx("Use um nome válido, sem caracteres reservados nem ponto ou espaço no final.")}</p> : null}
            <div className="dialog-inline-warning"><ShieldCheck size={17} /><p>{tx("Antes de copiar, o app cria um backup completo e confere a restauração. Depois, relê o arquivo do Kindle e compara SHA-256. Pode levar alguns minutos.")}</p></div>
            {error ? <div className="dialog-error" role="alert"><AlertTriangle size={16} />{tx(error)}</div> : null}
            <div className="dialog-footer">
              <UiButton className="button-quiet" type="button" onClick={onClose} disabled={busy}>{tx("Cancelar")}</UiButton>
              <UiButton className="button-primary" type="submit" disabled={busy || formats.length === 0 || !isValidStem}>
                {busy ? <><LoaderCircle className="spin" size={15} /> {tx("Backup e envio em andamento…")}</> : <><Send size={15} /> {tx("Fazer backup verificado e enviar")}</>}
              </UiButton>
            </div>
          </form>
        )}
        {formats.length === 0 ? <div className="dialog-footer"><UiButton className="button-quiet" type="button" onClick={onClose}>{tx("Fechar")}</UiButton></div> : null}
      </section>
    </div>
  );
}
