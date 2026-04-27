export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      colors: {
        ink: '#1e293b',
        muted: '#64748b',
        line: '#e5e7ec',
        panel: '#ffffff',
        canvas: '#f8f9fb',
        brand: '#2563eb',
      },
      boxShadow: {
        soft: '0 4px 12px rgba(15, 23, 42, 0.04)',
      },
    },
  },
  plugins: [],
}
