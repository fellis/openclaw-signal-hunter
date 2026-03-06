import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        // Linear-inspired palette
        accent: {
          DEFAULT: '#5E6AD2',
          hover: '#4F58C4',
          muted: '#5E6AD220',
        },
      },
      fontSize: {
        '2xs': ['0.65rem', '0.9rem'],
      },
    },
  },
  plugins: [],
} satisfies Config
