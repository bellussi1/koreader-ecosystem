import { playwright } from "@vitest/browser-playwright";
import { defineConfig, mergeConfig } from "vitest/config";
import rendererConfig from "./vite.renderer.config";

export default mergeConfig(
  rendererConfig,
  defineConfig({
    test: {
      include: ["src/renderer/**/*.browser.test.tsx"],
      browser: {
        enabled: true,
        headless: true,
        viewport: { width: 360, height: 900 },
        provider: playwright(),
        instances: [{ browser: "chromium" }],
      },
    },
  }),
);
