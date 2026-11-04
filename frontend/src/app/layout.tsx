import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

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
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-white text-slate-900 min-h-screen`}
      >
        {children}
      </body>
    </html>
  );
}
