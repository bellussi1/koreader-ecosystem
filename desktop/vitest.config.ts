import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    css: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
