import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端只跟"你的后端"说话（本机 8000）。后端再调 Hermes dashboard。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 注意这里不再是 Hermes 地址，而是你的后端
      // 用 127.0.0.1 而不是 localhost：Windows 上 localhost 会先解析成 IPv6 ::1，
      // 若有别的程序（例如 Docker 容器）占着 [::1]:8000，请求会被它接走，
      // 表现为「老接口正常、新接口 404」。写成 127.0.0.1 恒走 IPv4。
      "/api": { target: "http://127.0.0.1:8000" },
    },
  },
});