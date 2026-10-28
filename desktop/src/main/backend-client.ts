import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import type { ChildProcess, SpawnOptions } from "node:child_process";
import type { AllowedOperation, BackendClient } from "../shared/contracts";

const PROTOCOL_VERSION = 1;
const MAX_LINE_BYTES = 1024 * 1024;
const DEFAULT_TIMEOUT_MS = 180_000;
const KINDLE_TRANSFER_TIMEOUT_MS = 8 * 60 * 60 * 1000;
const BACKUP_OPERATION_TIMEOUT_MS = 8 * 60 * 60 * 1000;
const PROFILE_RECOVERY_TIMEOUT_MS = 30 * 60 * 1000;
const KNOWN_OPERATIONS = new Set<AllowedOperation>([
  "system.status",
  "local-library.list",
  "local-library.get",
  "local-library.add",
  "local-library.save",
  "calibre.list",
  "calibre.preview-import",
  "calibre.add-import",
  "calibre.edit",
  "calibre.remove",
  "kindle.status",
  "kindle.send",
  "hardcover.status",
  "hardcover.list",
  "hardcover.current-reading",
  "hardcover.configure",
  "hardcover.reconcile",
  "hardcover.outbox.list",
  "hardcover.outbox.resolve",
  "koreader.readings.preview",
  "koreader.readings.events",
  "koreader.readings.confirm",
  "koreader.readings.map",
  "calibre.readings.preview",
  "calibre.readings.apply",
  "backup.list",
  "backup.create",
  "backup.automation.status",
  "backup.automation.configure",
  "backup.restore",
  "backup.credentials",
  "backup.configure-password",
  "profile-recovery.preview",
  "profile-recovery.apply",
  "platform-csv.inspect",
  "platform-csv.preview-mapped",
  "platform-csv.page",
  "platform-csv.list",
  "platform-csv.confirm",
  "platform-csv.export",
]);

const SAFE_SERVICE_ENVIRONMENT = [
  "PATH",
  "LOCALAPPDATA",
  "USERPROFILE",
  "SYSTEMROOT",
  "WINDIR",
  "TEMP",
  "TMP",
  "HOMEDRIVE",
  "HOMEPATH",
  "PROGRAMDATA",
  "READING_CALIBRE_LIBRARY",
  "READING_STATE_ROOT",
  "READING_RESTIC_REPOSITORY",
] as const;

export type BackendErrorCode =
  | "invalid_request"
  | "invalid_protocol"
  | "operation_not_allowed"
  | "operation_timeout"
  | "service_unavailable"
  | "configuration_unavailable"
  | "calibre_unavailable"
  | "operation_failed"
  | "untrusted_sender";

export class BackendClientError extends Error {
  readonly code: BackendErrorCode | string;

  constructor(code: BackendErrorCode | string) {
    super(code);
    this.name = "BackendClientError";
    this.code = code;
  }
}

interface PendingRequest {
  resolve(value: unknown): void;
  reject(error: BackendClientError): void;
  timeout: NodeJS.Timeout;
}

export interface JsonlBackendClientOptions {
  timeoutMs?: number;
  onProtocolFailure?: () => void;
}

export class JsonlBackendClient implements BackendClient {
  private readonly pending = new Map<string, PendingRequest>();
  private readonly expired = new Set<string>();
  private buffer = "";
  private closed = false;

  constructor(
    private readonly writeLine: (line: string) => void,
    private readonly options: JsonlBackendClientOptions = {},
  ) {}

  request<T>(
    operation: AllowedOperation,
    payload: Record<string, unknown>,
  ): Promise<T> {
    if (this.closed) {
      return Promise.reject(new BackendClientError("service_unavailable"));
    }
    if (
      !KNOWN_OPERATIONS.has(operation) ||
      !isPlainRecord(payload)
    ) {
      return Promise.reject(new BackendClientError("invalid_request"));
    }

    const id = createRequestId();
    let serialized: string;
    try {
      serialized = JSON.stringify({
        version: PROTOCOL_VERSION,
        id,
        operation,
        payload,
      });
    } catch {
      return Promise.reject(new BackendClientError("invalid_request"));
    }
    if (Buffer.byteLength(serialized, "utf8") > MAX_LINE_BYTES) {
      return Promise.reject(new BackendClientError("invalid_request"));
    }

    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        const entry = this.pending.get(id);
        if (!entry) return;
        this.pending.delete(id);
        this.rememberExpired(id);
        reject(new BackendClientError("operation_timeout"));
      }, this.options.timeoutMs ?? (
        operation === "kindle.send"
          ? KINDLE_TRANSFER_TIMEOUT_MS
          : operation === "profile-recovery.preview" || operation === "profile-recovery.apply"
            ? PROFILE_RECOVERY_TIMEOUT_MS
            : operation === "backup.create" || operation === "backup.restore"
              ? BACKUP_OPERATION_TIMEOUT_MS
              : DEFAULT_TIMEOUT_MS
      ));

      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
        timeout,
      });

      try {
        this.writeLine(serialized + "\n");
      } catch {
        const entry = this.pending.get(id);
        if (entry) {
          clearTimeout(entry.timeout);
          this.pending.delete(id);
        }
        reject(new BackendClientError("service_unavailable"));
      }
    });
  }

  acceptStdout(chunk: string): void {
    if (this.closed || !chunk) return;
    this.buffer += chunk;

    while (true) {
      const newlineIndex = this.buffer.indexOf("\n");
      if (newlineIndex < 0) {
        if (Buffer.byteLength(this.buffer, "utf8") > MAX_LINE_BYTES) {
          this.failProtocol();
        }
        return;
      }

      const line = this.buffer.slice(0, newlineIndex).replace(/\r$/, "");
      this.buffer = this.buffer.slice(newlineIndex + 1);
      if (!line || Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) {
        this.failProtocol();
        return;
      }
      if (!this.acceptResponse(line)) return;
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.buffer = "";
    this.rejectPending("service_unavailable");
  }

  private acceptResponse(line: string): boolean {
    let response: unknown;
    try {
      response = JSON.parse(line);
    } catch {
      this.failProtocol();
      return false;
    }
    if (
      !isPlainRecord(response) ||
      response.version !== PROTOCOL_VERSION ||
      typeof response.id !== "string" ||
      typeof response.ok !== "boolean"
    ) {
      this.failProtocol();
      return false;
    }

    const entry = this.pending.get(response.id);
    if (!entry) {
      if (this.expired.delete(response.id)) return true;
      this.failProtocol();
      return false;
    }

    clearTimeout(entry.timeout);
    this.pending.delete(response.id);
    if (response.ok) {
      if (!Object.hasOwn(response, "result")) {
        this.failProtocol();
        return false;
      }
      entry.resolve(response.result);
      return true;
    }

    const error = response.error;
    if (!isPlainRecord(error) || typeof error.code !== "string") {
      this.failProtocol();
      return false;
    }
    entry.reject(new BackendClientError(safeErrorCode(error.code)));
    return true;
  }

  private failProtocol(): void {
    if (this.closed) return;
    this.closed = true;
    this.buffer = "";
    this.rejectPending("invalid_protocol");
    this.options.onProtocolFailure?.();
  }

  private rejectPending(code: BackendErrorCode): void {
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timeout);
      entry.reject(new BackendClientError(code));
      this.pending.delete(id);
    }
  }

  private rememberExpired(id: string): void {
    this.expired.add(id);
    if (this.expired.size > 256) {
      const oldest = this.expired.values().next().value;
      if (oldest) this.expired.delete(oldest);
    }
  }
}

export interface BackendSpawnOptions {
  cwd: string;
  env: NodeJS.ProcessEnv;
  shell: false;
  windowsHide: true;
  stdio: ["pipe", "pipe", "ignore"];
}

export function createBackendSpawnOptions(
  cwd: string,
  env: NodeJS.ProcessEnv,
): BackendSpawnOptions {
  return {
    cwd: path.resolve(cwd),
    env,
    shell: false,
    windowsHide: true,
    stdio: ["pipe", "pipe", "ignore"],
  };
}

export function createServiceEnvironment(
  source: NodeJS.ProcessEnv,
): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = { PYTHONUTF8: "1" };
  for (const key of SAFE_SERVICE_ENVIRONMENT) {
    const value = source[key];
    if (value !== undefined) environment[key] = value;
  }
  return environment;
}

export interface BackendProcessOptions {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
  timeoutMs?: number;
}

export interface BackendProcessHost {
  client: JsonlBackendClient;
  stop(): Promise<void>;
}

type SpawnFunction = (
  command: string,
  args: string[],
  options: SpawnOptions,
) => ChildProcess;

export function startBackendProcess(
  options: BackendProcessOptions,
  spawnProcess: SpawnFunction = spawn,
): BackendProcessHost {
  const child = spawnProcess(
    options.command,
    options.args,
    createBackendSpawnOptions(options.cwd, options.env),
  );
  const input = child.stdin;
  const output = child.stdout;
  if (!input || !output) {
    child.kill();
    throw new BackendClientError("service_unavailable");
  }

  const client = new JsonlBackendClient(
    (line) => {
      input.write(line);
    },
    {
      timeoutMs: options.timeoutMs,
      onProtocolFailure: () => child.kill(),
    },
  );
  output.setEncoding("utf8");
  output.on("data", (chunk: string | Buffer) => {
    client.acceptStdout(typeof chunk === "string" ? chunk : chunk.toString("utf8"));
  });
  input.on("error", () => client.close());
  child.once("error", () => client.close());
  child.once("exit", () => client.close());

  let stopping = false;
  const stop = (): Promise<void> => {
    if (stopping) return Promise.resolve();
    stopping = true;
    client.close();
    return new Promise((resolve) => {
      let finished = false;
      const finish = () => {
        if (finished) return;
        finished = true;
        clearTimeout(forceKill);
        resolve();
      };
      child.once("exit", finish);
      child.once("error", finish);
      const forceKill = setTimeout(() => {
        child.kill();
        finish();
      }, 1_500);
      forceKill.unref();
      if (child.exitCode !== null || child.signalCode !== null) {
        finish();
        return;
      }
      try {
        input.end();
      } catch {
        child.kill();
        finish();
      }
    });
  };

  return { client, stop };
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function createRequestId(): string {
  return randomUUID();
}

function safeErrorCode(code: string): string {
  return /^[a-z][a-z0-9_]{0,63}$/.test(code) ? code : "operation_failed";
}
