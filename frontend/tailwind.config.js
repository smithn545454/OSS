/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // OSS brand colors - per oss-pipeline-monitor-requirements.md
        oss: {
          // Backgrounds
          bg: '#0a0f14',
          surface: '#0d1318',
          elevated: 'rgba(15, 23, 42, 0.4)',
          hover: 'rgba(30, 41, 59, 0.5)',
          active: 'rgba(6, 182, 212, 0.1)',
          
          // Borders
          border: 'rgba(51, 65, 85, 0.5)',
          'border-subtle': 'rgba(51, 65, 85, 0.3)',
          'border-active': 'rgba(6, 182, 212, 0.4)',
          
          // Text
          text: '#f1f5f9',
          'text-secondary': '#e2e8f0',
          'text-tertiary': '#cbd5e1',
          muted: '#94a3b8',
          disabled: '#64748b',
          
          // Accent
          accent: '#22d3ee',
          'accent-secondary': '#06b6d4',
          
          // Semantic
          approve: '#10b981',
          'approve-text': '#34d399',
          watch: '#f59e0b',
          'watch-text': '#fbbf24',
          reject: '#ef4444',
          'reject-text': '#f87171',
          info: '#6366f1',
          'info-text': '#a5b4fc',
        }
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'SF Mono', 'Fira Code', 'Consolas', 'monospace'],
        sans: ['Instrument Sans', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        'xs': '10px',
        'sm': '11px',
        'base': '12px',
        'md': '13px',
        'lg': '14px',
        'xl': '24px',
        '2xl': '28px',
      },
      spacing: {
        '18': '72px',
        '80': '320px',  // Sidebar width
      },
    },
  },
  plugins: [],
}
