// WHY: Vite uses PostCSS to process CSS files; Tailwind runs as a PostCSS plugin
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
