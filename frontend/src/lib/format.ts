/**
 * Formatadores compartilhados pelos cards. Criar um Intl.DateTimeFormat custa caro;
 * `toLocaleTimeString` cria um a cada chamada, e a lista de resultados chama centenas de vezes.
 */
const TIME_FORMAT = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });

/** Hora no padrão HH:MM; devolve o texto original se a data for inválida. */
export function formatTime(isoString: string): string {
  const d = new Date(isoString);
  return Number.isNaN(d.getTime()) ? isoString : TIME_FORMAT.format(d);
}

/** Minutos em "Xh Ym". */
export function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}
