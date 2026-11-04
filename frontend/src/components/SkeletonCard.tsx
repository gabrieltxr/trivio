export default function SkeletonCard() {
  return (
    <div className="w-full bg-white border border-slate-200/90 rounded-2xl p-5 sm:p-6 shadow-sm animate-pulse">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6">
        
        {/* Lado Esquerdo: Modal, Operadora e Horários */}
        <div className="flex-1 space-y-4">
          <div className="flex items-center gap-3">
            {/* Modal Icon Badge */}
            <div className="w-8 h-8 rounded-xl bg-slate-100" />
            <div className="space-y-1.5">
              <div className="w-28 h-4 rounded-md bg-slate-200" />
              <div className="w-20 h-3 rounded-md bg-slate-100" />
            </div>
          </div>

          {/* Linha do Tempo da Viagem */}
          <div className="flex items-center gap-4 sm:gap-6 pt-1">
            <div>
              <div className="w-16 h-6 rounded-md bg-slate-200 mb-1" />
              <div className="w-12 h-3 rounded-md bg-slate-100" />
            </div>

            <div className="flex-1 flex flex-col items-center max-w-[200px]">
              <div className="w-16 h-3 rounded-md bg-slate-200 mb-2" />
              <div className="w-full h-1 bg-slate-100 rounded-full relative">
                <div className="w-2 h-2 rounded-full bg-cyan-400 absolute left-0 top-1/2 -translate-y-1/2" />
                <div className="w-2 h-2 rounded-full bg-cyan-400 absolute right-0 top-1/2 -translate-y-1/2" />
              </div>
              <div className="w-20 h-2.5 rounded-md bg-slate-100 mt-1.5" />
            </div>

            <div>
              <div className="w-16 h-6 rounded-md bg-slate-200 mb-1" />
              <div className="w-12 h-3 rounded-md bg-slate-100" />
            </div>
          </div>
        </div>

        {/* Lado Direito: Preço Efetivo e Trivio Score */}
        <div className="lg:border-l lg:border-slate-100 lg:pl-6 flex flex-row lg:flex-col items-center lg:items-end justify-between gap-3 min-w-[180px]">
          <div className="space-y-1 text-left lg:text-right">
            <div className="w-24 h-3 rounded-md bg-slate-100" />
            <div className="w-32 h-7 rounded-md bg-slate-200" />
            <div className="w-20 h-3 rounded-md bg-slate-100" />
          </div>

          <div className="w-28 h-7 rounded-full bg-slate-200" />
        </div>
      </div>
    </div>
  );
}
