import type { Config } from "tailwindcss";

// Curva de desaceleração usada em todas as entradas: rápida no início, assenta suave.
const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
      },
      // Só transform e opacity: animam no compositor, sem recalcular layout.
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "fade-in-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "card-in": {
          from: { opacity: "0", transform: "translateY(14px) scale(0.985)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        dropdown: {
          from: { opacity: "0", transform: "translateY(-6px) scale(0.98)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
      },
      animation: {
        "fade-in": `fade-in 200ms ${EASE_OUT} both`,
        "fade-in-up": `fade-in-up 260ms ${EASE_OUT} both`,
        "card-in": `card-in 380ms ${EASE_OUT} both`,
        dropdown: `dropdown 160ms ${EASE_OUT} both`,
      },
    },
  },
  plugins: [],
};
export default config;
