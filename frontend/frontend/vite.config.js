import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  base: '/static/spa/',
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: ['host.docker.internal', '127.0.0.1', 'localhost'],
    proxy: {
      '/api': 'http://app:8000',
      '/tasks': 'http://app:8000',
      '/datasources': 'http://app:8000',
      '/history': 'http://app:8000',
      '/lineage': 'http://app:8000',
      '/config': 'http://app:8000',
      '/results': 'http://app:8000',
    },
  },
  build: {
    outDir: '../../static/spa',
    emptyOutDir: true,
  },
})
