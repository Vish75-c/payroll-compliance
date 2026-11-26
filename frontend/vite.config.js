import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the screens run on :5173 and talk to Django on :8080.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/media": "http://127.0.0.1:8080",
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
