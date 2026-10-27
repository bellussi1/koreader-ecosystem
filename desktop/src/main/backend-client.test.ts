import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import type { ChildProcess } from "node:child_process";
import { describe, expect, it, vi } from "vitest";
import {
  JsonlBackendClient,
  createServiceEnvironment,
  startBackendProcess,
} from "./backend-client";

function createClient() {
  const requests: string[] = [];
  const client = new JsonlBackendClient((line) => requests.push(line), {
    timeoutMs: 2_000,
  });
  return { client, requests };
}

function response(requestLine: string, result: unknown) {
  const request = JSON.parse(requestLine) as { id: string };
  return JSON.stringify({
    version: 1,
    id: request.id,
    ok: true,
    result,
  });
}

describe("JSON Lines backend client", () => {
  it("matches out-of-order responses by request ID", async () => {
    const { client, requests } = createClient();
    const catalog = client.request("calibre.list", {});
    const status = client.request("system.status", {});

    client.acceptStdout(response(requests[1], { status: "ready" }) + "\n");
    client.acceptStdout(response(requests[0], { books: [], total: 0 }) + "\n");

    await expect(status).resolves.toEqual({ status: "ready" });
    await expect(catalog).resolves.toEqual({ books: [], total: 0 });
    client.close();
  });

  it("allows only the confirmed Calibre removal operation through JSONL", async () => {
    const { client, requests } = createClient();
    const removal = client.request("calibre.remove", {
      book_id: 7,
      confirmed: true,
      recovery_copy_confirmed: true,
    });

    const request = JSON.parse(requests[0]) as { operation: string; payload: unknown };
    expect(request).toEqual({
      version: 1,
      id: expect.any(String),
      operation: "calibre.remove",
      payload: { book_id: 7, confirmed: true, recovery_copy_confirmed: true },
    });
    client.acceptStdout(response(requests[0], { removed: true }) + "\n");
    await expect(removal).resolves.toEqual({ removed: true });
    client.close();
  });

  it("allows the bounded KOReader history and Calibre projection operations", async () => {
    const { client, requests } = createClient();
    const preview = client.request("koreader.readings.preview", {});
    const events = client.request("koreader.readings.events", { limit: 100, offset: 0 });

    expect(JSON.parse(requests[0])).toMatchObject({
      operation: "koreader.readings.preview",
      payload: {},
    });
    expect(JSON.parse(requests[1])).toMatchObject({
      operation: "koreader.readings.events",
      payload: { limit: 100, offset: 0 },
    });
    client.acceptStdout(response(requests[0], { batch: { status: "preview" } }) + "\n");
    client.acceptStdout(response(requests[1], { events: [], has_more: false }) + "\n");
    await expect(preview).resolves.toEqual({ batch: { status: "preview" } });
    await expect(events).resolves.toEqual({ events: [], has_more: false });
    client.close();
  });

  it("rejects pending requests when the service exits mid-response", async () => {
    const { client } = createClient();
    const request = client.request("calibre.list", {});
    client.acceptStdout('{"version":1,"id":');
    client.close();

    await expect(request).rejects.toMatchObject({
      code: "service_unavailable",
    });
  });

  it("rejects all pending requests when a response has an unknown ID", async () => {
    const { client } = createClient();
    const request = client.request("system.status", {});
    client.acceptStdout(
      JSON.stringify({
        version: 1,
        id: "unknown-request",
        ok: true,
        result: { status: "ready" },
      }) + "\n",
    );

    await expect(request).rejects.toMatchObject({ code: "invalid_protocol" });
  });

  it("starts the backend with hidden shell-free process options", async () => {
    const environment = { PATH: "C:\\Tools", LOCALAPPDATA: "C:\\Users\\Reader\\AppData" };
    const child = new EventEmitter() as ChildProcess;
    Object.assign(child, {
      stdin: new PassThrough(),
      stdout: new PassThrough(),
      exitCode: 0,
      signalCode: null,
      kill: vi.fn(),
    });
    const spawnProcess = vi.fn(() => child);

    const host = startBackendProcess(
      {
        command: "C:\\Tools\\uv.exe",
        args: ["run", "koreader-ecosystem-service"],
        cwd: "C:\\Program Files\\KOReader",
        env: environment,
      },
      spawnProcess as never,
    );

    expect(spawnProcess).toHaveBeenCalledWith(
      "C:\\Tools\\uv.exe",
      ["run", "koreader-ecosystem-service"],
      {
        cwd: "C:\\Program Files\\KOReader",
        env: environment,
        shell: false,
        windowsHide: true,
        stdio: ["pipe", "pipe", "ignore"],
      },
    );
    await host.stop();
  });

  it("passes only non-secret configuration and runtime environment variables", () => {
    const environment = createServiceEnvironment({
      PATH: "C:\\Tools",
      LOCALAPPDATA: "C:\\Users\\Reader\\AppData",
      READING_CALIBRE_LIBRARY: "C:\\Books\\Minha Biblioteca",
      READING_HARDCOVER_TOKEN: "private-token",
      RESTIC_PASSWORD: "private-password",
    });

    expect(environment).toEqual({
      PATH: "C:\\Tools",
      LOCALAPPDATA: "C:\\Users\\Reader\\AppData",
      READING_CALIBRE_LIBRARY: "C:\\Books\\Minha Biblioteca",
      PYTHONUTF8: "1",
    });
  });
});
