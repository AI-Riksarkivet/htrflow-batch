// Whether a campaign card is on screen, or nearly: the margin says so just
// before it scrolls in. A folded card reads its page count only then. One
// IntersectionObserver watches every card -- one each, for a card's whole
// life, was a page of observers for a page of cards (review of the loading
// change) -- and goes when the last card stops watching.

type Watch = (onScreen: boolean) => void;

let observer: IntersectionObserver | null = null;
const watching = new Map<Element, Watch>();

/**
 * Tells `watch` each time `node` comes on or goes off screen; returns the
 * way to stop. A browser with no IntersectionObserver cannot say, so the
 * node counts as on screen at once, which is what every card did before.
 */
export function watchOnScreen(node: Element, watch: Watch): () => void {
  if (typeof IntersectionObserver === "undefined") {
    watch(true);
    return () => {};
  }
  observer ??= new IntersectionObserver(
    (entries) => {
      for (const e of entries) watching.get(e.target)?.(e.isIntersecting);
    },
    { rootMargin: "200px" },
  );
  watching.set(node, watch);
  observer.observe(node);
  return () => {
    watching.delete(node);
    observer?.unobserve(node);
    if (watching.size > 0) return;
    observer?.disconnect();
    observer = null;
  };
}
