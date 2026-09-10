// Side panel: build a profile (upload a document, or type it), fill the form
// in this tab, then talk to the agent — answer its questions, correct a value
// ("set state to Karnataka"), or say "refill". It never submits.
import type { ChatFact, SessionState } from "../shared/messages";
import { ChatView } from "./chat-ui";
import { ProfileEditor } from "./profile";

const BACKEND = "http://127.0.0.1:8000";

// -- DOM -------------------------------------------------------------------

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const fillBtn = $<HTMLButtonElement>("fill");
const statusEl = $<HTMLElement>("status");
const composer = $<HTMLFormElement>("composer");
const chatInput = $<HTMLInputElement>("chatinput");
const profileSection = $<HTMLDetailsElement>("profile");
const fileInput = $<HTMLInputElement>("file");
const uploadBtn = $<HTMLButtonElement>("upload");
const clearBtn = $<HTMLButtonElement>("clear");

const profile = new ProfileEditor($<HTMLTextAreaElement>("facts"));
const chat = new ChatView($<HTMLElement>("chat"));
profile.load();

// -- small helpers -----------------------------------------------------------

function setStatus(text: string, cls = ""): void {
  statusEl.className = cls;
  statusEl.textContent = text;
}

let composerEnabled = false;
function setComposerEnabled(on: boolean): void {
  composerEnabled = on;
  chatInput.disabled = !on;
}

function send(message: Record<string, unknown>): void {
  void chrome.runtime.sendMessage(message);
}

function slug(s: string): string {
  return s.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

/** Answer a question: echo it and hand the fact to the agent, which re-fills. */
function answer(key: string, value: string): void {
  chat.bubble(`${key}: ${value}`, "user");
  send({ type: "FA_ANSWER", key, value });
}

// -- prompts -----------------------------------------------------------------

type Question = {
  field_id: string;
  kind: string;
  prompt: string;
  fact_keys?: string[];
  options?: string[] | null;
};

/** A question the agent needs answered, routed to the right fact key. */
function askQuestion(q: Question): void {
  const key = q.fact_keys?.[0] || slug(q.field_id);
  const options = (q.options ?? []).filter(Boolean);
  chat.prompt({
    title: q.prompt || q.field_id,
    input: { placeholder: `Value for “${key}”`, onSubmit: (v) => answer(key, v) },
    chips: options.length <= 12 ? options.map((o) => ({ label: o, onPick: () => answer(key, o) })) : [],
  });
}

/** A document's value for a key differs from the profile: let the user pick. */
function askConflict(c: { key: string; existing: string; incoming: string }): void {
  chat.prompt({
    title: `Which value for “${c.key}”?`,
    chips: [c.existing, c.incoming].map((v) => ({
      label: v,
      onPick: () => {
        profile.setField(c.key, v);
        chat.bubble(`${c.key}: ${v}`, "user");
      },
    })),
  });
}

// -- document upload -------------------------------------------------------

type PendingDoc = { filename: string; mimeType: string; contentBase64: string };

function sendDoc(doc: PendingDoc, facts: ChatFact[]): void {
  chat.bubble(`📄 ${doc.filename}`, "user");
  send({ type: "FA_PARSE_DOC", ...doc, facts });
}

/** With an existing profile, ask whether to merge the document or start fresh. */
function askProfileChoice(doc: PendingDoc): void {
  chat.prompt({
    title: `Add “${doc.filename}” to your current profile, or start a new profile?`,
    chips: [
      { label: "Add to current", onPick: () => sendDoc(doc, profile.facts()) },
      {
        label: "Start new",
        onPick: () => {
          profile.clear();
          sendDoc(doc, []);
        },
      },
    ],
  });
}

uploadBtn.addEventListener("click", () => fileInput.click());
clearBtn.addEventListener("click", () => {
  profile.clear();
  chat.bubble("Cleared the profile.", "sys");
});
fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = String(reader.result);
    const doc: PendingDoc = {
      filename: file.name,
      mimeType: file.type || "application/octet-stream",
      contentBase64: dataUrl.slice(dataUrl.indexOf(",") + 1),
    };
    if (profile.isEmpty()) sendDoc(doc, []);
    else askProfileChoice(doc);
  };
  reader.readAsDataURL(file);
  fileInput.value = ""; // allow re-selecting the same file
});

// -- fill + chat -------------------------------------------------------------

let filledCount = 0;

fillBtn.addEventListener("click", () => {
  profile.persist();
  const facts = profile.facts();
  if (facts.length === 0) {
    setStatus("Add at least one 'key: value' line to your profile.", "warn");
    return;
  }
  chat.clear();
  filledCount = 0;
  fillBtn.disabled = true;
  setComposerEnabled(false);
  profileSection.open = false;
  chat.bubble("Filling this form from your profile…", "agent");
  setStatus("Attaching to this tab and starting…", "");
  send({ type: "FA_FILL", facts, backendUrl: BACKEND });
});

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text || !composerEnabled) return;
  chat.bubble(text, "user");
  chatInput.value = "";
  send({ type: "FA_CHAT", text });
});

// -- results from the background -------------------------------------------

type FormState = {
  text?: string;
  empty_required?: { field_id: string; label: string; credential?: boolean; options?: string[] | null }[];
};

function showFillResult(result: Record<string, unknown>): void {
  if (result.type === "fill_error") {
    setStatus(`Error: ${String(result.error)}`, "err");
    chat.bubble(`Something went wrong: ${String(result.error)}`, "agent");
    return;
  }
  // A chat turn carries a `reply` (already shown as an agent message) — only a
  // full fill gets the outcome banner.
  const isChat = result.reply !== undefined || result.applied !== undefined;
  const outcome = result.outcome ? String(result.outcome) : "";
  const filled = (result.filled as string[]) ?? [];
  const questions = (result.questions as Question[]) ?? [];
  const issues = (result.validation_issues as { field_id: string; label?: string; detail: string }[]) ?? [];

  if (isChat) {
    setStatus("Updated.", "");
  } else if (outcome === "COMPLETED") {
    setStatus(`Done — ${filled.length} fields filled. Nothing submitted.`, "ok");
    chat.bubble(`Filled ${filled.length} field(s). Everything checks out — review and submit yourself.`, "agent");
  } else if (outcome === "NEEDS_USER") {
    const need = questions.length + issues.length;
    setStatus(`Filled ${filled.length}. ${need} item(s) need you. Nothing submitted.`, "warn");
    chat.bubble(
      `Filled ${filled.length} field(s). ${need} item(s) need your input — answer below, or just tell me the value.`,
      "agent",
    );
  } else if (outcome) {
    setStatus(`Finished: ${outcome}. ${String(result.detail ?? "")}`, "warn");
    chat.bubble(`Finished: ${outcome}. ${String(result.detail ?? "")}`, "agent");
  }

  // Situational report from the live form-state snapshot.
  const state = result.state as FormState | null;
  if (state?.text) chat.bubble(`Form status — ${state.text}`, "agent");

  const asked = new Set(questions.map((q) => q.field_id));
  questions.forEach(askQuestion);
  // Empty required fields the fill did not already ask about (e.g. cascade-
  // revealed State/District) get an inline prompt so nothing is silently left.
  for (const f of state?.empty_required ?? []) {
    if (asked.has(f.field_id)) continue;
    if (f.credential) {
      chat.bubble(`“${f.label}” has to be completed on the page itself (e.g. CAPTCHA).`, "sys");
      continue;
    }
    askQuestion({
      field_id: f.field_id,
      kind: "empty_required",
      prompt: `“${f.label}” is empty — what should I put there?`,
      fact_keys: [],
      options: f.options ?? null,
    });
  }
  for (const iss of issues) chat.issue(iss.label || iss.field_id, iss.detail);

  setComposerEnabled(true); // ready for the next conversational turn
  chatInput.focus();
}

function showDocumentFacts(msg: Record<string, unknown>): void {
  const fields = (msg.fields as { key: string; value: string }[]) ?? [];
  const conflicts = (msg.conflicts as { key: string; existing: string; incoming: string }[]) ?? [];
  const added = (msg.added as string[]) ?? [];
  profile.set(fields);
  profileSection.open = true;
  if (fields.length === 0) {
    chat.bubble(`I couldn't read any details from ${String(msg.filename)}.`, "agent");
  } else {
    const extra = conflicts.length ? `, ${conflicts.length} to resolve below` : "";
    chat.bubble(
      `Read ${String(msg.filename)} — added ${added.length} new field(s)${extra}. Review the profile above, then Fill.`,
      "agent",
    );
  }
  conflicts.forEach(askConflict);
}

chrome.runtime.onMessage.addListener((msg: Record<string, unknown>) => {
  switch (msg.type) {
    case "FA_FILL_STARTED":
      fillBtn.disabled = true;
      setComposerEnabled(false);
      setStatus("Working on the form in this tab…", "");
      break;
    case "FA_FILL_PROGRESS":
      if (msg.status === "EXECUTED" && msg.verification === "SUCCESS") {
        filledCount += 1;
        setStatus(`Filling… ${filledCount} field(s) done.`, "");
      }
      break;
    case "FA_AGENT_MSG":
      chat.bubble(String(msg.text), "agent");
      break;
    case "FA_DOC_FACTS":
      showDocumentFacts(msg);
      break;
    case "FA_DOC_ERROR":
      chat.bubble(`Couldn't read the document: ${String(msg.error)}`, "agent");
      break;
    case "FA_FILL_DONE":
      fillBtn.disabled = false;
      showFillResult(msg.result as Record<string, unknown>);
      break;
  }
});

void chrome.runtime.sendMessage({ type: "FA_GET_STATE" }).then((s: SessionState) => {
  if (s?.error) setStatus(s.error, "warn");
});
