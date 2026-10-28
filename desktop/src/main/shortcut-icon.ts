import { copyFileSync } from "node:fs";
import path from "node:path";

export function prepareShortcutIcon(resourcesPath: string, installRoot: string): string {
  const source = path.join(resourcesPath, "KOReaderEcosystem.ico");
  const destination = path.join(installRoot, "KOReaderEcosystem.ico");
  try {
    copyFileSync(source, destination);
    return destination;
  } catch {
    // Preserve shortcut creation if the installation root is unavailable.
    return source;
  }
}
