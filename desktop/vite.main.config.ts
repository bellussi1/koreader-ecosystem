import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/main/index.ts",
      fileName: () => "main.cjs",
      formats: ["cjs"],
    },
  },
});
