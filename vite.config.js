import { resolve } from "node:path";

import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/dist/",
  build: {
    emptyOutDir: true,
    manifest: "manifest.json",
    outDir: resolve(__dirname, "static/dist"),
    rollupOptions: {
      input: {
        app: resolve(__dirname, "frontend/entries/app.js"),
        grid: resolve(__dirname, "frontend/entries/grid.js"),
        charts: resolve(__dirname, "frontend/entries/charts.js"),
      },
    },
  },
});
