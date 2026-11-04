"use client";

import Image from "next/image";
import Link from "next/link";
import { ShieldCheck, Compass, Sparkles, Scale, Search } from "lucide-react";

interface HeaderProps {
  onSearchClick?: () => void;
}

export default function Header({ onSearchClick }: HeaderProps) {
  return (
    <header className="w-full">
      {/* 1. Top Utility Bar (Padrão Corporativo Clean) */}
      <div className="bg-slate-50 border-b border-slate-200/80 text-xs text-slate-500 py-1.5 px-4 sm:px-8">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1.5 text-cyan-700 font-semibold">
              <ShieldCheck className="w-3.5 h-3.5 text-cyan-600" />
              Auditado ANAC & ANTT
            </span>
            <span className="hidden md:inline text-slate-300">|</span>
            <span className="hidden md:inline text-slate-600 font-medium">
              Primeiro Comparador de Custo Real Porta a Porta do Brasil
            </span>
          </div>

          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 text-[11px] font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              API Online v1.0
            </span>
            <span className="hidden sm:inline text-slate-400 text-[11px]">
              Trivio Score Engine
            </span>
          </div>
        </div>
      </div>

      {/* 2. Main Navigation Bar (White Clean com Backdrop Blur) */}
      <nav className="sticky top-0 z-50 backdrop-blur-md bg-white/95 border-b border-slate-200/80 shadow-sm shadow-slate-100">
        <div className="max-w-7xl mx-auto px-4 sm:px-8 h-20 flex items-center justify-between">
          {/* Logo Oficial Trivio (Paleta Exata do Logo) */}
          <Link href="/" className="flex items-center gap-3 group">
            <div className="relative h-11 w-36 sm:w-40 transition-transform group-hover:scale-105">
              <Image
                src="/logo_clean.png"
                alt="Trivio Travel Intelligence"
                fill
                sizes="(max-width: 640px) 144px, 160px"
                className="object-contain object-left"
                priority
              />
            </div>
            <span className="hidden sm:inline-block px-2.5 py-0.5 rounded-md bg-orange-50 border border-orange-200/80 text-[10px] font-bold tracking-wider uppercase text-orange-600">
              Intelligence
            </span>
          </Link>

          {/* Links Centrais de Navegação */}
          <div className="hidden lg:flex items-center gap-8 text-sm font-semibold text-slate-600">
            <a
              href="#trivio-score"
              className="flex items-center gap-1.5 hover:text-cyan-600 transition-colors"
            >
              <Scale className="w-4 h-4 text-cyan-600" />
              Trivio Score
            </a>
            <a
              href="#hacker-fares"
              className="flex items-center gap-1.5 hover:text-orange-500 transition-colors"
            >
              <Sparkles className="w-4 h-4 text-orange-500" />
              Hacker Fares
            </a>
            <a
              href="#catchment"
              className="flex items-center gap-1.5 hover:text-cyan-600 transition-colors"
            >
              <Compass className="w-4 h-4 text-cyan-600" />
              Abrangência Regional
            </a>
          </div>

          {/* CTA Sunrise Orange (Botão no esquema de cores da logo) */}
          <div className="flex items-center gap-3">
            <button
              onClick={onSearchClick}
              className="bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 active:scale-95 text-white font-bold px-5 py-2.5 rounded-xl text-sm shadow-md shadow-orange-500/20 transition-all flex items-center gap-2"
            >
              <Search className="w-4 h-4 stroke-[2.5]" />
              <span>Nova Busca</span>
            </button>
          </div>
        </div>
      </nav>
    </header>
  );
}
