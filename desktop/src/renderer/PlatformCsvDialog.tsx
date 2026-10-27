import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowDownToLine, ArrowUpFromLine, Check, LoaderCircle, ShieldCheck, X } from "lucide-react";
import { PLATFORM_CSV_MAPPING_FIELDS } from "../shared/contracts";
import type {
  CalibreBook,
  HardcoverBook,
  PlatformCsvColumnMapping,
  PlatformCsvColumnMappingDraft,
  PlatformCsvMappingField,
  PlatformCsvImportBatch,
  PlatformCsvImportPreview,
  PlatformCsvPreviewPage,
  PlatformCsvSource,
  ReadingApi,
} from "../shared/contracts";
import { localizeText, type UiLanguage } from "./i18n";
import { UiButton, UiInput, UiSelect } from "./ui/controls";
import { findPlatformCsvMatches, type CsvMatchGroup, type CsvMatchReason } from "./csv-matching";

interface PlatformCsvDialogProps {
  language: UiLanguage;
  api: ReadingApi["platformCsv"];
  calibreBooks?: CalibreBook[];
  hardcoverBooks?: HardcoverBook[];
  onClose(): void;
}

const PAGE_SIZE = 8;
const SOURCES: Array<{ value: PlatformCsvSource; label: string }> = [
  { value: "goodreads", label: "Goodreads" },
  { value: "storygraph", label: "StoryGraph" },
  { value: "maratona", label: "Maratona" },
];

function errorText(language: UiLanguage, error: unknown): string {
  const code = error instanceof Error ? error.message : "";
  const messages: Record<string, { pt: string; en: string }> = {
    platform_csv_invalid_source: { pt: "Escolha uma plataforma compatível.", en: "Choose a supported platform." },
    platform_csv_invalid_file: { pt: "O CSV não tem uma estrutura reconhecida ou está inválido.", en: "The CSV has an unsupported structure or is invalid." },
    platform_csv_file_too_large: { pt: "O arquivo CSV excede o limite de 50 MB.", en: "The CSV file exceeds the 50 MB limit." },
    platform_csv_file_unavailable: { pt: "Não foi possível acessar o arquivo escolhido.", en: "The selected file could not be accessed." },
    platform_csv_source_changed: { pt: "A prévia mudou. Importe o arquivo novamente.", en: "The preview changed. Import the file again." },
    platform_csv_invalid_mapping: { pt: "Revise o vínculo das colunas e tente novamente.", en: "Review the column mapping and try again." },
    platform_csv_mapping_conflict: { pt: "Este CSV já tem uma importação salva com outro ou antigo mapeamento. Ela foi mantida; não foi criado outro lote.", en: "This CSV already has a saved import with a different or older mapping. It was kept; no new batch was created." },
    platform_csv_selection_expired: { pt: "A seleção expirou. Escolha o CSV novamente.", en: "The selection expired. Choose the CSV again." },
    platform_csv_import_not_pending: { pt: "Este lote não está mais aguardando confirmação.", en: "This import is no longer pending confirmation." },
    platform_csv_import_not_applied: { pt: "Confirme o lote antes de exportá-lo.", en: "Confirm the import before exporting it." },
    platform_csv_export_failed: { pt: "Não foi possível gravar o arquivo de exportação.", en: "The export file could not be written." },
    application_state_unavailable: { pt: "O banco local está indisponível. Nenhuma alteração foi aplicada.", en: "The local database is unavailable. No changes were applied." },
  };
  const fallback = language === "en"
    ? "The operation could not be completed. Try again."
    : "Não foi possível concluir a operação. Tente novamente.";
  return messages[code]?.[language === "en" ? "en" : "pt"] ?? fallback;
}

function formatDate(value: string, language: UiLanguage): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(language === "en" ? "en-US" : "pt-BR", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function sourceName(source: PlatformCsvSource): string {
  return SOURCES.find((candidate) => candidate.value === source)?.label ?? source;
}

function statusName(status: PlatformCsvImportBatch["status"], language: UiLanguage): string {
  const labels: Record<PlatformCsvImportBatch["status"], string> = {
    preview: "Aguardando revisão",
    applied: "Salvo localmente",
    failed: "Falhou",
  };
  return localizeText(language, labels[status]);
}

function readingStatusName(status: string, language: UiLanguage): string {
  const labels: Record<string, { pt: string; en: string }> = {
    to_read: { pt: "Quero ler", en: "Want to read" },
    currently_reading: { pt: "Lendo", en: "Currently reading" },
    read: { pt: "Lido", en: "Read" },
    did_not_finish: { pt: "Não concluído", en: "Did not finish" },
  };
  return labels[status]?.[language === "en" ? "en" : "pt"] ?? status;
}

function warningName(warning: string, language: UiLanguage): string {
  const labels: Record<string, { pt: string; en: string }> = {
    unmapped_status: { pt: "status sem correspondência", en: "unmapped status" },
    invalid_rating: { pt: "avaliação inválida", en: "invalid rating" },
    invalid_date: { pt: "data inválida", en: "invalid date" },
    invalid_read_count: { pt: "contagem de leituras inválida", en: "invalid read count" },
    invalid_isbn10: { pt: "ISBN-10 inválido", en: "invalid ISBN-10" },
    invalid_isbn13: { pt: "ISBN-13 inválido", en: "invalid ISBN-13" },
    preview_truncated: { pt: "prévia abreviada por segurança", en: "preview shortened for safety" },
  };
  return labels[warning]?.[language === "en" ? "en" : "pt"] ?? warning;
}

const mappingLabels: Record<(typeof PLATFORM_CSV_MAPPING_FIELDS)[number], { pt: string; en: string }> = {
  external_id: { pt: "ID externo", en: "External ID" },
  title: { pt: "Título do livro", en: "Book title" },
  author: { pt: "Autor(es)", en: "Author(s)" },
  isbn_10: { pt: "ISBN-10", en: "ISBN-10" },
  isbn_13: { pt: "ISBN-13", en: "ISBN-13" },
  rating: { pt: "Sua avaliação", en: "Your rating" },
  status: { pt: "Status de leitura", en: "Reading status" },
  shelves: { pt: "Tags/prateleiras", en: "Tags/shelves" },
  read_count: { pt: "Contagem de leituras", en: "Read count" },
  date_added: { pt: "Data adicionada", en: "Date added" },
  date_read: { pt: "Data de conclusão", en: "Date finished" },
  review: { pt: "Resenha (privada)", en: "Review (private)" },
};

function validColumnMapping(mapping: PlatformCsvColumnMapping): boolean {
  if (!mapping.title || !mapping.author) return false;
  const selected = Object.values(mapping).filter((value): value is string => value !== null);
  return new Set(selected).size === selected.length;
}

export function PlatformCsvDialog({
  language,
  api,
  calibreBooks = [],
  hardcoverBooks = [],
  onClose,
}: PlatformCsvDialogProps) {
  const tx = (text: string) => localizeText(language, text);
  const [source, setSource] = useState<PlatformCsvSource>("goodreads");
  const [imports, setImports] = useState<PlatformCsvImportBatch[]>([]);
  const [importsHaveMore, setImportsHaveMore] = useState(false);
  const [importsOffset, setImportsOffset] = useState(0);
  const [selectedBatch, setSelectedBatch] = useState<PlatformCsvImportBatch | null>(null);
  const [mappingDraft, setMappingDraft] = useState<PlatformCsvColumnMappingDraft | null>(null);
  const [columnMapping, setColumnMapping] = useState<PlatformCsvColumnMapping | null>(null);
  const [preview, setPreview] = useState<PlatformCsvImportPreview | null>(null);
  const [page, setPage] = useState<PlatformCsvPreviewPage | null>(null);
  const [pageOffset, setPageOffset] = useState(0);
  const [includeReviews, setIncludeReviews] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadImports = useCallback(async (offset = 0) => {
    try {
      const result = await api.list(20, offset);
      setImports(result.batches);
      setImportsHaveMore(result.has_more);
      setImportsOffset(offset);
    } catch (cause) {
      setError(errorText(language, cause));
    }
  }, [api, language]);

  useEffect(() => {
    void loadImports();
  }, [loadImports]);

  const loadPage = useCallback(async (batch: PlatformCsvImportBatch, offset = 0) => {
    const changingBatch = selectedBatch?.import_id !== batch.import_id;
    setError(null);
    setBusy(true);
    try {
      const result = await api.page(batch.import_id, PAGE_SIZE, offset);
      setSelectedBatch(batch);
      setPreview((current) => current?.batch.import_id === batch.import_id ? current : null);
      setPage(result);
      setPageOffset(offset);
      if (changingBatch) setIncludeReviews(false);
      setNotice(null);
    } catch (cause) {
      setError(errorText(language, cause));
    } finally {
      setBusy(false);
    }
  }, [api, language, selectedBatch?.import_id]);

  const visibleRecords = useMemo(() => page?.entries ?? [], [page]);
  const optionalMappingFields = PLATFORM_CSV_MAPPING_FIELDS.filter((field) => field !== "title" && field !== "author");
  const mappedOptionalCount = columnMapping
    ? optionalMappingFields.filter((field) => columnMapping[field] !== null).length
    : 0;

  function renderMappingField(field: PlatformCsvMappingField) {
    if (!mappingDraft || !columnMapping) return null;
    const label = mappingLabels[field][language === "en" ? "en" : "pt"];
    const required = field === "title" || field === "author";
    return (
      <label className="platform-csv-mapping-field" key={field}>
        <span>{label}{required ? " *" : ""}</span>
        <UiSelect
          aria-label={`${label}${required ? language === "en" ? " (required)" : " (obrigatório)" : ""} ${language === "en" ? "column" : "coluna"}`}
          value={columnMapping[field] ?? ""}
          disabled={busy}
          onChange={(event) => {
            const selectedHeader = event.currentTarget.value || null;
            setColumnMapping((current) => current
              ? { ...current, [field]: selectedHeader }
              : current);
          }}
        >
          <option value="">{language === "en" ? "Do not import" : "Não importar"}</option>
          {mappingDraft.headers.map((header) => {
            const usedByAnotherField = Object.entries(columnMapping).some(
              ([otherField, selectedHeader]) => otherField !== field && selectedHeader === header,
            );
            return <option value={header} key={header} disabled={usedByAnotherField}>{header}</option>;
          })}
        </UiSelect>
      </label>
    );
  }

  async function importCsv() {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const result = await api.selectImport(source, language);
      if (!result) return;
      setIncludeReviews(false);
      setPreview(null);
      setSelectedBatch(null);
      setPage(null);
      setMappingDraft(result);
      setColumnMapping(result.suggested_mapping);
      setPageOffset(0);
    } catch (cause) {
      setError(errorText(language, cause));
    } finally {
      setBusy(false);
    }
  }

  async function buildMappedPreview() {
    if (!mappingDraft || !columnMapping || !validColumnMapping(columnMapping)) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const result = await api.previewMapped(mappingDraft.selection_id, columnMapping);
      setPreview(result);
      setSelectedBatch(result.batch);
      setMappingDraft(null);
      setColumnMapping(null);
      setPageOffset(0);
      setPage(await api.page(result.batch.import_id, PAGE_SIZE, 0));
      await loadImports();
    } catch (cause) {
      setError(errorText(language, cause));
    } finally {
      setBusy(false);
    }
  }

  async function confirmImport() {
    const pending = preview?.batch ?? selectedBatch;
    const validRecordCount = preview?.record_count ?? pending?.record_count ?? 0;
    if (!pending || pending.status !== "preview" || validRecordCount < 1) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const applied = await api.confirm(pending.import_id, pending.source_digest);
      setSelectedBatch(applied);
      setPreview((current) => current ? { ...current, batch: applied } : null);
      setNotice(tx("Importação salva neste computador. Ela ainda não atualiza o Hardcover nem o Calibre."));
      await loadImports();
    } catch (cause) {
      setError(errorText(language, cause));
    } finally {
      setBusy(false);
    }
  }

  async function exportCsv() {
    if (!selectedBatch || selectedBatch.status !== "applied") return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const result = await api.export(selectedBatch.import_id, includeReviews, language);
      if (result) {
        setNotice(language === "en"
          ? `CSV exported: ${result.rows_exported} rows · ${result.bytes_written.toLocaleString("en-US")} bytes.`
          : `CSV exportado: ${result.rows_exported} linhas · ${result.bytes_written.toLocaleString("pt-BR")} bytes.`);
      }
    } catch (cause) {
      setError(errorText(language, cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <section className="action-dialog platform-csv-dialog" role="dialog" aria-modal="true" aria-labelledby="platform-csv-title">
        <header className="dialog-heading">
          <div>
            <span className="eyebrow">{tx("DADOS DE LEITURA")}</span>
            <h2 id="platform-csv-title">{tx("Histórico de leitura")}</h2>
            <p>{tx("Importe ou exporte CSV para levar seu histórico entre plataformas.")}</p>
          </div>
          <UiButton className="icon-button" type="button" aria-label={tx("Fechar")} onClick={onClose} disabled={busy}><X size={17} aria-hidden="true" /></UiButton>
        </header>

        <div className="platform-csv-notice">
          <ShieldCheck size={16} aria-hidden="true" />
          <p>{language === "en"
            ? "Imported data stays on this computer. Only mapped columns are kept; unmapped columns and private notes are discarded. Reviews are ignored unless you map them, stay hidden in the preview, and are exported only if you opt in. Confirmation does not change Hardcover or Calibre."
            : "Os dados importados ficam neste computador. Só as colunas mapeadas são guardadas; colunas não mapeadas e notas privadas são descartadas. Resenhas são ignoradas até você mapeá-las, ficam ocultas na prévia e só são exportadas se você optar por isso. A confirmação não altera o Hardcover nem o Calibre."}</p>
        </div>

        <div className="platform-csv-import-controls">
          <label className="platform-csv-source">
            <span>{tx("Plataforma de origem")}</span>
            <UiSelect value={source} onChange={(event) => setSource(event.currentTarget.value as PlatformCsvSource)} disabled={busy || Boolean(mappingDraft)}>
              {SOURCES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </UiSelect>
          </label>
          <UiButton className="button-primary" type="button" onClick={() => void importCsv()} disabled={busy}>
            {busy ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : <ArrowDownToLine size={15} aria-hidden="true" />}
            {tx("Escolher CSV para importar")}
          </UiButton>
        </div>

        {mappingDraft && columnMapping ? (
          <section className="platform-csv-mapping" aria-label={tx("Mapeamento de colunas do CSV")}>
            <div className="platform-csv-section-heading">
              <div>
                <h3>{language === "en" ? "Match CSV columns" : "Mapear colunas do CSV"}</h3>
                <span>{language === "en"
                  ? "Check the suggestions before reading any rows into the preview."
                  : "Confira as sugestões antes de carregar as linhas para a prévia."}</span>
              </div>
            </div>
            <p className="platform-csv-summary">
              {language === "en"
              ? "The mapping selects fields used for reading history. Source values stay local; nothing is sent to Hardcover or Calibre."
                : "O mapeamento seleciona os campos usados no histórico. Os valores da origem ficam locais; nada é enviado ao Hardcover ou ao Calibre."}
            </p>
            <div className="platform-csv-mapping-grid">
              {renderMappingField("title")}
              {renderMappingField("author")}
            </div>
            <details className="platform-csv-mapping-optional">
              <summary>{language === "en"
                ? `Optional fields · ${mappedOptionalCount} mapped`
                : `Campos opcionais · ${mappedOptionalCount} mapeados`}</summary>
              <div className="platform-csv-mapping-grid">
                {optionalMappingFields.map(renderMappingField)}
              </div>
            </details>
            <div className="dialog-footer">
              <UiButton type="button" onClick={() => {
                setMappingDraft(null);
                setColumnMapping(null);
              }} disabled={busy}>
                {language === "en" ? "Cancel mapping" : "Cancelar mapeamento"}
              </UiButton>
              <UiButton className="button-primary" type="button" onClick={() => void buildMappedPreview()} disabled={busy || !validColumnMapping(columnMapping)}>
                {busy ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}
                {language === "en" ? "Preview using these columns" : "Pré-visualizar com estas colunas"}
              </UiButton>
            </div>
          </section>
        ) : null}

        {preview ? (
          <section className="platform-csv-preview" aria-label={tx("Prévia do CSV") }>
            <div className="platform-csv-section-heading">
              <div><h3>{tx("Prévia da importação")}</h3><span>{sourceName(preview.batch.source)} · {preview.record_count.toLocaleString(language === "en" ? "en-US" : "pt-BR")} {tx("registros válidos")}</span></div>
              <span className={`platform-csv-status status-${preview.batch.status}`}>{statusName(preview.batch.status, language)}</span>
            </div>
            <p className="platform-csv-summary">
              {preview.issue_count ? (language === "en" ? `${preview.issue_count} rows need review and will be skipped.` : `${preview.issue_count} linhas precisam de revisão e serão ignoradas.`) : tx("Todas as linhas foram lidas.")}
              {Object.keys(preview.warning_counts).length ? ` ${tx("Avisos")}: ${Object.entries(preview.warning_counts).map(([key, count]) => `${warningName(key, language)} (${count})`).join(", ")}.` : ""}
            </p>
            {renderEntries(visibleRecords, language, preview.batch.source, calibreBooks, hardcoverBooks)}
            {renderPageControls(page, pageOffset, PAGE_SIZE, busy, tx, (offset) => void loadPage(preview.batch, offset))}
            {preview.batch.status === "preview" ? (
              <div className="dialog-footer">
                <UiButton className="button-primary" type="button" onClick={() => void confirmImport()} disabled={busy || preview.record_count < 1}>
                  {busy ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}
                  {tx("Confirmar e guardar localmente")}
                </UiButton>
              </div>
            ) : null}
          </section>
        ) : selectedBatch ? (
          <section className="platform-csv-preview" aria-label={tx("Detalhes da importação") }>
            <div className="platform-csv-section-heading">
              <div><h3>{sourceName(selectedBatch.source)}</h3><span>{selectedBatch.record_count.toLocaleString(language === "en" ? "en-US" : "pt-BR")} {tx("registros") } · {formatDate(selectedBatch.created_at, language)}</span></div>
              <span className={`platform-csv-status status-${selectedBatch.status}`}>{statusName(selectedBatch.status, language)}</span>
            </div>
            {renderEntries(visibleRecords, language, selectedBatch.source, calibreBooks, hardcoverBooks)}
            {renderPageControls(page, pageOffset, PAGE_SIZE, busy, tx, (offset) => void loadPage(selectedBatch, offset))}
            {selectedBatch.status === "preview" ? (
              <div className="dialog-footer">
                <UiButton className="button-primary" type="button" onClick={() => void confirmImport()} disabled={busy || selectedBatch.record_count < 1}>
                  {busy ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}
                  {tx("Confirmar e guardar localmente")}
                </UiButton>
              </div>
            ) : null}
          </section>
        ) : null}

        {selectedBatch?.status === "applied" ? (
          <section className="platform-csv-export">
            <div><h3>{tx("Exportar histórico")}</h3><p>{tx("O CSV do Goodreads é compatível com ferramentas que aceitam esse formato.")}</p></div>
            <label className="platform-csv-review-choice">
              <UiInput type="checkbox" checked={includeReviews} onChange={(event) => setIncludeReviews(event.currentTarget.checked)} disabled={busy} />
              <span>{tx("Incluir resenhas neste arquivo")}</span>
            </label>
            <UiButton className="button-secondary" type="button" onClick={() => void exportCsv()} disabled={busy}>
              <ArrowUpFromLine size={15} aria-hidden="true" /> {tx("Exportar CSV compatível com Goodreads")}
            </UiButton>
          </section>
        ) : null}

        <section className="platform-csv-history">
          <div className="platform-csv-section-heading"><div><h3>{tx("Importações recentes")}</h3><span>{tx("Histórico local")}</span></div>{busy ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : null}</div>
          {imports.length ? (
            <ul>
              {imports.map((batch) => (
                <li key={batch.import_id}>
                  <button type="button" onClick={() => void loadPage(batch)} disabled={busy} aria-current={selectedBatch?.import_id === batch.import_id ? "true" : undefined}>
                    <span><strong>{sourceName(batch.source)}</strong><small>{formatDate(batch.created_at, language)} · {batch.record_count.toLocaleString(language === "en" ? "en-US" : "pt-BR")} {tx("registros")}</small></span>
                    <span className={`platform-csv-status status-${batch.status}`}>{statusName(batch.status, language)}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="platform-csv-empty">{tx("Nenhum CSV foi importado ainda.")}</p>}
          {imports.length && (importsOffset > 0 || importsHaveMore) ? (
            <nav className="platform-csv-pagination" aria-label={tx("Navegação do histórico CSV")}>
              <UiButton className="button-secondary" type="button" onClick={() => void loadImports(Math.max(0, importsOffset - 20))} disabled={busy || importsOffset === 0}>{tx("Importações mais recentes")}</UiButton>
              <span>{tx("Importações")} {importsOffset + 1}–{importsOffset + imports.length}</span>
              <UiButton className="button-secondary" type="button" onClick={() => void loadImports(importsOffset + 20)} disabled={busy || !importsHaveMore}>{tx("Importações mais antigas")}</UiButton>
            </nav>
          ) : null}
        </section>

        {error ? <div className="dialog-error" role="alert"><AlertTriangle size={15} aria-hidden="true" />{error}</div> : null}
        {notice ? <p className="platform-csv-result" role="status"><Check size={15} aria-hidden="true" />{notice}</p> : null}
      </section>
    </div>
  );
}

function renderEntries(
  entries: PlatformCsvPreviewPage["entries"],
  language: UiLanguage,
  source: PlatformCsvImportBatch["source"],
  calibreBooks: CalibreBook[],
  hardcoverBooks: HardcoverBook[],
) {
  const tx = (text: string) => localizeText(language, text);
  if (!entries.length) return <p className="platform-csv-empty">{tx("Este lote não contém linhas para mostrar.")}</p>;
  return (
    <div className="platform-csv-entries">
      {entries.map((entry) => (
        <article className="platform-csv-entry" key={`${entry.row_number}-${entry.row_type}`}>
          {entry.record ? (
            <>
              <div className="platform-csv-entry-main"><strong>{entry.record.title}</strong><span>{entry.record.authors.join(", ")}</span></div>
              <div className="platform-csv-entry-meta">
                {entry.record.reading_status ? <span>{readingStatusName(entry.record.reading_status, language)}</span> : null}
                {entry.record.rating !== null ? <span>{entry.record.rating} / 5</span> : null}
                {entry.record.date_read ? <span>{entry.record.date_read}</span> : null}
              </div>
              <LocalMatchSuggestions
                record={entry.record}
                source={source}
                calibreBooks={calibreBooks}
                hardcoverBooks={hardcoverBooks}
                language={language}
              />
            </>
          ) : (
            <div className="platform-csv-entry-main"><strong>{tx("Linha ignorada")}</strong><span>{tx("Confira o arquivo original; o texto da linha não foi armazenado para exibição.")}</span></div>
          )}
          <small>{language === "en" ? `Row ${entry.row_number}` : `Linha ${entry.row_number}`}</small>
        </article>
      ))}
    </div>
  );
}

function LocalMatchSuggestions({
  record,
  source,
  calibreBooks,
  hardcoverBooks,
  language,
}: {
  record: NonNullable<PlatformCsvPreviewPage["entries"][number]["record"]>;
  source: PlatformCsvImportBatch["source"];
  calibreBooks: CalibreBook[];
  hardcoverBooks: HardcoverBook[];
  language: UiLanguage;
}) {
  const matches = findPlatformCsvMatches(record, source, calibreBooks, hardcoverBooks);
  const groups = [
    { provider: "Calibre", group: matches.calibre },
    { provider: "Hardcover", group: matches.hardcover },
  ];
  const matchedGroups = groups.filter(({ group }) => group.status !== "none");
  const summary = matchedGroups.length
    ? matchedGroups.map(({ provider, group }) => `${provider}: ${matchStatusName(group.status, language)}`).join(" · ")
    : language === "en" ? "No local match" : "Sem correspondência local";

  return (
    <details className="platform-csv-matches">
      <summary>{language === "en" ? `Local suggestions · ${summary}` : `Sugestões locais · ${summary}`}</summary>
      <p>{language === "en"
        ? "Candidates only; nothing is linked or changed. Suggestions use catalog data already loaded in this view."
        : "São apenas candidatos; nada é vinculado ou alterado. As sugestões usam os catálogos já carregados nesta tela."}</p>
      {matchedGroups.length ? matchedGroups.map(({ provider, group }) => (
        <section className={`platform-csv-match-group match-${group.status}`} key={provider}>
          <h4>{provider} · {matchStatusName(group.status, language)}</h4>
          <ul>
            {group.candidates.map((candidate) => (
              <li key={`${provider}-${candidate.id}`}>
                <strong>{candidate.title}</strong>
                <span>{candidate.authors.join(", ")}</span>
                <small>{candidate.reasons.map((reason) => matchReasonName(reason, language)).join(" · ")}</small>
              </li>
            ))}
          </ul>
        </section>
      )) : <span className="platform-csv-match-empty">{language === "en"
        ? "No ISBN, provider ID, title, and author match was found."
        : "Nenhuma correspondência por ISBN, ID da plataforma, título e autor foi encontrada."}</span>}
    </details>
  );
}

function matchStatusName(status: CsvMatchGroup["status"], language: UiLanguage): string {
  if (language === "en") {
    return {
      exact: "Exact identifier match",
      conflict: "Conflicting candidates",
      candidate: "Possible title + author match",
      none: "No local match",
    }[status];
  }
  return {
    exact: "Correspondência exata por identificador",
    conflict: "Candidatos conflitantes",
    candidate: "Possível correspondência por título + autor",
    none: "Sem correspondência local",
  }[status];
}

function matchReasonName(reason: CsvMatchReason, language: UiLanguage): string {
  if (language === "en") {
    return { provider_id: "Provider ID", isbn: "ISBN", title_author: "Title + author" }[reason];
  }
  return { provider_id: "ID da plataforma", isbn: "ISBN", title_author: "Título + autor" }[reason];
}

function renderPageControls(
  page: PlatformCsvPreviewPage | null,
  offset: number,
  pageSize: number,
  busy: boolean,
  tx: (text: string) => string,
  onPage: (offset: number) => void,
) {
  if (!page || (offset === 0 && !page.has_more)) return null;
  return (
    <nav className="platform-csv-pagination" aria-label={tx("Paginação da prévia") }>
      <UiButton className="button-secondary" type="button" onClick={() => onPage(Math.max(0, offset - pageSize))} disabled={busy || offset === 0}>{tx("Anterior")}</UiButton>
      <span>{tx("Linhas")} {offset + 1}–{offset + page.entries.length}</span>
      <UiButton className="button-secondary" type="button" onClick={() => onPage(offset + pageSize)} disabled={busy || !page.has_more}>{tx("Próxima")}</UiButton>
    </nav>
  );
}
