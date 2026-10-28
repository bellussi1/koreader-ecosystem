export type AllowedOperation =
  | "system.status"
  | "calibre.list"
  | "calibre.preview-import"
  | "calibre.add-import"
  | "calibre.edit"
  | "calibre.bulk-tags"
  | "calibre.remove"
  | "kindle.status"
  | "kindle.send"
  | "hardcover.status"
  | "hardcover.list"
  | "hardcover.current-reading"
  | "hardcover.configure"
  | "hardcover.reconcile"
  | "hardcover.outbox.list"
  | "hardcover.outbox.resolve"
  | "koreader.readings.preview"
  | "koreader.readings.events"
  | "koreader.readings.confirm"
  | "koreader.readings.map"
  | "calibre.readings.preview"
  | "calibre.readings.apply"
  | "backup.list"
  | "backup.create"
  | "backup.automation.status"
  | "backup.automation.configure"
  | "backup.restore"
  | "backup.credentials"
  | "backup.configure-password"
  | "profile-recovery.preview"
  | "profile-recovery.apply"
  | "platform-csv.inspect"
  | "platform-csv.preview-mapped"
  | "platform-csv.page"
  | "platform-csv.list"
  | "platform-csv.confirm"
  | "platform-csv.export";
export type IpcChannel =
  | "app:status"
  | "calibre:list"
  | "calibre:select-import"
  | "calibre:select-cover"
  | "calibre:add-import"
  | "calibre:edit"
  | "calibre:bulk-tags"
  | "calibre:remove"
  | "kindle:status"
  | "kindle:send"
  | "hardcover:status"
  | "hardcover:list"
  | "hardcover:current-reading"
  | "hardcover:configure"
  | "hardcover:reconcile"
  | "hardcover:outbox-list"
  | "hardcover:outbox-resolve"
  | "koreader:readings-preview"
  | "koreader:readings-events"
  | "koreader:readings-confirm"
  | "koreader:readings-map"
  | "calibre:readings-preview"
  | "calibre:readings-apply"
  | "backup:list"
  | "backup:create"
  | "backup:automation-status"
  | "backup:automation-configure"
  | "backup:select-restore-target"
  | "backup:restore"
  | "backup:credentials"
  | "backup:configure-password"
  | "profile-recovery:select-source"
  | "profile-recovery:preview"
  | "profile-recovery:apply"
  | "platform-csv:select-import"
  | "platform-csv:preview-mapped"
  | "platform-csv:list"
  | "platform-csv:page"
  | "platform-csv:confirm"
  | "platform-csv:export"
  | "app:restart";

export interface ServiceStatus {
  status: "ready";
  protocol_version: 1;
}

export interface CalibreBook {
  book_id: number;
  uuid: string;
  title: string;
  authors: string[];
  identifiers: Record<string, string>;
  custom_values: Record<string, unknown>;
  custom_column_definitions?: CalibreCustomColumn[];
  cover_url?: string | null;
  series?: string | null;
  series_index?: number | null;
  publisher?: string | null;
  pubdate?: string | null;
  languages?: string[];
  tags?: string[];
  comments?: string;
  rating?: number | null;
  formats?: string[];
  size?: number;
}

export interface CalibreCustomColumn {
  label: string;
  name: string;
  datatype: string;
  editable: boolean;
  is_multiple: boolean;
  enum_values: string[];
}

export interface CalibreCoverSelection {
  selection_id: string;
  name: string;
}

export interface CalibreCatalog {
  books: CalibreBook[];
  total: number;
}

export interface CalibreImportCandidate {
  book_id: number;
  title: string;
  authors: string[];
  formats: string[];
  reason: string;
}

export interface CalibreImportFilePreview {
  name: string;
  title: string;
  authors: string[];
  isbn: string | null;
  format: string;
  candidates: CalibreImportCandidate[];
}

export interface CalibreImportBatch {
  preview_id: string;
  files: CalibreImportFilePreview[];
}

export interface KindleStatus {
  connected: boolean;
  reason_code: string;
}

export interface KindleTransfer {
  backup_snapshot_id: string;
  format: string;
  filename: string;
  size: number;
  sha256: string;
  verified: boolean;
  already_present: boolean;
}

export type CalibreEditChanges = Record<string, unknown>;

export type KindleFormat = "AZW" | "AZW3" | "CBZ" | "EPUB" | "FB2" | "MOBI" | "PDF" | "TXT";

export interface HardcoverRead {
  id: number;
  started_at: string | null;
  finished_at: string | null;
  progress_pages: number;
  edition_id: number | null;
}

export interface HardcoverBook {
  user_book_id: number;
  book_id: number;
  title: string;
  authors: string[];
  status_id: number;
  rating: number | null;
  edition_id: number | null;
  edition_pages: number | null;
  reads: HardcoverRead[];
  cover_url: string | null;
  edition_format: string | null;
}

export type HardcoverDataSource = "live" | "cache";

export interface HardcoverBookPage {
  books: HardcoverBook[];
  has_more: boolean;
  source: HardcoverDataSource;
  fetched_at: string;
}

export interface HardcoverCurrentReading {
  book: HardcoverBook | null;
  source: HardcoverDataSource;
  fetched_at: string;
}

export interface HardcoverStatus {
  configured: boolean;
  requests_used: number;
  request_limit: number;
  requests_remaining: number;
  window_seconds: number;
  pending_mutations: number;
  reconciliation_required: number;
  safe_to_retry: number;
  manual_review_required: number;
}

export interface HardcoverReconciliationResult {
  attempted: number;
  completed: number;
  failed: number;
  reconciliation_required: number;
  safe_to_retry: number;
  manual_review_required: number;
}

export type HardcoverReviewResolution = "confirmed_remote" | "closed_locally";
export type HardcoverOutboxOperation =
  | "start_read"
  | "finish_read"
  | "update_rating"
  | "add_journal_entry";

export interface HardcoverManualReviewItem {
  operation_id: string;
  operation: HardcoverOutboxOperation;
  status: "pending" | "reconcile" | "failed";
  reason_code: string | null;
  attempts: number;
  created_at: string;
  updated_at: string;
  book_title: string | null;
  authors: string[];
  book_reference: string | null;
  rating: number | null;
  page: number | null;
}

export interface HardcoverReviewResolutionResult {
  operation_id: string;
  resolution: HardcoverReviewResolution;
  reviewed_at: string;
}

export interface BackupSnapshot {
  id: string;
  time: string;
  scope: "calibre_only" | "calibre_and_kindle" | "unknown";
  includes_app_state?: boolean;
}

export interface BackupCreateResult {
  snapshot_id: string;
  file_count: number;
  total_bytes: number;
  calibre_file_count: number;
  kindle_file_count: number;
  restore_verified: boolean;
  application_state_file_count?: number;
}

export interface BackupRestoreTarget {
  target_id: string;
  folder_name: string;
  parent_name: string;
}

export interface BackupRestoreResult {
  snapshot_id: string;
  file_count: number;
  total_bytes: number;
  profile_source_id: string | null;
}

export interface ProfileRecoverySource {
  source_id: string;
  source_name: string;
}

export interface ProfileRecoveryPreview {
  preview_id: string;
  source_name: string;
  schema_version: number;
  row_counts: Record<string, number>;
  configured_calibre: boolean;
  configured_restic: boolean;
  configured_remote_backup: boolean;
}

export interface ProfileRecoveryResult {
  checkpoint_id: string;
  schema_version: number;
  requires_restart: true;
}

export interface BackupCredentialStatus {
  password_configured: boolean;
}

export interface BackupAutomationStatus {
  daily_backup_enabled: boolean;
  daily_backup_needs_reactivation?: boolean;
  daily_backup_time: string;
  backup_on_kindle_connect: boolean;
  last_backup: {
    status: "running" | "success" | "failed" | "cancelled";
    updated_at: string;
    last_success_at: string | null;
    reason_code: string | null;
  } | null;
}

export interface BackupAutomationPreferences {
  daily_backup_enabled: boolean;
  daily_backup_time: string;
  backup_on_kindle_connect: boolean;
}

export interface ReadingImportBatch {
  import_id: string;
  source: "koreader";
  source_digest: string;
  status: "preview" | "applied" | "failed";
  created_at: string;
  completed_at: string | null;
  record_count: number;
  reason_code: string | null;
}

export interface ReadingSidecarSummary {
  progress: number | null;
  status: string | null;
  rating: number | null;
  has_note: boolean;
  annotation_count: number;
  warnings: string[];
}

export interface UnmatchedReadingSidecarSummary extends ReadingSidecarSummary {
  sidecar_id: string;
  title: string | null;
  title_source: "metadata" | "folder" | null;
  authors: string | null;
}

export interface KoreaderReadingBookSummary {
  koreader_book_id: number;
  title: string;
  authors: string | null;
  series: string | null;
  language: string | null;
  pages: number | null;
  last_opened_at: string | null;
  statistics_progress: number | null;
  warnings: string[];
  sidecars: ReadingSidecarSummary[];
}

export interface EstimatedReadingSession {
  session_id: string;
  koreader_book_id: number;
  book_title: string;
  started_at: string;
  ended_at: string;
  page_event_count: number;
  duration_seconds: number;
  first_page: number;
  last_page: number;
  estimated: true;
}

export interface KoreaderReadingPreview {
  batch: ReadingImportBatch;
  source_digest: string;
  observed_at: string;
  summary: {
    book_count: number;
    page_event_count: number;
    estimated_session_count: number;
    sidecar_count: number;
    warnings: string[];
  };
  books: KoreaderReadingBookSummary[];
  unmatched_sidecars: UnmatchedReadingSidecarSummary[];
  estimated_sessions: EstimatedReadingSession[];
  sessions_truncated: boolean;
}

export interface StoredReadingPageEvent {
  event_id: string;
  identity_id: string;
  source_event_id: string;
  occurred_at: string;
  observed_at: string;
  source_digest: string;
  payload: Record<string, unknown>;
}

export interface ReadingEventsPage {
  events: StoredReadingPageEvent[];
  has_more: boolean;
}

export interface CalibreReadingProjectionItem {
  book_id: number | null;
  calibre_uuid: string | null;
  title: string | null;
  authors: string[];
  old_values: Record<string, unknown>;
  new_values: Record<string, unknown>;
  reason_code: string;
  warnings: string[];
}

export interface CalibreReadingProjectionIssue {
  koreader_book_id: number | null;
  title: string | null;
  reason_code: string;
}

export interface CalibreReadingProjection {
  projection_id: string;
  import_id: string;
  source_digest: string;
  items: CalibreReadingProjectionItem[];
  issues: CalibreReadingProjectionIssue[];
}

export interface CalibreReadingApplyResult {
  snapshot_id: string | null;
  applied_book_count: number;
  applied_field_count: number;
}

export type PlatformCsvSource = "goodreads" | "storygraph" | "maratona";
export type PlatformCsvDialogLanguage = "pt-BR" | "en";
export type PlatformCsvImportStatus = "preview" | "applied" | "failed";

export interface PlatformCsvImportBatch {
  import_id: string;
  source: PlatformCsvSource;
  source_digest: string;
  status: PlatformCsvImportStatus;
  created_at: string;
  completed_at: string | null;
  record_count: number;
  reason_code: string | null;
}

export interface PlatformCsvImportPreview {
  batch: PlatformCsvImportBatch;
  record_count: number;
  issue_count: number;
  warning_counts: Record<string, number>;
}

export const PLATFORM_CSV_MAPPING_FIELDS = [
  "external_id", "title", "author", "isbn_10", "isbn_13", "rating", "status",
  "shelves", "read_count", "date_added", "date_read", "review",
] as const;
const MAX_PLATFORM_CSV_HEADERS = 256;
export type PlatformCsvMappingField = (typeof PLATFORM_CSV_MAPPING_FIELDS)[number];
export type PlatformCsvColumnMapping = Record<PlatformCsvMappingField, string | null>;

export interface PlatformCsvColumnMappingDraft {
  selection_id: string;
  source: PlatformCsvSource;
  source_digest: string;
  headers: string[];
  suggested_mapping: PlatformCsvColumnMapping;
}

export interface PlatformCsvRecord {
  row_number: number;
  external_id: string | null;
  title: string;
  authors: string[];
  isbn_10: string | null;
  isbn_13: string | null;
  rating: number | null;
  exclusive_shelf: string | null;
  reading_status: string | null;
  shelves: string[];
  read_count: number | null;
  date_added: string | null;
  date_read: string | null;
  warnings: string[];
}

export interface PlatformCsvPreviewEntry {
  row_number: number;
  row_type: "record" | "issue";
  record: PlatformCsvRecord | null;
  reason_code: string | null;
}

export interface PlatformCsvPreviewPage {
  entries: PlatformCsvPreviewEntry[];
  has_more: boolean;
}

export interface PlatformCsvImportListPage {
  batches: PlatformCsvImportBatch[];
  has_more: boolean;
}

export interface PlatformCsvExportResult {
  file_name: string;
  bytes_written: number;
  rows_exported: number;
  formula_escaped_cells: number;
  reviews_included: boolean;
  unexported_fields: string[];
  warnings: string[];
}

export interface ReadingApi {
  app: {
    status(): Promise<ServiceStatus>;
  };
  calibre: {
    list(): Promise<CalibreCatalog>;
    selectImport(): Promise<CalibreImportBatch | null>;
    addImport(
      previewId: string,
      fileIndex: number,
      mode: "new" | "attach",
      targetBookId?: number,
    ): Promise<CalibreBook>;
    selectCover(bookId: number): Promise<CalibreCoverSelection | null>;
    edit(
      bookId: number,
      changes: CalibreEditChanges,
      coverSelectionId?: string,
    ): Promise<CalibreBook>;
    editTagsBatch(
      bookIds: number[],
      addTags: string[],
      removeTags: string[],
    ): Promise<CalibreBook[]>;
    remove(bookId: number): Promise<void>;
    readings: {
      preview(importId: string): Promise<CalibreReadingProjection>;
      apply(importId: string, projectionId: string): Promise<CalibreReadingApplyResult>;
    };
  };
  koreader: {
    readings: {
      preview(): Promise<KoreaderReadingPreview>;
      events(limit?: number, offset?: number): Promise<ReadingEventsPage>;
      confirm(importId: string, sourceDigest: string): Promise<ReadingImportBatch>;
      map(importId: string, koreaderBookId: number, calibreBookId: number): Promise<void>;
    };
  };
  kindle: {
    status(): Promise<KindleStatus>;
    send(bookId: number, format: KindleFormat, filename: string): Promise<KindleTransfer>;
  };
  hardcover: {
    status(): Promise<HardcoverStatus>;
    list(limit?: number, offset?: number): Promise<HardcoverBookPage>;
    currentReading(): Promise<HardcoverCurrentReading>;
    configure(token: string): Promise<HardcoverStatus>;
    reconcile(): Promise<HardcoverReconciliationResult>;
    manualReviews(): Promise<HardcoverManualReviewItem[]>;
    resolveReview(
      operationId: string,
      resolution: HardcoverReviewResolution,
    ): Promise<HardcoverReviewResolutionResult>;
  };
  backup: {
    list(): Promise<BackupSnapshot[]>;
    create(calibreOnly: boolean, verifyRestore: boolean): Promise<BackupCreateResult>;
    automationStatus(): Promise<BackupAutomationStatus>;
    configureAutomation(
      preferences: BackupAutomationPreferences,
    ): Promise<BackupAutomationStatus>;
    selectRestoreTarget(): Promise<BackupRestoreTarget | null>;
    restore(snapshotId: string, targetId: string): Promise<BackupRestoreResult>;
    credentials(): Promise<BackupCredentialStatus>;
    configurePassword(password: string): Promise<BackupCredentialStatus>;
  };
  profileRecovery: {
    selectSource(): Promise<ProfileRecoverySource | null>;
    preview(sourceId: string): Promise<ProfileRecoveryPreview>;
    apply(previewId: string): Promise<ProfileRecoveryResult>;
    restart(): Promise<void>;
  };
  platformCsv: {
    selectImport(source: PlatformCsvSource, language?: PlatformCsvDialogLanguage): Promise<PlatformCsvColumnMappingDraft | null>;
    previewMapped(selectionId: string, mapping: PlatformCsvColumnMapping): Promise<PlatformCsvImportPreview>;
    list(limit?: number, offset?: number, source?: PlatformCsvSource): Promise<PlatformCsvImportListPage>;
    page(importId: string, limit?: number, offset?: number): Promise<PlatformCsvPreviewPage>;
    confirm(importId: string, sourceDigest: string): Promise<PlatformCsvImportBatch>;
    export(importId: string, includeReviews: boolean, language?: PlatformCsvDialogLanguage): Promise<PlatformCsvExportResult | null>;
  };
}

export interface IpcTransport {
  invoke(channel: IpcChannel, payload?: Record<string, unknown>): Promise<unknown>;
}

export interface BackendClient {
  request<T>(
    operation: AllowedOperation,
    payload: Record<string, unknown>,
  ): Promise<T>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalidResponse(): Error {
  return new Error("invalid_response");
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return (
    isRecord(value) &&
    Object.values(value).every((item) => typeof item === "string")
  );
}

function isCalibreBook(value: unknown): value is CalibreBook {
  if (!isRecord(value)) return false;

  return (
    Number.isSafeInteger(value.book_id) &&
    Number(value.book_id) > 0 &&
    typeof value.uuid === "string" &&
    value.uuid.length > 0 &&
    typeof value.title === "string" &&
    Array.isArray(value.authors) &&
    value.authors.every((author) => typeof author === "string") &&
    isStringRecord(value.identifiers) &&
    isRecord(value.custom_values) &&
    (value.custom_column_definitions === undefined ||
      (Array.isArray(value.custom_column_definitions) &&
        value.custom_column_definitions.every(isCalibreCustomColumn))) &&
    (value.cover_url === undefined ||
      value.cover_url === null ||
      typeof value.cover_url === "string") &&
    (value.series === undefined || value.series === null || typeof value.series === "string") &&
    (value.series_index === undefined ||
      value.series_index === null ||
      (typeof value.series_index === "number" && Number.isFinite(value.series_index))) &&
    (value.publisher === undefined || value.publisher === null || typeof value.publisher === "string") &&
    (value.pubdate === undefined || value.pubdate === null || typeof value.pubdate === "string") &&
    (value.languages === undefined ||
      (Array.isArray(value.languages) && value.languages.every((item) => typeof item === "string"))) &&
    (value.tags === undefined ||
      (Array.isArray(value.tags) && value.tags.every((item) => typeof item === "string"))) &&
    (value.comments === undefined || typeof value.comments === "string") &&
    (value.rating === undefined ||
      value.rating === null ||
      (typeof value.rating === "number" && Number.isFinite(value.rating))) &&
    (value.formats === undefined ||
      (Array.isArray(value.formats) && value.formats.every((item) => typeof item === "string"))) &&
    (value.size === undefined ||
      (Number.isSafeInteger(value.size) && Number(value.size) >= 0))
  );
}

function isCalibreCustomColumn(value: unknown): value is CalibreCustomColumn {
  return (
    isRecord(value) &&
    typeof value.label === "string" &&
    typeof value.name === "string" &&
    typeof value.datatype === "string" &&
    typeof value.editable === "boolean" &&
    typeof value.is_multiple === "boolean" &&
    Array.isArray(value.enum_values) &&
    value.enum_values.every((item) => typeof item === "string")
  );
}

function asServiceStatus(value: unknown): ServiceStatus {
  if (
    !isRecord(value) ||
    value.status !== "ready" ||
    value.protocol_version !== 1
  ) {
    throw invalidResponse();
  }
  return { status: "ready", protocol_version: 1 };
}

function asCalibreCatalog(value: unknown): CalibreCatalog {
  if (
    !isRecord(value) ||
    !Array.isArray(value.books) ||
    !Number.isSafeInteger(value.total) ||
    value.total !== value.books.length ||
    !value.books.every(isCalibreBook)
  ) {
    throw invalidResponse();
  }
  return { books: value.books, total: value.total as number };
}

function asCalibreBookResult(value: unknown): CalibreBook {
  if (!isRecord(value) || !isCalibreBook(value.book)) throw invalidResponse();
  return value.book;
}

function asCalibreRemovalResult(value: unknown): void {
  if (!isRecord(value) || value.removed !== true) throw invalidResponse();
}

function isImportCandidate(value: unknown): value is CalibreImportCandidate {
  return (
    isRecord(value) &&
    Number.isSafeInteger(value.book_id) && Number(value.book_id) > 0 &&
    typeof value.title === "string" &&
    Array.isArray(value.authors) && value.authors.every((item) => typeof item === "string") &&
    Array.isArray(value.formats) && value.formats.every((item) => typeof item === "string") &&
    typeof value.reason === "string"
  );
}

function isImportFilePreview(value: unknown): value is CalibreImportFilePreview {
  return (
    isRecord(value) && typeof value.name === "string" &&
    typeof value.title === "string" &&
    Array.isArray(value.authors) && value.authors.every((item) => typeof item === "string") &&
    (value.isbn === null || typeof value.isbn === "string") &&
    typeof value.format === "string" &&
    Array.isArray(value.candidates) && value.candidates.every(isImportCandidate)
  );
}

function asCalibreImportBatch(value: unknown): CalibreImportBatch {
  if (
    !isRecord(value) || typeof value.preview_id !== "string" || !value.preview_id ||
    !Array.isArray(value.files) || value.files.length !== 1 || !value.files.every(isImportFilePreview)
  ) {
    throw invalidResponse();
  }
  return { preview_id: value.preview_id, files: value.files };
}

function asKindleStatus(value: unknown): KindleStatus {
  if (!isRecord(value) || typeof value.connected !== "boolean" || typeof value.reason_code !== "string") {
    throw invalidResponse();
  }
  return { connected: value.connected, reason_code: value.reason_code };
}

function asKindleTransfer(value: unknown): KindleTransfer {
  if (
    !isRecord(value) || typeof value.backup_snapshot_id !== "string" ||
    typeof value.format !== "string" || typeof value.filename !== "string" ||
    !Number.isSafeInteger(value.size) || Number(value.size) < 0 ||
    typeof value.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.sha256) ||
    value.verified !== true || typeof value.already_present !== "boolean"
  ) {
    throw invalidResponse();
  }
  return value as unknown as KindleTransfer;
}

function isHardcoverRead(value: unknown): value is HardcoverRead {
  if (!isRecord(value)) return false;
  return (
    Number.isSafeInteger(value.id) &&
    Number(value.id) > 0 &&
    (value.started_at === null || typeof value.started_at === "string") &&
    (value.finished_at === null || typeof value.finished_at === "string") &&
    Number.isSafeInteger(value.progress_pages) &&
    Number(value.progress_pages) >= 0 &&
    (value.edition_id === null ||
      (Number.isSafeInteger(value.edition_id) && Number(value.edition_id) > 0))
  );
}

function isHardcoverBook(value: unknown): value is HardcoverBook {
  if (!isRecord(value)) return false;
  return (
    Number.isSafeInteger(value.user_book_id) &&
    Number(value.user_book_id) > 0 &&
    Number.isSafeInteger(value.book_id) &&
    Number(value.book_id) > 0 &&
    typeof value.title === "string" &&
    Array.isArray(value.authors) &&
    value.authors.every((author) => typeof author === "string") &&
    Number.isSafeInteger(value.status_id) &&
    (value.rating === null ||
      (typeof value.rating === "number" && Number.isFinite(value.rating))) &&
    (value.edition_id === null ||
      (Number.isSafeInteger(value.edition_id) && Number(value.edition_id) > 0)) &&
    (value.edition_pages === null ||
      (Number.isSafeInteger(value.edition_pages) && Number(value.edition_pages) > 0)) &&
    Array.isArray(value.reads) &&
    value.reads.every(isHardcoverRead) &&
    (value.cover_url === null || typeof value.cover_url === "string") &&
    (value.edition_format === null || typeof value.edition_format === "string")
  );
}

function asHardcoverStatus(value: unknown): HardcoverStatus {
  if (
    !isRecord(value) ||
    typeof value.configured !== "boolean" ||
    typeof value.requests_used !== "number" ||
    !Number.isSafeInteger(value.requests_used) ||
    value.requests_used < 0 ||
    typeof value.request_limit !== "number" ||
    !Number.isSafeInteger(value.request_limit) ||
    value.request_limit < 1 ||
    typeof value.requests_remaining !== "number" ||
    !Number.isSafeInteger(value.requests_remaining) ||
    value.requests_remaining < 0 ||
    typeof value.window_seconds !== "number" ||
    !Number.isSafeInteger(value.window_seconds) ||
    value.window_seconds < 1 ||
    typeof value.pending_mutations !== "number" ||
    !Number.isSafeInteger(value.pending_mutations) ||
    value.pending_mutations < 0 ||
    typeof value.reconciliation_required !== "number" ||
    !Number.isSafeInteger(value.reconciliation_required) ||
    value.reconciliation_required < 0 ||
    value.reconciliation_required > value.pending_mutations ||
    typeof value.safe_to_retry !== "number" ||
    !Number.isSafeInteger(value.safe_to_retry) ||
    value.safe_to_retry < 0 ||
    typeof value.manual_review_required !== "number" ||
    !Number.isSafeInteger(value.manual_review_required) ||
    value.manual_review_required < 0 ||
    value.safe_to_retry + value.manual_review_required !== value.pending_mutations
  ) throw invalidResponse();
  return {
    configured: value.configured,
    requests_used: Number(value.requests_used),
    request_limit: Number(value.request_limit),
    requests_remaining: Number(value.requests_remaining),
    window_seconds: Number(value.window_seconds),
    pending_mutations: Number(value.pending_mutations),
    reconciliation_required: Number(value.reconciliation_required),
    safe_to_retry: Number(value.safe_to_retry),
    manual_review_required: Number(value.manual_review_required),
  };
}

function asHardcoverReconciliationResult(value: unknown): HardcoverReconciliationResult {
  if (
    !isRecord(value) ||
    !Number.isSafeInteger(value.attempted) || Number(value.attempted) < 0 ||
    !Number.isSafeInteger(value.completed) || Number(value.completed) < 0 ||
    !Number.isSafeInteger(value.failed) || Number(value.failed) < 0 ||
    Number(value.completed) + Number(value.failed) > Number(value.attempted) ||
    !Number.isSafeInteger(value.reconciliation_required) ||
    Number(value.reconciliation_required) < 0 ||
    !Number.isSafeInteger(value.safe_to_retry) || Number(value.safe_to_retry) < 0 ||
    !Number.isSafeInteger(value.manual_review_required) ||
    Number(value.manual_review_required) < 0
  ) throw invalidResponse();
  return {
    attempted: Number(value.attempted),
    completed: Number(value.completed),
    failed: Number(value.failed),
    reconciliation_required: Number(value.reconciliation_required),
    safe_to_retry: Number(value.safe_to_retry),
    manual_review_required: Number(value.manual_review_required),
  };
}

function asHardcoverManualReviewItem(value: unknown): HardcoverManualReviewItem {
  const allowedKeys = [
    "operation_id", "operation", "status", "reason_code", "attempts", "created_at",
    "updated_at", "book_title", "authors", "book_reference", "rating", "page",
  ];
  if (
    !isRecord(value) ||
    Object.keys(value).length !== allowedKeys.length ||
    Object.keys(value).some((key) => !allowedKeys.includes(key)) ||
    typeof value.operation_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.operation_id) ||
    !["start_read", "finish_read", "update_rating", "add_journal_entry"].includes(String(value.operation)) ||
    !["pending", "reconcile", "failed"].includes(String(value.status)) ||
    (value.reason_code !== null && typeof value.reason_code !== "string") ||
    !Number.isSafeInteger(value.attempts) || Number(value.attempts) < 0 ||
    typeof value.created_at !== "string" || Number.isNaN(Date.parse(value.created_at)) ||
    typeof value.updated_at !== "string" || Number.isNaN(Date.parse(value.updated_at)) ||
    (value.book_title !== null && typeof value.book_title !== "string") ||
    !Array.isArray(value.authors) || !value.authors.every((author) => typeof author === "string") ||
    (value.book_reference !== null && typeof value.book_reference !== "string") ||
    (value.rating !== null && (typeof value.rating !== "number" || !Number.isFinite(value.rating) || value.rating < 0 || value.rating > 5)) ||
    (value.page !== null && (!Number.isSafeInteger(value.page) || Number(value.page) < 0))
  ) throw invalidResponse();
  return {
    operation_id: value.operation_id,
    operation: value.operation as HardcoverOutboxOperation,
    status: value.status as HardcoverManualReviewItem["status"],
    reason_code: value.reason_code as string | null,
    attempts: Number(value.attempts),
    created_at: value.created_at,
    updated_at: value.updated_at,
    book_title: value.book_title as string | null,
    authors: [...value.authors] as string[],
    book_reference: value.book_reference as string | null,
    rating: value.rating as number | null,
    page: value.page as number | null,
  };
}

function asHardcoverReviewResolutionResult(value: unknown): HardcoverReviewResolutionResult {
  if (
    !isRecord(value) ||
    Object.keys(value).length !== 3 ||
    Object.keys(value).some((key) => !["operation_id", "resolution", "reviewed_at"].includes(key)) ||
    typeof value.operation_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.operation_id) ||
    (value.resolution !== "confirmed_remote" && value.resolution !== "closed_locally") ||
    typeof value.reviewed_at !== "string" || Number.isNaN(Date.parse(value.reviewed_at))
  ) throw invalidResponse();
  return {
    operation_id: value.operation_id,
    resolution: value.resolution,
    reviewed_at: value.reviewed_at,
  };
}

function asHardcoverPage(value: unknown): HardcoverBookPage {
  if (
    !isRecord(value) ||
    !Array.isArray(value.books) ||
    !value.books.every(isHardcoverBook) ||
    typeof value.has_more !== "boolean" ||
    (value.source !== "live" && value.source !== "cache") ||
    typeof value.fetched_at !== "string" ||
    Number.isNaN(Date.parse(value.fetched_at))
  ) {
    throw invalidResponse();
  }
  return {
    books: value.books,
    has_more: value.has_more,
    source: value.source,
    fetched_at: value.fetched_at,
  };
}

function asHardcoverCurrentReading(value: unknown): HardcoverCurrentReading {
  if (
    !isRecord(value) ||
    (value.book !== null && !isHardcoverBook(value.book)) ||
    (value.source !== "live" && value.source !== "cache") ||
    typeof value.fetched_at !== "string" ||
    Number.isNaN(Date.parse(value.fetched_at))
  ) {
    throw invalidResponse();
  }
  return {
    book: value.book as HardcoverBook | null,
    source: value.source,
    fetched_at: value.fetched_at,
  };
}

function asBackupSnapshot(value: unknown): value is BackupSnapshot {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    /^[0-9a-f]{64}$/.test(value.id) &&
    typeof value.time === "string" &&
    (value.scope === "calibre_only" ||
      value.scope === "calibre_and_kindle" ||
      value.scope === "unknown") &&
    (value.includes_app_state === undefined || typeof value.includes_app_state === "boolean")
  );
}

function asBackupCreateResult(value: unknown): BackupCreateResult {
  if (
    !isRecord(value) ||
    typeof value.snapshot_id !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.snapshot_id) ||
    !Number.isSafeInteger(value.file_count) ||
    Number(value.file_count) < 0 ||
    !Number.isSafeInteger(value.total_bytes) ||
    Number(value.total_bytes) < 0 ||
    !Number.isSafeInteger(value.calibre_file_count) ||
    Number(value.calibre_file_count) < 0 ||
    !Number.isSafeInteger(value.kindle_file_count) ||
    Number(value.kindle_file_count) < 0 ||
    (value.application_state_file_count !== undefined &&
      (!Number.isSafeInteger(value.application_state_file_count) ||
        Number(value.application_state_file_count) < 0)) ||
    typeof value.restore_verified !== "boolean"
  ) {
    throw invalidResponse();
  }
  return value as unknown as BackupCreateResult;
}

function asBackupRestoreTarget(value: unknown): BackupRestoreTarget {
  if (
    !isRecord(value) ||
    typeof value.target_id !== "string" ||
    !value.target_id ||
    typeof value.folder_name !== "string" ||
    !value.folder_name ||
    typeof value.parent_name !== "string" ||
    !value.parent_name
  ) {
    throw invalidResponse();
  }
  return value as unknown as BackupRestoreTarget;
}

function asBackupRestoreResult(value: unknown): BackupRestoreResult {
  if (
    !isRecord(value) ||
    typeof value.snapshot_id !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.snapshot_id) ||
    !Number.isSafeInteger(value.file_count) ||
    Number(value.file_count) < 0 ||
    !Number.isSafeInteger(value.total_bytes) ||
    Number(value.total_bytes) < 0 ||
    (value.profile_source_id !== null && typeof value.profile_source_id !== "string")
  ) {
    throw invalidResponse();
  }
  return value as unknown as BackupRestoreResult;
}

function asProfileRecoveryPreview(value: unknown): ProfileRecoveryPreview {
  if (
    !isRecord(value) ||
    typeof value.preview_id !== "string" ||
    !/^[0-9a-f-]{36}$/.test(value.preview_id) ||
    typeof value.source_name !== "string" ||
    !Number.isSafeInteger(value.schema_version) ||
    Number(value.schema_version) < 1 ||
    Number(value.schema_version) > 5 ||
    !isRecord(value.row_counts) ||
    !Object.values(value.row_counts).every((count) =>
      typeof count === "number" && Number.isSafeInteger(count) && count >= 0
    ) ||
    typeof value.configured_calibre !== "boolean" ||
    typeof value.configured_restic !== "boolean" ||
    typeof value.configured_remote_backup !== "boolean"
  ) {
    throw invalidResponse();
  }
  return value as unknown as ProfileRecoveryPreview;
}

function asProfileRecoveryResult(value: unknown): ProfileRecoveryResult {
  if (
    !isRecord(value) ||
    typeof value.checkpoint_id !== "string" ||
    !/^[0-9a-f]{32}$/.test(value.checkpoint_id) ||
    !Number.isSafeInteger(value.schema_version) ||
    Number(value.schema_version) < 1 ||
    Number(value.schema_version) > 5 ||
    value.requires_restart !== true
  ) {
    throw invalidResponse();
  }
  return value as unknown as ProfileRecoveryResult;
}

function asProfileRecoverySource(value: unknown): ProfileRecoverySource {
  if (
    !isRecord(value) ||
    typeof value.source_id !== "string" ||
    !/^[0-9a-f-]{36}$/.test(value.source_id) ||
    typeof value.source_name !== "string"
  ) {
    throw invalidResponse();
  }
  return value as unknown as ProfileRecoverySource;
}

function asBackupCredentialStatus(value: unknown): BackupCredentialStatus {
  if (!isRecord(value) || typeof value.password_configured !== "boolean") {
    throw invalidResponse();
  }
  return { password_configured: value.password_configured };
}

function asBackupAutomationStatus(value: unknown): BackupAutomationStatus {
  if (
    !isRecord(value) ||
    typeof value.daily_backup_enabled !== "boolean" ||
    typeof value.daily_backup_time !== "string" ||
    !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value.daily_backup_time) ||
    typeof value.backup_on_kindle_connect !== "boolean"
    || (value.daily_backup_needs_reactivation !== undefined && typeof value.daily_backup_needs_reactivation !== "boolean")
  ) {
    throw invalidResponse();
  }

  let lastBackup: BackupAutomationStatus["last_backup"] = null;
  if (value.last_backup !== null) {
    const record = value.last_backup;
    if (
      !isRecord(record) ||
      (record.status !== "running" && record.status !== "success" &&
        record.status !== "failed" && record.status !== "cancelled") ||
      typeof record.updated_at !== "string" || Number.isNaN(Date.parse(record.updated_at)) ||
      (record.last_success_at !== null &&
        (typeof record.last_success_at !== "string" || Number.isNaN(Date.parse(record.last_success_at)))) ||
      (record.reason_code !== null &&
        (typeof record.reason_code !== "string" || !/^[a-z0-9_]{1,64}$/.test(record.reason_code)))
    ) {
      throw invalidResponse();
    }
    lastBackup = {
      status: record.status,
      updated_at: record.updated_at,
      last_success_at: record.last_success_at,
      reason_code: record.reason_code,
    };
  }

  return {
    daily_backup_enabled: value.daily_backup_enabled,
    daily_backup_needs_reactivation: value.daily_backup_needs_reactivation === true,
    daily_backup_time: value.daily_backup_time,
    backup_on_kindle_connect: value.backup_on_kindle_connect,
    last_backup: lastBackup,
  };
}

function isDigest(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function isOptionalString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isCount(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isReadingImportBatch(value: unknown): value is ReadingImportBatch {
  return (
    isRecord(value) &&
    isDigest(value.import_id) &&
    value.source === "koreader" &&
    isDigest(value.source_digest) &&
    (value.status === "preview" || value.status === "applied" || value.status === "failed") &&
    typeof value.created_at === "string" &&
    isOptionalString(value.completed_at) &&
    isCount(value.record_count) &&
    isOptionalString(value.reason_code)
  );
}

function isReadingSidecarSummary(value: unknown): value is ReadingSidecarSummary {
  if (!isRecord(value)) return false;
  return (
    (value.progress === null ||
      (typeof value.progress === "number" && Number.isFinite(value.progress) &&
        value.progress >= 0 && value.progress <= 1)) &&
    isOptionalString(value.status) &&
    (value.rating === null ||
      (typeof value.rating === "number" && Number.isFinite(value.rating) &&
        value.rating >= 0 && value.rating <= 5)) &&
    typeof value.has_note === "boolean" &&
    isCount(value.annotation_count) &&
    isStringArray(value.warnings)
  );
}

function isKoreaderReadingBook(value: unknown): value is KoreaderReadingBookSummary {
  return (
    isRecord(value) &&
    Number.isSafeInteger(value.koreader_book_id) &&
    Number(value.koreader_book_id) > 0 &&
    typeof value.title === "string" &&
    isOptionalString(value.authors) &&
    isOptionalString(value.series) &&
    isOptionalString(value.language) &&
    (value.pages === null || (Number.isSafeInteger(value.pages) && Number(value.pages) >= 0)) &&
    isOptionalString(value.last_opened_at) &&
    (value.statistics_progress === null ||
      (typeof value.statistics_progress === "number" && Number.isFinite(value.statistics_progress) &&
        value.statistics_progress >= 0 && value.statistics_progress <= 1)) &&
    isStringArray(value.warnings) &&
    Array.isArray(value.sidecars) && value.sidecars.every(isReadingSidecarSummary)
  );
}

function isUnmatchedReadingSidecar(value: unknown): value is UnmatchedReadingSidecarSummary {
  return (
    isRecord(value) &&
    isDigest(value.sidecar_id) &&
    isOptionalString(value.title) &&
    (value.title_source === null || value.title_source === "metadata" || value.title_source === "folder") &&
    isOptionalString(value.authors) &&
    isReadingSidecarSummary(value)
  );
}

function isEstimatedReadingSession(value: unknown): value is EstimatedReadingSession {
  return (
    isRecord(value) &&
    isDigest(value.session_id) &&
    Number.isSafeInteger(value.koreader_book_id) &&
    Number(value.koreader_book_id) > 0 &&
    typeof value.book_title === "string" &&
    typeof value.started_at === "string" &&
    typeof value.ended_at === "string" &&
    isCount(value.page_event_count) &&
    isCount(value.duration_seconds) &&
    Number.isSafeInteger(value.first_page) &&
    Number(value.first_page) > 0 &&
    Number.isSafeInteger(value.last_page) &&
    Number(value.last_page) > 0 &&
    value.estimated === true
  );
}

function asKoreaderReadingPreview(value: unknown): KoreaderReadingPreview {
  if (
    !isRecord(value) ||
    !isReadingImportBatch(value.batch) ||
    !isDigest(value.source_digest) ||
    typeof value.observed_at !== "string" ||
    !isRecord(value.summary) ||
    !isCount(value.summary.book_count) ||
    !isCount(value.summary.page_event_count) ||
    !isCount(value.summary.estimated_session_count) ||
    !isCount(value.summary.sidecar_count) ||
    !isStringArray(value.summary.warnings) ||
    !Array.isArray(value.books) ||
    !value.books.every(isKoreaderReadingBook) ||
    !Array.isArray(value.unmatched_sidecars) ||
    !value.unmatched_sidecars.every(isUnmatchedReadingSidecar) ||
    !Array.isArray(value.estimated_sessions) ||
    !value.estimated_sessions.every(isEstimatedReadingSession) ||
    typeof value.sessions_truncated !== "boolean"
  ) {
    throw invalidResponse();
  }
  return value as unknown as KoreaderReadingPreview;
}

function asReadingEventsPage(value: unknown): ReadingEventsPage {
  if (
    !isRecord(value) ||
    !Array.isArray(value.events) ||
    !value.events.every((event) =>
      isRecord(event) &&
      isDigest(event.event_id) &&
      isDigest(event.identity_id) &&
      typeof event.source_event_id === "string" &&
      typeof event.occurred_at === "string" &&
      typeof event.observed_at === "string" &&
      isDigest(event.source_digest) &&
      isRecord(event.payload)
    ) ||
    typeof value.has_more !== "boolean"
  ) {
    throw invalidResponse();
  }
  return value as unknown as ReadingEventsPage;
}

function asCalibreReadingProjection(value: unknown): CalibreReadingProjection {
  if (
    !isRecord(value) ||
    !isDigest(value.projection_id) ||
    !isDigest(value.import_id) ||
    !isDigest(value.source_digest) ||
    !Array.isArray(value.items) ||
    !value.items.every((item) =>
      isRecord(item) &&
      (item.book_id === null || (Number.isSafeInteger(item.book_id) && Number(item.book_id) > 0)) &&
      isOptionalString(item.calibre_uuid) &&
      isOptionalString(item.title) &&
      isStringArray(item.authors) &&
      isRecord(item.old_values) &&
      isRecord(item.new_values) &&
      typeof item.reason_code === "string" &&
      isStringArray(item.warnings)
    ) ||
    !Array.isArray(value.issues) ||
    !value.issues.every((issue) =>
      isRecord(issue) &&
      (issue.koreader_book_id === null ||
        (Number.isSafeInteger(issue.koreader_book_id) && Number(issue.koreader_book_id) > 0)) &&
      isOptionalString(issue.title) &&
      typeof issue.reason_code === "string"
    )
  ) {
    throw invalidResponse();
  }
  return value as unknown as CalibreReadingProjection;
}

function asCalibreReadingApplyResult(value: unknown): CalibreReadingApplyResult {
  if (
    !isRecord(value) ||
    !(value.snapshot_id === null || isDigest(value.snapshot_id)) ||
    !isCount(value.applied_book_count) ||
    !isCount(value.applied_field_count)
  ) {
    throw invalidResponse();
  }
  return value as unknown as CalibreReadingApplyResult;
}

function isPlatformCsvSource(value: unknown): value is PlatformCsvSource {
  return value === "goodreads" || value === "storygraph" || value === "maratona";
}

function isPlatformCsvImportBatch(value: unknown): value is PlatformCsvImportBatch {
  return (
    isRecord(value) &&
    isDigest(value.import_id) &&
    isPlatformCsvSource(value.source) &&
    isDigest(value.source_digest) &&
    (value.status === "preview" || value.status === "applied" || value.status === "failed") &&
    typeof value.created_at === "string" &&
    isOptionalString(value.completed_at) &&
    isCount(value.record_count) &&
    isOptionalString(value.reason_code)
  );
}

function isOptionalCount(value: unknown): value is number | null {
  return value === null || isCount(value);
}

function isOptionalRating(value: unknown): value is number | null {
  return value === null ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 5);
}

function isPlatformCsvRecord(value: unknown): value is PlatformCsvRecord {
  if (!isRecord(value)) return false;
  const allowedFields = new Set([
    "row_number", "external_id", "title", "authors", "isbn_10", "isbn_13", "rating",
    "exclusive_shelf", "reading_status", "shelves", "read_count", "date_added", "date_read",
    "warnings",
  ]);
  return (
    Object.keys(value).every((key) => allowedFields.has(key)) &&
    Number.isSafeInteger(value.row_number) && Number(value.row_number) >= 2 &&
    isOptionalString(value.external_id) && typeof value.title === "string" &&
    isStringArray(value.authors) && value.authors.length > 0 &&
    isOptionalString(value.isbn_10) && isOptionalString(value.isbn_13) &&
    isOptionalRating(value.rating) && isOptionalString(value.exclusive_shelf) &&
    isOptionalString(value.reading_status) && isStringArray(value.shelves) &&
    isOptionalCount(value.read_count) && isOptionalString(value.date_added) &&
    isOptionalString(value.date_read) && isStringArray(value.warnings)
  );
}

function asPlatformCsvImportPreview(value: unknown): PlatformCsvImportPreview {
  if (
    !isRecord(value) || !isPlatformCsvImportBatch(value.batch) ||
    !isCount(value.record_count) || !isCount(value.issue_count) ||
    !isRecord(value.warning_counts) ||
    !Object.entries(value.warning_counts).every(([warning, count]) => Boolean(warning) && isCount(count))
  ) {
    throw invalidResponse();
  }
  return value as unknown as PlatformCsvImportPreview;
}

function asPlatformCsvColumnMappingDraft(value: unknown): PlatformCsvColumnMappingDraft {
  const isUuid = (candidate: unknown) => typeof candidate === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(candidate);
  if (
    !isRecord(value) || !isUuid(value.selection_id) || !isPlatformCsvSource(value.source) ||
    !isDigest(value.source_digest) || !Array.isArray(value.headers) ||
    !isRecord(value.suggested_mapping) ||
    Object.keys(value.suggested_mapping).length !== PLATFORM_CSV_MAPPING_FIELDS.length ||
    Object.keys(value).some((key) => ["path", "file_path", "row_values"].includes(key))
  ) {
    throw invalidResponse();
  }
  const headers: unknown[] = value.headers;
  const suggestedMapping = value.suggested_mapping;
  if (
    headers.length > MAX_PLATFORM_CSV_HEADERS ||
    !headers.every((header) => typeof header === "string" && header.length > 0 && header.length <= 256) ||
    new Set(headers).size !== headers.length ||
    !PLATFORM_CSV_MAPPING_FIELDS.every((field) => {
      const header = suggestedMapping[field];
      return header === null || (typeof header === "string" && headers.includes(header));
    })
  ) {
    throw invalidResponse();
  }
  const suggestedHeaders = PLATFORM_CSV_MAPPING_FIELDS
    .map((field) => suggestedMapping[field])
    .filter((header): header is string => typeof header === "string");
  if (new Set(suggestedHeaders).size !== suggestedHeaders.length) throw invalidResponse();
  return value as unknown as PlatformCsvColumnMappingDraft;
}

function asPlatformCsvPreviewPage(value: unknown): PlatformCsvPreviewPage {
  if (
    !isRecord(value) || typeof value.has_more !== "boolean" ||
    !Array.isArray(value.entries) ||
    !value.entries.every((entry) =>
      isRecord(entry) && Number.isSafeInteger(entry.row_number) && Number(entry.row_number) >= 2 &&
      (entry.row_type === "record" || entry.row_type === "issue") &&
      (entry.record === null || isPlatformCsvRecord(entry.record)) &&
      isOptionalString(entry.reason_code) &&
      (entry.row_type !== "record" || entry.record !== null) &&
      (entry.row_type !== "issue" || entry.record === null)
    )
  ) {
    throw invalidResponse();
  }
  return value as unknown as PlatformCsvPreviewPage;
}

function asPlatformCsvImportListPage(value: unknown): PlatformCsvImportListPage {
  if (
    !isRecord(value) || typeof value.has_more !== "boolean" ||
    !Array.isArray(value.batches) || !value.batches.every(isPlatformCsvImportBatch)
  ) {
    throw invalidResponse();
  }
  return value as unknown as PlatformCsvImportListPage;
}

function asPlatformCsvExportResult(value: unknown): PlatformCsvExportResult {
  if (
    !isRecord(value) || typeof value.file_name !== "string" || !value.file_name ||
    /[\\/]/.test(value.file_name) || !isCount(value.bytes_written) ||
    !isCount(value.rows_exported) || !isCount(value.formula_escaped_cells) ||
    typeof value.reviews_included !== "boolean" || !isStringArray(value.unexported_fields) ||
    !isStringArray(value.warnings) || Object.keys(value).some((key) => key === "path" || key === "destination")
  ) {
    throw invalidResponse();
  }
  return value as unknown as PlatformCsvExportResult;
}

export function createReadingApi(transport: IpcTransport): ReadingApi {
  return Object.freeze({
    app: Object.freeze({
      status: async () => asServiceStatus(await transport.invoke("app:status")),
    }),
    calibre: Object.freeze({
      list: async () => asCalibreCatalog(await transport.invoke("calibre:list")),
      selectImport: async () => {
        const result = await transport.invoke("calibre:select-import");
        return result === null ? null : asCalibreImportBatch(result);
      },
      addImport: async (
        previewId: string,
        fileIndex: number,
        mode: "new" | "attach",
        targetBookId?: number,
      ) =>
        asCalibreBookResult(await transport.invoke("calibre:add-import", {
          preview_id: previewId,
          file_index: fileIndex,
          mode,
          ...(targetBookId === undefined ? {} : { target_book_id: targetBookId }),
          recovery_copy_confirmed: true,
        })),
      selectCover: async (bookId: number) => {
        const result = await transport.invoke("calibre:select-cover", { book_id: bookId });
        if (result === null) return null;
        if (
          !isRecord(result) ||
          typeof result.selection_id !== "string" ||
          typeof result.name !== "string"
        ) {
          throw invalidResponse();
        }
        return { selection_id: result.selection_id, name: result.name };
      },
      edit: async (
        bookId: number,
        changes: CalibreEditChanges,
        coverSelectionId?: string,
      ) =>
        asCalibreBookResult(await transport.invoke("calibre:edit", {
          book_id: bookId,
          changes,
          ...(coverSelectionId === undefined
            ? {}
            : { cover_selection_id: coverSelectionId }),
          recovery_copy_confirmed: true,
        })),
      editTagsBatch: async (
        bookIds: number[],
        addTags: string[],
        removeTags: string[],
      ) => {
        const result = await transport.invoke("calibre:bulk-tags", {
          book_ids: bookIds,
          add_tags: addTags,
          remove_tags: removeTags,
          confirmed: true,
          recovery_copy_confirmed: true,
        });
        if (!isRecord(result) || !Array.isArray(result.books) || !result.books.every(isCalibreBook)) {
          throw invalidResponse();
        }
        return result.books;
      },
      remove: async (bookId: number) =>
        asCalibreRemovalResult(await transport.invoke("calibre:remove", {
          book_id: bookId,
          confirmed: true,
          recovery_copy_confirmed: true,
        })),
      readings: Object.freeze({
        preview: async (importId: string) =>
          asCalibreReadingProjection(
            await transport.invoke("calibre:readings-preview", { import_id: importId }),
          ),
        apply: async (importId: string, projectionId: string) =>
          asCalibreReadingApplyResult(
            await transport.invoke("calibre:readings-apply", {
              import_id: importId,
              projection_id: projectionId,
              confirmed: true,
            }),
          ),
      }),
    }),
    koreader: Object.freeze({
      readings: Object.freeze({
        preview: async () =>
          asKoreaderReadingPreview(await transport.invoke("koreader:readings-preview")),
        events: async (limit = 100, offset = 0) =>
          asReadingEventsPage(
            await transport.invoke("koreader:readings-events", { limit, offset }),
          ),
        confirm: async (importId: string, sourceDigest: string) => {
          const result = await transport.invoke("koreader:readings-confirm", {
            import_id: importId,
            source_digest: sourceDigest,
            confirmed: true,
          });
          if (!isReadingImportBatch(result)) throw invalidResponse();
          return result;
        },
        map: async (importId: string, koreaderBookId: number, calibreBookId: number) => {
          const result = await transport.invoke("koreader:readings-map", {
            import_id: importId,
            koreader_book_id: koreaderBookId,
            calibre_book_id: calibreBookId,
            confirmed: true,
          });
          if (!isRecord(result) || result.mapped !== true) throw invalidResponse();
        },
      }),
    }),
    kindle: Object.freeze({
      status: async () => asKindleStatus(await transport.invoke("kindle:status")),
      send: async (bookId: number, format: KindleFormat, filename: string) => asKindleTransfer(await transport.invoke("kindle:send", {
        book_id: bookId,
        format,
        filename,
        recovery_copy_confirmed: true,
      })),
    }),
    hardcover: Object.freeze({
      status: async () => asHardcoverStatus(await transport.invoke("hardcover:status")),
      list: async (limit = 100, offset = 0) =>
        asHardcoverPage(
          await transport.invoke("hardcover:list", { limit, offset }),
        ),
      currentReading: async () => {
        const result = await transport.invoke("hardcover:current-reading");
        return asHardcoverCurrentReading(result);
      },
      configure: async (token: string) =>
        asHardcoverStatus(
          await transport.invoke("hardcover:configure", { token }),
        ),
      reconcile: async () =>
        asHardcoverReconciliationResult(
          await transport.invoke("hardcover:reconcile", { confirmed: true }),
        ),
      manualReviews: async () => {
        const result = await transport.invoke("hardcover:outbox-list", {});
        if (!Array.isArray(result)) throw invalidResponse();
        return result.map(asHardcoverManualReviewItem);
      },
      resolveReview: async (
        operationId: string,
        resolution: HardcoverReviewResolution,
      ) =>
        asHardcoverReviewResolutionResult(
          await transport.invoke("hardcover:outbox-resolve", {
            operation_id: operationId,
            resolution,
            confirmed: true,
          }),
        ),
    }),
    backup: Object.freeze({
      list: async () => {
        const result = await transport.invoke("backup:list");
        if (!Array.isArray(result) || !result.every(asBackupSnapshot)) {
          throw invalidResponse();
        }
        return result;
      },
      create: async (calibreOnly: boolean, verifyRestore: boolean) =>
        asBackupCreateResult(await transport.invoke("backup:create", {
          calibre_only: calibreOnly,
          verify_restore: verifyRestore,
          recovery_copy_confirmed: true,
        })),
      automationStatus: async () =>
        asBackupAutomationStatus(await transport.invoke("backup:automation-status")),
      configureAutomation: async (preferences: BackupAutomationPreferences) =>
        asBackupAutomationStatus(await transport.invoke("backup:automation-configure", {
          daily_backup_enabled: preferences.daily_backup_enabled,
          daily_backup_time: preferences.daily_backup_time,
          backup_on_kindle_connect: preferences.backup_on_kindle_connect,
        })),
      selectRestoreTarget: async () => {
        const result = await transport.invoke("backup:select-restore-target");
        return result === null ? null : asBackupRestoreTarget(result);
      },
      restore: async (snapshotId: string, targetId: string) =>
        asBackupRestoreResult(await transport.invoke("backup:restore", {
          snapshot_id: snapshotId,
          target_id: targetId,
          recovery_copy_confirmed: true,
        })),
      credentials: async () =>
        asBackupCredentialStatus(await transport.invoke("backup:credentials")),
      configurePassword: async (password: string) =>
        asBackupCredentialStatus(await transport.invoke("backup:configure-password", { password })),
    }),
    profileRecovery: Object.freeze({
      selectSource: async () => {
        const result = await transport.invoke("profile-recovery:select-source");
        return result === null ? null : asProfileRecoverySource(result);
      },
      preview: async (sourceId: string) =>
        asProfileRecoveryPreview(
          await transport.invoke("profile-recovery:preview", { source_id: sourceId }),
        ),
      apply: async (previewId: string) =>
        asProfileRecoveryResult(
          await transport.invoke("profile-recovery:apply", {
            preview_id: previewId,
            confirmed: true,
          }),
        ),
      restart: async () => {
        const result = await transport.invoke("app:restart");
        if (!isRecord(result) || result.scheduled !== true) throw invalidResponse();
      },
    }),
    platformCsv: Object.freeze({
    selectImport: async (source: PlatformCsvSource, language: PlatformCsvDialogLanguage = "pt-BR") => {
        if (!isPlatformCsvSource(source)) throw new Error("invalid_request");
        if (language !== "pt-BR" && language !== "en") throw new Error("invalid_request");
        const result = await transport.invoke("platform-csv:select-import", { source, language });
        return result === null ? null : asPlatformCsvColumnMappingDraft(result);
      },
      previewMapped: async (selectionId: string, mapping: PlatformCsvColumnMapping) => {
        if (
          !isRecord(mapping) || Object.keys(mapping).length !== PLATFORM_CSV_MAPPING_FIELDS.length ||
          !PLATFORM_CSV_MAPPING_FIELDS.every((field) => Object.hasOwn(mapping, field))
        ) throw new Error("invalid_request");
        return asPlatformCsvImportPreview(await transport.invoke("platform-csv:preview-mapped", {
          selection_id: selectionId,
          column_mapping: mapping,
        }));
      },
      list: async (limit = 100, offset = 0, source?: PlatformCsvSource) => {
        const result = await transport.invoke("platform-csv:list", {
          limit,
          offset,
          ...(source ? { source } : {}),
        });
        return asPlatformCsvImportListPage(result);
      },
      page: async (importId: string, limit = 100, offset = 0) =>
        asPlatformCsvPreviewPage(await transport.invoke("platform-csv:page", {
          import_id: importId,
          limit,
          offset,
        })),
      confirm: async (importId: string, sourceDigest: string) => {
        const result = await transport.invoke("platform-csv:confirm", {
          import_id: importId,
          source_digest: sourceDigest,
          confirmed: true,
        });
        if (!isPlatformCsvImportBatch(result)) throw invalidResponse();
        return result;
      },
      export: async (importId: string, includeReviews: boolean, language: PlatformCsvDialogLanguage = "pt-BR") => {
        if (!isDigest(importId) || typeof includeReviews !== "boolean" || (language !== "pt-BR" && language !== "en")) {
          throw new Error("invalid_request");
        }
        const result = await transport.invoke("platform-csv:export", {
          import_id: importId,
          include_reviews: includeReviews,
          language,
          confirmed: true,
        });
        return result === null ? null : asPlatformCsvExportResult(result);
      },
    }),
  });
}

declare global {
  interface Window {
    readonly reading: ReadingApi;
  }
}
