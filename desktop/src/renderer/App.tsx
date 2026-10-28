import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  BookOpenCheck,
  Check,
  CircleHelp,
  FolderOpen,
  HardDrive,
  KeyRound,
  LoaderCircle,
  RotateCcw,
  RefreshCw,
  Settings2,
  ShieldCheck,
  TabletSmartphone,
} from "lucide-react";
import type {
  CalibreEditChanges,
  CalibreImportBatch,
  CalibreBook,
  BackupCreateResult,
  BackupAutomationPreferences,
  BackupAutomationStatus,
  BackupRestoreResult,
  BackupRestoreTarget,
  BackupSnapshot,
  HardcoverBook,
  HardcoverBookPage,
  HardcoverDataSource,
  HardcoverManualReviewItem,
  HardcoverReconciliationResult,
  HardcoverReviewResolution,
  HardcoverReviewResolutionResult,
  HardcoverStatus,
  ProfileRecoveryPreview,
  ProfileRecoveryResult,
  KindleFormat,
  KindleStatus,
  KindleTransfer,
  ServiceStatus,
} from "../shared/contracts";
import { LibraryPage, type HardcoverUiStatus, type LibraryStatus } from "./LibraryPage";
import { ReadingHistoryPanel } from "./ReadingHistoryPanel";
import { UiButton, UiInput, UiSelect } from "./ui/controls";
import { getUiLocale, loadLanguagePreference, localizeText, resolveInitialLanguage, saveLanguagePreference, translate, type TranslationKey, type UiLanguage } from "./i18n";
import { StableDeviceBackupTrigger } from "./backup-automation-trigger";

type AppPage = "library" | "kindle" | "settings";

const navigation: Array<{
  id: AppPage;
  labelKey: TranslationKey;
  compactLabelKey: TranslationKey;
  icon: typeof BookOpen;
}> = [
  { id: "library", labelKey: "nav.library", compactLabelKey: "nav.library", icon: BookOpen },
  {
    id: "kindle",
    labelKey: "nav.sync",
    compactLabelKey: "nav.sync.compact",
    icon: TabletSmartphone,
  },
  { id: "settings", labelKey: "nav.settings", compactLabelKey: "nav.settings.compact", icon: Settings2 },
];

export function App() {
  const [language, setLanguage] = useState<UiLanguage>(() => {
    try {
      return loadLanguagePreference(window.localStorage, navigator.language);
    } catch {
      return resolveInitialLanguage(null, navigator.language);
    }
  });
  const changeLanguage = (nextLanguage: UiLanguage) => {
    setLanguage(nextLanguage);
    try {
      saveLanguagePreference(window.localStorage, nextLanguage);
    } catch {
      // The selection still applies for this session if local storage is unavailable.
    }
  };
  const [page, setPage] = useState<AppPage>("library");
  const [books, setBooks] = useState<CalibreBook[]>([]);
  const [libraryStatus, setLibraryStatus] = useState<LibraryStatus>("loading");
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus | null>(null);
  const [hardcoverBooks, setHardcoverBooks] = useState<HardcoverBook[]>([]);
  const [currentReading, setCurrentReading] = useState<HardcoverBook | null>(null);
  const [hardcoverStatus, setHardcoverStatus] = useState<HardcoverUiStatus>("checking");
  const [hardcoverUsage, setHardcoverUsage] = useState<HardcoverStatus | null>(null);
  const [hardcoverErrorCode, setHardcoverErrorCode] = useState<string | null>(null);
  const [hardcoverCheckedAt, setHardcoverCheckedAt] = useState<string | null>(null);
  const [hardcoverSource, setHardcoverSource] = useState<HardcoverDataSource | null>(null);
  const [kindleStatus, setKindleStatus] = useState<KindleStatus | null>(null);
  const [kindleCheckedAt, setKindleCheckedAt] = useState<string | null>(null);
  const [kindleChecking, setKindleChecking] = useState(false);
  const [backupAutomation, setBackupAutomation] = useState<BackupAutomationStatus | null>(null);
  const [autoBackupNotice, setAutoBackupNotice] = useState<"running" | "success" | "failed" | null>(null);
  const initialLoadStarted = useRef(false);
  const kindleStatusRequest = useRef<Promise<void> | null>(null);
  const kindleActionRunning = useRef(false);
  const automaticBackupRunning = useRef(false);
  const deviceBackupTrigger = useRef(new StableDeviceBackupTrigger());

  const refreshBackupAutomation = useCallback(async () => {
    try {
      setBackupAutomation(await window.reading.backup.automationStatus());
    } catch {
      // Keep the last known automation state if local status cannot be read.
    }
  }, []);

  const configureBackupAutomation = useCallback(async (
    preferences: BackupAutomationPreferences,
  ) => {
    const updated = await window.reading.backup.configureAutomation(preferences);
    setBackupAutomation(updated);
    setAutoBackupNotice(null);
    return updated;
  }, []);

  const runAutomaticDeviceBackup = useCallback(async () => {
    if (automaticBackupRunning.current) return;
    automaticBackupRunning.current = true;
    kindleActionRunning.current = true;
    setAutoBackupNotice("running");
    try {
      await window.reading.backup.create(false, false);
      setAutoBackupNotice("success");
    } catch {
      setAutoBackupNotice("failed");
    } finally {
      automaticBackupRunning.current = false;
      kindleActionRunning.current = false;
      await refreshBackupAutomation();
    }
  }, [refreshBackupAutomation]);

  const loadHardcover = useCallback(async () => {
    setHardcoverStatus("checking");
    setHardcoverErrorCode(null);
    try {
      const status = await window.reading.hardcover.status();
      setHardcoverUsage(status);
      if (!status.configured) {
        setHardcoverBooks([]);
        setCurrentReading(null);
        setHardcoverSource(null);
        setHardcoverCheckedAt(null);
        setHardcoverStatus("unconfigured");
        return;
      }

      setHardcoverStatus("loading");
      const result = await loadAllHardcoverBooks();
      setCurrentReading(result.books.find((book) => book.status_id === 2) ?? null);
      setHardcoverBooks(result.books);
      setHardcoverSource(result.source);
      setHardcoverCheckedAt(result.fetched_at);
      setHardcoverStatus("ready");
    } catch (error) {
      setHardcoverErrorCode(error instanceof Error ? error.message : "hardcover_unavailable");
      setHardcoverStatus("error");
    } finally {
      try {
        setHardcoverUsage(await window.reading.hardcover.status());
      } catch {
        // Keep the most recent local usage snapshot if the backend status read fails.
      }
    }
  }, []);

  const loadLibrary = useCallback(async () => {
    setLibraryStatus("loading");
    void loadHardcover();
    const [serviceResult, catalogResult] = await Promise.allSettled([
      window.reading.app.status(),
      window.reading.calibre.list(),
    ]);
    setServiceStatus(serviceResult.status === "fulfilled" ? serviceResult.value : null);
    if (catalogResult.status === "rejected") {
      setBooks([]);
      setLibraryStatus("error");
      return;
    }
    setBooks(catalogResult.value.books);
    setLibraryStatus("ready");
  }, [loadHardcover]);

  const configureHardcover = useCallback(async (token: string) => {
    try {
      await window.reading.hardcover.configure(token);
    } catch (error) {
      setHardcoverErrorCode(error instanceof Error ? error.message : "hardcover_unavailable");
      try {
        setHardcoverUsage(await window.reading.hardcover.status());
      } catch {
        // Keep the last usage snapshot if the local status endpoint is unavailable.
      }
      throw error;
    }
    await loadHardcover();
  }, [loadHardcover]);

  const reconcileHardcover = useCallback(async (): Promise<HardcoverReconciliationResult> => {
    const result = await window.reading.hardcover.reconcile();
    if (result.completed > 0) {
      try {
        const reading = await window.reading.hardcover.currentReading();
        setCurrentReading(reading.book);
        setHardcoverSource(reading.source);
        setHardcoverCheckedAt(reading.fetched_at);
      } catch {
        // Keep the last current-reading card if its refresh is unavailable.
      }
    }
    try {
      setHardcoverUsage(await window.reading.hardcover.status());
    } catch {
      // Keep the last local queue summary if status refresh is unavailable.
    }
    return result;
  }, []);

  const resolveHardcoverReview = useCallback(async (
    operationId: string,
    resolution: HardcoverReviewResolution,
  ): Promise<HardcoverReviewResolutionResult> => {
    const result = await window.reading.hardcover.resolveReview(operationId, resolution);
    try {
      setHardcoverUsage(await window.reading.hardcover.status());
    } catch {
      // A local review is complete even if the local counter cannot be refreshed.
    }
    return result;
  }, []);

  const configureResticPassword = useCallback(async (password: string) => {
    await window.reading.backup.configurePassword(password);
  }, []);

  const refreshCalibre = useCallback(async () => {
    setLibraryStatus("loading");
    try {
      const catalog = await window.reading.calibre.list();
      setBooks(catalog.books);
      setLibraryStatus("ready");
    } catch {
      setBooks([]);
      setLibraryStatus("error");
    }
  }, []);

  const refreshKindle = useCallback((): Promise<void> => {
    if (kindleActionRunning.current) return Promise.resolve();
    if (kindleStatusRequest.current) return kindleStatusRequest.current;

    setKindleChecking(true);
    const request = window.reading.kindle.status()
      .then((status) => {
        setKindleStatus(status);
        if (
          deviceBackupTrigger.current.observe(
            status.connected,
            backupAutomation?.backup_on_kindle_connect ?? null,
          )
        ) {
          void runAutomaticDeviceBackup();
        }
      })
      .catch(() => setKindleStatus({ connected: false, reason_code: "inventory_failed" }))
      .finally(() => {
        setKindleCheckedAt(new Date().toISOString());
        setKindleChecking(false);
        if (kindleStatusRequest.current === request) kindleStatusRequest.current = null;
      });
    kindleStatusRequest.current = request;
    return request;
  }, [backupAutomation?.backup_on_kindle_connect, runAutomaticDeviceBackup]);

  const selectImport = useCallback((): Promise<CalibreImportBatch | null> =>
    window.reading.calibre.selectImport(), []);

  const addImport = useCallback(async (
    previewId: string,
    fileIndex: number,
    mode: "new" | "attach",
    targetBookId?: number,
  ) => {
    const updated = await window.reading.calibre.addImport(previewId, fileIndex, mode, targetBookId);
    await refreshCalibre();
    return updated;
  }, [refreshCalibre]);

  const selectCover = useCallback(
    (bookId: number) => window.reading.calibre.selectCover(bookId),
    [],
  );

  const editBook = useCallback(async (
    bookId: number,
    changes: CalibreEditChanges,
    coverSelectionId?: string,
  ) => {
    const updated = await window.reading.calibre.edit(bookId, changes, coverSelectionId);
    setBooks((current) => current.map((book) => book.book_id === updated.book_id ? updated : book));
    return updated;
  }, []);

  const editTagsBatch = useCallback(async (
    bookIds: number[],
    addTags: string[],
    removeTags: string[],
  ) => {
    const updated = await window.reading.calibre.editTagsBatch(bookIds, addTags, removeTags);
    setBooks((current) => current.map((book) =>
      updated.find((candidate) => candidate.book_id === book.book_id) ?? book,
    ));
    return updated;
  }, []);

  const removeBook = useCallback(async (bookId: number) => {
    await window.reading.calibre.remove(bookId);
    await refreshCalibre();
  }, [refreshCalibre]);

  const sendBook = useCallback(async (
    bookId: number,
    format: KindleFormat,
    filename: string,
  ): Promise<KindleTransfer> => {
    await refreshKindle();
    kindleActionRunning.current = true;
    try {
      return await window.reading.kindle.send(bookId, format, filename);
    } finally {
      kindleActionRunning.current = false;
      void refreshKindle();
    }
  }, [refreshKindle]);

  useEffect(() => {
    if (initialLoadStarted.current) return;
    initialLoadStarted.current = true;
    void loadLibrary();
    void refreshKindle();
    void refreshBackupAutomation();
  }, [loadLibrary, refreshBackupAutomation, refreshKindle]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshKindle();
      void refreshBackupAutomation();
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [refreshBackupAutomation, refreshKindle]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <a
          className="brand"
          href="#biblioteca"
          aria-label={`KOReader Ecosystem, ${language === "en" ? "open Library" : "abrir Biblioteca"}`}
          onClick={(event) => {
            event.preventDefault();
            setPage("library");
          }}
        >
          <span className="brand-mark">
            <BookOpen size={21} strokeWidth={2.2} />
          </span>
          <span className="brand-name">KOReader <b>Ecosystem</b></span>
        </a>

        <nav className="primary-navigation" aria-label={translate(language, "nav.aria")}>
          {navigation.map(({ id, labelKey, compactLabelKey, icon: Icon }) => (
            <UiButton
              className={`navigation-item${page === id ? " is-active" : ""}`}
              key={id}
              type="button"
              aria-current={page === id ? "page" : undefined}
              aria-label={translate(language, labelKey)}
              onClick={() => setPage(id)}
            >
              <Icon size={17} strokeWidth={1.9} aria-hidden="true" />
              <span className="navigation-label-full" aria-hidden="true">
                {translate(language, labelKey)}
              </span>
              <span className="navigation-label-compact" aria-hidden="true">
                {translate(language, compactLabelKey)}
              </span>
            </UiButton>
          ))}
        </nav>

        <div className={`connection-state${libraryStatus === "ready" ? " is-ready" : ""}`}>
          {libraryStatus === "ready" ? <Check size={14} aria-hidden="true" /> : <CircleHelp size={14} aria-hidden="true" />}
          <span>{translate(language, libraryStatus === "ready" ? "connection.local" : libraryStatus === "loading" ? "connection.connecting" : "connection.offline")}</span>
        </div>
      </header>

      <div className="workspace">
        {page === "library" ? (
          <LibraryPage
            language={language}
            books={books}
            status={libraryStatus}
            hardcoverBooks={hardcoverBooks}
            hardcoverCurrent={currentReading}
            hardcoverStatus={hardcoverStatus}
            hardcoverSource={hardcoverSource}
            hardcoverFetchedAt={hardcoverCheckedAt}
            onSelectImport={selectImport}
            onAddImport={addImport}
            onSelectCover={selectCover}
            onEditBook={editBook}
            onEditTagsBatch={editTagsBatch}
            onRemoveBook={removeBook}
            onSendBook={sendBook}
            platformCsv={window.reading.platformCsv}
            onRetry={() => void loadLibrary()}
            onRetryHardcover={() => void loadHardcover()}
          />
        ) : null}
        {page === "kindle" ? (
          <KindlePage
            language={language}
            calibreBooks={books}
            status={kindleStatus}
            checkedAt={kindleCheckedAt}
            checking={kindleChecking}
            hardcoverStatus={hardcoverStatus}
            hardcoverCheckedAt={hardcoverCheckedAt}
            hardcoverSource={hardcoverSource}
            deviceConnected={kindleStatus?.connected === true}
            onRefresh={() => void refreshKindle()}
          />
        ) : null}
        {page === "settings" ? (
          <SettingsPage
            status={libraryStatus}
            service={serviceStatus}
            hardcoverStatus={hardcoverStatus}
            hardcoverUsage={hardcoverUsage}
            hardcoverSource={hardcoverSource}
            hardcoverFetchedAt={hardcoverCheckedAt}
            hardcoverErrorCode={hardcoverErrorCode}
            language={language}
            onLanguageChange={changeLanguage}
            onConfigureHardcover={configureHardcover}
            onReconcileHardcover={reconcileHardcover}
            onResolveHardcoverReview={resolveHardcoverReview}
            onConfigureResticPassword={configureResticPassword}
            backupAutomation={backupAutomation}
            autoBackupNotice={autoBackupNotice}
            onConfigureBackupAutomation={configureBackupAutomation}
            onRefreshBackupAutomation={refreshBackupAutomation}
          />
        ) : null}
      </div>

      <footer className="app-footer">
        <span><span className="footer-dot" aria-hidden="true" /> {translate(language, "footer.localData")}</span>
        <span>KOReader Ecosystem <b>·</b> {__APP_VERSION__}</span>
      </footer>
    </main>
  );
}

async function loadAllHardcoverBooks(): Promise<{
  books: HardcoverBook[];
  source: HardcoverDataSource;
  fetched_at: string;
}> {
  const books: HardcoverBook[] = [];
  const pages: HardcoverBookPage[] = [];
  let offset = 0;
  for (let page = 0; page < 1000; page += 1) {
    const result = await retryTransientHardcoverRequest(() => window.reading.hardcover.list(100, offset));
    pages.push(result);
    books.push(...result.books);
    if (!result.has_more || result.books.length === 0) {
      const fetchedAt = pages
        .map((entry) => entry.fetched_at)
        .sort((left, right) => Date.parse(left) - Date.parse(right))[0];
      return {
        books,
        source: pages.some((entry) => entry.source === "cache") ? "cache" : "live",
        fetched_at: fetchedAt,
      };
    }
    offset += result.books.length;
  }
  throw new Error("hardcover_page_limit");
}

async function retryTransientHardcoverRequest<T>(request: () => Promise<T>): Promise<T> {
  const retryDelays = [450, 1_200];
  for (let attempt = 0; attempt <= retryDelays.length; attempt += 1) {
    try {
      return await request();
    } catch (error) {
      const retryable = error instanceof Error && error.message === "hardcover_unavailable";
      if (!retryable || attempt === retryDelays.length) throw error;
      await new Promise<void>((resolve) => window.setTimeout(resolve, retryDelays[attempt]));
    }
  }
  throw new Error("hardcover_unavailable");
}

function formatKindleCheckTime(value: string | null, language: UiLanguage = "pt-BR"): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? null
    : new Intl.DateTimeFormat(getUiLocale(language), { dateStyle: "short", timeStyle: "short" }).format(date);
}

function KindlePage({
  language,
  calibreBooks,
  status,
  checkedAt,
  checking,
  hardcoverStatus,
  hardcoverCheckedAt,
  hardcoverSource,
  deviceConnected,
  onRefresh,
}: {
  language: UiLanguage;
  calibreBooks: CalibreBook[];
  status: KindleStatus | null;
  checkedAt: string | null;
  checking: boolean;
  hardcoverStatus: HardcoverUiStatus;
  hardcoverCheckedAt: string | null;
  hardcoverSource: HardcoverDataSource | null;
  deviceConnected: boolean;
  onRefresh(): void;
}) {
  const tx = (text: string) => localizeText(language, text);
  const connected = status?.connected === true;
  const checkedTime = formatKindleCheckTime(checkedAt, language);
  const hardcoverTime = formatKindleCheckTime(hardcoverCheckedAt, language);
  const hardcoverSummary = hardcoverStatus === "unconfigured"
    ? tx("Conecte sua conta em Configuração para consultar as estantes.")
    : hardcoverStatus === "checking" || hardcoverStatus === "loading"
      ? language === "en" ? `Checking your shelves…${hardcoverTime ? ` Last successful check in this session: ${hardcoverTime}.` : ""}` : `Consultando as estantes…${hardcoverTime ? ` Última consulta confirmada nesta sessão: ${hardcoverTime}.` : ""}`
      : hardcoverStatus === "error"
        ? hardcoverTime
          ? language === "en" ? `The check failed. Last successful check in this session: ${hardcoverTime}.` : `A consulta falhou. Última consulta confirmada nesta sessão: ${hardcoverTime}.`
          : tx("A consulta falhou e ainda não houve uma consulta bem-sucedida nesta sessão.")
        : hardcoverSource === "cache"
          ? language === "en" ? `Hardcover is unavailable. Showing local data saved at ${hardcoverTime ?? "an unknown time"}; it may be out of date.` : `A consulta ao Hardcover não está disponível. Mostrando dados locais salvos em ${hardcoverTime ?? "uma data desconhecida"}; podem estar desatualizados.`
          : hardcoverTime
          ? `${tx("Dados consultados em")} ${hardcoverTime}.`
          : tx("As estantes ainda não foram consultadas nesta sessão.");
  const connectionMessage = !status
    ? tx("Aguardando a primeira verificação automática.")
    : connected
      ? tx("O Kindle está visível por USB/MTP. Antes de enviar, o app também verifica o backup e o arquivo escolhido.")
      : status.reason_code === "multiple_devices"
        ? tx("Mais de um dispositivo Kindle foi encontrado. Deixe apenas o aparelho desejado conectado.")
        : status.reason_code === "storage_not_found" || status.reason_code === "multiple_storages"
          ? tx("O armazenamento interno ainda não está disponível. Deixe o Kindle na tela USB conectado.")
          : status.reason_code === "device_not_found" || status.reason_code === "kindle_unavailable"
            ? tx("Conecte o Kindle por USB e mantenha a tela USB conectado aberta.")
            : tx("O Windows não conseguiu consultar o Kindle. A aplicação tentará de novo automaticamente.");
  return (
    <section className="future-page" aria-labelledby="kindle-title">
      <div className="page-heading">
        <div className="page-heading-copy">
          <span className="eyebrow">{tx("DISPOSITIVO E LEITURA")}</span>
          <h1 id="kindle-title">{language === "en" ? "KOReader & sync" : "Kindle e sincronização"}</h1>
          <p>{tx("Uma visão clara do caminho entre a biblioteca, a leitura e o Hardcover.")}</p>
        </div>
        <span className={`pending-pill${connected ? " device-connected" : ""}`}><span /> {tx(connected ? "Kindle conectado" : "Kindle desconectado")}</span>
      </div>

      <div className="device-overview">
        <div className="device-icon"><TabletSmartphone size={25} aria-hidden="true" /></div>
        <div className="device-overview-copy">
          <span className="eyebrow">{tx("INTEGRAÇÃO KINDLE")}</span>
          <h2>{tx(connected ? "Kindle conectado por USB" : "Kindle não detectado")}</h2>
          <p>{connectionMessage}</p>
          <small className="device-last-checked" role="status" aria-live="polite">
            {checking
              ? tx("Verificando conexão…")
              : checkedTime
                ? language === "en" ? `Last checked at ${checkedTime} · automatically refreshes every 15 seconds.` : `Última verificação às ${checkedTime} · atualização automática a cada 15 segundos.`
                : tx("A conexão é verificada automaticamente a cada 15 segundos.")}
          </small>
        </div>
        <UiButton className="button-secondary kindle-refresh-button" type="button" onClick={onRefresh} disabled={checking}>
          <RefreshCw size={14} className={checking ? "spin" : undefined} aria-hidden="true" />
          {checking ? tx("Verificando…") : tx("Verificar agora")}
        </UiButton>
      </div>

      <div className="next-steps device-guidance">
        <article className="next-step">
          <span className="step-number">01</span>
          <div><h3>{tx("Envio protegido")}</h3><p>{tx("Exporta do Calibre, salva em documents/Books e nunca substitui um arquivo existente.")}</p></div>
          <span className="step-state">{tx("Backup + SHA-256")}</span>
        </article>
        <article className="next-step">
          <span className="step-number">02</span>
          <div><h3>{tx("Leitura no KOReader")}</h3><p>{tx("Abra os arquivos enviados no KOReader. A sincronização de progresso continua pelo plugin Hardcover instalado no Kindle.")}</p></div>
          <span className="step-state">{tx("Wi-Fi para sync")}</span>
        </article>
        <article className="next-step">
          <span className="step-number">03</span>
          <div><h3>{tx("Estantes Hardcover")}</h3><p>{hardcoverSummary} {language === "en" ? "Reading progress is sent from the Kindle by KOReader over Wi-Fi; this check only reports requests made by the app." : "O progresso de leitura é enviado do Kindle pelo KOReader via Wi-Fi; esta checagem acompanha apenas a consulta feita pelo aplicativo."}</p></div>
          <span className="step-state">{tx(hardcoverStatus === "ready" ? "Consulta concluída" : hardcoverStatus === "error" ? "Consulta com falha" : hardcoverStatus === "unconfigured" ? "Sem conexão" : "Atualizando")}</span>
        </article>
      </div>

      <ReadingHistoryPanel language={language} books={calibreBooks} deviceConnected={connected} />

      <BackupPanel language={language} deviceConnected={deviceConnected} />
    </section>
  );
}

function BackupPanel({ language, deviceConnected }: { language: UiLanguage; deviceConnected: boolean }) {
  const tx = (text: string) => localizeText(language, text);
  const [snapshots, setSnapshots] = useState<BackupSnapshot[]>([]);
  const [snapshotsState, setSnapshotsState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [selectedSnapshotId, setSelectedSnapshotId] = useState("");
  const [restoreTarget, setRestoreTarget] = useState<BackupRestoreTarget | null>(null);
  const [includeKindle, setIncludeKindle] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmingRestore, setConfirmingRestore] = useState(false);
  const [profilePreview, setProfilePreview] = useState<ProfileRecoveryPreview | null>(null);
  const [profileApplyResult, setProfileApplyResult] = useState<ProfileRecoveryResult | null>(null);
  const [confirmingProfileRecovery, setConfirmingProfileRecovery] = useState(false);
  const [profileBusy, setProfileBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("Consultando os snapshots locais…");
  const [statusKind, setStatusKind] = useState<"info" | "success" | "error">("info");

  const refreshSnapshots = useCallback(async () => {
    setSnapshotsState("loading");
    try {
      const result = await window.reading.backup.list();
      const newestFirst = [...result].sort(
        (left, right) => new Date(right.time).getTime() - new Date(left.time).getTime(),
      );
      setSnapshots(newestFirst);
      setSnapshotsState(newestFirst.length > 0 ? "ready" : "empty");
      setSelectedSnapshotId((current) =>
        newestFirst.some((snapshot) => snapshot.id === current) ? current : (newestFirst[0]?.id ?? ""),
      );
      setStatusMessage(
        newestFirst.length > 0
          ? language === "en" ? `${newestFirst.length} encrypted snapshots available on this computer.` : newestFirst.length + " snapshots criptografados disponíveis neste computador."
          : language === "en" ? "There are no snapshots in this local repository yet." : "Ainda não há snapshots neste repositório local.",
      );
      setStatusKind("info");
    } catch (error) {
      setSnapshots([]);
      setSnapshotsState("error");
      setStatusMessage(formatBackupError(error, language));
      setStatusKind("error");
    }
  }, [language]);

  useEffect(() => {
    void refreshSnapshots();
  }, [refreshSnapshots]);

  const createBackup = async () => {
    if (busy) return;
    setBusy(true);
    setStatusMessage("Verificando o Calibre e criando o snapshot criptografado…");
    setStatusKind("info");
    try {
      const result = await window.reading.backup.create(!includeKindle, true);
      setStatusMessage(formatBackupResult(result, language));
      setStatusKind("success");
      setRestoreTarget(null);
      setConfirmingRestore(false);
      await refreshSnapshots();
      setStatusKind("success");
      setStatusMessage(formatBackupResult(result, language));
    } catch (error) {
      setStatusMessage(formatBackupError(error, language));
      setStatusKind("error");
    } finally {
      setBusy(false);
    }
  };

  const chooseRestoreTarget = async () => {
    if (busy) return;
    try {
      const target = await window.reading.backup.selectRestoreTarget();
      if (!target) return;
      setRestoreTarget(target);
      setConfirmingRestore(false);
      setStatusMessage(language === "en" ? `Selected destination: ${target.folder_name} in ${target.parent_name}.` : "Destino escolhido: " + target.folder_name + " dentro de " + target.parent_name + ".");
      setStatusKind("info");
    } catch (error) {
      setStatusMessage(formatBackupError(error, language));
      setStatusKind("error");
    }
  };

  const restoreSnapshot = async () => {
    if (busy || !restoreTarget || !selectedSnapshotId) return;
    setBusy(true);
    setConfirmingRestore(false);
    setStatusMessage("Restaurando e comparando os arquivos do snapshot…");
    setStatusKind("info");
    try {
      const result = await window.reading.backup.restore(selectedSnapshotId, restoreTarget.target_id);
      setStatusMessage(formatRestoreResult(result, language));
      setStatusKind("success");
      setRestoreTarget(null);
      setProfilePreview(null);
      setProfileApplyResult(null);
      if (result.profile_source_id) {
        setProfileBusy(true);
        try {
          const preview = await window.reading.profileRecovery.preview(result.profile_source_id);
          setProfilePreview(preview);
          setStatusMessage(tx("Restauração verificada. Os dados locais incluídos podem ser revisados para recuperar este perfil."));
        } finally {
          setProfileBusy(false);
        }
      }
    } catch (error) {
      setStatusMessage(formatBackupError(error, language));
      setStatusKind("error");
      setRestoreTarget(null);
    } finally {
      setBusy(false);
    }
  };

  const selectProfileRestore = async () => {
    if (profileBusy || busy) return;
    setProfileBusy(true);
    setStatusMessage("Selecione a pasta criada por uma restauração verificada…");
    setStatusKind("info");
    try {
      const source = await window.reading.profileRecovery.selectSource();
      if (!source) return;
      const preview = await window.reading.profileRecovery.preview(source.source_id);
      setProfilePreview(preview);
      setProfileApplyResult(null);
      setConfirmingProfileRecovery(false);
      setStatusMessage(tx("Perfil encontrado. Revise os dados antes de confirmar a recuperação."));
      setStatusKind("success");
    } catch (error) {
      setStatusMessage(formatBackupError(error, language));
      setStatusKind("error");
    } finally {
      setProfileBusy(false);
    }
  };

  const applyProfileRestore = async () => {
    if (!profilePreview || profileBusy || busy) return;
    setProfileBusy(true);
    setConfirmingProfileRecovery(false);
    try {
      const result = await window.reading.profileRecovery.apply(profilePreview.preview_id);
      setProfileApplyResult(result);
      setStatusMessage(tx("Perfil recuperado. O checkpoint anterior está guardado para rollback; reinicie para carregar os dados."));
      setStatusKind("success");
    } catch (error) {
      setStatusMessage(formatBackupError(error, language));
      setStatusKind("error");
    } finally {
      setProfileBusy(false);
    }
  };

  const restartAfterProfileRestore = async () => {
    try {
      await window.reading.profileRecovery.restart();
    } catch {
      setStatusMessage(tx("Não foi possível reiniciar automaticamente. Feche e abra o aplicativo para aplicar o perfil."));
      setStatusKind("error");
    }
  };

  const selectedSnapshot = snapshots.find((snapshot) => snapshot.id === selectedSnapshotId);
  return (
    <section className="backup-panel" aria-labelledby="backup-panel-title">
      <div className="backup-panel-heading">
        <div className="backup-panel-icon"><HardDrive size={18} aria-hidden="true" /></div>
        <div className="backup-panel-heading-copy">
      <span className="eyebrow">{tx("PROTEÇÃO LOCAL")}</span>
      <h2 id="backup-panel-title">{tx("Backup e restauração")}</h2>
      <p>{tx("Os snapshots são criptografados pelo Restic e ficam neste computador.")}</p>
        </div>
    <span className="backup-local-badge">{tx("Somente local")}</span>
      </div>

      <div className="backup-controls">
        <label className="backup-content-choice">
        <span>{tx("Conteúdo do backup")}</span>
          <UiSelect
            value={includeKindle ? "kindle" : "calibre"}
            onChange={(event) => setIncludeKindle(event.currentTarget.value === "kindle")}
            disabled={busy}
          >
        <option value="calibre">{tx("Calibre + dados locais do app")}</option>
        <option value="kindle" disabled={!deviceConnected}>{tx("Calibre + Kindle + dados locais do app")}</option>
          </UiSelect>
        </label>
        {!deviceConnected ? (
      <p className="backup-device-hint">{tx("O Kindle não está conectado. O backup Calibre e dos dados do app continua disponível.")}</p>
        ) : (
      <p className="backup-device-hint">{tx("O Kindle está disponível; deixe-o na tela USB para incluí-lo.")}</p>
        )}
        <UiButton className="button-primary backup-create-button" type="button" onClick={() => void createBackup()} disabled={busy}>
          {busy ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <ShieldCheck size={15} aria-hidden="true" />}
      {tx(busy ? "Aguarde…" : "Criar e verificar backup")}
        </UiButton>
      </div>
  <p className="backup-safety-note">{tx("O backup inclui configuração, histórico de operações e estado local do app. O aplicativo exige o Calibre fechado e valida metadata.db; com a verificação ativa, restaura o snapshot em uma área temporária e compara os arquivos.")}</p>

      <div className="backup-restore">
        <div className="backup-restore-heading">
          <div>
        <h3>{tx("Snapshots disponíveis")}</h3>
        <p>{tx("Restaure para uma pasta nova e revise os arquivos antes de reabrir a biblioteca.")}</p>
          </div>
          <UiButton className="button-secondary" type="button" onClick={() => void refreshSnapshots()} disabled={busy || snapshotsState === "loading"}>
            <RefreshCw size={14} className={snapshotsState === "loading" ? "spin" : undefined} aria-hidden="true" />
        {tx("Atualizar")}
          </UiButton>
        </div>
        <div className="backup-restore-controls">
          <label className="backup-content-choice">
        <span>{tx("Snapshot")}</span>
            <UiSelect
              value={selectedSnapshotId}
              onChange={(event) => { setSelectedSnapshotId(event.currentTarget.value); setConfirmingRestore(false); }}
              disabled={busy || snapshots.length === 0}
            >
              <option value="" disabled>
          {tx(snapshotsState === "loading" ? "Carregando snapshots…" : snapshotsState === "error" ? "Snapshots indisponíveis" : "Nenhum snapshot")}
              </option>
              {snapshots.map((snapshot) => (
                <option value={snapshot.id} key={snapshot.id}>
              {formatSnapshotTime(snapshot.time, language)} · {formatSnapshotScope(snapshot.scope, snapshot.includes_app_state === true, language)} · {snapshot.id.slice(0, 12)}
                </option>
              ))}
            </UiSelect>
          </label>
          <UiButton className="button-secondary backup-target-button" type="button" onClick={() => void chooseRestoreTarget()} disabled={busy}>
            <FolderOpen size={14} aria-hidden="true" />
        {tx(restoreTarget ? "Trocar destino" : "Escolher destino")}
          </UiButton>
        </div>
        {restoreTarget ? (
      <p className="backup-target-summary">{tx("Pasta nova:")} <strong>{restoreTarget.folder_name}</strong> {language === "en" ? "in" : "em"} <strong>{restoreTarget.parent_name}</strong>.</p>
        ) : null}
        {confirmingRestore ? (
          <div className="backup-restore-confirm">
        <p>{language === "en" ? `Snapshot ${selectedSnapshot?.id.slice(0, 12)} will be restored to a new folder. The active library will not be replaced.` : `O snapshot ${selectedSnapshot?.id.slice(0, 12)} será restaurado para uma pasta nova. A biblioteca ativa não será substituída.`}</p>
            <div>
          <UiButton className="button-quiet" type="button" onClick={() => setConfirmingRestore(false)} disabled={busy}>{tx("Cancelar")}</UiButton>
              <UiButton className="button-primary" type="button" onClick={() => void restoreSnapshot()} disabled={busy}>
            <RotateCcw size={14} aria-hidden="true" /> {tx("Confirmar restauração")}
              </UiButton>
            </div>
          </div>
        ) : (
          <UiButton
            className="button-secondary backup-restore-button"
            type="button"
            onClick={() => setConfirmingRestore(true)}
            disabled={busy || !selectedSnapshot || !restoreTarget}
          >
            <RotateCcw size={14} aria-hidden="true" />
          {tx("Revisar restauração")}
          </UiButton>
        )}
        <div className="profile-recovery-panel">
          <div>
        <h3>{tx("Recuperar perfil local")}</h3>
        <p>{tx("Reaplica a configuração e os dados SQLite de uma pasta restaurada e validada. Senhas e tokens continuam no Gerenciador de Credenciais do Windows; o cache Hardcover é descartado para evitar misturar contas.")}</p>
          </div>
          {!profileApplyResult ? (
            <UiButton className="button-secondary" type="button" onClick={() => void selectProfileRestore()} disabled={busy || profileBusy}>
              {profileBusy ? <LoaderCircle size={14} className="spin" aria-hidden="true" /> : <FolderOpen size={14} aria-hidden="true" />}
            {tx(profileBusy ? "Verificando perfil…" : "Escolher pasta restaurada")}
            </UiButton>
          ) : null}
          {profilePreview && !profileApplyResult ? (
            <div className="profile-recovery-preview">
              <strong>Prévia · {profilePreview.source_name}</strong>
          <span>{language === "en" ? "SQLite schema" : "Schema SQLite"} v{profilePreview.schema_version} · {formatRecoveryCounts(profilePreview.row_counts, language)}</span>
          <span>{tx(profilePreview.configured_calibre ? "Configuração Calibre presente" : "Configuração Calibre ausente")} · {tx(profilePreview.configured_restic ? "Restic configurado" : "Restic não configurado")} · {tx(profilePreview.configured_remote_backup ? "Destino remoto configurado" : "Sem destino remoto")}</span>
              {confirmingProfileRecovery ? (
                <div className="profile-recovery-confirm">
            <p>{tx("Esta ação substituirá o perfil local atual. O aplicativo cria um checkpoint antes e mantém a biblioteca Calibre e o Kindle intactos. Confirme para continuar.")}</p>
                  <div>
              <UiButton className="button-quiet" type="button" onClick={() => setConfirmingProfileRecovery(false)} disabled={profileBusy}>{tx("Cancelar")}</UiButton>
              <UiButton className="button-primary" type="button" onClick={() => void applyProfileRestore()} disabled={profileBusy}>{tx("Confirmar recuperação")}</UiButton>
                  </div>
                </div>
              ) : (
          <UiButton className="button-primary" type="button" onClick={() => setConfirmingProfileRecovery(true)} disabled={profileBusy}>{tx("Revisar e recuperar perfil")}</UiButton>
              )}
            </div>
          ) : null}
          {profileApplyResult ? (
            <div className="profile-recovery-preview" role="status">
        <strong>{language === "en" ? `Profile installed · checkpoint ${profileApplyResult.checkpoint_id.slice(0, 8)}` : `Perfil instalado · checkpoint ${profileApplyResult.checkpoint_id.slice(0, 8)}`}</strong>
        <span>{tx("Feche e abra o aplicativo para carregar o perfil recuperado.")}</span>
        <UiButton className="button-primary" type="button" onClick={() => void restartAfterProfileRestore()}>{tx("Reiniciar agora")}</UiButton>
            </div>
          ) : null}
        </div>
      </div>

      <p className={"backup-operation-status backup-status-" + statusKind} role={statusKind === "error" ? "alert" : "status"} aria-live="polite">
      {tx(statusMessage)}
      </p>
    </section>
  );
}

function formatSnapshotTime(value: string, language: UiLanguage = "pt-BR"): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(getUiLocale(language), { dateStyle: "short", timeStyle: "short" }).format(date);
}

function formatSnapshotScope(scope: BackupSnapshot["scope"], includesAppState: boolean, language: UiLanguage = "pt-BR"): string {
  if (scope === "calibre_only") return includesAppState ? "Calibre + app" : "Calibre";
  if (scope === "calibre_and_kindle") return includesAppState ? "Calibre + Kindle + app" : "Calibre + Kindle";
  return language === "en" ? "Unidentified contents" : "Conteúdo não identificado";
}

function formatBytes(value: number, language: UiLanguage = "pt-BR"): string {
  if (value < 1024 * 1024) return Math.round(value / 1024) + " KB";
  return new Intl.NumberFormat(getUiLocale(language), { maximumFractionDigits: 1 }).format(value / (1024 * 1024)) + " MB";
}

function formatBackupResult(result: BackupCreateResult, language: UiLanguage = "pt-BR"): string {
  const contents = [
    result.kindle_file_count > 0 ? "Calibre + Kindle" : "Calibre",
    (result.application_state_file_count ?? 0) > 0 ? language === "en" ? "local app data" : "dados locais do app" : "",
  ].filter(Boolean).join(" + ");
  const verification = result.restore_verified
    ? language === "en" ? "Repository integrity and temporary restore were verified." : "A integridade do repositório e a restauração temporária foram validadas."
    : language === "en" ? "The repository was checked, but a temporary restore has not been verified." : "O repositório foi verificado; uma restauração temporária ainda não foi validada.";
  return language === "en"
    ? `Backup ${contents} created (${result.file_count} files, ${formatBytes(result.total_bytes, language)}). ${verification} Snapshot ${result.snapshot_id.slice(0, 12)}.`
    : `Backup ${contents} criado (${result.file_count} arquivos, ${formatBytes(result.total_bytes, language)}). ${verification} Snapshot ${result.snapshot_id.slice(0, 12)}.`;
}

function formatRestoreResult(result: BackupRestoreResult, language: UiLanguage = "pt-BR"): string {
  const profile = result.profile_source_id
    ? language === "en" ? " Profile data is also available for guided recovery." : " Os dados do perfil também estão disponíveis para recuperação guiada."
    : language === "en" ? " This snapshot does not contain a recoverable profile configuration and database." : " Este snapshot não contém uma configuração e um banco do perfil recuperáveis.";
  return language === "en"
    ? `Restore verified (${result.file_count} files, ${formatBytes(result.total_bytes, language)}). Files were restored to the selected folder; review them before use.${profile}`
    : `Restauração verificada (${result.file_count} arquivos, ${formatBytes(result.total_bytes, language)}). Os dados foram restaurados para a pasta escolhida; revise-os antes de usar.${profile}`;
}

function formatRecoveryCounts(counts: Record<string, number>, language: UiLanguage = "pt-BR"): string {
  const labels: Record<string, string> = {
    book_identities: language === "en" ? "identities" : "identidades",
    identity_links: language === "en" ? "links" : "vínculos",
    reading_events: language === "en" ? "reading events" : "eventos de leitura",
    annotation_versions: language === "en" ? "annotation versions" : "versões de anotações",
    import_batches: language === "en" ? "imports" : "importações",
    remote_cache: language === "en" ? "cached items" : "itens em cache",
    sync_outbox: language === "en" ? "sync operations" : "operações de sincronização",
  };
  const rows = Object.entries(counts).filter(([, count]) => count > 0);
  return rows.length
    ? rows.map(([key, count]) => `${count.toLocaleString(getUiLocale(language))} ${labels[key] ?? key}`).join(" · ")
    : language === "en" ? "no local records found" : "nenhum registro local encontrado";
}

function formatBackupError(error: unknown, language: UiLanguage = "pt-BR"): string {
  const code = error instanceof Error ? error.message : "";
  const messages: Record<string, string> = {
    restic_password_unavailable: "Cadastre a senha Restic em Configuração. Use a mesma senha guardada fora deste computador.",
    restic_password_invalid: "Essa senha não abriu o repositório Restic. A senha anterior foi mantida.",
    restic_repository_unavailable: "O repositório local de backup não pôde ser aberto. Confira a configuração e o disco.",
    calibre_open: "Feche o Calibre e o servidor do Calibre antes do backup.",
    kindle_unavailable: "O Kindle não está disponível. Conecte-o e deixe a tela USB aberta.",
    kindle_multiple_devices: "Deixe conectado somente o Kindle que deseja incluir no backup.",
    restore_target_invalid: "Escolha um destino novo fora da biblioteca Calibre e do repositório de backup.",
    restore_failed: "A restauração falhou. A pasta incompleta foi removida quando possível; escolha um destino novo ao tentar novamente.",
    backup_unavailable: "Não foi possível concluir o backup. Confira a biblioteca e o repositório local.",
    backup_in_progress: "Outra operação protegida está em andamento. Aguarde ela terminar e tente novamente.",
    backup_lock_unavailable: "Não foi possível coordenar esta operação com segurança.",
    scheduled_task_unavailable: "Não foi possível configurar a tarefa diária do Windows.",
    scheduled_backup_disabled: "A tarefa diária está desativada.",
    configuration_unavailable: "A configuração local não está disponível.",
    operation_timeout: "A operação demorou mais do que o limite permitido. Atualize os snapshots antes de tentar novamente.",
    invalid_request: "A escolha do destino expirou. Escolha uma pasta nova e tente novamente.",
    invalid_protocol: "O serviço local retornou uma resposta inválida.",
    profile_restore_unverified: "Essa pasta não foi criada por uma restauração validada pelo aplicativo.",
    profile_recovery_profile_incomplete: "A pasta não contém um perfil SQLite completo.",
    profile_recovery_ambiguous: "Há mais de um perfil na pasta. Escolha a pasta exata de uma restauração.",
    profile_recovery_config_invalid: "A configuração restaurada é inválida ou contém campos não permitidos.",
    profile_recovery_database_invalid: "O banco SQLite não passou na verificação de integridade.",
    profile_recovery_schema_unsupported: "O schema SQLite desta restauração não é compatível com esta versão.",
    profile_recovery_source_changed: "A pasta mudou desde a prévia. Gere uma nova prévia antes de continuar.",
    profile_recovery_checkpoint_failed: "Não foi possível guardar um checkpoint do perfil atual. Nada foi substituído.",
    profile_recovery_install_failed: "A recuperação falhou. O perfil anterior foi restaurado pelo rollback.",
    profile_recovery_rollback_failed: "A instalação e o rollback automático falharam. O checkpoint anterior foi preservado para recuperação manual.",
    profile_recovery_active_state_invalid: "O caminho do perfil ativo não pôde ser validado com segurança.",
    profile_recovery_confirmation_required: "Confirme a recuperação depois de revisar a prévia.",
    profile_restore_marker_write_failed: "A restauração foi validada, mas não foi possível preparar o perfil para recuperação guiada.",
  };
  return localizeText(language, messages[code] ?? "Não foi possível concluir a operação de backup.");
}

function SettingsPage({
  status,
  service,
  hardcoverStatus,
  hardcoverUsage,
  hardcoverSource,
  hardcoverFetchedAt,
  hardcoverErrorCode,
  language,
  onLanguageChange,
  onConfigureHardcover,
  onReconcileHardcover,
  onResolveHardcoverReview,
  onConfigureResticPassword,
  backupAutomation,
  autoBackupNotice,
  onConfigureBackupAutomation,
  onRefreshBackupAutomation,
}: {
  status: LibraryStatus;
  service: ServiceStatus | null;
  hardcoverStatus: HardcoverUiStatus;
  hardcoverUsage: HardcoverStatus | null;
  hardcoverSource: HardcoverDataSource | null;
  hardcoverFetchedAt: string | null;
  hardcoverErrorCode: string | null;
  language: UiLanguage;
  onLanguageChange(language: UiLanguage): void;
  onConfigureHardcover(token: string): Promise<void>;
  onReconcileHardcover(): Promise<HardcoverReconciliationResult>;
  onResolveHardcoverReview(
    operationId: string,
    resolution: HardcoverReviewResolution,
  ): Promise<HardcoverReviewResolutionResult>;
  onConfigureResticPassword(password: string): Promise<void>;
  backupAutomation: BackupAutomationStatus | null;
  autoBackupNotice: "running" | "success" | "failed" | null;
  onConfigureBackupAutomation(preferences: BackupAutomationPreferences): Promise<BackupAutomationStatus>;
  onRefreshBackupAutomation(): Promise<void>;
}) {
  const tx = (text: string) => localizeText(language, text);
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [tokenMessage, setTokenMessage] = useState<string | null>(null);
  const [tokenMessageKind, setTokenMessageKind] = useState<"success" | "error" | null>(null);
  const [reconciliationConfirming, setReconciliationConfirming] = useState(false);
  const [reconciliationRunning, setReconciliationRunning] = useState(false);
  const [reconciliationMessage, setReconciliationMessage] = useState<string | null>(null);
  const [reconciliationError, setReconciliationError] = useState(false);
  const [manualReviews, setManualReviews] = useState<HardcoverManualReviewItem[]>([]);
  const [manualReviewsLoading, setManualReviewsLoading] = useState(true);
  const [manualReviewsError, setManualReviewsError] = useState(false);
  const [reviewSelection, setReviewSelection] = useState<{
    operationId: string;
    resolution: HardcoverReviewResolution | null;
  } | null>(null);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [reviewMessage, setReviewMessage] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState(false);
  const [changingToken, setChangingToken] = useState(false);
  const [resticPassword, setResticPassword] = useState("");
  const [resticConfigured, setResticConfigured] = useState<boolean | null>(null);
  const [resticLoading, setResticLoading] = useState(true);
  const [savingResticPassword, setSavingResticPassword] = useState(false);
  const [changingResticPassword, setChangingResticPassword] = useState(false);
  const [resticMessage, setResticMessage] = useState<string | null>(null);
  const [resticMessageKind, setResticMessageKind] = useState<"success" | "error" | null>(null);
  const [automationSaving, setAutomationSaving] = useState(false);
  const [automationMessage, setAutomationMessage] = useState<string | null>(null);
  const [automationMessageKind, setAutomationMessageKind] = useState<"success" | "error" | null>(null);
  const showTokenForm = changingToken || hardcoverStatus === "unconfigured" || hardcoverStatus === "error";
  const showResticPasswordForm = changingResticPassword || resticConfigured !== true;

  const refreshManualReviews = useCallback(async () => {
    setManualReviewsLoading(true);
    setManualReviewsError(false);
    try {
      setManualReviews(await window.reading.hardcover.manualReviews());
    } catch {
      setManualReviewsError(true);
    } finally {
      setManualReviewsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshManualReviews();
  }, [refreshManualReviews]);

  const runHardcoverReconciliation = async () => {
    setReconciliationRunning(true);
    setReconciliationError(false);
    setReconciliationConfirming(false);
    setReconciliationMessage(null);
    try {
      const result = await onReconcileHardcover();
      await refreshManualReviews();
      setReconciliationMessage(
        language === "en"
          ? `${result.completed} action(s) completed. ${result.safe_to_retry} safe action(s) remain; ${result.manual_review_required} need manual review.`
          : `${result.completed} ação(ões) concluída(s). Restam ${result.safe_to_retry} ação(ões) seguras; ${result.manual_review_required} exigem revisão manual.`,
      );
    } catch {
      setReconciliationError(true);
      setReconciliationMessage(tx("Não foi possível reconciliar a fila do Hardcover. Confira a conexão e tente novamente."));
    } finally {
      setReconciliationRunning(false);
    }
  };

  const confirmHardcoverReview = async () => {
    if (!reviewSelection || !reviewSelection.resolution || reviewSaving) return;
    setReviewSaving(true);
    setReviewMessage(null);
    setReviewError(false);
    try {
      await onResolveHardcoverReview(reviewSelection.operationId, reviewSelection.resolution);
      setReviewSelection(null);
      await refreshManualReviews();
      setReviewMessage(
        reviewSelection.resolution === "confirmed_remote"
          ? tx("Revisão salva localmente como confirmada no Hardcover.")
          : tx("Pendência encerrada apenas nesta aplicação.")
      );
    } catch {
      setReviewError(true);
      setReviewMessage(tx("Não foi possível salvar a revisão local. Atualize a lista e tente novamente."));
    } finally {
      setReviewSaving(false);
    }
  };

  useEffect(() => {
    let active = true;
    void window.reading.backup.credentials()
      .then((result) => { if (active) setResticConfigured(result.password_configured); })
      .catch(() => { if (active) setResticConfigured(null); })
      .finally(() => { if (active) setResticLoading(false); });
    return () => { active = false; };
  }, []);

  const submitToken = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token.trim() || saving) return;
    setSaving(true);
    setTokenMessage(null);
    setTokenMessageKind(null);
    try {
      await onConfigureHardcover(token);
      setToken("");
      setTokenMessage(tx("Chave validada e guardada no Windows."));
      setTokenMessageKind("success");
      setChangingToken(false);
    } catch (error) {
      const code = error instanceof Error ? error.message : "";
      const message = code === "hardcover_daily_budget_reached"
        ? "A cota diária local foi atingida. Tente novamente quando algumas chamadas completarem 24 horas."
        : code === "hardcover_rate_limited"
          ? "O Hardcover pediu uma pausa. Aguarde antes de tentar validar novamente."
          : code === "hardcover_budget_unavailable"
            ? "O contador local não está disponível; a validação foi bloqueada por segurança."
            : "Não foi possível validar a chave. Confira a conexão e tente novamente.";
      setTokenMessage(tx(message));
      setTokenMessageKind("error");
    } finally {
      setSaving(false);
    }
  };

  const submitResticPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!resticPassword.trim() || savingResticPassword) return;
    setSavingResticPassword(true);
    setResticMessage(null);
    setResticMessageKind(null);
    try {
      await onConfigureResticPassword(resticPassword);
      setResticPassword("");
      setResticConfigured(true);
      setChangingResticPassword(false);
      setResticMessage(tx("Senha guardada no Gerenciador de Credenciais do Windows."));
      setResticMessageKind("success");
    } catch {
      setResticMessage(tx("Não foi possível guardar a senha no Gerenciador de Credenciais do Windows."));
      setResticMessageKind("error");
    } finally {
      setSavingResticPassword(false);
    }
  };

  const saveAutomation = async (changes: Partial<BackupAutomationPreferences>) => {
    if (!backupAutomation || automationSaving) return;
    setAutomationSaving(true);
    setAutomationMessage(null);
    setAutomationMessageKind(null);
    try {
      await onConfigureBackupAutomation({
        daily_backup_enabled: changes.daily_backup_enabled ?? backupAutomation.daily_backup_enabled,
        daily_backup_time: changes.daily_backup_time ?? backupAutomation.daily_backup_time,
        backup_on_kindle_connect:
          changes.backup_on_kindle_connect ?? backupAutomation.backup_on_kindle_connect,
      });
      setAutomationMessage(tx("Preferências de automação salvas."));
      setAutomationMessageKind("success");
    } catch (error) {
      setAutomationMessage(formatBackupError(error, language));
      setAutomationMessageKind("error");
      void onRefreshBackupAutomation();
    } finally {
      setAutomationSaving(false);
    }
  };

  const calibreLabel = tx(status === "ready" ? "Conectada" : status === "loading" ? "Verificando" : "Indisponível");
  const hardcoverLabel = hardcoverStatus === "ready"
    ? tx("Conectado")
    : hardcoverStatus === "checking" || hardcoverStatus === "loading"
      ? tx("Verificando")
      : hardcoverStatus === "unconfigured" ? tx("Configurar") : tx("Offline");

  return (
    <section className="settings-page" aria-labelledby="settings-title">
      <div className="page-heading">
        <div className="page-heading-copy">
          <span className="eyebrow">{translate(language, "settings.eyebrow")}</span>
          <h1 id="settings-title">{translate(language, "settings.title")}</h1>
          <p>{translate(language, "settings.description")}</p>
        </div>
      </div>

      <section className="settings-panel settings-language-panel" aria-labelledby="language-settings-title">
        <div className="settings-panel-heading">
          <div className="settings-heading-copy">
            <h2 id="language-settings-title">{translate(language, "settings.language.title")}</h2>
            <p>{translate(language, "settings.language.description")}</p>
          </div>
          <label className="language-setting-field">
            <span>{translate(language, "settings.language.label")}</span>
            <UiSelect
              aria-label={translate(language, "settings.language.label")}
              value={language}
              onChange={(event) => onLanguageChange(event.currentTarget.value as UiLanguage)}
            >
              <option value="pt-BR">{translate(language, "settings.language.ptBR")}</option>
              <option value="en">{translate(language, "settings.language.english")}</option>
            </UiSelect>
          </label>
        </div>
      </section>

      <div className="settings-status-grid" aria-label={tx("Estado das conexões")}>
        <article className="settings-status-card">
          <div className="settings-status-card-top"><span className="settings-card-icon"><HardDrive size={18} aria-hidden="true" /></span><span className={`setting-status${status === "ready" ? " status-ready" : ""}`}>{calibreLabel}</span></div>
          <strong>{tx("Biblioteca Calibre")}</strong>
          <p>{tx(status === "ready" ? "Disponível para buscar, editar e enviar livros." : status === "loading" ? "Verificando o acesso à biblioteca local…" : "Confira se a pasta configurada está disponível.")}</p>
        </article>
        <article className="settings-status-card">
          <div className="settings-status-card-top"><span className="settings-card-icon"><BookOpenCheck size={18} aria-hidden="true" /></span><span className={`setting-status${hardcoverStatus === "ready" ? " status-ready" : ""}`}>{hardcoverLabel}</span></div>
          <strong>Hardcover</strong>
          <p>{tx(hardcoverStatus === "ready" ? "Estantes e leitura atual estão disponíveis." : hardcoverStatus === "checking" || hardcoverStatus === "loading" ? "Consultando a conta…" : hardcoverStatus === "unconfigured" ? "Conecte sua conta para carregar as estantes." : "Sem conexão; o catálogo Calibre continua disponível.")}</p>
          {hardcoverSource === "cache" && hardcoverFetchedAt ? <small>{language === "en" ? `Showing local cache saved at ${formatKindleCheckTime(hardcoverFetchedAt)}; it may be out of date.` : `Exibindo cache local salvo em ${formatKindleCheckTime(hardcoverFetchedAt)}; pode estar desatualizado.`}</small> : null}
          {hardcoverUsage && hardcoverUsage.pending_mutations > 0 ? (
            <small role="status">
              {language === "en"
                ? `${hardcoverUsage.safe_to_retry} safe action(s) can be processed after confirmation; ${hardcoverUsage.manual_review_required} require manual review.`
                : `${hardcoverUsage.safe_to_retry} ação(ões) segura(s) podem ser processadas após confirmação; ${hardcoverUsage.manual_review_required} exigem revisão manual.`}
            </small>
          ) : null}
        </article>
        <article className="settings-status-card">
          <div className="settings-status-card-top"><span className="settings-card-icon"><Activity size={18} aria-hidden="true" /></span><span className={`setting-status${service ? " status-ready" : ""}`}>{tx(service ? "Ativo" : "Iniciando")}</span></div>
          <strong>{tx("Serviço local")}</strong>
          <p>{tx(service ? "Interface e núcleo Python estão comunicando em segundo plano." : "O núcleo local está iniciando.")}</p>
        </article>
      </div>

      <section className="settings-panel settings-hardcover-panel" aria-labelledby="hardcover-settings-title">
        <div className="settings-panel-heading">
          <div className="settings-icon"><KeyRound size={19} aria-hidden="true" /></div>
          <div className="settings-heading-copy">
          <span className="eyebrow">{tx("CONTA E CREDENCIAL")}</span>
          <h2 id="hardcover-settings-title">{tx("Conexão Hardcover")}</h2>
          <p>{tx("A chave é validada antes de ser salva no Windows.")}</p>
          </div>
          {hardcoverStatus === "ready" && !changingToken ? (
            <UiButton className="button-secondary settings-change-key" type="button" onClick={() => { setChangingToken(true); setTokenMessage(null); setTokenMessageKind(null); }}>
              {tx("Trocar chave")}
            </UiButton>
          ) : null}
        </div>

        {showTokenForm ? (
          <form className="credential-form" onSubmit={(event) => void submitToken(event)}>
          <label htmlFor="hardcover-token"><span>{tx("Chave de API")}</span></label>
            <div className="credential-input-row">
              <UiInput
                id="hardcover-token"
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                value={token}
                onChange={(event) => setToken(event.currentTarget.value)}
              placeholder={tx(changingToken ? "Cole a nova chave do Hardcover" : "Cole sua chave do Hardcover")}
                maxLength={4096}
                aria-describedby="hardcover-key-hint"
              />
              <UiButton className="button-primary" type="submit" disabled={!token.trim() || saving}>
                {saving ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : null}
              {saving ? tx("Validando…") : tx("Validar e salvar")}
              </UiButton>
              {changingToken ? (
                <UiButton className="button-quiet settings-cancel-key" type="button" disabled={saving} onClick={() => { setChangingToken(false); setToken(""); setTokenMessage(null); setTokenMessageKind(null); }}>
                {tx("Cancelar")}
                </UiButton>
              ) : null}
            </div>
            {tokenMessage ? <p className={`credential-message${tokenMessageKind === "error" ? " credential-message-error" : " credential-message-success"}`} role={tokenMessageKind === "error" ? "alert" : "status"}>{tokenMessageKind === "error" ? <AlertTriangle size={15} aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}{tokenMessage}</p> : null}
          <p className="credential-hint" id="hardcover-key-hint">{tx("A chave fica oculta e não aparece novamente. Se já houver uma credencial válida, ela só será trocada depois da validação.")}</p>
          </form>
        ) : (
        <div className="credential-saved"><ShieldCheck size={17} aria-hidden="true" /><p>{tokenMessageKind === "success" ? tx(tokenMessage ?? "") : tx("Credencial protegida no Gerenciador de Credenciais do Windows. Ela não é exibida nesta tela.")}</p></div>
        )}
      <div className="hardcover-budget" aria-label={tx("Uso local da API Hardcover")}>
          <div className="hardcover-budget-heading">
            <div>
            <strong>{tx("Limite local de chamadas")}</strong>
            <span>{tx("Aplicativo Windows · janela móvel de 24 horas")}</span>
            </div>
          <b>{hardcoverUsage ? `${hardcoverUsage.requests_used.toLocaleString(getUiLocale(language))} / ${hardcoverUsage.request_limit.toLocaleString(getUiLocale(language))}` : tx("Verificando…")}</b>
          </div>
          <div
            className="hardcover-budget-track"
            role="progressbar"
          aria-label={tx("Chamadas Hardcover feitas pelo aplicativo Windows nas últimas 24 horas")}
            aria-valuemin={0}
            aria-valuemax={hardcoverUsage?.request_limit ?? 1000}
            aria-valuenow={hardcoverUsage?.requests_used ?? 0}
          >
            <span style={{ width: `${hardcoverUsage ? Math.min(100, hardcoverUsage.requests_used / hardcoverUsage.request_limit * 100) : 0}%` }} />
          </div>
        <p>
          {language === "en"
            ? `The app stops at ${hardcoverUsage?.request_limit.toLocaleString(getUiLocale(language)) ?? "1,000"} requests per 24 hours and 25 per minute. The updated KOReader plugin has a separate limit of up to 2,000 per 24 hours and 25 per minute; together, the project components are capped at 3,000 per day and 50 per minute.`
            : `O aplicativo para em ${hardcoverUsage?.request_limit.toLocaleString(getUiLocale(language)) ?? "1.000"} chamadas por 24 horas e 25 por minuto. O plugin KOReader atualizado usa uma cota separada de até 2.000 por 24 horas e 25 por minuto; juntos, os componentes do projeto ficam limitados a 3.000 por dia e 50 por minuto.`}
        </p>
        <p className="hardcover-budget-caveat">{tx("O site do Hardcover e outros aplicativos usam a mesma conta, mas não podem ser contados por esta aplicação.")}</p>
        {(hardcoverUsage && hardcoverUsage.manual_review_required > 0) || manualReviews.length > 0 || manualReviewsError || reviewMessage !== null ? (
          <p className="hardcover-budget-caveat" role="status">
            {tx("Inícios de leitura, notas de diário e ações com envio incerto exigem conferência manual.")}
          </p>
        ) : null}
        {hardcoverUsage && hardcoverUsage.safe_to_retry > 0 ? (
          <div className="hardcover-reconciliation" aria-labelledby="hardcover-reconciliation-title">
            <div>
              <strong id="hardcover-reconciliation-title">{tx("Ações pendentes do Hardcover")}</strong>
              <p>{tx("Registros ainda não enviados podem ser processados uma vez. Se um envio foi interrompido, só repetimos atualizações de leitura existente ou avaliação; inícios e notas de diário ficam para revisão manual.")}</p>
              <p className="hardcover-budget-caveat">{tx("Envios ambíguos de início de leitura e notas de diário ficam para revisão manual. Até 4 ações por clique.")}</p>
            </div>
            {!reconciliationConfirming ? (
              <UiButton
                className="button-secondary"
                type="button"
                disabled={reconciliationRunning}
                onClick={() => { setReconciliationConfirming(true); setReconciliationMessage(null); }}
              >
                {tx("Processar até 4 ações seguras")}
              </UiButton>
            ) : (
              <div className="hardcover-reconciliation-confirmation">
                <p>{tx("Confirme para enviar até 4 ações seguras ao Hardcover agora. As demais permanecem na fila.")}</p>
                <UiButton
                  className="button-primary"
                  type="button"
                  disabled={reconciliationRunning}
                  onClick={() => void runHardcoverReconciliation()}
                >
                  {reconciliationRunning ? tx("Processando ações…") : tx("Confirmar e reconciliar")}
                </UiButton>
                <UiButton
                  className="button-quiet"
                  type="button"
                  disabled={reconciliationRunning}
                  onClick={() => setReconciliationConfirming(false)}
                >
                  {tx("Cancelar")}
                </UiButton>
              </div>
            )}
            {reconciliationMessage ? (
              <p role={reconciliationError ? "alert" : "status"}>{reconciliationMessage}</p>
            ) : null}
          </div>
        ) : null}
        {hardcoverUsage && hardcoverUsage.manual_review_required > 0 ? (
          <div className="hardcover-manual-reviews" aria-labelledby="hardcover-manual-reviews-title">
            <div className="hardcover-manual-reviews-heading">
              <div>
                <strong id="hardcover-manual-reviews-title">{tx("Revisão manual de sincronização")}</strong>
                <p>{tx("Confira a conta Hardcover. Esta lista é local e não faz chamadas à API.")}</p>
              </div>
              <UiButton
                className="button-quiet"
                type="button"
                disabled={manualReviewsLoading}
                onClick={() => void refreshManualReviews()}
              >
                {manualReviewsLoading ? tx("Atualizando lista…") : tx("Atualizar lista")}
              </UiButton>
            </div>
            {manualReviewsLoading ? <p role="status">{tx("Carregando itens locais…")}</p> : null}
            {manualReviewsError ? (
              <p role="alert">{tx("Não foi possível carregar os itens locais para revisão.")}</p>
            ) : null}
            {!manualReviewsLoading && !manualReviewsError && manualReviews.length === 0 ? (
              <p role="status">{tx("Nenhuma ação exige revisão manual.")}</p>
            ) : null}
            <div className="hardcover-manual-review-list">
              {manualReviews.map((item) => {
                const operationLabel = item.operation === "start_read"
                  ? tx("Início de leitura")
                  : item.operation === "finish_read"
                    ? tx("Conclusão de leitura")
                    : item.operation === "update_rating"
                      ? tx("Avaliação")
                      : tx("Nota de diário");
                const selected = reviewSelection?.operationId === item.operation_id;
                const dateLabel = new Date(item.created_at).toLocaleString(getUiLocale(language));
                return (
                  <article className="hardcover-manual-review-item" key={item.operation_id}>
                    <div className="hardcover-manual-review-copy">
                      <strong>{item.book_title ?? tx("Título indisponível no cache")}</strong>
                      {item.authors.length > 0 ? <span>{item.authors.join(", ")}</span> : null}
                      {item.book_reference ? <small>{tx("Referência Hardcover")}: {item.book_reference}</small> : null}
                      <span>{operationLabel}{item.rating === null ? "" : ` · ${item.rating.toFixed(1)} / 5`}{item.page === null ? "" : ` · ${tx("Página")} ${item.page}`}</span>
                      <small>{tx(item.status === "failed" ? "Falha final" : item.status === "reconcile" ? "Envio incerto" : "Pendente")} · {dateLabel}</small>
                      {item.operation === "add_journal_entry" ? (
                        <small className="hardcover-review-private-note">{tx("O texto da nota está oculto por privacidade.")}</small>
                      ) : null}
                    </div>
                    {!selected ? (
                      <UiButton
                        className="button-secondary hardcover-review-action"
                        type="button"
                        disabled={reviewSaving}
                        onClick={() => { setReviewMessage(null); setReviewSelection({ operationId: item.operation_id, resolution: null }); }}
                      >
                        {tx("Revisar ação")}
                      </UiButton>
                    ) : (
                      <div className="hardcover-review-confirmation">
                        <p>{tx(reviewSelection.resolution === null
                          ? "Escolha como deseja encerrar esta pendência."
                          : reviewSelection.resolution === "confirmed_remote"
                            ? "Você conferiu o Hardcover. Isso só encerra a pendência local e não altera sua conta."
                            : "Isso encerra a pendência local, mas não confirma se o Hardcover recebeu a ação.")}</p>
                        <div className="hardcover-review-choice-row">
                          <UiButton
                            className={reviewSelection.resolution === "confirmed_remote" ? "button-secondary" : "button-quiet"}
                            type="button"
                            disabled={reviewSaving}
                            onClick={() => setReviewSelection({ operationId: item.operation_id, resolution: "confirmed_remote" })}
                          >
                            {tx("Já aparece no Hardcover")}
                          </UiButton>
                          <UiButton
                            className={reviewSelection.resolution === "closed_locally" ? "button-secondary" : "button-quiet"}
                            type="button"
                            disabled={reviewSaving}
                            onClick={() => setReviewSelection({ operationId: item.operation_id, resolution: "closed_locally" })}
                          >
                            {tx("Encerrar sem confirmar")}
                          </UiButton>
                          <UiButton className="button-primary" type="button" disabled={reviewSaving || reviewSelection.resolution === null} onClick={() => void confirmHardcoverReview()}>
                            {reviewSaving ? tx("Salvando revisão…") : tx("Confirmar revisão")}
                          </UiButton>
                          <UiButton className="button-quiet" type="button" disabled={reviewSaving} onClick={() => setReviewSelection(null)}>
                            {tx("Cancelar")}
                          </UiButton>
                        </div>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
            {reviewMessage ? <p role={reviewError ? "alert" : "status"}>{reviewMessage}</p> : null}
          </div>
        ) : null}
          {hardcoverErrorCode === "hardcover_daily_budget_reached" ? (
          <p className="hardcover-budget-alert" role="status">{tx("A cota do aplicativo foi atingida. As consultas voltam à medida que as chamadas completarem 24 horas.")}</p>
          ) : hardcoverErrorCode === "hardcover_rate_limited" ? (
          <p className="hardcover-budget-alert" role="status">{tx("O Hardcover pediu uma pausa. Aguarde antes de atualizar novamente.")}</p>
          ) : hardcoverErrorCode === "hardcover_budget_unavailable" ? (
          <p className="hardcover-budget-alert" role="alert">{tx("O contador local não está disponível; chamadas foram bloqueadas por segurança.")}</p>
          ) : null}
        </div>
      </section>

      <section className="settings-panel settings-restic-panel" aria-labelledby="restic-settings-title">
        <div className="settings-panel-heading">
          <div className="settings-icon"><HardDrive size={19} aria-hidden="true" /></div>
          <div className="settings-heading-copy">
          <span className="eyebrow">{tx("BACKUP LOCAL CRIPTOGRAFADO")}</span>
          <h2 id="restic-settings-title">{tx("Senha do Restic")}</h2>
          <p>{tx(resticLoading ? "Verificando o Gerenciador de Credenciais…" : resticConfigured === true ? "A senha está protegida pelo Windows." : "Cadastre a senha usada para abrir o repositório.")}</p>
          </div>
          {resticConfigured === true && !changingResticPassword ? (
            <UiButton className="button-secondary settings-change-key" type="button" onClick={() => { setChangingResticPassword(true); setResticMessage(null); setResticMessageKind(null); }}>
            {tx("Trocar senha")}
            </UiButton>
          ) : null}
        </div>
        {showResticPasswordForm ? (
          <form className="credential-form" onSubmit={(event) => void submitResticPassword(event)}>
          <label htmlFor="restic-password"><span>{tx("Senha do repositório")}</span></label>
            <div className="credential-input-row">
              <UiInput
                id="restic-password"
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                value={resticPassword}
                onChange={(event) => setResticPassword(event.currentTarget.value)}
            placeholder={tx("Cole a senha Restic")}
                maxLength={4096}
                aria-describedby="restic-password-hint"
              />
              <UiButton className="button-primary" type="submit" disabled={!resticPassword.trim() || savingResticPassword}>
                {savingResticPassword ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : null}
            {savingResticPassword ? tx("Guardando…") : tx("Salvar no Windows")}
              </UiButton>
              {changingResticPassword ? (
                <UiButton className="button-quiet settings-cancel-key" type="button" disabled={savingResticPassword} onClick={() => { setChangingResticPassword(false); setResticPassword(""); setResticMessage(null); setResticMessageKind(null); }}>
              {tx("Cancelar")}
                </UiButton>
              ) : null}
            </div>
          <p className="credential-hint" id="restic-password-hint">{tx("Guarde uma cópia da senha em outro gerenciador. Sem ela, os snapshots criptografados não podem ser restaurados. O valor não volta a ser exibido.")}</p>
          </form>
        ) : (
        <div className="credential-saved"><ShieldCheck size={17} aria-hidden="true" /><p>{tx("A senha permanece no Gerenciador de Credenciais do Windows e não é exportada.")}</p></div>
        )}
        {resticMessage ? <p className={"credential-message credential-message-" + resticMessageKind} role={resticMessageKind === "error" ? "alert" : "status"}>{resticMessage}</p> : null}
      </section>

      <section className="settings-panel settings-automation-panel" aria-labelledby="backup-automation-title">
        <div className="settings-panel-heading">
          <div className="settings-icon"><Activity size={19} aria-hidden="true" /></div>
          <div className="settings-heading-copy">
            <span className="eyebrow">{tx("OPCIONAL · DESLIGADO POR PADRÃO")}</span>
            <h2 id="backup-automation-title">{tx("Automação local")}</h2>
            <p>{tx("Configure quando a aplicação cria snapshots locais criptografados.")}</p>
          </div>
        </div>
        <div className="settings-automation-controls">
          <label className="settings-toggle">
            <span>
              <strong>{tx("Backup diário")}</strong>
              <small>{tx("Agenda Calibre e dados do app no Windows Task Scheduler.")}</small>
            </span>
            <input
              aria-label={tx("Backup diário")}
              type="checkbox"
              checked={backupAutomation?.daily_backup_enabled ?? false}
              disabled={!backupAutomation || automationSaving}
              onChange={(event) => void saveAutomation({ daily_backup_enabled: event.currentTarget.checked })}
            />
          </label>
          <label className="settings-automation-time">
            <span>{tx("Executar às")}</span>
            <UiInput
              aria-label={tx("Horário do backup diário")}
              type="time"
              value={backupAutomation?.daily_backup_time ?? "20:00"}
              disabled={!backupAutomation || !backupAutomation.daily_backup_enabled || automationSaving}
              onChange={(event) => void saveAutomation({ daily_backup_time: event.currentTarget.value })}
            />
          </label>
          <label className="settings-toggle">
            <span>
              <strong>{tx("Ao conectar por USB")}</strong>
              <small>{tx("Enquanto a aplicação estiver aberta, aguarda duas verificações estáveis antes de incluir o dispositivo no backup.")}</small>
            </span>
            <input
              aria-label={tx("Ao conectar por USB")}
              type="checkbox"
              checked={backupAutomation?.backup_on_kindle_connect ?? false}
              disabled={!backupAutomation || automationSaving}
              onChange={(event) => void saveAutomation({ backup_on_kindle_connect: event.currentTarget.checked })}
            />
          </label>
        </div>
        {backupAutomation?.daily_backup_needs_reactivation ? (
          <div role="alert">
            <p>{tx("Não foi possível ativar o backup diário. Confira a senha do Restic e o Windows Task Scheduler e tente reativar a tarefa.")}</p>
            <button type="button" disabled={automationSaving} onClick={() => void saveAutomation({ daily_backup_enabled: true })}>
              {tx("Reativar backup diário")}
            </button>
          </div>
        ) : null}
        <div className="settings-automation-notes">
          <p>{tx("A tarefa diária roda somente com sua sessão do Windows iniciada e o computador acordado. Ela não acessa o dispositivo.")}</p>
          <p>{tx("O backup ao conectar exige a aplicação aberta e a tela USB ativa. Ao desligar uma opção, execuções futuras são removidas; um backup que já começou termina com segurança.")}</p>
        </div>
        {backupAutomation?.last_backup ? (
          <p className="settings-automation-last" role="status">
            <strong>{tx("Último backup")}</strong>
            <span>{tx(backupAutomation.last_backup.status)} · {formatKindleCheckTime(backupAutomation.last_backup.updated_at, language)}</span>
            {backupAutomation.last_backup.reason_code ? <small>{formatBackupError(new Error(backupAutomation.last_backup.reason_code), language)}</small> : null}
          </p>
        ) : null}
        {autoBackupNotice ? (
          <p className={"settings-automation-feedback" + (autoBackupNotice === "failed" ? " feedback-error" : "")} role={autoBackupNotice === "failed" ? "alert" : "status"}>
            {tx(autoBackupNotice === "running" ? "Backup automático em andamento…" : autoBackupNotice === "success" ? "Backup automático concluído. A restauração completa não foi testada nesta execução." : "O backup automático falhou. Confira o último resultado e reconecte o dispositivo para tentar novamente.")}
          </p>
        ) : null}
        {automationMessage ? <p className={"credential-message credential-message-" + automationMessageKind} role={automationMessageKind === "error" ? "alert" : "status"}>{automationMessage}</p> : null}
      </section>

      <div className="settings-note">
        <CircleHelp size={17} aria-hidden="true" />
      <p>{tx("Os metadados bibliográficos ficam no Calibre e as estantes de leitura ficam no Hardcover. Conectar uma conta não altera livros nem status da estante.")}</p>
      </div>
    </section>
  );
}
