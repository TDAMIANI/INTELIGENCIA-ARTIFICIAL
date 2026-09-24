import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En desarrollo, /api y /ws van a la API (docker compose la publica en :8000).
const api = process.env.MVT_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
  test: { environment: "node" },
});
