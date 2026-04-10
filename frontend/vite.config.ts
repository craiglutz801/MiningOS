import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Listen on IPv4 so http://127.0.0.1:5173 works (not only ::1 / "localhost")
    host: "127.0.0.1",
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
