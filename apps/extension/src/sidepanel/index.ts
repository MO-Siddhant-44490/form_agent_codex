// Minimal side panel: explicit attach gesture + observation display.
// (Grows into the React clarification/approval UI in later slices.)
import type { PanelCommand, SessionState } from "../shared/messages";

const statusEl = document.getElementById("status")!;
const fieldsEl = document.getElementById("fields")!;

function esc(s: string): string {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function render(state: SessionState): void {
  if (state.error) {
    statusEl.innerHTML = `<span class="error">${esc(state.error)}</span>`;
  } else if (!state.attached) {
    statusEl.textContent = "Not attached.";
  } else {
    const obs = state.lastObservation;
    statusEl.textContent = obs
      ? `Attached to ${state.origin} — ${obs.fields?.length ?? 0} fields ` +
        `(seq ${obs.observation_seq}${obs.login_detected ? ", LOGIN" : ""}` +
        `${obs.captcha_detected ? ", CAPTCHA" : ""})`
      : `Attached to ${state.origin} — no observation yet.`;
  }
  const obs = state.lastObservation;
  fieldsEl.innerHTML = obs?.fields?.length
    ? "<table><tr><th>Field</th><th>Type</th><th>Label</th><th>Req</th><th>Value</th></tr>" +
      obs.fields
        .map(
          (f) =>
            `<tr><td>${esc(f.field_id)}</td><td>${esc(f.input_type)}</td>` +
            `<td>${esc(f.label ?? "")}</td><td>${f.required ? "✓" : ""}</td>` +
            `<td>${f.value_redacted ? "[redacted]" : esc(f.current_value ?? "")}</td></tr>`,
        )
        .join("") +
      "</table>"
    : "";
}

async function send(command: PanelCommand): Promise<void> {
  render((await chrome.runtime.sendMessage(command)) as SessionState);
}

document.getElementById("attach")!.addEventListener("click", () => {
  void send({ type: "FA_ATTACH_ACTIVE_TAB" }).then(() => send({ type: "FA_OBSERVE_NOW" }));
});
document.getElementById("observe")!.addEventListener("click", () => {
  void send({ type: "FA_OBSERVE_NOW" });
});
void send({ type: "FA_GET_STATE" });
