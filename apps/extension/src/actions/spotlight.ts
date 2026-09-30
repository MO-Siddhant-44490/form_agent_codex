// A visual cue on the page: scroll the field being filled into view and
// outline it briefly, so the user can watch the agent work down the form in
// order. Purely presentational — it never touches the value, and it restores
// the element's own outline afterwards.
import { isHtmlEl } from "./dom-types";
const BRAND = "#2b6cb0";
const HOLD_MS = 900;
const ORIGINAL = new WeakMap<HTMLElement, { outline: string; outlineOffset: string; transition: string }>();
const TIMERS = new WeakMap<HTMLElement, ReturnType<typeof setTimeout>>();

export function spotlight(el: Element | null | undefined): void {
  if (!(isHtmlEl(el))) return;
  try {
    el.scrollIntoView?.({ block: "center", inline: "nearest" });
    const saved = ORIGINAL.get(el) ?? {
      outline: el.style.outline,
      outlineOffset: el.style.outlineOffset,
      transition: el.style.transition,
    };
    ORIGINAL.set(el, saved);
    const { outline, outlineOffset, transition } = saved;
    el.style.transition = "outline-color .2s ease";
    el.style.outline = `2px solid ${BRAND}`;
    el.style.outlineOffset = "2px";
    clearTimeout(TIMERS.get(el));
    TIMERS.set(
      el,
      setTimeout(() => {
        el.style.outline = outline;
        el.style.outlineOffset = outlineOffset;
        el.style.transition = transition;
        ORIGINAL.delete(el);
      }, HOLD_MS),
    );
  } catch {
    // Never let a cosmetic cue affect a fill.
  }
}
