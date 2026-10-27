import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const mainProcessSource = readFileSync(new URL("./index.ts", import.meta.url), "utf8");

describe("Squirrel Start Menu shortcut icon", () => {
  it("passes the shortcut icon option when creating or updating the shortcut", () => {
    expect(mainProcessSource).toMatch(
      /updateArguments = \["--createShortcut", executableName, "--icon", iconPath\]/,
    );
  });
});
