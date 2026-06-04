import type { Config } from "tailwindcss";

/**
 * KT UX Design System "Seamless Flow" 토큰
 * - Primary: Black (CTA) — ink
 * - Accent Primary: KT Red (point/강조) — ktred
 * - Accent Secondary: KT Teal (graph/AI 보조) — ktteal
 * - Gray Scale와 Accent는 8:2 비율 권장 (대부분 grayscale, accent는 절제)
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "#1a1a1a",
          900: "#1a1a1a",
          800: "#262626",
          700: "#3f3f3f",
        },
        ktred: {
          50: "#fdeced",
          100: "#fbd6d8",
          200: "#f5adb1",
          300: "#ef8388",
          400: "#e54d53",
          500: "#e0282f",
          600: "#c01f26",
          700: "#9c1a20",
        },
        ktteal: {
          50: "#e6f6f5",
          100: "#c3e9e6",
          200: "#8ad6d1",
          300: "#4cc0b9",
          400: "#00a39b",
          500: "#007f7f",
          600: "#006a6a",
        },
      },
      keyframes: {
        "flow-drift": {
          "0%, 100%": { transform: "translateX(0) translateY(0)" },
          "50%": { transform: "translateX(-2%) translateY(-1.5%)" },
        },
      },
      animation: {
        "flow-drift": "flow-drift 16s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;
