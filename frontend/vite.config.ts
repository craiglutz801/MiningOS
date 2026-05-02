import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Dual-stack loopback: browsers often resolve `localhost` to ::1 first; binding only 127.0.0.1 breaks those requests ("Failed to fetch").
    host: "::",
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // Batch import can trigger many PLSS geocodes (up to 15s each); discovery/batch PDF also need headroom
        timeout: 3_600_000, // 60 minutes
        proxyTimeout: 3_600_000,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
