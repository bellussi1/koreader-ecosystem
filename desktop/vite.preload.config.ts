import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/preload/index.ts",
      fileName: () => "preload.cjs",
      formats: ["cjs"],
    },
  },
});
