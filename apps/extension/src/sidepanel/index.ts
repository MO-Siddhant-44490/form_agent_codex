// Side panel: a chat surface over the fill. Click "Fill this form" to start,
// then talk to the agent — answer its questions, correct a value
// ("set state to Karnataka"), or say "refill". It never submits.
import type { ChatFact, SessionState } from "../shared/messages";

const factsEl = document.getElementById("facts") as HTMLTextAreaElement;
const fillBtn = document.getElementById("fill") as HTMLButtonElement;
const statusEl = document.getElementById("status")!;
const chatEl = document.getElementById("chat")!;
const composer = document.getElementById("composer") as HTMLFormElement;
const chatInput = document.getElementById("chatinput") as HTMLInputElement;
const profile = document.getElementById("profile") as HTMLDetailsElement;

const BACKEND = "http://127.0.0.1:8000";

const DEFAULT_PROFILE = `full_name: Rohan V. Deshmukh
gender: M
email: rohan.deshmukh@examplemail.in
mobile: 9820447631
phone: 02226734410
address: Flat 1204 Sunbeam Heights, Plot 27, Palm Beach Road
sub_locality: Sector 15
locality: Navi Mumbai
pincode: 400703
country: India
state: Maharashtra
district: Thane`;

try {
  factsEl.value = localStorage.getItem("fa_profile") || DEFAULT_PROFILE;
} catch {
  factsEl.value = DEFAULT_PROFILE;
}

function setStatus(text: string, cls = ""): void {
  statusEl.className = cls;
  statusEl.textContent = text;
}

function bubble(text: string, who: "agent" | "user" | "sys"): void {
  const d = document.createElement("div");
  d.className = `msg ${who}`;
  d.textContent = text;
  chatEl.appendChild(d);
  chatEl.scrollTop = chatEl.scrollHeight;
}

const PUBLIC_KEYS = new Set(["country", "state", "district", "locality", "pincode", "gender"]);

function parseFacts(text: string): ChatFact[] {
  const facts: ChatFact[] = [];
  for (const line of text.split("\n")) {
    const idx = line.indexOf(":");
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    if (key && value) {
      facts.push({ key, value, sensitivity: PUBLIC_KEYS.has(key) ? "public" : "personal" });
    }
  }
  return facts;
}

let composerEnabled = false;
function setComposerEnabled(on: boolean): void {
  composerEnabled = on;
  chatInput.disabled = !on;
}

// Render a question the agent needs answered, with an inline answer input that
// routes to the right fact. `fact_keys` (from the backend) or the field id give
// the fact key the answer should update.
function renderQuestion(q: {
  field_id: string;
  kind: string;
  prompt: string;
  fact_keys?: string[];
  options?: string[] | null;
}): void {
  const key = (q.fact_keys && q.fact_keys[0]) || slug(q.field_id);
  const wrap = document.createElement("div");
  wrap.className = "q";
  const title = document.createElement("b");
  title.textContent = q.prompt || q.field_id;
  wrap.appendChild(title);

  const row = document.createElement("div");
  row.className = "row";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = `Value for “${key}”`;
  const btn = document.createElement("button");
  btn.textContent = "Answer";
  btn.className = "chip";
  const submit = () => {
    const value = input.value.trim();
    if (!value) return;
    bubble(`${key}: ${value}`, "user");
    void chrome.runtime.sendMessage({ type: "FA_ANSWER", key, value });
    wrap.remove();
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });
  row.appendChild(input);
  row.appendChild(btn);
  wrap.appendChild(row);

  // Enumerated confirmation: offer the options as quick chips.
  if (q.options && q.options.length > 0 && q.options.length <= 12) {
    const chips = document.createElement("div");
    chips.className = "row";
    for (const opt of q.options) {
      if (!opt) continue;
      const c = document.createElement("button");
      c.className = "chip";
      c.textContent = opt;
      c.addEventListener("click", () => {
        bubble(`${key}: ${opt}`, "user");
        void chrome.runtime.sendMessage({ type: "FA_ANSWER", key, value: opt });
        wrap.remove();
      });
      chips.appendChild(c);
    }
    wrap.appendChild(chips);
  }
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function slug(s: string): string {
  return s.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

let filledCount = 0;

fillBtn.addEventListener("click", () => {
  const facts = parseFacts(factsEl.value);
  try {
    localStorage.setItem("fa_profile", factsEl.value);
  } catch {
    /* ignore */
  }
  if (facts.length === 0) {
    setStatus("Add at least one 'key: value' line to your profile.", "warn");
    return;
  }
  chatEl.innerHTML = "";
  filledCount = 0;
  fillBtn.disabled = true;
  setComposerEnabled(false);
  profile.open = false;
  bubble("Filling this form from your profile…", "agent");
  setStatus("Attaching to this tab and starting…", "");
  void chrome.runtime.sendMessage({ type: "FA_FILL", facts, backendUrl: BACKEND });
});

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text || !composerEnabled) return;
  bubble(text, "user");
  chatInput.value = "";
  void chrome.runtime.sendMessage({ type: "FA_CHAT", text });
});

// Live updates from the background.
chrome.runtime.onMessage.addListener((msg: Record<string, unknown>) => {
  if (msg.type === "FA_FILL_STARTED") {
    fillBtn.disabled = true;
    setComposerEnabled(false);
    setStatus("Working on the form in this tab…", "");
  } else if (msg.type === "FA_FILL_PROGRESS") {
    if (msg.status === "EXECUTED" && msg.verification === "SUCCESS") {
      filledCount += 1;
      setStatus(`Filling… ${filledCount} field(s) done.`, "");
    }
  } else if (msg.type === "FA_AGENT_MSG") {
    bubble(String(msg.text), "agent");
  } else if (msg.type === "FA_FILL_DONE") {
    fillBtn.disabled = false;
    const result = msg.result as Record<string, unknown>;
    if (result.type === "fill_error") {
      setStatus(`Error: ${String(result.error)}`, "err");
      bubble(`Something went wrong: ${String(result.error)}`, "agent");
      return;
    }
    // A chat turn carries a `reply` (already shown as an agent message) — don't
    // duplicate it with an outcome line. Only a full fill gets the outcome banner.
    const isChat = result.reply !== undefined || result.applied !== undefined;
    const outcome = result.outcome ? String(result.outcome) : "";
    const filled = (result.filled as string[]) ?? [];
    const questions =
      (result.questions as {
        field_id: string;
        kind: string;
        prompt: string;
        fact_keys?: string[];
        options?: string[] | null;
      }[]) ?? [];
    const issues =
      (result.validation_issues as { field_id: string; label?: string; detail: string }[]) ?? [];

    if (!isChat) {
      if (outcome === "COMPLETED") {
        setStatus(`Done — ${filled.length} fields filled. Nothing submitted.`, "ok");
        bubble(`Filled ${filled.length} field(s). Everything checks out — review and submit yourself.`, "agent");
      } else if (outcome === "NEEDS_USER") {
        const need = questions.length + issues.length;
        setStatus(`Filled ${filled.length}. ${need} item(s) need you. Nothing submitted.`, "warn");
        bubble(
          `Filled ${filled.length} field(s). ${need} item(s) need your input — answer below, or just tell me the value.`,
          "agent",
        );
      } else if (outcome) {
        setStatus(`Finished: ${outcome}. ${String(result.detail ?? "")}`, "warn");
        bubble(`Finished: ${outcome}. ${String(result.detail ?? "")}`, "agent");
      }
    } else {
      setStatus("Updated.", "");
    }

    // Situational report from the live form-state snapshot.
    const state = result.state as {
      text?: string;
      empty_required?: { field_id: string; label: string; credential?: boolean; options?: string[] | null }[];
    } | null;
    if (state?.text) bubble(`Form status — ${state.text}`, "agent");

    const asked = new Set(questions.map((q) => q.field_id));
    for (const q of questions) {
      renderQuestion(q);
    }
    // Empty required fields the fill did not already ask about (e.g. cascade-
    // revealed State/District) get an inline prompt so nothing is silently left.
    for (const f of state?.empty_required ?? []) {
      if (asked.has(f.field_id)) continue;
      if (f.credential) {
        bubble(`“${f.label}” has to be completed on the page itself (e.g. CAPTCHA).`, "sys");
        continue;
      }
      renderQuestion({
        field_id: f.field_id,
        kind: "empty_required",
        prompt: `“${f.label}” is empty — what should I put there?`,
        fact_keys: [],
        options: f.options ?? null,
      });
    }
    for (const iss of issues) {
      const div = document.createElement("div");
      div.className = "q err";
      div.innerHTML = "";
      const b = document.createElement("b");
      b.textContent = `⚠ ${iss.label || iss.field_id}`;
      const p = document.createElement("div");
      p.textContent = iss.detail;
      div.appendChild(b);
      div.appendChild(p);
      chatEl.appendChild(div);
    }
    chatEl.scrollTop = chatEl.scrollHeight;
    // Ready for the next conversational turn.
    setComposerEnabled(true);
    chatInput.focus();
  }
});

void chrome.runtime.sendMessage({ type: "FA_GET_STATE" }).then((s: SessionState) => {
  if (s?.error) setStatus(s.error, "warn");
});
