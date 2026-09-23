import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端只跟"你的后端"说话（本机 8000）。后端再调 Hermes dashboard。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 注意这里不再是 Hermes 地址，而是你的后端
      "/api": { target: "http://localhost:8000" },
    },
  },
});