import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import process from "node:process";

// API the dev server proxies to; override to point the UI at another backend port.
const API = process.env.NEXTNODE_API_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": API,
      "/health": API,
    },
  },
});
