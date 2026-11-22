/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts}'],
  theme: {
    extend: {
      colors: {
        paper: '#FAF9F7',
        line: '#E9E6E1',
        ink: {
          DEFAULT: '#1A1917',
          soft: '#6B6660',
          mute: '#A8A29C'
        },
        brick: { DEFAULT: '#B5462B', soft: '#F7EAE5' },
        gold: { DEFAULT: '#A8803D', soft: '#F5EFE3' },
        sage: { DEFAULT: '#5F7A6B', soft: '#E9EFEB' }
      },
      fontFamily: {
        sans: ['"Inter Variable"', 'Inter', '"PingFang SC"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', '"Cascadia Mono"', 'Consolas', 'monospace']
      },
      boxShadow: {
        soft: '0 1px 2px rgba(26,25,23,0.04), 0 8px 24px -12px rgba(26,25,23,0.10)',
        float: '0 12px 40px -12px rgba(26,25,23,0.22)'
      }
    }
  },
  plugins: []
}
