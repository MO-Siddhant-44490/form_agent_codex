// Visibility and honeypot filtering (Module 2): non-visible controls are
// excluded from observations entirely — they are never actionable and hidden
// honeypots must never be filled (threat T2).

const OFFSCREEN_PX = -999;

function offscreenByStyle(style: CSSStyleDeclaration): boolean {
  if (style.position !== "absolute" && style.position !== "fixed") return false;
  const left = Number.parseFloat(style.left);
  const top = Number.parseFloat(style.top);
  return (
    (Number.isFinite(left) && left < OFFSCREEN_PX) ||
    (Number.isFinite(top) && top < OFFSCREEN_PX)
  );
}

/** True when the environment computes real layout (Chrome yes, jsdom no). */
export function hasLayout(doc: Document): boolean {
  return doc.documentElement.getBoundingClientRect().width > 0;
}

export function isVisible(el: HTMLElement): boolean {
  if (el.hidden) return false;
  if (el instanceof HTMLInputElement && el.type === "hidden") return false;
  if (el.closest('[aria-hidden="true"]') !== null) return false;

  const win = el.ownerDocument.defaultView;
  if (!win) return false;
  const layout = hasLayout(el.ownerDocument);

  // Walk self + ancestors for style-based hiding and offscreen honeypots.
  for (
    let node: HTMLElement | null = el;
    node && node !== el.ownerDocument.body;
    node = node.parentElement
  ) {
    const style = win.getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (style.opacity !== "" && Number.parseFloat(style.opacity) === 0) return false;
    if (offscreenByStyle(style)) return false;
    // jsdom fallback: computed cascade may miss inline styles in odd cases.
    if (!layout && offscreenByStyle(node.style)) return false;
  }

  if (layout) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    if (rect.right <= 0 || rect.bottom <= 0) return false;
  }
  return true;
}
