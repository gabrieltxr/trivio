import { Scale, Sparkles, TrendingDown, ArrowRight } from "lucide-react";

export default function TrustPillars() {
  const pillars = [
    {
      icon: TrendingDown,
      color: "text-cyan-600",
      bgColor: "bg-cyan-50",
      borderColor: "border-cyan-200/80",
      tagColor: "bg-cyan-50 text-cyan-800 border-cyan-200",
      tag: "Transparência Financeira",
      title: "Custo Efetivo Real Porta a Porta",
      description:
        "A passagem aérea de R$ 200 muitas vezes custa R$ 550 com bagagem e táxi. O Trivio decompõe taxas ANAC, franquias de 23kg e transfers urbanos até o seu destino final.",
      linkHover: "text-cyan-600 group-hover:text-cyan-700",
      titleHover: "group-hover:text-cyan-600",
    },
    {
      icon: Scale,
      color: "text-sky-600",
      bgColor: "bg-sky-50",
      borderColor: "border-sky-200/80",
      tagColor: "bg-sky-50 text-sky-800 border-sky-200",
      tag: "Algoritmo Exclusivo",
      title: "Trivio Score (Índice de Agonia)",
      description:
        "Uma métrica que quantifica tempo, preço e desgaste físico na mesma régua. Ônibus Leito Cama Noturno recebe bônus por proporcionar sono produtivo e economizar 1 diária de hotel.",
      linkHover: "text-sky-600 group-hover:text-sky-700",
      titleHover: "group-hover:text-sky-600",
    },
    {
      icon: Sparkles,
      color: "text-orange-500",
      bgColor: "bg-orange-50",
      borderColor: "border-orange-200/80",
      tagColor: "bg-orange-50 text-orange-800 border-orange-200",
      tag: "Economia de até 60%",
      title: "Hacker Fares & Virtual Interlining",
      description:
        "Desbloqueie combinações híbridas não vendidas pelas agências tradicionais: chegue rápido na ida de avião e retorne com tranquilidade no ônibus leito eliminando voos inflacionados.",
      linkHover: "text-orange-500 group-hover:text-orange-600",
      titleHover: "group-hover:text-orange-600",
    },
  ];

  return (
    <section className="w-full max-w-7xl mx-auto px-4 sm:px-8 py-16" id="trivio-score">
      <div className="text-center max-w-3xl mx-auto mb-12">
        <span className="inline-flex items-center text-xs font-bold tracking-widest uppercase text-cyan-800 bg-cyan-50 border border-cyan-200 px-3 py-1 rounded-full">
          Por que usar o Trivio?
        </span>
        <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-900 mt-4 tracking-tight">
          A inteligência que faltava para suas viagens pelo Brasil
        </h2>
        <p className="text-slate-600 text-sm sm:text-base mt-3">
          Inspirado nos mais avançados sistemas de mobilidade internacional, adaptado com rigor para as realidades geográficas e rodoviárias brasileiras.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {pillars.map((pillar, idx) => {
          const IconComponent = pillar.icon;
          return (
            <div
              key={idx}
              className="bg-white border border-slate-200/90 rounded-2xl p-6 transition-all hover:shadow-xl hover:shadow-slate-200/60 hover:-translate-y-1 flex flex-col justify-between group"
            >
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div className={`w-12 h-12 rounded-xl ${pillar.bgColor} ${pillar.borderColor} border flex items-center justify-center ${pillar.color}`}>
                    <IconComponent className="w-6 h-6" />
                  </div>
                  <span className={`text-[11px] font-semibold px-2.5 py-1 rounded-md border ${pillar.tagColor}`}>
                    {pillar.tag}
                  </span>
                </div>

                <h3 className={`text-lg font-bold text-slate-900 transition-colors ${pillar.titleHover}`}>
                  {pillar.title}
                </h3>
                <p className="text-slate-600 text-xs sm:text-sm mt-2.5 leading-relaxed">
                  {pillar.description}
                </p>
              </div>

              <div className={`pt-6 mt-6 border-t border-slate-100 flex items-center gap-1.5 text-xs font-bold transition-all group-hover:translate-x-1 ${pillar.linkHover}`}>
                <span>Ver metodologia completa</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
