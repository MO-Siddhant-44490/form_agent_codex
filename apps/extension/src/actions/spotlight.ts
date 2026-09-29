// A visual cue on the page: scroll the field being filled into view and
// outline it briefly, so the user can watch the agent work down the form in
// order. Purely presentational — it never touches the value, and it restores
// the element's own outline afterwards.
const BRAND = "#2b6cb0";
const HOLD_MS = 900;

export function spotlight(el: Element | null | undefined): void {
  if (!(el instanceof HTMLElement)) return;
  try {
    el.scrollIntoView?.({ block: "center", inline: "nearest" });
    const { outline, outlineOffset, transition } = el.style;
    el.style.transition = "outline-color .2s ease";
    el.style.outline = `2px solid ${BRAND}`;
    el.style.outlineOffset = "2px";
    setTimeout(() => {
      el.style.outline = outline;
      el.style.outlineOffset = outlineOffset;
      el.style.transition = transition;
    }, HOLD_MS);
  } catch {
    // Never let a cosmetic cue affect a fill.
  }
}
