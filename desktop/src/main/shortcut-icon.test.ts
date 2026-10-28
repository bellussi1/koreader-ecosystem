import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { prepareShortcutIcon } from "./shortcut-icon";

describe("Windows shortcut icon", () => {
  it("keeps the icon in the installation root across version removal", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "shortcut-icon-"));
    try {
      const resources = path.join(root, "app-1.0.0", "resources");
      mkdirSync(resources, { recursive: true });
      writeFileSync(path.join(resources, "KOReaderEcosystem.ico"), "icon data");
      const icon = prepareShortcutIcon(resources, root);
      rmSync(path.join(root, "app-1.0.0"), { recursive: true });
      expect(icon).toBe(path.join(root, "KOReaderEcosystem.ico"));
      expect(readFileSync(icon, "utf8")).toBe("icon data");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("updates the stable icon on a new version and falls back if copying fails", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "shortcut-icon-"));
    try {
      const resources = path.join(root, "app-2.0.0", "resources");
      mkdirSync(resources, { recursive: true });
      writeFileSync(path.join(root, "KOReaderEcosystem.ico"), "old icon");
      writeFileSync(path.join(resources, "KOReaderEcosystem.ico"), "new icon");
      expect(readFileSync(prepareShortcutIcon(resources, root), "utf8")).toBe("new icon");
      expect(prepareShortcutIcon(resources, path.join(root, "missing"))).toBe(
        path.join(resources, "KOReaderEcosystem.ico"),
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
