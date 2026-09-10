// Page-level observation: classification signals, dialogs, frames,
// fingerprinting (plan.md §8.2). Read-only; never mutates the page.
import type { FormField, PageObservation } from "@form-agent/contracts";
import { detectDialogs } from "./dialogs";
import { discoverFields, discoverFieldsWithReport } from "./fields";
import { summarizeNavigation } from "./navigation";

const CAPTCHA_SELECTORS = [
  ".g-recaptcha",
  ".h-captcha",
  ".cf-turnstile",
  'iframe[src*="recaptcha"]',
  'iframe[src*="hcaptcha"]',
  'iframe[src*="turnstile"]',
].join(", ");

/** A login / MFA gate: a visible password-type CREDENTIAL field. A masked
 * identifier (Aadhaar as a password input) is not a login. */
export function detectLogin(doc: Document, fields: FormField[] = discoverFields(doc)): boolean {
  return fields.some((f) => f.purpose === "credential" && f.input_type === "password");
}

/** A captcha widget, or a captcha text box among the fields. */
export function detectCaptcha(doc: Document, fields: FormField[] = discoverFields(doc)): boolean {
  return doc.querySelector(CAPTCHA_SELECTORS) !== null || fields.some((f) => f.purpose === "captcha");
}

function detectFrames(doc: Document): PageObservation["iframes"] {
  const frames: NonNullable<PageObservation["iframes"]> = [];
  doc.querySelectorAll("iframe").forEach((frame, i) => {
    let origin: string;
    try {
      origin = new URL(frame.src, doc.baseURI).origin;
    } catch {
      return;
    }
    if (origin === "null" || !/^https?:/.test(origin)) return;
    let accessible = false;
    try {
      accessible = frame.contentDocument !== null;
    } catch {
      accessible = false;
    }
    frames.push({ frame_id: frame.id || `frame-${i}`, origin, accessible });
  });
  return frames;
}

async function sha256Hex(text: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle) {
    const digest = await subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  }
  // Environments without WebCrypto (some jsdom configs): stable djb2 fallback.
  let hash = 5381;
  for (let i = 0; i < text.length; i++) hash = ((hash << 5) + hash + text.charCodeAt(i)) >>> 0;
  return `djb2-${hash.toString(16)}`;
}

export type ObservationContext = {
  runId: string;
  tabId: number;
  observationSeq: number;
  domStable: boolean;
};

export async function buildObservation(
  doc: Document,
  ctx: ObservationContext,
): Promise<PageObservation> {
  const win = doc.defaultView;
  if (!win) throw new Error("document has no window");
  const { fields, unrecognized } = discoverFieldsWithReport(doc);
  const loginDetected = detectLogin(doc, fields);
  const captchaDetected = detectCaptcha(doc, fields);

  // Structural fingerprint: url + title + field structure (not values).
  const structure = fields.map((f) => `${f.field_id}|${f.input_type}|${f.required}`);
  const fingerprint = await sha256Hex(
    JSON.stringify([win.location.href, doc.title, structure]),
  );

  return {
    run_id: ctx.runId,
    tab_id: ctx.tabId,
    frame_id: "main",
    url: win.location.href,
    origin: win.location.origin,
    title: doc.title || null,
    page_fingerprint: `sha256:${fingerprint}`,
    observation_seq: ctx.observationSeq,
    observed_at: new Date().toISOString(),
    classification: [
      loginDetected
        ? { label: "login" as const, confidence: 0.9 }
        : { label: fields.length > 0 ? ("form" as const) : ("other" as const), confidence: 0.8 },
    ],
    fields,
    dialogs: detectDialogs(doc),
    iframes: detectFrames(doc),
    ...summarizeNavigation(doc),
    login_detected: loginDetected,
    captcha_detected: captchaDetected,
    dom_stable: ctx.domStable,
    unrecognized_controls: unrecognized,
  };
}
