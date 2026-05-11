/** @type {import('tailwindcss').Config} */
export default {
  // WHY: explicit content paths prevent Tailwind from scanning node_modules,
  // which would slow the build and occasionally produce false positives
  content: [
    './index.html',
    './src/**/*.{js,jsx}',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
