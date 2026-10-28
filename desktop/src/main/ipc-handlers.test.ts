import { describe, expect, it, vi } from "vitest";
import { isTrustedMainFrame, registerIpcHandlers } from "./ipc-handlers";

const handlers = vi.hoisted(() => new Set<string>());
vi.mock("electron", () => ({
  app: {}, BrowserWindow: class {}, dialog: {},
  ipcMain: {
    handle: (channel: string) => {
      if (handlers.has(channel)) throw new Error("duplicate handler");
      handlers.add(channel);
    },
    removeHandler: (channel: string) => handlers.delete(channel),
  },
}));

function makeSender(origin: string, isMainFrame: boolean) {
  const mainFrame = { origin };
  const sender = { mainFrame };
  return {
    sender,
    senderFrame: isMainFrame ? mainFrame : { origin },
    mainFrame,
  };
}

describe("IPC sender validation", () => {
  it("removes registered handlers so a window can be opened again", () => {
    const client = {} as never;
    const window = { webContents: {} } as never;
    const dispose = registerIpcHandlers(client, window, "koreader://bundle", new Map());
    expect(handlers.has("local-library:get")).toBe(true);
    dispose();
    expect(handlers.size).toBe(0);
    const disposeAgain = registerIpcHandlers(client, window, "koreader://bundle", new Map());
    disposeAgain();
    expect(handlers.size).toBe(0);
  });
  it("accepts only the trusted main frame and app origin", () => {
    const source = makeSender("koreader://bundle", true);

    expect(
      isTrustedMainFrame(
        source.sender as never,
        source.senderFrame as never,
        source.sender as never,
        "koreader://bundle",
      ),
    ).toBe(true);
  });

  it("rejects foreign origins and subframes", () => {
    const foreign = makeSender("https://example.invalid", true);
    const subframe = makeSender("koreader://bundle", false);

    expect(
      isTrustedMainFrame(
        foreign.sender as never,
        foreign.senderFrame as never,
        foreign.sender as never,
        "koreader://bundle",
      ),
    ).toBe(false);
    expect(
      isTrustedMainFrame(
        subframe.sender as never,
        subframe.senderFrame as never,
        subframe.sender as never,
        "koreader://bundle",
      ),
    ).toBe(false);
  });

  it("rejects a trusted-origin main frame from another window", () => {
    const source = makeSender("koreader://bundle", true);
    const otherWindow = { mainFrame: source.mainFrame };

    expect(
      isTrustedMainFrame(
        source.sender as never,
        source.senderFrame as never,
        otherWindow as never,
        "koreader://bundle",
      ),
    ).toBe(false);
  });
});
