import { app, BrowserWindow, nativeImage, net, protocol } from "electron";
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import type { AllowedOperation, BackendClient, ServiceStatus } from "../shared/contracts";
import {
  BackendClientError,
  createServiceEnvironment,
  startBackendProcess,
} from "./backend-client";
import type { BackendProcessHost } from "./backend-client";
import { registerIpcHandlers } from "./ipc-handlers";
import { scheduledBackupLaunch, scheduledBackupTaskLauncher } from "./scheduled-backup-launch";

declare const MAIN_WINDOW_VITE_DEV_SERVER_URL: string | undefined;
declare const MAIN_WINDOW_VITE_NAME: string;

const appProtocol = "koreader";
const appOrigin = appProtocol + "://bundle";
const squirrelAppName = "koreader-ecosystem";
const isSmokeTest = process.argv.includes("--smoke-test");
const isScheduledBackup = process.argv.includes("--scheduled-backup");
const calibreCoverPaths = new Map<number, string>();
const calibreCoverCache = new Map<
  number,
  { path: string; modifiedAt: number; size: number; bytes: Uint8Array }
>();
const maxCalibreCoverBytes = 12 * 1024 * 1024;
const maxCachedCalibreCovers = 64;

if (process.platform === "win32") {
  app.setAppUserModelId("com.squirrel.koreader-ecosystem.KOReaderEcosystem");
}

function ensureWindowsUninstallIcon(): void {
  if (process.platform !== "win32" || !app.isPackaged || isSmokeTest) return;

  const registryKey = `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${squirrelAppName}`;
  const iconPath = path.join(process.resourcesPath, "KOReaderEcosystem.ico");
  if (!existsSync(iconPath)) return;

  try {
    const query = spawnSync("reg.exe", ["query", registryKey], {
      encoding: "utf8",
      windowsHide: true,
    });
    if (query.status !== 0) return;

    const currentIcon = query.stdout.match(/^\s*DisplayIcon\s+REG_SZ\s+(.+)$/im)?.[1]?.trim();
    if (currentIcon?.toLocaleLowerCase() === iconPath.toLocaleLowerCase()) return;

    spawnSync(
      "reg.exe",
      ["add", registryKey, "/v", "DisplayIcon", "/t", "REG_SZ", "/d", iconPath, "/f"],
      { stdio: "ignore", windowsHide: true },
    );
  } catch {
    // An icon registration failure must not prevent the reader from opening.
  }
}

protocol.registerSchemesAsPrivileged([
  {
    scheme: appProtocol,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
    },
  },
]);

function isInsideDirectory(directory: string, candidate: string): boolean {
  const relative = path.relative(directory, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function isAllowedNavigation(destination: string, expectedOrigin: string): boolean {
  try {
    return new URL(destination).origin === expectedOrigin;
  } catch {
    return false;
  }
}

function imageResponse(bytes: Uint8Array, status = 200): Response {
  const body = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(body).set(bytes);
  return new Response(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "image/jpeg",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

async function loadCalibreCover(bookId: number): Promise<Response> {
  const coverPath = calibreCoverPaths.get(bookId);
  if (!coverPath) return new Response("Capa não encontrada", { status: 404 });

  try {
    const details = await fs.stat(coverPath);
    if (!details.isFile() || details.size < 1 || details.size > maxCalibreCoverBytes) {
      return new Response("Capa indisponível", { status: 404 });
    }

    const cached = calibreCoverCache.get(bookId);
    if (
      cached &&
      cached.path === coverPath &&
      cached.modifiedAt === details.mtimeMs &&
      cached.size === details.size
    ) {
      calibreCoverCache.delete(bookId);
      calibreCoverCache.set(bookId, cached);
      return imageResponse(cached.bytes);
    }

    const image = nativeImage.createFromBuffer(await fs.readFile(coverPath));
    if (image.isEmpty()) return new Response("Imagem de capa inválida", { status: 415 });

    const thumbnail = image.resize({ width: 120, height: 180, quality: "good" });
    const bytes = Uint8Array.from(thumbnail.toJPEG(84));
    if (calibreCoverCache.size >= maxCachedCalibreCovers) {
      const oldestBookId = calibreCoverCache.keys().next().value;
      if (oldestBookId !== undefined) calibreCoverCache.delete(oldestBookId);
    }
    const cachedCover = {
      path: coverPath,
      modifiedAt: details.mtimeMs,
      size: details.size,
      bytes,
    };
    calibreCoverCache.set(bookId, cachedCover);
    return imageResponse(bytes);
  } catch {
    return new Response("Capa não encontrada", { status: 404 });
  }
}

function handleSquirrelEvent(): boolean {
  if (process.platform !== "win32") return false;
  const event = process.argv[1];
  const executableName = path.basename(process.execPath);
  const updateExecutable = path.resolve(path.dirname(process.execPath), "..", "Update.exe");
  const iconPath = path.join(process.resourcesPath, "KOReaderEcosystem.ico");
  let updateArguments: string[] | undefined;

  if (event === "--squirrel-install" || event === "--squirrel-updated") {
    updateArguments = ["--createShortcut", executableName, "--icon", iconPath];
  } else if (event === "--squirrel-uninstall") {
    updateArguments = ["--removeShortcut", executableName];
  } else if (event !== "--squirrel-obsolete") {
    return false;
  }

  if (updateArguments) {
    if (event === "--squirrel-install" || event === "--squirrel-updated") {
      ensureWindowsUninstallIcon();
    }

    try {
      const update = spawn(updateExecutable, updateArguments, {
        cwd: path.dirname(updateExecutable),
        detached: true,
        shell: false,
        windowsHide: true,
        stdio: "ignore",
      });
      update.once("error", () => undefined);
      update.unref();
    } catch {
      // Squirrel events must exit quickly even if shortcut creation is unavailable.
    }
  }
  setTimeout(app.quit, 1000);
  return true;
}

function findExecutable(name: string, environment: NodeJS.ProcessEnv): string | undefined {
  const pathValue = environment.PATH;
  if (!pathValue) return undefined;
  const executableNames = process.platform === "win32" ? [name, `${name}.exe`] : [name];

  for (const directory of pathValue.split(path.delimiter)) {
    for (const executableName of executableNames) {
      const candidate = path.resolve(directory, executableName);
      if (existsSync(candidate)) return candidate;
    }
  }
  return undefined;
}

function startDesktopBackend(): BackendProcessHost | undefined {
  const environment = createServiceEnvironment(process.env);

  if (app.isPackaged) {
    if (process.platform === "win32") {
      const taskLauncher = scheduledBackupTaskLauncher(process.execPath, process.platform);
      if (taskLauncher) environment.KOREADER_ECOSYSTEM_SCHEDULER_EXECUTABLE = taskLauncher;
    }
    const executable = path.join(process.resourcesPath, "sidecar", "KOReaderEcosystem-Service.exe");
    if (!existsSync(executable)) return undefined;
    return startBackendProcess({
      command: executable,
      args: [],
      cwd: process.resourcesPath,
      env: environment,
    });
  }

  const repositoryRoot = path.resolve(__dirname, "../../..");
  const uvExecutable = findExecutable("uv", environment);
  if (!uvExecutable) return undefined;
  return startBackendProcess({
    command: uvExecutable,
    args: ["run", "--project", repositoryRoot, "koreader-ecosystem-service"],
    cwd: repositoryRoot,
    env: environment,
  });
}

function unavailableBackend(): BackendClient {
  return {
    request: async () => {
      throw new BackendClientError("service_unavailable");
    },
  };
}

function createSmokeTestClient(backend: BackendClient): BackendClient {
  return {
    request<T>(operation: AllowedOperation, payload: Record<string, unknown>): Promise<T> {
      if (operation === "calibre.list") {
        return Promise.reject(new BackendClientError("operation_not_allowed"));
      }
      return backend.request<T>(operation, payload);
    },
  };
}

async function registerAppProtocol(): Promise<void> {
  const rendererDirectory = path.resolve(
    __dirname,
    "../renderer",
    MAIN_WINDOW_VITE_NAME,
  );

  await protocol.handle(appProtocol, async (request) => {
    let requestUrl: URL;
    try {
      requestUrl = new URL(request.url);
    } catch {
      return new Response("Solicitação inválida", { status: 400 });
    }

    if (requestUrl.hostname !== "bundle") {
      return new Response("Recurso não encontrado", { status: 404 });
    }

    const coverMatch = /^\/covers\/([1-9]\d*)$/.exec(requestUrl.pathname);
    if (coverMatch) {
      if (request.method !== "GET") {
        return new Response("Método não permitido", { status: 405 });
      }
      const bookId = Number(coverMatch[1]);
      if (!Number.isSafeInteger(bookId)) {
        return new Response("Solicitação inválida", { status: 400 });
      }
      return loadCalibreCover(bookId);
    }

    let requestedPath: string;
    try {
      requestedPath = decodeURIComponent(requestUrl.pathname);
    } catch {
      return new Response("Solicitação inválida", { status: 400 });
    }
    if (requestedPath === "/") requestedPath = "/index.html";

    const assetPath = path.resolve(rendererDirectory, "." + requestedPath);
    if (!isInsideDirectory(rendererDirectory, assetPath)) {
      return new Response("Recurso inválido", { status: 400 });
    }

    try {
      await fs.access(assetPath);
      return await net.fetch(pathToFileURL(assetPath).toString());
    } catch {
      return new Response("Recurso não encontrado", { status: 404 });
    }
  });
}

function createMainWindow(client: BackendClient): BrowserWindow {
  const developmentUrl = MAIN_WINDOW_VITE_DEV_SERVER_URL;
  const expectedOrigin = developmentUrl
    ? new URL(developmentUrl).origin
    : appOrigin;

  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 720,
    minHeight: 560,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: "#202b3b",
    icon: app.isPackaged
      ? path.join(process.resourcesPath, "KOReaderEcosystem.ico")
      : path.resolve(__dirname, "../../..", "assets", "KOReaderEcosystem.ico"),
    webPreferences: {
      preload: path.join(__dirname, "index.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  const unregisterHandlers = registerIpcHandlers(
    client,
    window,
    expectedOrigin,
    calibreCoverPaths,
  );
  window.once("closed", unregisterHandlers);

  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, destination) => {
    if (!isAllowedNavigation(destination, expectedOrigin)) event.preventDefault();
  });
  window.once("ready-to-show", () => {
    if (!isSmokeTest) window.show();
  });

  if (developmentUrl) {
    void window.loadURL(developmentUrl);
  } else {
    void window.loadURL(appOrigin + "/index.html");
  }
  return window;
}

function runPackagedSmokeTest(window: BrowserWindow, client: BackendClient): void {
  if (!isSmokeTest) return;

  let finished = false;
  const finish = async (exitCode: number) => {
    if (finished) return;
    finished = true;
    clearTimeout(timeout);
    try {
      await backendHost?.stop();
    } catch {
      exitCode = 1;
    }
    app.exit(exitCode);
  };

  const timeout = setTimeout(() => void finish(1), 15_000);
  timeout.unref();
  window.once("ready-to-show", () => {
    void client
      .request<ServiceStatus>("system.status", {})
      .then((status) => {
        if (status.status !== "ready" || status.protocol_version !== 1) {
          throw new BackendClientError("invalid_protocol");
        }
      })
      .then(() => finish(0))
      .catch(() => finish(1));
  });
}

let backendHost: BackendProcessHost | undefined;
let stoppingBackend = false;

const handledSquirrelEvent = handleSquirrelEvent();

if (!handledSquirrelEvent) {
  app.whenReady().then(async () => {
    if (isScheduledBackup) {
      const launch = scheduledBackupLaunch(process.resourcesPath, process.platform);
      if (!app.isPackaged || !launch) {
        app.exit(1);
        return;
      }
      try {
        const child = spawn(launch.command, launch.args, {
          cwd: launch.cwd,
          env: createServiceEnvironment(process.env),
          windowsHide: launch.windowsHide,
          shell: launch.shell,
          stdio: launch.stdio,
        });
        let finished = false;
        const finish = (code: number) => {
          if (finished) return;
          finished = true;
          app.exit(code);
        };
        child.once("error", () => finish(1));
        child.once("exit", (code) => finish(code === 0 ? 0 : 1));
      } catch {
        app.exit(1);
      }
      return;
    }
    ensureWindowsUninstallIcon();
    await registerAppProtocol();
    try {
      backendHost = startDesktopBackend();
    } catch {
      backendHost = undefined;
    }
    const serviceClient = backendHost?.client ?? unavailableBackend();
    const client = isSmokeTest ? createSmokeTestClient(serviceClient) : serviceClient;
    const window = createMainWindow(client);
    runPackagedSmokeTest(window, client);

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow(client);
    });
  });
}

app.on("before-quit", (event) => {
  if (!backendHost || stoppingBackend) return;
  event.preventDefault();
  stoppingBackend = true;
  void backendHost.stop().finally(() => {
    backendHost = undefined;
    app.quit();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
