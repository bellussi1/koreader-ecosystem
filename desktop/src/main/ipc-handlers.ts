import { app, BrowserWindow, dialog, ipcMain, type IpcMainInvokeEvent } from "electron";
import { randomUUID } from "node:crypto";
import { lstatSync, realpathSync, statSync } from "node:fs";
import path from "node:path";
import type { BackendClient } from "../shared/contracts";
import { BackendClientError } from "./backend-client";

interface FrameLike {
  origin: string;
}

interface WebContentsLike {
  mainFrame: FrameLike;
}

interface CalibreBookWithCoverPath extends Record<string, unknown> {
  book_id?: unknown;
  cover_path?: unknown;
}

const supportedCoverExtensions = new Set([".jpg", ".jpeg", ".png", ".webp"]);
const supportedPlatformCsvSources = new Set(["goodreads", "storygraph", "maratona"]);
const platformCsvMappingFields = [
  "external_id", "title", "author", "isbn_10", "isbn_13", "rating", "status",
  "shelves", "read_count", "date_added", "date_read", "review",
] as const;
const MAX_PLATFORM_CSV_HEADERS = 256;
const PLATFORM_CSV_SELECTION_TTL_MS = 15 * 60 * 1000;
const MAX_PENDING_PLATFORM_CSV_SELECTIONS = 10;

type UiDialogLanguage = "pt-BR" | "en";

function isPlatformCsvSource(value: unknown): value is "goodreads" | "storygraph" | "maratona" {
  return typeof value === "string" && supportedPlatformCsvSources.has(value);
}

function isDialogLanguage(value: unknown): value is UiDialogLanguage {
  return value === "pt-BR" || value === "en";
}

function validateSelectedCsv(pathname: string): string {
  try {
    const selected = lstatSync(pathname);
    const resolved = realpathSync(pathname);
    const details = statSync(resolved);
    if (
      selected.isSymbolicLink() || !path.isAbsolute(resolved) || !details.isFile() ||
      path.extname(resolved).toLowerCase() !== ".csv" || details.size > 50 * 1024 * 1024
    ) {
      throw new BackendClientError("invalid_request");
    }
    return resolved;
  } catch (error) {
    if (error instanceof BackendClientError) throw error;
    throw new BackendClientError("invalid_request");
  }
}

export function isTrustedMainFrame(
  sender: WebContentsLike,
  senderFrame: FrameLike | null,
  expectedSender: WebContentsLike,
  expectedOrigin: string,
): boolean {
  return (
    sender === expectedSender &&
    senderFrame !== null &&
    senderFrame === sender.mainFrame &&
    senderFrame.origin === expectedOrigin
  );
}

export function registerIpcHandlers(
  client: BackendClient,
  window: BrowserWindow,
  allowedOrigin: string,
  coverPaths: Map<number, string>,
): () => void {
  const pendingImportPreviews = new Map<string, { path: string; expiresAt: number }>();
  const pendingCoverSelections = new Map<
    string,
    { path: string; bookId: number; name: string; expiresAt: number }
  >();
  const pendingRestoreTargets = new Map<string, {
    path: string;
    expiresAt: number;
  }>();
  const pendingProfileSources = new Map<string, { path: string; expiresAt: number }>();
  const pendingProfilePreviews = new Map<
    string,
    { path: string; sourceName: string; fingerprint: string; expiresAt: number }
  >();
  const pendingPlatformCsvSelections = new Map<string, {
    path: string;
    source: "goodreads" | "storygraph" | "maratona";
    sourceDigest: string;
    headers: string[];
    expiresAt: number;
  }>();
  const assertTrusted = (event: IpcMainInvokeEvent) => {
    if (
      !isTrustedMainFrame(
        event.sender,
        event.senderFrame,
        window.webContents,
        allowedOrigin,
      )
    ) {
      throw new BackendClientError("untrusted_sender");
    }
  };

  const loadPublicCalibreCatalog = async () => {
    const catalog = await client.request<unknown>("calibre.list", {});
    if (
      typeof catalog !== "object" ||
      catalog === null ||
      Array.isArray(catalog) ||
      !Array.isArray((catalog as { books?: unknown }).books) ||
      !Number.isSafeInteger((catalog as { total?: unknown }).total) ||
      !((catalog as { books: unknown[] }).books.every(isRecord))
    ) {
      throw new BackendClientError("invalid_protocol");
    }

    const nextCoverPaths = new Map<number, string>();
    const books = (catalog as { books: CalibreBookWithCoverPath[] }).books.map((book) => {
      const publicBook = { ...book };
      delete publicBook.cover_path;

      const bookId = book.book_id;
      const coverPath = book.cover_path;
      const hasSafeCover =
        Number.isSafeInteger(bookId) &&
        Number(bookId) > 0 &&
        typeof coverPath === "string" &&
        path.isAbsolute(coverPath) &&
        supportedCoverExtensions.has(path.extname(coverPath).toLowerCase());

      if (hasSafeCover) {
        const id = Number(bookId);
        nextCoverPaths.set(id, coverPath as string);
        publicBook.cover_url = `koreader://bundle/covers/${id}`;
      } else {
        publicBook.cover_url = null;
      }

      return publicBook;
    });

    coverPaths.clear();
    for (const [bookId, coverPath] of nextCoverPaths) {
      coverPaths.set(bookId, coverPath);
    }
    return { ...catalog, books };
  };

  const handle = (
    channel: "app:status" | "calibre:list",
    operation: "system.status" | "calibre.list",
  ) => {
    ipcMain.handle(channel, (event: IpcMainInvokeEvent) => {
      assertTrusted(event);
      return client.request(operation, {});
    });
  };

  handle("app:status", "system.status");

  ipcMain.handle("calibre:list", async (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    return loadPublicCalibreCatalog();
  });

  ipcMain.handle("calibre:select-import", async (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    const selected = await dialog.showOpenDialog(window, {
      title: "Adicionar livro à biblioteca Calibre",
      properties: ["openFile"],
      filters: [{
        name: "Arquivos de livro",
        extensions: ["azw", "azw1", "azw3", "azw4", "cb7", "cbc", "cbr", "cbz", "chm", "docx", "epub", "fb2", "fbz", "html", "htmlz", "imp", "kepub", "lit", "lrf", "lrx", "mobi", "odt", "oebzip", "pdb", "pdf", "pml", "pmlz", "pobi", "prc", "rar", "rb", "rtf", "snb", "tpz", "txt", "txtz", "updb", "zip"],
      }, { name: "Todos os arquivos", extensions: ["*"] }],
    });
    if (selected.canceled || selected.filePaths.length !== 1) return null;

    const preview = await client.request<unknown>("calibre.preview-import", {
      files: selected.filePaths,
    });
    if (
      !isRecord(preview) || !Array.isArray(preview.files) || preview.files.length !== 1 ||
      !isRecord(preview.files[0])
    ) {
      throw new BackendClientError("invalid_protocol");
    }
    for (const [id, entry] of pendingImportPreviews) {
      if (entry.expiresAt <= Date.now()) pendingImportPreviews.delete(id);
    }
    if (pendingImportPreviews.size >= 20) {
      const oldest = pendingImportPreviews.keys().next().value;
      if (oldest) pendingImportPreviews.delete(oldest);
    }
    const previewId = randomUUID();
    pendingImportPreviews.set(previewId, {
      path: selected.filePaths[0],
      expiresAt: Date.now() + 30 * 60 * 1000,
    });
    return { preview_id: previewId, files: preview.files };
  });

  ipcMain.handle("calibre:select-cover", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 1 ||
      !Number.isSafeInteger(payload.book_id) ||
      Number(payload.book_id) < 1
    ) {
      throw new BackendClientError("invalid_request");
    }
    const selected = await dialog.showOpenDialog(window, {
      title: "Escolher capa do livro",
      properties: ["openFile"],
      filters: [{ name: "Imagens de capa", extensions: ["jpg", "jpeg", "png", "webp"] }],
    });
    if (selected.canceled || selected.filePaths.length !== 1) return null;
    const selectedPath = selected.filePaths[0];
    const extension = path.extname(selectedPath).toLowerCase();
    try {
      const details = lstatSync(selectedPath);
      const resolved = realpathSync(selectedPath);
      const resolvedDetails = statSync(resolved);
      if (
        details.isSymbolicLink() ||
        !resolvedDetails.isFile() ||
        !supportedCoverExtensions.has(extension) ||
        resolvedDetails.size <= 0 ||
        resolvedDetails.size > 20 * 1024 * 1024
      ) {
        throw new BackendClientError("invalid_request");
      }
      for (const [id, entry] of pendingCoverSelections) {
        if (entry.expiresAt <= Date.now()) pendingCoverSelections.delete(id);
      }
      if (pendingCoverSelections.size >= 20) {
        const oldest = pendingCoverSelections.keys().next().value;
        if (oldest) pendingCoverSelections.delete(oldest);
      }
      const selectionId = randomUUID();
      const name = path.basename(resolved);
      pendingCoverSelections.set(selectionId, {
        path: resolved,
        bookId: Number(payload.book_id),
        name,
        expiresAt: Date.now() + 30 * 60 * 1000,
      });
      return { selection_id: selectionId, name };
    } catch (error) {
      if (error instanceof BackendClientError) throw error;
      throw new BackendClientError("invalid_request");
    }
  });

  ipcMain.handle("calibre:add-import", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (!isRecord(payload)) throw new BackendClientError("invalid_request");
    const previewId = payload.preview_id;
    const fileIndex = payload.file_index;
    const mode = payload.mode;
    const targetBookId = payload.target_book_id;
    if (
      Object.keys(payload).some((key) => !["preview_id", "file_index", "mode", "target_book_id", "recovery_copy_confirmed"].includes(key)) ||
      typeof previewId !== "string" ||
      !Number.isSafeInteger(fileIndex) || Number(fileIndex) !== 0 ||
      (mode !== "new" && mode !== "attach") ||
      payload.recovery_copy_confirmed !== true ||
      (mode === "attach" && (!Number.isSafeInteger(targetBookId) || Number(targetBookId) < 1)) ||
      (mode === "new" && targetBookId !== undefined)
    ) {
      throw new BackendClientError("invalid_request");
    }
    const pending = pendingImportPreviews.get(previewId);
    if (!pending || pending.expiresAt <= Date.now()) {
      pendingImportPreviews.delete(previewId);
      throw new BackendClientError("invalid_request");
    }
    const result = await client.request<unknown>("calibre.add-import", {
      path: pending.path,
      mode,
      ...(mode === "attach" ? { target_book_id: Number(targetBookId) } : {}),
      recovery_copy_confirmed: true,
    });
    pendingImportPreviews.delete(previewId);
    const importedBook = isRecord(result) ? result.book : null;
    if (!isRecord(importedBook) || !Number.isSafeInteger(importedBook.book_id)) {
      throw new BackendClientError("invalid_protocol");
    }
    const importedBookId = Number(importedBook.book_id);
    const refreshed = await loadPublicCalibreCatalog();
    const updatedBook = (refreshed as { books: Record<string, unknown>[] }).books.find(
      (book) => book.book_id === importedBookId,
    );
    if (!updatedBook) throw new BackendClientError("invalid_protocol");
    return { book: updatedBook };
  });

  ipcMain.handle("calibre:edit", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).some((key) => !["book_id", "changes", "cover_selection_id", "recovery_copy_confirmed"].includes(key)) ||
      !Number.isSafeInteger(payload.book_id) || Number(payload.book_id) < 1 ||
      !isRecord(payload.changes) ||
      (Object.keys(payload.changes).length === 0 && payload.cover_selection_id === undefined) ||
      (payload.cover_selection_id !== undefined && typeof payload.cover_selection_id !== "string") ||
      payload.recovery_copy_confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    const bookId = Number(payload.book_id);
    const coverSelectionId = payload.cover_selection_id as string | undefined;
    const coverSelection = coverSelectionId === undefined
      ? undefined
      : pendingCoverSelections.get(coverSelectionId);
    if (
      coverSelectionId !== undefined &&
      (!coverSelection || coverSelection.expiresAt <= Date.now() || coverSelection.bookId !== bookId)
    ) {
      pendingCoverSelections.delete(coverSelectionId);
      throw new BackendClientError("invalid_request");
    }
    await client.request("calibre.edit", {
      book_id: Number(payload.book_id),
      changes: payload.changes,
      ...(coverSelection ? { cover_path: coverSelection.path } : {}),
      recovery_copy_confirmed: true,
    });
    if (coverSelectionId) pendingCoverSelections.delete(coverSelectionId);
    const refreshed = await loadPublicCalibreCatalog();
    const updatedBook = (refreshed as { books: Record<string, unknown>[] }).books.find(
      (book) => book.book_id === bookId,
    );
    if (!updatedBook) throw new BackendClientError("invalid_protocol");
    return { book: updatedBook };
  });

  ipcMain.handle("calibre:bulk-tags", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 5 ||
      Object.keys(payload).some((key) => !["book_ids", "add_tags", "remove_tags", "confirmed", "recovery_copy_confirmed"].includes(key)) ||
      !Array.isArray(payload.book_ids) ||
      payload.book_ids.length < 1 ||
      payload.book_ids.length > 100 ||
      !payload.book_ids.every((id) => Number.isSafeInteger(id) && Number(id) > 0) ||
      new Set(payload.book_ids).size !== payload.book_ids.length ||
      !Array.isArray(payload.add_tags) ||
      !payload.add_tags.every((tag) => typeof tag === "string") ||
      !Array.isArray(payload.remove_tags) ||
      !payload.remove_tags.every((tag) => typeof tag === "string") ||
      payload.confirmed !== true ||
      payload.recovery_copy_confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    const result = await client.request<unknown>("calibre.bulk-tags", {
      book_ids: payload.book_ids,
      add_tags: payload.add_tags,
      remove_tags: payload.remove_tags,
      confirmed: true,
      recovery_copy_confirmed: true,
    });
    if (
      !isRecord(result) ||
      !Array.isArray(result.books) ||
      !result.books.every((book) => isRecord(book) && Number.isSafeInteger(book.book_id))
    ) {
      throw new BackendClientError("invalid_protocol");
    }
    const refreshed = await loadPublicCalibreCatalog() as { books: Record<string, unknown>[] };
    const books = payload.book_ids.map((id) => refreshed.books.find((book) => book.book_id === id));
    if (books.some((book) => !book)) throw new BackendClientError("invalid_protocol");
    return { books };
  });

  ipcMain.handle("calibre:remove", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).some((key) => !["book_id", "confirmed", "recovery_copy_confirmed"].includes(key)) ||
      Object.keys(payload).length !== 3 ||
      !Number.isSafeInteger(payload.book_id) || Number(payload.book_id) < 1 ||
      payload.confirmed !== true ||
      payload.recovery_copy_confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    const result = await client.request<{ removed?: unknown }>("calibre.remove", {
      book_id: Number(payload.book_id),
      confirmed: true,
      recovery_copy_confirmed: true,
    });
    if (result.removed !== true) throw new BackendClientError("invalid_protocol");
    coverPaths.delete(Number(payload.book_id));
    return { removed: true };
  });

  ipcMain.handle("kindle:status", (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    return client.request("kindle.status", {});
  });

  ipcMain.handle("koreader:readings-preview", (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    return client.request("koreader.readings.preview", {});
  });

  ipcMain.handle("koreader:readings-events", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).some((key) => key !== "limit" && key !== "offset")
    ) {
      throw new BackendClientError("invalid_request");
    }
    const limit = payload.limit ?? 100;
    const offset = payload.offset ?? 0;
    if (
      !Number.isSafeInteger(limit) ||
      Number(limit) < 1 ||
      Number(limit) > 100 ||
      !Number.isSafeInteger(offset) ||
      Number(offset) < 0
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("koreader.readings.events", { limit, offset });
  });

  ipcMain.handle("koreader:readings-confirm", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 3 ||
      Object.keys(payload).some(
        (key) => !["import_id", "source_digest", "confirmed"].includes(key),
      ) ||
      typeof payload.import_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.import_id) ||
      typeof payload.source_digest !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.source_digest) ||
      payload.confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("koreader.readings.confirm", {
      import_id: payload.import_id,
      source_digest: payload.source_digest,
      confirmed: true,
    });
  });

  ipcMain.handle("koreader:readings-map", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 4 ||
      Object.keys(payload).some(
        (key) =>
          !["import_id", "koreader_book_id", "calibre_book_id", "confirmed"].includes(key),
      ) ||
      typeof payload.import_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.import_id) ||
      !Number.isSafeInteger(payload.koreader_book_id) ||
      Number(payload.koreader_book_id) < 1 ||
      !Number.isSafeInteger(payload.calibre_book_id) ||
      Number(payload.calibre_book_id) < 1 ||
      payload.confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("koreader.readings.map", {
      import_id: payload.import_id,
      koreader_book_id: payload.koreader_book_id,
      calibre_book_id: payload.calibre_book_id,
      confirmed: true,
    });
  });

  ipcMain.handle("calibre:readings-preview", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 1 ||
      typeof payload.import_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.import_id)
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("calibre.readings.preview", { import_id: payload.import_id });
  });

  ipcMain.handle("calibre:readings-apply", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 3 ||
      Object.keys(payload).some(
        (key) => !["import_id", "projection_id", "confirmed"].includes(key),
      ) ||
      typeof payload.import_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.import_id) ||
      typeof payload.projection_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.projection_id) ||
      payload.confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("calibre.readings.apply", {
      import_id: payload.import_id,
      projection_id: payload.projection_id,
      confirmed: true,
    });
  });

  ipcMain.handle("kindle:send", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    const allowedFormats = new Set(["AZW", "AZW3", "CBZ", "EPUB", "FB2", "MOBI", "PDF", "TXT"]);
    if (
      !isRecord(payload) ||
      Object.keys(payload).some((key) => !["book_id", "format", "filename", "recovery_copy_confirmed"].includes(key)) ||
      !Number.isSafeInteger(payload.book_id) || Number(payload.book_id) < 1 ||
      typeof payload.format !== "string" || !allowedFormats.has(payload.format) ||
      typeof payload.filename !== "string" || payload.filename.length < 1 || payload.filename.length > 180 ||
      payload.recovery_copy_confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("kindle.send", {
      book_id: Number(payload.book_id),
      format: payload.format,
      filename: payload.filename,
      recovery_copy_confirmed: true,
    });
  });

  ipcMain.handle("backup:list", (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    return client.request("backup.list", {});
  });

  ipcMain.handle("backup:create", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 3 ||
      typeof payload.calibre_only !== "boolean" ||
      typeof payload.verify_restore !== "boolean" ||
      payload.recovery_copy_confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("backup.create", {
      calibre_only: payload.calibre_only,
      verify_restore: payload.verify_restore,
      recovery_copy_confirmed: true,
    });
  });

  ipcMain.handle("backup:automation-status", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (payload !== undefined) throw new BackendClientError("invalid_request");
    return client.request("backup.automation.status", {});
  });

  ipcMain.handle("backup:automation-configure", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 3 ||
      typeof payload.daily_backup_enabled !== "boolean" ||
      typeof payload.daily_backup_time !== "string" ||
      !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(payload.daily_backup_time) ||
      typeof payload.backup_on_kindle_connect !== "boolean"
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("backup.automation.configure", {
      daily_backup_enabled: payload.daily_backup_enabled,
      daily_backup_time: payload.daily_backup_time,
      backup_on_kindle_connect: payload.backup_on_kindle_connect,
    });
  });

  ipcMain.handle("backup:select-restore-target", async (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    const selected = await dialog.showOpenDialog(window, {
      title: "Escolher onde criar a pasta de restauração",
      properties: ["openDirectory"],
    });
    if (selected.canceled || selected.filePaths.length !== 1) return null;

    let parentPath: string;
    try {
      parentPath = realpathSync.native(selected.filePaths[0]);
      if (!statSync(parentPath).isDirectory()) {
        throw new Error("not_directory");
      }
    } catch {
      throw new BackendClientError("invalid_request");
    }

    const timestamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
    const baseName = "KOReader-Ecosystem-restore-" + timestamp;
    let folderName: string;
    for (let suffix = 1; ; suffix += 1) {
      const candidate = suffix === 1 ? baseName : baseName + "-" + suffix;
      try {
        lstatSync(path.join(parentPath, candidate));
      } catch (error) {
        if (isNodeError(error) && error.code === "ENOENT") {
          folderName = candidate;
          break;
        }
        throw new BackendClientError("invalid_request");
      }
    }

    for (const [id, target] of pendingRestoreTargets) {
      if (target.expiresAt <= Date.now()) pendingRestoreTargets.delete(id);
    }
    if (pendingRestoreTargets.size >= 20) {
      const oldest = pendingRestoreTargets.keys().next().value;
      if (oldest) pendingRestoreTargets.delete(oldest);
    }
    const targetId = randomUUID();
    pendingRestoreTargets.set(targetId, {
      path: path.join(parentPath, folderName),
      expiresAt: Date.now() + 30 * 60 * 1000,
    });
    return {
      target_id: targetId,
      folder_name: folderName,
      parent_name: path.basename(parentPath) || parentPath,
    };
  });

  ipcMain.handle("backup:restore", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 3 ||
      typeof payload.snapshot_id !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.snapshot_id) ||
      typeof payload.target_id !== "string" ||
      payload.recovery_copy_confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    const target = pendingRestoreTargets.get(payload.target_id);
    if (!target || target.expiresAt <= Date.now()) {
      pendingRestoreTargets.delete(payload.target_id);
      throw new BackendClientError("invalid_request");
    }
    try {
      lstatSync(target.path);
      pendingRestoreTargets.delete(payload.target_id);
      throw new BackendClientError("invalid_request");
    } catch (error) {
      if (error instanceof BackendClientError) throw error;
      if (!isNodeError(error) || error.code !== "ENOENT") {
        pendingRestoreTargets.delete(payload.target_id);
        throw new BackendClientError("invalid_request");
      }
    }
    pendingRestoreTargets.delete(payload.target_id);
    const result = await client.request<unknown>("backup.restore", {
      snapshot_id: payload.snapshot_id,
      target: target.path,
      recovery_copy_confirmed: true,
    });
    if (
      !isRecord(result) ||
      result.snapshot_id !== payload.snapshot_id ||
      !Number.isSafeInteger(result.file_count) ||
      !Number.isSafeInteger(result.total_bytes) ||
      typeof result.profile_recovery_available !== "boolean"
    ) {
      throw new BackendClientError("invalid_protocol");
    }
    let profileSourceId: string | null = null;
    if (result.profile_recovery_available) {
      for (const [id, source] of pendingProfileSources) {
        if (source.expiresAt <= Date.now()) pendingProfileSources.delete(id);
      }
      if (pendingProfileSources.size < 20) {
        profileSourceId = randomUUID();
        pendingProfileSources.set(profileSourceId, {
          path: target.path,
          expiresAt: Date.now() + 30 * 60 * 1000,
        });
      }
    }
    return {
      snapshot_id: result.snapshot_id,
      file_count: result.file_count,
      total_bytes: result.total_bytes,
      profile_source_id: profileSourceId,
    };
  });

  ipcMain.handle("profile-recovery:select-source", async (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    const selected = await dialog.showOpenDialog(window, {
      title: "Selecionar uma pasta criada por restauração verificada",
      properties: ["openDirectory"],
    });
    if (selected.canceled || selected.filePaths.length !== 1) return null;
    let sourcePath: string;
    try {
      const selectedStats = lstatSync(selected.filePaths[0]);
      if (!selectedStats.isDirectory() || selectedStats.isSymbolicLink()) {
        throw new Error("not_directory");
      }
      sourcePath = realpathSync.native(selected.filePaths[0]);
      if (!statSync(sourcePath).isDirectory()) throw new Error("not_directory");
    } catch {
      throw new BackendClientError("invalid_request");
    }
    for (const [id, source] of pendingProfileSources) {
      if (source.expiresAt <= Date.now()) pendingProfileSources.delete(id);
    }
    if (pendingProfileSources.size >= 20) {
      const oldest = pendingProfileSources.keys().next().value;
      if (oldest) pendingProfileSources.delete(oldest);
    }
    const sourceId = randomUUID();
    pendingProfileSources.set(sourceId, {
      path: sourcePath,
      expiresAt: Date.now() + 30 * 60 * 1000,
    });
    return { source_id: sourceId, source_name: path.basename(sourcePath) || "Pasta selecionada" };
  });

  ipcMain.handle("profile-recovery:preview", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 1 ||
      typeof payload.source_id !== "string"
    ) {
      throw new BackendClientError("invalid_request");
    }
    const source = pendingProfileSources.get(payload.source_id);
    if (!source || source.expiresAt <= Date.now()) {
      pendingProfileSources.delete(payload.source_id);
      throw new BackendClientError("invalid_request");
    }
    const result = await client.request<unknown>("profile-recovery.preview", {
      restore_root: source.path,
    });
    if (
      !isRecord(result) ||
      typeof result.source_fingerprint !== "string" ||
      !/^[0-9a-f]{64}$/.test(result.source_fingerprint) ||
      !Number.isSafeInteger(result.schema_version) ||
      !isRecord(result.row_counts) ||
      !Object.values(result.row_counts).every((count) => Number.isSafeInteger(count) && Number(count) >= 0) ||
      typeof result.configured_calibre !== "boolean" ||
      typeof result.configured_restic !== "boolean" ||
      typeof result.configured_remote_backup !== "boolean"
    ) {
      throw new BackendClientError("invalid_protocol");
    }
    pendingProfileSources.delete(payload.source_id);
    for (const [id, preview] of pendingProfilePreviews) {
      if (preview.expiresAt <= Date.now()) pendingProfilePreviews.delete(id);
    }
    if (pendingProfilePreviews.size >= 20) {
      const oldest = pendingProfilePreviews.keys().next().value;
      if (oldest) pendingProfilePreviews.delete(oldest);
    }
    const previewId = randomUUID();
    const sourceName = path.basename(source.path) || "Pasta restaurada";
    pendingProfilePreviews.set(previewId, {
      path: source.path,
      sourceName,
      fingerprint: result.source_fingerprint,
      expiresAt: Date.now() + 10 * 60 * 1000,
    });
    return {
      preview_id: previewId,
      source_name: sourceName,
      schema_version: result.schema_version,
      row_counts: result.row_counts,
      configured_calibre: result.configured_calibre,
      configured_restic: result.configured_restic,
      configured_remote_backup: result.configured_remote_backup,
    };
  });

  ipcMain.handle("profile-recovery:apply", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 2 ||
      typeof payload.preview_id !== "string" ||
      payload.confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    const preview = pendingProfilePreviews.get(payload.preview_id);
    pendingProfilePreviews.delete(payload.preview_id);
    if (!preview || preview.expiresAt <= Date.now()) throw new BackendClientError("invalid_request");
    const result = await client.request<unknown>("profile-recovery.apply", {
      restore_root: preview.path,
      source_fingerprint: preview.fingerprint,
      confirmed: true,
    });
    if (
      !isRecord(result) ||
      typeof result.checkpoint_id !== "string" ||
      !/^[0-9a-f]{32}$/.test(result.checkpoint_id) ||
      !Number.isSafeInteger(result.schema_version) ||
      result.requires_restart !== true
    ) {
      throw new BackendClientError("invalid_protocol");
    }
    return result;
  });

  ipcMain.handle("app:restart", (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    setTimeout(() => {
      app.relaunch();
      app.quit();
    }, 100);
    return { scheduled: true };
  });

  ipcMain.handle("backup:credentials", (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    return client.request("backup.credentials", {});
  });

  ipcMain.handle("backup:configure-password", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 1 ||
      typeof payload.password !== "string" ||
      !payload.password.trim() ||
      payload.password.length > 4096
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("backup.configure-password", { password: payload.password });
  });

  ipcMain.handle("hardcover:status", (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    return client.request("hardcover.status", {});
  });

  ipcMain.handle("hardcover:current-reading", (event: IpcMainInvokeEvent) => {
    assertTrusted(event);
    return client.request("hardcover.current-reading", {});
  });

  ipcMain.handle("hardcover:list", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).some((key) => key !== "limit" && key !== "offset")
    ) {
      throw new BackendClientError("invalid_request");
    }
    const limit = payload.limit ?? 100;
    const offset = payload.offset ?? 0;
    if (
      !Number.isSafeInteger(limit) ||
      Number(limit) < 1 ||
      Number(limit) > 100 ||
      !Number.isSafeInteger(offset) ||
      Number(offset) < 0
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("hardcover.list", { limit, offset });
  });

  ipcMain.handle("hardcover:configure", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 1 ||
      typeof payload.token !== "string" ||
      !payload.token.trim() ||
      payload.token.length > 4096
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("hardcover.configure", { token: payload.token });
  });

  ipcMain.handle("hardcover:reconcile", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 1 ||
      payload.confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("hardcover.reconcile", { confirmed: true });
  });

  ipcMain.handle("hardcover:outbox-list", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (!isRecord(payload) || Object.keys(payload).length !== 0) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("hardcover.outbox.list", {});
  });

  ipcMain.handle("hardcover:outbox-resolve", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).length !== 3 ||
      typeof payload.operation_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(payload.operation_id) ||
      (payload.resolution !== "confirmed_remote" && payload.resolution !== "closed_locally") ||
      payload.confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("hardcover.outbox.resolve", {
      operation_id: payload.operation_id,
      resolution: payload.resolution,
      confirmed: true,
    });
  });

  ipcMain.handle("platform-csv:select-import", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) || Object.keys(payload).length !== 2 ||
      !isPlatformCsvSource(payload.source) || !isDialogLanguage(payload.language)
    ) {
      throw new BackendClientError("invalid_request");
    }
    const english = payload.language === "en";
    const sourceLabels = english
      ? { goodreads: "Goodreads", storygraph: "StoryGraph", maratona: "Maratona" }
      : { goodreads: "Goodreads", storygraph: "StoryGraph", maratona: "Maratona" };
    const selected = await dialog.showOpenDialog(window, {
      title: english ? `Import ${sourceLabels[payload.source]} reading CSV` : `Importar CSV de leitura do ${sourceLabels[payload.source]}`,
      properties: ["openFile"],
      filters: [{ name: "CSV", extensions: ["csv"] }],
    });
    if (selected.canceled || selected.filePaths.length !== 1) return null;
    const safePath = validateSelectedCsv(selected.filePaths[0]);
    const inspected = await client.request<unknown>("platform-csv.inspect", {
      path: safePath,
      source: payload.source,
    });
    if (
      !isRecord(inspected) || inspected.source !== payload.source ||
      typeof inspected.source_digest !== "string" || !/^[0-9a-f]{64}$/.test(inspected.source_digest) ||
      !Array.isArray(inspected.headers) || !isRecord(inspected.suggested_mapping)
    ) {
      throw new BackendClientError("invalid_protocol");
    }
    const headers: unknown[] = inspected.headers;
    const suggestedMapping = inspected.suggested_mapping;
    if (
      headers.length > MAX_PLATFORM_CSV_HEADERS ||
      !headers.every((header) => typeof header === "string" && header.length > 0 && header.length <= 256) ||
      new Set(headers).size !== headers.length ||
      Object.keys(suggestedMapping).length !== platformCsvMappingFields.length ||
      !platformCsvMappingFields.every((field) => {
        const value = suggestedMapping[field];
        return value === null || (typeof value === "string" && headers.includes(value));
      })
    ) throw new BackendClientError("invalid_protocol");
    const suggestedHeaders = platformCsvMappingFields
      .map((field) => suggestedMapping[field])
      .filter((value): value is string => typeof value === "string");
    if (new Set(suggestedHeaders).size !== suggestedHeaders.length) {
      throw new BackendClientError("invalid_protocol");
    }
    const now = Date.now();
    for (const [selectionId, pending] of pendingPlatformCsvSelections) {
      if (pending.expiresAt <= now) pendingPlatformCsvSelections.delete(selectionId);
    }
    while (pendingPlatformCsvSelections.size >= MAX_PENDING_PLATFORM_CSV_SELECTIONS) {
      const oldestId = pendingPlatformCsvSelections.keys().next().value;
      if (oldestId === undefined) break;
      pendingPlatformCsvSelections.delete(oldestId);
    }
    const selectionId = randomUUID();
    pendingPlatformCsvSelections.set(selectionId, {
      path: safePath,
      source: payload.source,
      sourceDigest: inspected.source_digest,
      headers: headers as string[],
      expiresAt: now + PLATFORM_CSV_SELECTION_TTL_MS,
    });
    return {
      selection_id: selectionId,
      source: payload.source,
      source_digest: inspected.source_digest,
      headers: headers as string[],
      suggested_mapping: suggestedMapping,
    };
  });

  ipcMain.handle("platform-csv:preview-mapped", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) || Object.keys(payload).length !== 2 ||
      typeof payload.selection_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(payload.selection_id) ||
      !isRecord(payload.column_mapping) ||
      Object.keys(payload.column_mapping).length !== platformCsvMappingFields.length ||
      !platformCsvMappingFields.every((field) => Object.hasOwn(payload.column_mapping as object, field))
    ) {
      throw new BackendClientError("invalid_request");
    }
    const pending = pendingPlatformCsvSelections.get(payload.selection_id);
    if (!pending || pending.expiresAt <= Date.now()) {
      pendingPlatformCsvSelections.delete(payload.selection_id);
      throw new BackendClientError("platform_csv_selection_expired");
    }
    const mapping = payload.column_mapping;
    const selectedHeaders = platformCsvMappingFields
      .map((field) => mapping[field])
      .filter((value): value is string => typeof value === "string");
    if (
      platformCsvMappingFields.some((field) =>
        mapping[field] !== null && typeof mapping[field] !== "string"
      ) ||
      typeof mapping.title !== "string" || typeof mapping.author !== "string" ||
      selectedHeaders.length !== new Set(selectedHeaders).size ||
      selectedHeaders.some((header) => !pending.headers.includes(header))
    ) {
      throw new BackendClientError("invalid_request");
    }
    const preview = await client.request("platform-csv.preview-mapped", {
      path: pending.path,
      source: pending.source,
      source_digest: pending.sourceDigest,
      column_mapping: mapping,
    });
    pendingPlatformCsvSelections.delete(payload.selection_id);
    return preview;
  });

  ipcMain.handle("platform-csv:list", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) ||
      Object.keys(payload).some((key) => !["limit", "offset", "source"].includes(key)) ||
      (payload.source !== undefined && !isPlatformCsvSource(payload.source))
    ) {
      throw new BackendClientError("invalid_request");
    }
    const limit = payload.limit ?? 100;
    const offset = payload.offset ?? 0;
    if (
      !Number.isSafeInteger(limit) || Number(limit) < 1 || Number(limit) > 100 ||
      !Number.isSafeInteger(offset) || Number(offset) < 0 || Number(offset) > 50_000
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("platform-csv.list", {
      limit,
      offset,
      ...(payload.source === undefined ? {} : { source: payload.source }),
    });
  });

  ipcMain.handle("platform-csv:page", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) || Object.keys(payload).some((key) => !["import_id", "limit", "offset"].includes(key)) ||
      typeof payload.import_id !== "string" || !/^[0-9a-f]{64}$/.test(payload.import_id)
    ) {
      throw new BackendClientError("invalid_request");
    }
    const limit = payload.limit ?? 100;
    const offset = payload.offset ?? 0;
    if (
      !Number.isSafeInteger(limit) || Number(limit) < 1 || Number(limit) > 100 ||
      !Number.isSafeInteger(offset) || Number(offset) < 0 || Number(offset) > 50_000
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("platform-csv.page", { import_id: payload.import_id, limit, offset });
  });

  ipcMain.handle("platform-csv:confirm", (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) || Object.keys(payload).length !== 3 ||
      typeof payload.import_id !== "string" || !/^[0-9a-f]{64}$/.test(payload.import_id) ||
      typeof payload.source_digest !== "string" || !/^[0-9a-f]{64}$/.test(payload.source_digest) ||
      payload.confirmed !== true
    ) {
      throw new BackendClientError("invalid_request");
    }
    return client.request("platform-csv.confirm", {
      import_id: payload.import_id,
      source_digest: payload.source_digest,
      confirmed: true,
    });
  });

  ipcMain.handle("platform-csv:export", async (event: IpcMainInvokeEvent, payload: unknown) => {
    assertTrusted(event);
    if (
      !isRecord(payload) || Object.keys(payload).length !== 3 ||
      typeof payload.import_id !== "string" || !/^[0-9a-f]{64}$/.test(payload.import_id) ||
      typeof payload.include_reviews !== "boolean" || !isDialogLanguage(payload.language)
    ) {
      throw new BackendClientError("invalid_request");
    }
    const selected = await dialog.showSaveDialog(window, {
      title: payload.language === "en" ? "Export reading history" : "Exportar histórico de leitura",
      defaultPath: "reading-history-goodreads.csv",
      filters: [{ name: "CSV", extensions: ["csv"] }],
    });
    if (selected.canceled || !selected.filePath) return null;
    if (path.extname(selected.filePath).toLowerCase() !== ".csv") {
      throw new BackendClientError("invalid_request");
    }
    return client.request("platform-csv.export", {
      import_id: payload.import_id,
      path: selected.filePath,
      include_reviews: payload.include_reviews,
      confirmed: true,
    });
  });

  return () => {
    ipcMain.removeHandler("app:status");
    ipcMain.removeHandler("calibre:list");
    ipcMain.removeHandler("calibre:select-import");
    ipcMain.removeHandler("calibre:add-import");
    ipcMain.removeHandler("calibre:edit");
    ipcMain.removeHandler("calibre:remove");
    ipcMain.removeHandler("kindle:status");
    ipcMain.removeHandler("kindle:send");
    ipcMain.removeHandler("koreader:readings-preview");
    ipcMain.removeHandler("koreader:readings-events");
    ipcMain.removeHandler("koreader:readings-confirm");
    ipcMain.removeHandler("koreader:readings-map");
    ipcMain.removeHandler("calibre:readings-preview");
    ipcMain.removeHandler("calibre:readings-apply");
    ipcMain.removeHandler("backup:list");
    ipcMain.removeHandler("backup:create");
    ipcMain.removeHandler("backup:automation-status");
    ipcMain.removeHandler("backup:automation-configure");
    ipcMain.removeHandler("backup:select-restore-target");
    ipcMain.removeHandler("backup:restore");
    ipcMain.removeHandler("backup:credentials");
    ipcMain.removeHandler("backup:configure-password");
    ipcMain.removeHandler("profile-recovery:select-source");
    ipcMain.removeHandler("profile-recovery:preview");
    ipcMain.removeHandler("profile-recovery:apply");
    ipcMain.removeHandler("app:restart");
    ipcMain.removeHandler("hardcover:status");
    ipcMain.removeHandler("hardcover:current-reading");
    ipcMain.removeHandler("hardcover:list");
    ipcMain.removeHandler("hardcover:configure");
    ipcMain.removeHandler("hardcover:reconcile");
    ipcMain.removeHandler("hardcover:outbox-list");
    ipcMain.removeHandler("hardcover:outbox-resolve");
    ipcMain.removeHandler("platform-csv:select-import");
    ipcMain.removeHandler("platform-csv:preview-mapped");
    ipcMain.removeHandler("platform-csv:list");
    ipcMain.removeHandler("platform-csv:page");
    ipcMain.removeHandler("platform-csv:confirm");
    ipcMain.removeHandler("platform-csv:export");
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}
