import { useMemo, useState } from "react";
import { AlertTriangle, BookOpenCheck, Check, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import type {
  CalibreBook,
  CalibreReadingProjection,
  KoreaderReadingBookSummary,
  KoreaderReadingPreview,
  ReadingEventsPage,
  UnmatchedReadingSidecarSummary,
} from "../shared/contracts";
import { getUiLocale, localizeText, type UiLanguage } from "./i18n";
import { UiButton, UiInput, UiSelect } from "./ui/controls";

const eventPageSize = 20;
const bookPageSize = 8;

type Notice = { text: string; kind: "info" | "success" | "error" };

function formatDate(value: string | null, language: UiLanguage = "pt-BR"): string {
  if (!value) return localizeText(language, "Data indisponível");
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? localizeText(language, "Data indisponível")
    : new Intl.DateTimeFormat(getUiLocale(language), { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatDuration(seconds: number, language: UiLanguage = "pt-BR"): string {
  const minutes = Math.max(0, Math.round(seconds / 60));
  const minuteLabel = language === "en" ? "min" : "min";
  const hourLabel = language === "en" ? "hr" : "h";
  if (minutes < 60) return `${minutes} ${minuteLabel}`;
  const hours = Math.floor(minutes / 60);
  return `${hours} ${hourLabel}${minutes % 60 ? ` ${minutes % 60} ${minuteLabel}` : ""}`;
}

function formatProgress(value: number | null, language: UiLanguage = "pt-BR"): string {
  return value === null ? localizeText(language, "indisponível") : `${Math.round(value * 100)}%`;
}

function readingWarningLabel(code: string, language: UiLanguage = "pt-BR"): string {
  const labels: Record<string, string> = {
    invalid_progress: "Progresso inválido no sidecar",
    invalid_summary: "Resumo de leitura inválido no sidecar",
    unknown_status: "Status de leitura não reconhecido",
    invalid_rating: "Avaliação inválida no sidecar",
    invalid_note: "Nota de leitura inválida no sidecar",
    invalid_position: "Posição de leitura inválida no sidecar",
    invalid_annotations: "Anotações inválidas no sidecar",
    sidecar_unmatched: "Sem vínculo exato no histórico do KOReader",
  };
  return localizeText(language, labels[code] ?? "Aviso nos dados de leitura");
}

function formatValue(value: unknown): string {
  if (typeof value === "string") return value.length > 180 ? `${value.slice(0, 180)}…` : value;
  if (value === null || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  const serialized = JSON.stringify(value);
  return serialized.length > 180 ? `${serialized.slice(0, 180)}…` : serialized;
}

function formatError(error: unknown, language: UiLanguage = "pt-BR"): string {
  const code = error instanceof Error ? error.message : "";
  const messages: Record<string, string> = {
    calibre_open: "Feche o Calibre para vincular a edição ou aplicar alterações.",
    kindle_unavailable: "O Kindle não está disponível. Mantenha a tela USB conectada aberta.",
    kindle_multiple_devices: "Mais de um dispositivo foi encontrado. Deixe só o Kindle desejado conectado.",
    reading_history_unsupported: "A versão do histórico do KOReader não é compatível.",
    reading_history_unavailable: "Não foi possível ler o histórico. Confira a conexão USB e tente novamente.",
    reading_mapping_unavailable: "Não foi possível salvar o vínculo com a edição do Calibre.",
    reading_projection_unavailable: "Não foi possível preparar a revisão dos campos do Calibre.",
    backup_unavailable: "O backup verificado falhou. O Calibre não foi alterado.",
    operation_timeout: "A operação demorou mais que o esperado. Confira o status antes de tentar novamente.",
  };
  return localizeText(language, messages[code] ?? "A operação não foi concluída. Confira as conexões e tente novamente.");
}

function fieldLabel(field: string, language: UiLanguage = "pt-BR"): string {
  const labels: Record<string, string> = {
    koreader_progress: "Progresso",
    koreader_status: "Status de leitura",
    koreader_position: "Posição no livro",
    koreader_rating: "Avaliação",
    koreader_note: "Nota pessoal",
    koreader_annotations: "Anotações",
  };
  return localizeText(language, labels[field] ?? field);
}

function readingStatusLabel(status: string | null | undefined, language: UiLanguage = "pt-BR"): string {
  if (!status) return localizeText(language, "Sem status");
  const labels: Record<string, string> = {
    new: "Novo",
    reading: "Lendo",
    complete: "Concluído",
    abandoned: "Abandonado",
  };
  return localizeText(language, labels[status] ?? "Status registrado");
}

function languageLabel(language: string): string {
  const normalized = language.trim().toLocaleLowerCase();
  if (["por", "pt", "pt-br", "br"].includes(normalized)) return "PT-BR";
  if (["eng", "en", "en-us", "en-gb"].includes(normalized)) return "ENG";
  return language.toLocaleUpperCase();
}

function calibreEditionLabel(book: CalibreBook, language: UiLanguage = "pt-BR"): string {
  const formats = book.formats?.includes("EPUB") ? "EPUB" : book.formats?.join(", ");
  const languages = book.languages?.map(languageLabel).join("/");
  const details = [formats, languages, book.publisher, book.pubdate?.slice(0, 4)]
    .filter((value): value is string => Boolean(value));
  const edition = details.length > 0 ? ` (${details.join(" · ")})` : "";
  return `${book.title} — ${book.authors.join(", ") || localizeText(language, "Autor desconhecido")}${edition}`;
}

function ReadingBookCard({
  language,
  book,
  calibreBooks,
  selectedBookId,
  mappedBookId,
  disabled,
  onSelect,
  onMap,
}: {
  language: UiLanguage;
  book: KoreaderReadingBookSummary;
  calibreBooks: CalibreBook[];
  selectedBookId: string;
  mappedBookId: number | undefined;
  disabled: boolean;
  onSelect(value: string): void;
  onMap(): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const progressConflict = book.sidecars
    .filter((sidecar) => sidecar.progress !== null && book.statistics_progress !== null &&
      Math.abs(sidecar.progress - book.statistics_progress) > 0.01)
    .map((sidecar) => formatProgress(sidecar.progress, language));
  const sidecarSummary = book.sidecars.length === 0
    ? tx("Sem sidecar de leitura")
    : book.sidecars.map((sidecar, index) => {
      const status = readingStatusLabel(sidecar.status, language);
      const progress = sidecar.progress === null ? null : `${Math.round(sidecar.progress * 100)}%`;
      const rating = sidecar.rating === null ? null : `${sidecar.rating}/5`;
      const annotations = sidecar.annotation_count > 0
        ? `${sidecar.annotation_count} ${tx(sidecar.annotation_count === 1 ? "anotação" : "anotações")}`
        : null;
      const note = sidecar.has_note ? tx("nota") : null;
      const details = [status, progress, rating, note, annotations].filter(Boolean).join(" · ");
      return book.sidecars.length > 1 ? `${tx("Arquivo")} ${index + 1}: ${details}` : details;
    }).join(" / ");
  return (
    <article className="reading-book-item">
      <div className="reading-book-copy">
        <strong>{book.title}</strong>
        <span>{book.authors || tx("Autor desconhecido")}{book.series ? ` · ${book.series}` : ""}</span>
        <small>{sidecarSummary}</small>
        {book.warnings.includes("progress_conflict") ? (
          <span className="reading-warning">
            {tx("Progresso divergente: estatísticas")} {formatProgress(book.statistics_progress, language)} · sidecar {progressConflict.join(" / ") || tx("indisponível")}
          </span>
        ) : null}
        {book.sidecars.flatMap((sidecar) => sidecar.warnings).map((warning) => (
          <span className="reading-warning" key={warning}>{readingWarningLabel(warning, language)}</span>
        ))}
      </div>
      {mappedBookId !== undefined ? (
        <span className="reading-mapped-label"><Check size={13} aria-hidden="true" /> {tx("Edição vinculada")}</span>
      ) : (
        <div className="reading-map-control">
          <UiSelect
            aria-label={`${tx("Edição do Calibre para")} ${book.title}`}
            value={selectedBookId}
            onChange={(event) => onSelect(event.currentTarget.value)}
            disabled={disabled}
          >
            <option value="">{tx("Selecionar edição do Calibre")}</option>
            {calibreBooks.map((candidate) => (
              <option value={candidate.book_id} key={candidate.book_id}>
                {calibreEditionLabel(candidate, language)}
              </option>
            ))}
          </UiSelect>
          <UiButton
            className="button-secondary"
            type="button"
            onClick={onMap}
            disabled={disabled || !selectedBookId}
          >
            {tx("Confirmar vínculo da edição")}
          </UiButton>
        </div>
      )}
    </article>
  );
}

function UnmatchedSidecarReview({ sidecars, language }: { sidecars: UnmatchedReadingSidecarSummary[]; language: UiLanguage }) {
  const tx = (text: string) => localizeText(language, text);
  if (sidecars.length === 0) return null;
  return (
    <details className="reading-unmatched-details">
      <summary>{tx("Arquivos de leitura sem vínculo exato")} ({sidecars.length})</summary>
      <p>{tx("O KOReader não encontrou uma identidade exata para estes arquivos. O app mantém os dados separados e não escolhe um livro automaticamente.")}</p>
      <div className="reading-unmatched-list">
        {sidecars.map((sidecar) => {
          const details = [
            sidecar.status ? readingStatusLabel(sidecar.status, language) : null,
            sidecar.progress === null ? null : `${formatProgress(sidecar.progress, language)} ${tx("lido")}`,
            sidecar.rating === null ? null : `${sidecar.rating}/5`,
            sidecar.has_note ? tx("nota") : null,
            sidecar.annotation_count > 0
              ? `${sidecar.annotation_count} ${tx(sidecar.annotation_count === 1 ? "anotação" : "anotações")}`
              : null,
          ].filter(Boolean).join(" · ");
          return (
            <article className="reading-unmatched-item" key={sidecar.sidecar_id}>
              <strong>{sidecar.title || tx("Título indisponível")}</strong>
              {sidecar.title_source ? (
                <small className="reading-sidecar-title-source">
                  {tx(sidecar.title_source === "folder" ? "Nome da pasta .sdr" : "Metadados do KOReader")}
                </small>
              ) : null}
              <span>{sidecar.authors || tx("Autor desconhecido")}</span>
              {details ? <small>{details}</small> : null}
              <span className="reading-warning">{tx("Sem associação automática; o texto das notas e anotações não é exibido nesta lista.")}</span>
              {sidecar.warnings.filter((warning) => warning !== "sidecar_unmatched").map((warning) => (
                <span className="reading-warning" key={warning}>{readingWarningLabel(warning, language)}</span>
              ))}
            </article>
          );
        })}
      </div>
    </details>
  );
}

function ProjectionReview({
  language,
  projection,
  busy,
  confirming,
  applied,
  onReview,
  onCancel,
  onApply,
}: {
  language: UiLanguage;
  projection: CalibreReadingProjection;
  busy: boolean;
  confirming: boolean;
  applied: boolean;
  onReview(): void;
  onCancel(): void;
  onApply(): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const changes = projection.items.filter((item) => Object.keys(item.new_values).length > 0);
  const changedFieldCount = changes.reduce((total, item) => total + Object.keys(item.new_values).length, 0);
  return (
    <div className="reading-projection-review">
      <div className="reading-section-heading">
        <div>
          <span className="eyebrow">{tx("REVISÃO DO CALIBRE")}</span>
          <h3>{language === "en" ? `${changedFieldCount} ${changedFieldCount === 1 ? "field" : "fields"} prepared for review` : `${changedFieldCount} ${changedFieldCount === 1 ? "campo preparado" : "campos preparados"} para revisão`}</h3>
        </div>
        <span className="reading-count">{changes.length} {tx(changes.length === 1 ? "livro" : "livros")}</span>
      </div>
      {changes.length > 0 ? (
        <div className="reading-projection-list">
          {changes.map((item) => (
            <article className="reading-projection-item" key={`${item.calibre_uuid ?? item.book_id}`}>
              <strong>{item.title ?? tx("Livro sem título")}</strong>
              {Object.entries(item.new_values).map(([field, value]) => (
                <div className="reading-projection-field" key={field}>
                  <span>{fieldLabel(field, language)}</span>
                  <code>{formatValue(value)}</code>
                </div>
              ))}
              {item.warnings.map((warning) => (
                <span className="reading-warning" key={warning}>{warning}</span>
              ))}
            </article>
          ))}
        </div>
      ) : (
        <p className="reading-muted">{tx("Não há campos novos para gravar no Calibre.")}</p>
      )}
      {projection.issues.length > 0 ? (
        <p className="reading-warning-summary">
          {language === "en" ? `${projection.issues.length} book(s) need a link or review before appearing in this preview.` : `${projection.issues.length} livro(s) precisam de vínculo ou revisão antes de aparecerem nesta prévia.`}
        </p>
      ) : null}
      {applied ? (
        <p className="reading-status reading-status-success" role="status">
          {tx("Os campos foram aplicados e conferidos no Calibre.")}
        </p>
      ) : confirming ? (
        <div className="reading-confirm-box" role="group" aria-label={tx("Confirmar gravação no Calibre")}>
          <p>{tx("Será criado e verificado um backup antes de gravar os campos acima. O Calibre precisa estar fechado.")}</p>
          <div className="reading-actions">
            <UiButton className="button-secondary" type="button" onClick={onCancel} disabled={busy}>
              {tx("Voltar")}
            </UiButton>
            <UiButton className="button-primary" type="button" onClick={onApply} disabled={busy || changes.length === 0}>
              {tx(busy ? "Aplicando…" : "Criar backup verificado e aplicar")}
            </UiButton>
          </div>
        </div>
      ) : (
        <UiButton
          className="button-secondary"
          type="button"
          onClick={onReview}
          disabled={busy || changes.length === 0}
        >
          {tx("Revisar escrita no Calibre")}
        </UiButton>
      )}
    </div>
  );
}

export function ReadingHistoryPanel({
  language = "pt-BR",
  books,
  deviceConnected,
}: {
  language?: UiLanguage;
  books: CalibreBook[];
  deviceConnected: boolean;
}) {
  const tx = (text: string) => localizeText(language, text);
  const [preview, setPreview] = useState<KoreaderReadingPreview | null>(null);
  const [projection, setProjection] = useState<CalibreReadingProjection | null>(null);
  const [events, setEvents] = useState<ReadingEventsPage | null>(null);
  const [selectedBookIds, setSelectedBookIds] = useState<Record<number, string>>({});
  const [mappedBookIds, setMappedBookIds] = useState<Record<number, number>>({});
  const [bookQuery, setBookQuery] = useState("");
  const [bookPage, setBookPage] = useState(0);
  const [eventOffset, setEventOffset] = useState(0);
  const [busy, setBusy] = useState<"preview" | "confirm" | "map" | "projection" | "apply" | null>(null);
  const [confirmingProjection, setConfirmingProjection] = useState(false);
  const [projectionApplied, setProjectionApplied] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const filteredBooks = useMemo(() => {
    const query = bookQuery.trim().toLocaleLowerCase();
    const matching = (preview?.books ?? []).filter((book) => {
      if (!query) return true;
      return `${book.title} ${book.authors ?? ""} ${book.series ?? ""}`
        .toLocaleLowerCase()
        .includes(query);
    });
    return { matching, pages: Math.max(1, Math.ceil(matching.length / bookPageSize)) };
  }, [bookQuery, preview?.books]);

  const visibleBooks = filteredBooks.matching.slice(
    bookPage * bookPageSize,
    (bookPage + 1) * bookPageSize,
  );

  const readDeviceHistory = async () => {
    if (busy || !deviceConnected) return;
    setBusy("preview");
    setNotice(null);
    setProjection(null);
    setProjectionApplied(false);
    setConfirmingProjection(false);
    try {
      const result = await window.reading.koreader.readings.preview();
      setPreview(result);
      setBookPage(0);
      setSelectedBookIds({});
      setMappedBookIds({});
      setNotice({
        kind: result.batch.status === "applied" ? "success" : "info",
        text: result.batch.status === "applied"
          ? tx("Este histórico já está importado neste computador.")
          : tx("Prévia pronta. Confira os dados antes de importar para o histórico local."),
      });
      if (result.batch.status === "applied") {
        setEvents(await window.reading.koreader.readings.events(eventPageSize, 0));
        setEventOffset(0);
      }
    } catch (error) {
      setNotice({ kind: "error", text: formatError(error, language) });
    } finally {
      setBusy(null);
    }
  };

  const confirmLocalImport = async () => {
    if (!preview || busy || preview.batch.status !== "preview") return;
    setBusy("confirm");
    setNotice(null);
    try {
      const batch = await window.reading.koreader.readings.confirm(
        preview.batch.import_id,
        preview.source_digest,
      );
      const updated = { ...preview, batch };
      setPreview(updated);
      setEvents(await window.reading.koreader.readings.events(eventPageSize, 0));
      setEventOffset(0);
      setNotice({ kind: "success", text: tx("Histórico importado neste computador. Nenhuma alteração foi enviada ao Hardcover.") });
    } catch (error) {
      setNotice({ kind: "error", text: formatError(error, language) });
    } finally {
      setBusy(null);
    }
  };

  const mapBook = async (book: KoreaderReadingBookSummary) => {
    const selected = selectedBookIds[book.koreader_book_id];
    const calibreBookId = Number(selected);
    if (!preview || !Number.isSafeInteger(calibreBookId) || calibreBookId < 1 || busy) return;
    setBusy("map");
    setNotice(null);
    try {
      await window.reading.koreader.readings.map(
        preview.batch.import_id,
        book.koreader_book_id,
        calibreBookId,
      );
      setMappedBookIds((current) => ({ ...current, [book.koreader_book_id]: calibreBookId }));
      setProjection(null);
      setProjectionApplied(false);
      setNotice({ kind: "success", text: language === "en" ? `Edition linked: ${book.title}.` : `Edição vinculada: ${book.title}.` });
    } catch (error) {
      setNotice({ kind: "error", text: formatError(error, language) });
    } finally {
      setBusy(null);
    }
  };

  const prepareProjection = async () => {
    if (!preview || preview.batch.status !== "applied" || busy) return;
    setBusy("projection");
    setNotice(null);
    setProjectionApplied(false);
    setConfirmingProjection(false);
    try {
      setProjection(await window.reading.calibre.readings.preview(preview.batch.import_id));
    } catch (error) {
      setNotice({ kind: "error", text: formatError(error, language) });
    } finally {
      setBusy(null);
    }
  };

  const applyProjection = async () => {
    if (!projection || !preview || busy) return;
    setBusy("apply");
    setNotice(null);
    try {
      const result = await window.reading.calibre.readings.apply(
        preview.batch.import_id,
        projection.projection_id,
      );
      setProjectionApplied(true);
      setConfirmingProjection(false);
      setNotice({
        kind: "success",
        text: language === "en"
          ? `${result.applied_book_count} ${result.applied_book_count === 1 ? "book" : "books"} updated in Calibre; ${result.applied_field_count} fields verified.`
          : `${result.applied_book_count} ${result.applied_book_count === 1 ? "livro atualizado" : "livros atualizados"} no Calibre; ${result.applied_field_count} campos conferidos.`,
      });
    } catch (error) {
      setNotice({ kind: "error", text: formatError(error, language) });
    } finally {
      setBusy(null);
    }
  };

  const loadEvents = async (nextOffset: number) => {
    if (busy) return;
    setBusy("preview");
    try {
      setEvents(await window.reading.koreader.readings.events(eventPageSize, nextOffset));
      setEventOffset(nextOffset);
    } catch (error) {
      setNotice({ kind: "error", text: formatError(error, language) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="reading-history-panel" aria-labelledby="reading-history-title">
      <header className="reading-history-heading">
        <div className="reading-history-icon"><BookOpenCheck size={19} aria-hidden="true" /></div>
        <div>
          <span className="eyebrow">{tx("HISTÓRICO DE LEITURA")}</span>
          <h2 id="reading-history-title">{tx("KOReader → histórico local → Calibre")}</h2>
          <p>{tx("O progresso e as anotações são importados localmente. A escrita no Calibre fica numa revisão separada.")}</p>
        </div>
      </header>

      <div className="reading-history-start">
        <div>
          <strong>{preview
            ? `${preview.summary.book_count} ${tx(preview.summary.book_count === 1 ? "livro encontrado" : "livros encontrados")}`
            : tx("Importe a leitura do Kindle")}</strong>
          <span>
            {deviceConnected
              ? tx("Deixe o Kindle na tela USB conectado. A leitura da origem é somente cópia.")
              : tx("Conecte o Kindle por USB para ler o histórico do KOReader.")}
          </span>
        </div>
        <UiButton
          className="button-primary"
          type="button"
          onClick={() => void readDeviceHistory()}
          disabled={!deviceConnected || busy !== null}
        >
          <RefreshCw size={14} className={busy === "preview" ? "spin" : undefined} aria-hidden="true" />
          {tx(busy === "preview" ? "Lendo…" : "Ler histórico do Kindle")}
        </UiButton>
      </div>

      {!deviceConnected ? (
        <p className="reading-inline-hint">{tx("Conecte o Kindle por USB e mantenha aberta a tela “Conectado ao computador”.")}</p>
      ) : null}

      {notice ? (
        <p className={`reading-status reading-status-${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
          {notice.kind === "error" ? <AlertTriangle size={15} aria-hidden="true" /> : notice.kind === "success" ? <Check size={15} aria-hidden="true" /> : null}
          {tx(notice.text)}
        </p>
      ) : null}

      {preview ? (
        <div className="reading-history-details">
          <div className="reading-summary-grid">
            <div><strong>{preview.summary.book_count}</strong><span>{tx("livros")}</span></div>
            <div><strong>{preview.summary.page_event_count}</strong><span>{tx("eventos de página")}</span></div>
            <div><strong>{preview.summary.estimated_session_count}</strong><span>{tx("sessões estimadas")}</span></div>
            <div><strong>{preview.summary.sidecar_count}</strong><span>{tx("arquivos de leitura")}</span></div>
          </div>

          {preview.summary.warnings.length > 0 ? (
            <p className="reading-warning-summary">
              <AlertTriangle size={14} aria-hidden="true" />
              {preview.summary.warnings.length} {tx("avisos para revisar.")}
              {preview.books.some((book) => book.warnings.includes("progress_conflict"))
                ? language === "en" ? ` Conflicting progress in ${preview.books.filter((book) => book.warnings.includes("progress_conflict")).length} book(s).` : ` Há progresso divergente em ${preview.books.filter((book) => book.warnings.includes("progress_conflict")).length} livro(s).`
                : ""}
              {preview.unmatched_sidecars.length > 0
                ? language === "en" ? ` ${preview.unmatched_sidecars.length} reading file(s) have no exact link.` : ` ${preview.unmatched_sidecars.length} arquivo(s) de leitura estão sem vínculo exato.`
                : ""}
            </p>
          ) : null}

          <UnmatchedSidecarReview sidecars={preview.unmatched_sidecars} language={language} />

          {preview.batch.status === "preview" ? (
            <div className="reading-confirm-box">
              <p>{tx("Esta etapa grava os eventos no banco local do aplicativo. Não atualiza Hardcover nem Calibre.")}</p>
              <UiButton className="button-primary" type="button" onClick={() => void confirmLocalImport()} disabled={busy !== null}>
                {tx(busy === "confirm" ? "Importando…" : "Confirmar importação local")}
              </UiButton>
            </div>
          ) : (
            <p className="reading-imported-label"><Check size={14} aria-hidden="true" /> {language === "en" ? `Imported on this computer at ${formatDate(preview.batch.completed_at, language)}.` : `Importado neste computador em ${formatDate(preview.batch.completed_at, language)}.`}</p>
          )}

          <div className="reading-section-heading">
            <div>
              <span className="eyebrow">{tx("VÍNCULOS DE EDIÇÃO")}</span>
              <h3>{tx("Associe cada livro à edição exata do Calibre")}</h3>
            </div>
            <span className="reading-count">{filteredBooks.matching.length} livros</span>
          </div>
          <label className="reading-search-label" htmlFor="reading-book-search">{tx("Filtrar títulos e autores")}</label>
          <UiInput
            id="reading-book-search"
            type="search"
            value={bookQuery}
            onChange={(event) => {
              setBookQuery(event.currentTarget.value);
              setBookPage(0);
            }}
            placeholder={tx("Buscar no histórico do KOReader")}
            aria-label={tx("Buscar no histórico do KOReader")}
          />
          <div className="reading-book-list">
            {visibleBooks.map((book) => (
              <ReadingBookCard
                language={language}
                key={book.koreader_book_id}
                book={book}
                calibreBooks={books}
                selectedBookId={selectedBookIds[book.koreader_book_id] ?? ""}
                mappedBookId={mappedBookIds[book.koreader_book_id]}
                disabled={busy !== null || preview.batch.status !== "applied"}
                onSelect={(value) => setSelectedBookIds((current) => ({
                  ...current,
                  [book.koreader_book_id]: value,
                }))}
                onMap={() => void mapBook(book)}
              />
            ))}
            {visibleBooks.length === 0 ? <p className="reading-muted">{tx("Nenhum livro corresponde à busca.")}</p> : null}
          </div>
          {filteredBooks.matching.length > bookPageSize ? (
            <div className="reading-pagination" aria-label={tx("Paginação dos livros do histórico")}>
              <span>{language === "en" ? `${(bookPage * bookPageSize + 1).toLocaleString(getUiLocale(language))}–${Math.min((bookPage + 1) * bookPageSize, filteredBooks.matching.length).toLocaleString(getUiLocale(language))} of ${filteredBooks.matching.length.toLocaleString(getUiLocale(language))}` : `${bookPage * bookPageSize + 1}–${Math.min((bookPage + 1) * bookPageSize, filteredBooks.matching.length)} de ${filteredBooks.matching.length}`}</span>
              <div>
                <UiButton className="button-secondary" type="button" onClick={() => setBookPage((page) => Math.max(0, page - 1))} disabled={bookPage === 0} aria-label={tx("Página anterior dos livros do histórico")}>
                  <ChevronLeft size={15} aria-hidden="true" />
                </UiButton>
                <span>{language === "en" ? `Page ${bookPage + 1} of ${filteredBooks.pages}` : `Página ${bookPage + 1} de ${filteredBooks.pages}`}</span>
                <UiButton className="button-secondary" type="button" onClick={() => setBookPage((page) => Math.min(filteredBooks.pages - 1, page + 1))} disabled={bookPage + 1 >= filteredBooks.pages} aria-label={tx("Próxima página dos livros do histórico")}>
                  <ChevronRight size={15} aria-hidden="true" />
                </UiButton>
              </div>
            </div>
          ) : null}

          {preview.batch.status === "applied" ? (
            <div className="reading-projection-action">
              <div>
                <strong>{tx("Campos de leitura no Calibre")}</strong>
                <span>{tx("Inclui progresso, status, posição, avaliação, notas e anotações em colunas KOReader configuradas.")}</span>
              </div>
              <UiButton className="button-secondary" type="button" onClick={() => void prepareProjection()} disabled={busy !== null}>
                {tx(busy === "projection" ? "Preparando…" : "Preparar prévia para o Calibre")}
              </UiButton>
            </div>
          ) : null}

          {projection ? (
            <ProjectionReview
              language={language}
              projection={projection}
              busy={busy === "apply"}
              confirming={confirmingProjection}
              applied={projectionApplied}
              onReview={() => setConfirmingProjection(true)}
              onCancel={() => setConfirmingProjection(false)}
              onApply={() => void applyProjection()}
            />
          ) : null}

          {preview.batch.status === "applied" ? (
            <details className="reading-events-details">
              <summary>{tx("Eventos de página e sessões calculadas")}</summary>
              <div className="reading-events-content">
                <p>{tx("As sessões são estimadas agrupando eventos separados por menos de 30 minutos.")}</p>
                <div className="reading-session-list">
                  {preview.estimated_sessions.slice(0, 5).map((session) => (
                    <div className="reading-session-item" key={session.session_id}>
                      <strong>{session.book_title}</strong>
                      <span>{formatDate(session.started_at, language)} · {session.page_event_count} {tx("registros")} · {formatDuration(session.duration_seconds, language)} · {tx("páginas")} {session.first_page}–{session.last_page}</span>
                      <small>{tx("Estimativa, não uma sessão registrada pelo KOReader.")}</small>
                    </div>
                  ))}
                  {preview.summary.estimated_session_count > 5 ? (
                    <span className="reading-muted">
                      {language === "en" ? `Showing 5 of the ${preview.summary.estimated_session_count} most recent estimated sessions.` : `Mostrando 5 das ${preview.summary.estimated_session_count} sessões estimadas mais recentes.`}
                    </span>
                  ) : null}
                </div>
                {events ? (
                  <>
                    <div className="reading-section-heading reading-events-heading">
                      <h3>{tx("Eventos de página importados")}</h3>
                      <span>{language === "en" ? `${events.events.length} on this page` : `${events.events.length} nesta página`}</span>
                    </div>
                    <div className="reading-events-list">
                      {events.events.map((event) => {
                        const bookId = event.payload.koreader_book_id;
                        const eventBook = typeof bookId === "number"
                          ? preview.books.find((book) => book.koreader_book_id === bookId)
                          : undefined;
                        const page = event.payload.page;
                        const totalPages = event.payload.total_pages;
                        const duration = event.payload.duration_seconds;
                        return (
                          <div className="reading-event-item" key={event.event_id}>
                          <strong>{eventBook?.title ?? tx("Evento de leitura")}</strong>
                            <span>{formatDate(event.occurred_at, language)} · {tx("página")} {String(page)}{typeof totalPages === "number" ? ` ${tx("de")} ${totalPages}` : ""}{typeof duration === "number" ? ` · ${formatDuration(duration, language)}` : ""}</span>
                          </div>
                        );
                      })}
                      {events.events.length === 0 ? <span className="reading-muted">{tx("Nenhum evento de página importado ainda.")}</span> : null}
                    </div>
                    <div className="reading-pagination">
                      <span>{tx("Eventos de página")} · {tx("lote de")} {eventPageSize}</span>
                      <div>
                        <UiButton className="button-secondary" type="button" onClick={() => void loadEvents(Math.max(0, eventOffset - eventPageSize))} disabled={eventOffset === 0 || busy !== null} aria-label={tx("Eventos anteriores")}>
                          <ChevronLeft size={15} aria-hidden="true" />
                        </UiButton>
                          <span>{events.events.length > 0
                            ? `${tx("Registros")} ${eventOffset + 1}–${eventOffset + events.events.length}`
                            : tx("Sem eventos nesta página")}</span>
                        <UiButton className="button-secondary" type="button" onClick={() => void loadEvents(eventOffset + eventPageSize)} disabled={!events.has_more || busy !== null} aria-label={tx("Próximos eventos")}>
                          <ChevronRight size={15} aria-hidden="true" />
                        </UiButton>
                      </div>
                    </div>
                  </>
                ) : null}
              </div>
            </details>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
