// The editable profile: the textarea is the working copy ("key: value" per
// line), persisted to localStorage so it survives panel reloads.
import type { ChatFact } from "../shared/messages";
import { parseFacts, upsertFact } from "../shared/facts";

const STORAGE_KEY = "fa_profile";
// The demo profile bundled with an earlier build; cleared if it lingers so it
// isn't mistaken for real data.
const STALE_DEMO_MARKER = "Rohan V. Deshmukh";

export class ProfileEditor {
  constructor(private readonly el: HTMLTextAreaElement) {}

  /** Restore a previously-saved profile (never the stale bundled demo). */
  load(): void {
    let saved = "";
    try {
      saved = localStorage.getItem(STORAGE_KEY) || "";
      if (saved.includes(STALE_DEMO_MARKER)) {
        saved = "";
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      saved = "";
    }
    this.el.value = saved;
  }

  facts(): ChatFact[] {
    return parseFacts(this.el.value);
  }

  isEmpty(): boolean {
    return this.facts().length === 0;
  }

  /** Replace the whole profile (e.g. with the backend's merged fields). */
  set(fields: { key: string; value: string }[]): void {
    this.el.value = fields.map((f) => `${f.key}: ${f.value}`).join("\n");
    this.persist();
  }

  /** Add or overwrite one field. */
  setField(key: string, value: string): void {
    const facts = this.facts();
    upsertFact(facts, key, value);
    this.set(facts);
  }

  clear(): void {
    this.set([]);
  }

  persist(): void {
    try {
      localStorage.setItem(STORAGE_KEY, this.el.value);
    } catch {
      /* ignore */
    }
  }
}
