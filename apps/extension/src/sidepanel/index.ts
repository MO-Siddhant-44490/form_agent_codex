// Side panel: build a profile (upload a document, or type it), fill the form
// in this tab, then talk to the agent — answer its questions, correct a value
// ("set state to Karnataka"), or say "refill". It never submits.
import type { ChatFact, SessionState } from "../shared/messages";
import { ChatView, ProgressCard, type Progress } from "./chat-ui";
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
  label?: string;
};

// Question kinds where the agent has a CANDIDATE fact and needs a yes/no, not
// a value: "I found aID — use it for Aadhaar Number?"
const CONFIRM_KINDS = new Set(["low_confidence", "sensitive_mapping"]);

/** A value for a page-specific field: one-off, applied to that field only. */
function setField(fieldId: string, label: string, value: string): void {
  chat.bubble(`${label}: ${value}`, "user");
  send({ type: "FA_SET_FIELD", fieldId, value });
}

/** A question the agent needs answered, routed to the right fact key. */
function askQuestion(q: Question): void {
  const key = q.fact_keys?.[0];
  const label = q.label || q.field_id;
  const options = (q.options ?? []).filter(Boolean);

  if (key && CONFIRM_KINDS.has(q.kind)) {
    // Confirm the proposed binding; "No" falls back to asking for a value.
    chat.prompt({
      title: q.prompt,
      chips: [
        {
          label: `Yes, use ${key}`,
          onPick: () => {
            chat.bubble(`Use ${key} for ${label}`, "user");
            send({ type: "FA_BIND", fieldId: q.field_id, key });
          },
        },
        {
          label: "No, I'll give a value",
          onPick: () => askQuestion({ ...q, kind: "missing_fact", fact_keys: [] }),
        },
      ],
    });
    return;
  }

  // A real profile fact: answer it and re-fill. A page-only field: set it directly.
  const submit = key ? (v: string) => answer(key, v) : (v: string) => setField(q.field_id, label, v);
  chat.prompt({
    title: q.prompt || label,
    input: { placeholder: key ? `Value for “${key}”` : `Value for “${label}”`, onSubmit: submit },
    chips: options.length <= 12 ? options.map((o) => ({ label: o, onPick: () => submit(o) })) : [],
  });
}

type EmptyField = {
  field_id: string;
  label: string;
  input_type?: string;
  human_only?: boolean;
  consent?: boolean;
  options?: string[] | null;
};

/** An empty required field the fill did not already ask about. What we ask
 * depends on what the field is FOR: a captcha/credential is the user's to type
 * on the page; a consent needs their explicit decision; a checkbox is yes/no. */
function askEmptyField(f: EmptyField): void {
  if (f.human_only) {
    chat.bubble(`“${f.label}” is yours to complete on the page (CAPTCHA / OTP / password).`, "sys");
    return;
  }
  if (f.consent) {
    chat.prompt({
      title: `The form asks you to confirm: “${f.label}” — tick it?`,
      chips: [
        { label: "Tick it", onPick: () => setField(f.field_id, f.label, "yes") },
        { label: "Leave it", onPick: () => chat.bubble(`Left “${f.label}” unticked.`, "sys") },
      ],
    });
    return;
  }
  if (f.input_type === "checkbox") {
    chat.prompt({
      title: `“${f.label}” — tick it?`,
      chips: [
        { label: "Yes", onPick: () => setField(f.field_id, f.label, "yes") },
        { label: "No", onPick: () => chat.bubble(`Left “${f.label}” unticked.`, "sys") },
      ],
    });
    return;
  }
  askQuestion({
    field_id: f.field_id,
    kind: "empty_required",
    prompt: `“${f.label}” is empty — what should I put there?`,
    fact_keys: [],
    options: f.options ?? null,
    label: f.label,
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
let progressCard: ProgressCard | null = null;

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
  empty_required?: EmptyField[];
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
  const allIssues = (result.validation_issues as { field_id: string; label?: string; detail: string }[]) ?? [];
  // One card per field: a field the agent already asks about (or prompts for as
  // empty-required) does not also get a "required field has no value" card.
  const askedIds = new Set([
    ...((result.questions as Question[]) ?? []).map((q) => q.field_id),
    ...(((result.state as FormState | null)?.empty_required ?? []).map((f) => f.field_id)),
  ]);
  const issues = allIssues.filter((i) => !askedIds.has(i.field_id));

  if (isChat) {
    setStatus("Updated.", "");
  } else if (outcome === "COMPLETED") {
    setStatus(`Done — ${filled.length} fields filled. Nothing submitted.`, "ok");
    chat.bubble(`Filled ${filled.length} field(s). Everything checks out — review and submit yourself.`, "agent");
  } else if (outcome === "NEEDS_USER" && result.model_unavailable) {
    // The backend could not reach the model: say so plainly instead of
    // "0 items need you" — the user must fix the environment, not the form.
    setStatus(`Filled ${filled.length}. The model is unreachable — see below.`, "err");
    chat.bubble(
      `I filled ${filled.length} field(s) but could not map the rest: the backend can't reach the model ` +
        `(${String(result.model_unavailable)}). Fix the backend's AWS session (aws sso login, then restart it ` +
        `with AWS_PROFILE/AWS_REGION set) and click Fill again.`,
      "agent",
    );
  } else if (outcome === "NEEDS_USER") {
    const need = new Set([...askedIds, ...issues.map((i) => i.field_id)]).size;
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

  const labels = new Map((state?.empty_required ?? []).map((f) => [f.field_id, f.label]));
  const asked = new Set(questions.map((q) => q.field_id));
  for (const q of questions) askQuestion({ ...q, label: labels.get(q.field_id) });
  // Empty required fields the fill did not already ask about (e.g. cascade-
  // revealed State/District) get an inline prompt so nothing is silently left.
  for (const f of state?.empty_required ?? []) {
    if (!asked.has(f.field_id)) askEmptyField(f);
  }
  for (const iss of issues) chat.issue(iss.label || iss.field_id, iss.detail);
  const leftBlank = (result.left_blank as string[] | undefined) ?? [];
  if (leftBlank.length) {
    chat.bubble(`Left blank (optional, nothing in your profile fits): ${leftBlank.join(", ")}.`, "sys");
  }

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
      progressCard?.finish();
      progressCard = new ProgressCard($<HTMLElement>("chat"));
      break;
    case "FA_PROGRESS": {
      const ev = msg as unknown as Progress;
      progressCard?.update(ev);
      const line = progressCard?.statusLine(ev);
      if (line) setStatus(line, "");
      break;
    }
    case "FA_FILL_PROGRESS":
      break; // superseded by FA_PROGRESS (named fields, real counts)
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
      progressCard?.finish();
      progressCard = null;
      fillBtn.disabled = false;
      showFillResult(msg.result as Record<string, unknown>);
      break;
  }
});

void chrome.runtime.sendMessage({ type: "FA_GET_STATE" }).then((s: SessionState) => {
  if (s?.error) setStatus(s.error, "warn");
});
