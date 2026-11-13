import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trivio | Inteligência em Viagens Intermodais",
  description:
    "Compare passagens de avião e ônibus na mesma régua. Custo real porta a porta, Trivio Score, conexões híbridas e Hacker Fares no Brasil.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR">
      <body className="antialiased bg-white text-slate-900 min-h-screen">{children}</body>
    </html>
  );
}
