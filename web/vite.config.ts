import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev: API на 127.0.0.1:18080 (deckgen serve); prod: web/dist раздаёт сам FastAPI
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5173, proxy: { "/api": process.env.API_PROXY ?? "http://127.0.0.1:18080" } },
});
