import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const API = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Two pages: the public landing page at / (static HTML, crawlable) and the app at /app/ (React, hash routes).
  build: {
    rollupOptions: {
      input: {
        landing: fileURLToPath(new URL("index.html", import.meta.url)),
        app: fileURLToPath(new URL("app/index.html", import.meta.url)),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
      "/ws": { target: API.replace(/^http/, "ws"), ws: true },
    },
  },
});
