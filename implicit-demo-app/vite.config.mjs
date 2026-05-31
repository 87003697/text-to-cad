import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("/node_modules/react") || id.includes("/node_modules/react-dom")) {
            return "react";
          }
          if (id.includes("/node_modules/three/")) {
            return "three";
          }
          if (id.includes("/node_modules/lucide-react/")) {
            return "icons";
          }
          if (id.includes("/packages/implicitjs/")) {
            return "implicitjs";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
  },
});
