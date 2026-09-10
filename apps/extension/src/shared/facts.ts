// The user's profile as simple facts, shared by the side panel (where it is
// edited) and the background worker (where it is sent to the backend).
import type { ChatFact } from "./messages";

// Non-sensitive geography keys; everything else defaults to personal. Mirrors
// the backend's profile.PUBLIC_KEYS.
export const PUBLIC_KEYS = new Set(["country", "state", "district", "locality", "pincode", "gender"]);

export function sensitivityFor(key: string): ChatFact["sensitivity"] {
  return PUBLIC_KEYS.has(key) ? "public" : "personal";
}

/** Parse the profile textarea ("key: value" per line) into facts. */
export function parseFacts(text: string): ChatFact[] {
  const facts: ChatFact[] = [];
  for (const line of text.split("\n")) {
    const idx = line.indexOf(":");
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    if (key && value) facts.push({ key, value, sensitivity: sensitivityFor(key) });
  }
  return facts;
}

/** Add or overwrite one fact, in place. */
export function upsertFact(facts: ChatFact[], key: string, value: string): void {
  const existing = facts.find((f) => f.key === key);
  if (existing) existing.value = value;
  else facts.push({ key, value, sensitivity: sensitivityFor(key) });
}
