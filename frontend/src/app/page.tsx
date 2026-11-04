"use client";

import { useState } from "react";
import Image from "next/image";
import Header from "@/components/Header";
import SearchBar from "@/components/SearchBar";
import TrustPillars from "@/components/TrustPillars";
import { SearchParams } from "@/types";
import { Sparkles } from "lucide-react";

export default function Home() {
  const [activeParams, setActiveParams] = useState<SearchParams | null>(null);

  const handleSearch = (params: SearchParams) => {
    setActiveParams(params);
    // Rola suavemente para a área de resultados
    const resultsElem = document.getElementById("results-section");
    if (resultsElem) {
      resultsElem.scrollIntoView({ behavior: "smooth" });
    }
  };

  return (
    <div className="flex flex-col min-h-screen bg-white text-slate-900">
      {/* 1. Header Corporativo com Utility Bar */}
      <Header onSearchClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} />

      {/* 2. Hero Section Imersiva (White Clean com Acentos do Logo Trivio) */}
      <section className="relative pt-12 pb-24 px-4 sm:px-8 bg-gradient-to-b from-white via-cyan-50/20 to-slate-50/80 border-b border-slate-200/80 overflow-hidden">
        {/* Glow de Fundo e Padrão de Grade Geodésica Clean */}
        <div className="absolute inset-0 bg-[radial-gradient(#cbd5e1_1px,transparent_1px)] [background-size:24px_24px] opacity-40 pointer-events-none" />
        <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[350px] bg-cyan-400/10 rounded-full blur-[130px] pointer-events-none" />
        <div className="absolute top-1/3 right-1/4 w-[400px] h-[300px] bg-orange-400/10 rounded-full blur-[110px] pointer-events-none" />

        <div className="max-w-7xl mx-auto relative z-10">
          {/* Título Principal de Impacto */}
          <div className="text-center max-w-4xl mx-auto mb-10">
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-cyan-50 border border-cyan-200 text-cyan-800 text-xs font-bold mb-6 shadow-sm">
              <Sparkles className="w-3.5 h-3.5 text-orange-500" />
              <span>A Revolução da Mobilidade Intermodal Brasileira</span>
            </div>

            <h1 className="text-4xl sm:text-6xl font-black text-slate-950 tracking-tight leading-[1.15]">
              Viaje com mais inteligência e <br />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-cyan-600 via-sky-600 to-orange-500">
                zero atrito de porta a porta
              </span>
            </h1>

            <p className="text-slate-600 text-base sm:text-lg mt-5 max-w-2xl mx-auto leading-relaxed">
              O primeiro comparador que coloca <strong className="text-slate-900 font-bold">Avião</strong> e <strong className="text-slate-900 font-bold">Ônibus</strong> na mesma régua: cálculo de custo real, bônus de sono produtivo no leito noturno e combinações Hacker Fares de até 60% de economia.
            </p>
          </div>

          {/* 3. Floating Interactive Search Widget White Clean */}
          <SearchBar onSearch={handleSearch} />
        </div>
      </section>

      {/* 4. Pilares de Confiança e Metodologia (Design Clean) */}
      <TrustPillars />

      {/* 5. Placeholder para os Resultados da Busca (Passo 6.3 e 6.4) */}
      <section id="results-section" className="w-full max-w-7xl mx-auto px-4 sm:px-8 py-12">
        {activeParams ? (
          <div className="bg-cyan-50/40 border border-cyan-200 rounded-3xl p-8 text-center animate-fadeIn shadow-sm">
            <h3 className="text-xl font-bold text-slate-900 mb-2">
              Busca iniciada para {activeParams.origin} ➔ {activeParams.destination}
            </h3>
            <p className="text-slate-600 text-sm max-w-md mx-auto mb-6">
              O canal de streaming em tempo real com WebSockets (Passo 6.3) e os cards visuais de cotação com Trivio Score (Passo 6.4) serão conectados a este contêiner.
            </p>
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-cyan-100 text-cyan-800 border border-cyan-300 text-xs font-semibold">
              <span className="w-2 h-2 rounded-full bg-cyan-600 animate-ping" />
              Pronto para conexão com o endpoint /ws/search do FastAPI
            </div>
          </div>
        ) : (
          <div className="border-2 border-dashed border-slate-200 bg-slate-50/60 rounded-3xl p-12 text-center text-slate-500 text-sm">
            Selecione uma rota acima ou utilize os atalhos rápidos de São Paulo, Rio ou Curitiba para comparar tarifas.
          </div>
        )}
      </section>

      {/* 6. Footer Corporativo Clean */}
      <footer className="mt-auto border-t border-slate-200 bg-white py-10 px-4 sm:px-8 text-xs text-slate-500">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-3">
            <div className="relative w-8 h-8 rounded-lg overflow-hidden border border-slate-200 bg-slate-50 flex items-center justify-center p-1">
              <Image
                src="/logo_icon.png"
                alt="Trivio Icon"
                fill
                className="object-contain p-0.5"
              />
            </div>
            <span className="font-bold text-slate-900 text-sm">Trivio Intelligence</span>
            <span className="text-slate-300">|</span>
            <span className="text-slate-600">Transformando a mobilidade de longa distância no Brasil.</span>
          </div>

          <div className="flex flex-wrap items-center gap-6 text-slate-500 font-medium">
            <a href="#trivio-score" className="hover:text-cyan-600 transition-colors">Trivio Score</a>
            <a href="#hacker-fares" className="hover:text-orange-600 transition-colors">Hacker Fares</a>
            <a href="/docs" target="_blank" className="hover:text-cyan-600 transition-colors">API REST (FastAPI)</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
