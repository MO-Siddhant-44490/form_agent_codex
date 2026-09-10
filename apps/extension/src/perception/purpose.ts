// Field PURPOSE classification: what a control is for, judged from its own
// text (label, name, id, placeholder, aria, autocomplete) and input type — no
// site-specific knowledge. This decides which rule the backend applies:
//   credential -> never read, never written (the human types it)
//   captcha    -> never written (human-only challenge)
//   consent    -> only on the user's explicit say-so
//   standard   -> fillable, including masked identifiers rendered as a
//                 password-type input (Aadhaar/PAN/account no.), whose value
//                 stays redacted but which are not login secrets.
import type { FormField } from "@form-agent/contracts";

export type FieldPurpose = FormField["purpose"];

// autocomplete tokens that name a secret outright.
const CREDENTIAL_AUTOCOMPLETE = new Set([
  "current-password",
  "new-password",
  "one-time-code",
  "cc-number",
  "cc-csc",
]);

// A secret the user must type themselves. "pin" alone is a secret; "PIN code"
// / "pincode" is an Indian postal code and is NOT matched.
const SECRET_RE =
  /\b(password|passwd|pwd|pass ?code|pass ?phrase|m-?pin|otp|one[- ]?time[- ]?(password|passcode|code)|verification code|auth(entication)? code|security (code|answer)|cvv|cvc|csc)\b|\bpin\b(?!\s*-?\s*code)/i;

// Identifiers that sites often MASK for privacy but which are profile data,
// not secrets: national / tax / financial IDs.
const IDENTIFIER_RE =
  /\b(aadha?ar|uidai|uid|vid|virtual id|pan|ssn|social security|passport|account (no|number|#)|acct|iban|ifsc|identity|id (number|no)|national id|nid|tin|nino|voter|epic|abha|uan|gstin|cin|din|licen[cs]e)\b/i;

const CAPTCHA_RE =
  /captcha|are you human|image code|(enter|type) the (code|text|characters|letters)( shown| above| in the image| from the image)?/i;

// Declarations and consents: a first-person statement, or a terms/privacy
// acknowledgement, on a checkbox/radio.
const CONSENT_RE =
  /^\s*(i|we)\s+(hereby\s+)?(consent|agree|accept|confirm|declare|acknowledge|certify|authori[sz]e|undertake|understand|have read|am aware)\b|\b(terms (and|&) conditions|terms of (use|service)|privacy policy|declaration)\b/i;

export type PurposeSignals = {
  inputType: string;
  autocomplete?: string | null;
  /** Any text that names the control: label, accessible name, placeholder,
   * name/id attributes, group legend. */
  texts: (string | null | undefined)[];
};

export function classifyPurpose(signals: PurposeSignals): FieldPurpose {
  const autocomplete = signals.autocomplete?.trim().toLowerCase();
  if (autocomplete && CREDENTIAL_AUTOCOMPLETE.has(autocomplete)) return "credential";

  const text = signals.texts.filter((t): t is string => !!t).join(" | ");
  const type = signals.inputType;

  if (CAPTCHA_RE.test(text)) return "captcha";

  if (type === "password") {
    // Masked identifier (Aadhaar, PAN, account no.): fillable profile data.
    if (IDENTIFIER_RE.test(text) && !SECRET_RE.test(text)) return "standard";
    return "credential";
  }
  if ((type === "checkbox" || type === "radio") && CONSENT_RE.test(text)) return "consent";
  // An OTP / PIN / password box rendered as a plain text or number input.
  if (type !== "checkbox" && type !== "radio" && SECRET_RE.test(text)) return "credential";
  return "standard";
}
