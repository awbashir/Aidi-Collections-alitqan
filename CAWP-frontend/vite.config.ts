import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import dynamicImport from 'vite-plugin-dynamic-import'

// Same-origin API for remote dev access (tools/dev-up.sh --tailnet): with VITE_API_URL=/api
// the browser calls /api/... on whatever host it opened, and the dev server forwards to the
// API with the prefix stripped. Unused while VITE_API_URL is an absolute URL (the default).
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:9797'

// https://vitejs.dev/config/
export default defineConfig({
  server: {
    proxy: {
      '^/api(/|$)': {
        target: apiProxyTarget,
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/api(?=\/|$)/, '') || '/',
      },
    },
  },
  plugins: [react({
    babel: {
      plugins: [
        'babel-plugin-macros'
      ]
    }
  }),
  dynamicImport()],
  assetsInclude: ['**/*.md'],
  resolve: {
    alias: {
      '@': path.join(__dirname, 'src'),
    },
  },
  build: {
    outDir: 'build'
  }
});
