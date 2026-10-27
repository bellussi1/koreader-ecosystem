import { describe, expect, it } from "vitest";
import { isTrustedMainFrame } from "./ipc-handlers";

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
