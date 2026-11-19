import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

// npm run dev -> http://localhost:5173 with /api and /webhooks proxied to Flask (KZ_API, default http://127.0.0.1:5000).
// The PWA: installable on Android (Chrome "Add to Home screen" / install banner) and desktop; the app shell is cached
// for fast start and an offline notice; API calls are never cached (always live data).
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'prompt',
      includeAssets: ['icons/favicon.svg', 'icons/apple-touch-icon.png'],
      manifest: {
        name: 'Kiswa-Zaibus Operations',
        short_name: 'Kiswa-Zaibus',
        description: 'Billing, stock, purchasing, cash and accounts for Kiswa Garments and Zaibus Enterprises; online store for parents.',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'any',
        background_color: '#0e1c30',
        theme_color: '#0e1c30',
        lang: 'en-IN',
        categories: ['business', 'finance', 'shopping'],
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
        shortcuts: [
          { name: 'New bill', url: '/pos', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] },
          { name: 'Approvals', url: '/approvals', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] },
          { name: 'Parent store', url: '/shop', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/webhooks\//],
        runtimeCaching: [{ urlPattern: /^\/api\//, handler: 'NetworkOnly' }],
        cleanupOutdatedCaches: true,
      },
    }),
  ],
  server: {
    host: '0.0.0.0',   // npm run dev: reachable from this PC and the LAN
    port: 5173,
    proxy: {
      '/api': { target: process.env.KZ_API || 'http://127.0.0.1:5000', changeOrigin: false },
      '/webhooks': { target: process.env.KZ_API || 'http://127.0.0.1:5000', changeOrigin: false },
    },
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 900 },
});
