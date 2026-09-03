// DOM stability tracking: an observation notes whether the DOM was quiet just
// before it was taken (re-perception triggers, plan.md §6.1).

const QUIET_MS = 250;

export class StabilityTracker {
  private lastMutation = 0;
  private observer: MutationObserver | null = null;

  start(doc: Document): void {
    if (this.observer) return;
    this.lastMutation = Date.now();
    this.observer = new MutationObserver(() => {
      this.lastMutation = Date.now();
    });
    this.observer.observe(doc.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      characterData: true,
    });
  }

  isStable(now: number = Date.now()): boolean {
    return now - this.lastMutation >= QUIET_MS;
  }

  stop(): void {
    this.observer?.disconnect();
    this.observer = null;
  }
}
