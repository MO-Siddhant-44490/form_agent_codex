// Deterministic parsing of a free-text chat command into an intent the agent
// acts on. Phase 1 supports correcting/adding a value and re-filling; anything
// else returns `unknown` so the panel can guide the user. (An LLM fallback for
// richer commands is a later phase.)

export type ChatIntent =
  | { kind: "set"; key: string; value: string }
  | { kind: "refill" }
  | { kind: "unknown" };

export function slugKey(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export function parseIntent(text: string): ChatIntent {
  const t = text.trim();
  if (/^(re-?fill|fill(\s+it)?(\s+again)?|try\s+again|retry|go)$/i.test(t)) {
    return { kind: "refill" };
  }
  // "set/change/update/make X to|=|: Y"
  let m = t.match(/^(?:set|change|update|make)\s+(.+?)\s+(?:to|=|:)\s+(.+)$/i);
  if (m && slugKey(m[1])) return { kind: "set", key: slugKey(m[1]), value: m[2].trim() };
  // Bare "X = Y" / "X: Y"
  m = t.match(/^(.+?)\s*[:=]\s*(.+)$/);
  if (m && slugKey(m[1])) return { kind: "set", key: slugKey(m[1]), value: m[2].trim() };
  return { kind: "unknown" };
}
