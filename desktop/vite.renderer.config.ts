import { readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const desktopRoot = path.dirname(fileURLToPath(import.meta.url));
const packageManifest = JSON.parse(
  readFileSync(path.join(desktopRoot, "package.json"), "utf8"),
) as { version: string };

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(packageManifest.version),
  },
  server: {
    fs: {
      allow: [path.resolve(desktopRoot, "..")],
    },
  },
});
