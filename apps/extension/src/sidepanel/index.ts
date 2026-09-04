// Side panel: the product surface. Enter a profile, click "Fill this form",
// watch it fill THIS tab, and see the questions it needs you to answer.
// Orchestration + mapping run in the local backend; this panel triggers it and
// shows progress. It never submits.
import type { SessionState } from "../shared/messages";

const factsEl = document.getElementById("facts") as HTMLTextAreaElement;
const fillBtn = document.getElementById("fill") as HTMLButtonElement;
const statusEl = document.getElementById("status")!;
const logEl = document.getElementById("log")!;
const questionsEl = document.getElementById("questions")!;

const BACKEND = "http://127.0.0.1:8000";

// A default demo profile so the panel is usable immediately.
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

function esc(s: string): string {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// Public value keys are non-sensitive geography; everything else is personal.
const PUBLIC_KEYS = new Set(["country", "state", "district", "locality", "pincode", "gender"]);

function parseFacts(text: string): { key: string; value: string; sensitivity: string }[] {
  const facts: { key: string; value: string; sensitivity: string }[] = [];
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
  logEl.innerHTML = "";
  questionsEl.innerHTML = "";
  filledCount = 0;
  fillBtn.disabled = true;
  setStatus("Attaching to this tab and starting…", "");
  void chrome.runtime.sendMessage({ type: "FA_FILL", facts, backendUrl: BACKEND });
});

// Live updates from the background as the fill proceeds.
chrome.runtime.onMessage.addListener((msg: Record<string, unknown>) => {
  if (msg.type === "FA_FILL_STARTED") {
    setStatus("Filling the form in this tab…", "");
  } else if (msg.type === "FA_FILL_PROGRESS") {
    if (msg.status === "EXECUTED" && msg.verification === "SUCCESS") {
      filledCount += 1;
      const d = document.createElement("div");
      d.textContent = `✓ filled and verified field ${filledCount}`;
      logEl.appendChild(d);
    }
  } else if (msg.type === "FA_FILL_DONE") {
    fillBtn.disabled = false;
    const result = msg.result as Record<string, unknown>;
    if (result.type === "fill_error") {
      setStatus(`Error: ${esc(String(result.error))}`, "err");
      return;
    }
    const outcome = String(result.outcome);
    const filled = (result.filled as string[]) ?? [];
    const questions = (result.questions as { field_id: string; prompt: string }[]) ?? [];
    if (outcome === "COMPLETED") {
      setStatus(`Done — ${filled.length} fields filled and verified. Nothing submitted; review and submit yourself.`, "ok");
    } else if (outcome === "NEEDS_USER") {
      setStatus(`Filled ${filled.length} fields. ${questions.length} item(s) need you (below). Nothing submitted.`, "warn");
    } else {
      setStatus(`Finished: ${outcome}. ${esc(String(result.detail ?? ""))}`, "warn");
    }
    for (const q of questions) {
      const div = document.createElement("div");
      div.className = "q";
      div.innerHTML = `<b>${esc(q.field_id)}</b>${esc(q.prompt)}`;
      questionsEl.appendChild(div);
    }
    const issues = (result.validation_issues as { field_id: string; label?: string; detail: string }[]) ?? [];
    for (const iss of issues) {
      const div = document.createElement("div");
      div.className = "q";
      div.style.background = "#fde2e1";
      div.style.borderColor = "#f0a8a4";
      div.innerHTML = `<b>⚠ ${esc(iss.label || iss.field_id)}</b>${esc(iss.detail)}`;
      questionsEl.appendChild(div);
    }
    if (outcome === "NEEDS_USER" && issues.length > 0) {
      setStatus(`Filled ${filled.length} fields. ${questions.length + issues.length} item(s) need you (incl. ${issues.length} validation issue(s)). Nothing submitted.`, "warn");
    }
  }
});

void chrome.runtime.sendMessage({ type: "FA_GET_STATE" }).then((s: SessionState) => {
  if (s?.error) setStatus(s.error, "warn");
});
