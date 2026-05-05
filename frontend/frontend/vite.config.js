/// <reference types="vitest" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  base: '/static/spa/',
  // Vitest 配置 —— jsdom 环境跑组件 mount，globals 让 expect / describe 不用 import
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.js'],
    include: ['tests/**/*.test.{js,mjs}'],
    // 不跟 e2e 撞：playwright e2e 在 ../../tests/e2e/，不在前端测试范围
    exclude: ['node_modules', 'dist', '../../static', '../../tests'],
  },
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
    // 把大的图/编辑器 vendor 拆出独立 chunk —— LineageGraph / LineageGraphCytoscape /
    // SqlEditor 自己只剩薄壳，G6 / Cytoscape / CodeMirror 第一次访问时各自下载，
    // 之后浏览器缓存命中。
    rolldownOptions: {
      output: {
        advancedChunks: {
          groups: [
            { name: 'g6-vendor',         test: /[\\/]node_modules[\\/]@antv[\\/]/ },
            { name: 'cytoscape-vendor',  test: /[\\/]node_modules[\\/]cytoscape/ },
            { name: 'codemirror-vendor', test: /[\\/]node_modules[\\/]@?codemirror[\\/]/ },
          ],
        },
      },
    },
  },
})
