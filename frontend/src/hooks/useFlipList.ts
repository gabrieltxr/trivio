"use client";

import { RefObject, useEffect, useLayoutEffect, useRef } from "react";

// useLayoutEffect avisa no prerender do servidor; lá não há nada para animar.
const useIsomorphicLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

const DURATION_MS = 420;
const EASING = "cubic-bezier(0.22, 1, 0.36, 1)";

/** Posição vertical de cada filho marcado com `data-flip-key`, relativa ao contêiner. */
function measure(container: HTMLElement | null): Map<string, number> | null {
  if (!container) return null;
  const positions = new Map<string, number>();
  for (const child of Array.from(container.children)) {
    const el = child as HTMLElement;
    const key = el.dataset.flipKey;
    if (key) positions.set(key, el.offsetTop);
  }
  return positions;
}

/**
 * Anima a reordenação de uma lista (técnica FLIP): quando `trigger` muda, cada item que
 * já existia desliza da posição antiga para a nova, em vez de pular de lugar.
 *
 * O contêiner precisa ser `position: relative` (offsetTop é medido em relação a ele) e cada
 * item precisa de `data-flip-key`. Só `transform` é animado, então roda no compositor.
 */
export function useFlipList(containerRef: RefObject<HTMLElement>, trigger: unknown) {
  const committedTrigger = useRef(trigger);
  const before = useRef<Map<string, number> | null>(null);

  // Mede antes do commit: no render o DOM ainda mostra a ordem antiga.
  if (committedTrigger.current !== trigger && typeof window !== "undefined") {
    before.current = measure(containerRef.current);
  }

  useIsomorphicLayoutEffect(() => {
    if (committedTrigger.current === trigger) return;
    committedTrigger.current = trigger;

    const container = containerRef.current;
    const previous = before.current;
    before.current = null;
    if (!container || !previous) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    // Só anima o que passa perto da tela: itens distantes mudam de lugar sem custo.
    const containerTop = container.getBoundingClientRect().top;
    const viewport = window.innerHeight;
    const nearScreen = (top: number) => {
      const y = containerTop + top;
      return y > -viewport && y < viewport * 2;
    };

    for (const child of Array.from(container.children)) {
      const el = child as HTMLElement;
      const key = el.dataset.flipKey;
      if (!key) continue;
      const oldTop = previous.get(key);
      if (oldTop === undefined) continue; // item novo: a animação de entrada cuida dele
      const newTop = el.offsetTop;
      const delta = oldTop - newTop;
      if (delta === 0 || (!nearScreen(oldTop) && !nearScreen(newTop))) continue;
      el.animate(
        [{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }],
        { duration: DURATION_MS, easing: EASING }
      );
    }
  }, [trigger]);
}
