"""Field PURPOSE classification — the backend's mirror of the extension's
`perception/purpose.ts` (keep the rules in sync; both have the same test cases).

The extension classifies at perception time; the backend re-derives here for
defence in depth at the policy gate (an observation cannot talk the gate into
filling a password box by labelling it "standard") and to keep the fake
transport faithful in tests.

    credential -> never read, never written (the human types it)
    captcha    -> never written (human-only challenge)
    consent    -> only on the user's explicit say-so
    standard   -> fillable, including masked identifiers rendered as a
                  password-type input (Aadhaar/PAN/account no.)
"""

import re

from form_contracts import FieldPurpose, FormField

CREDENTIAL_AUTOCOMPLETE = frozenset(
    {"current-password", "new-password", "one-time-code", "cc-number", "cc-csc"}
)

# A secret the user must type themselves. "pin" alone is a secret; "PIN code" /
# "pincode" is an Indian postal code and is NOT matched.
SECRET_RE = re.compile(
    r"\b(password|passwd|pwd|pass ?code|pass ?phrase|m-?pin|otp"
    r"|one[- ]?time[- ]?(password|passcode|code)|verification code"
    r"|auth(entication)? code|security (code|answer)|cvv|cvc|csc)\b"
    r"|\bpin\b(?!\s*-?\s*code)",
    re.I,
)

# Identifiers sites often MASK for privacy but which are profile data.
IDENTIFIER_RE = re.compile(
    r"\b(aadha?ar|uidai|uid|vid|virtual id|pan|ssn|social security|passport"
    r"|account (no|number|#)|acct|iban|ifsc|identity|id (number|no)|national id"
    r"|nid|tin|nino|voter|epic|abha|uan|gstin|cin|din|licen[cs]e)\b",
    re.I,
)

CAPTCHA_RE = re.compile(
    r"captcha|are you human|image code"
    r"|(enter|type) the (code|text|characters|letters)"
    r"( shown| above| in the image| from the image)?",
    re.I,
)

CONSENT_RE = re.compile(
    r"^\s*(i|we)\s+(hereby\s+)?(consent|agree|accept|confirm|declare|acknowledge|certify"
    r"|authori[sz]e|undertake|understand|have read|am aware)\b"
    r"|\b(terms (and|&) conditions|terms of (use|service)|privacy policy|declaration)\b",
    re.I,
)


def classify_purpose(
    input_type: str, texts: list[str | None], autocomplete: str | None = None
) -> FieldPurpose:
    ac = (autocomplete or "").strip().lower()
    if ac in CREDENTIAL_AUTOCOMPLETE:
        return FieldPurpose.CREDENTIAL
    text = " | ".join(t for t in texts if t)
    if CAPTCHA_RE.search(text):
        return FieldPurpose.CAPTCHA
    if input_type == "password":
        if IDENTIFIER_RE.search(text) and not SECRET_RE.search(text):
            return FieldPurpose.STANDARD
        return FieldPurpose.CREDENTIAL
    if input_type in ("checkbox", "radio"):
        return FieldPurpose.CONSENT if CONSENT_RE.search(text) else FieldPurpose.STANDARD
    if SECRET_RE.search(text):
        return FieldPurpose.CREDENTIAL
    return FieldPurpose.STANDARD


def derive_purpose(field: FormField) -> FieldPurpose:
    """Re-derive a field's purpose from its own observed text."""
    t = field.target
    return classify_purpose(
        field.input_type,
        [
            field.label,
            field.accessible_name,
            t.placeholder,
            t.name_attr,
            field.field_id,
            field.nearby_text,
        ],
        t.autocomplete,
    )


_STRICTNESS = {
    FieldPurpose.CREDENTIAL: 3,
    FieldPurpose.CAPTCHA: 2,
    FieldPurpose.CONSENT: 1,
    FieldPurpose.STANDARD: 0,
}


def effective_purpose(field: FormField) -> FieldPurpose:
    """The stricter of the observed and the re-derived purpose: perception may
    know more (a widget, a group legend), but it can never relax the rule."""
    observed, derived = field.purpose, derive_purpose(field)
    return observed if _STRICTNESS[observed] >= _STRICTNESS[derived] else derived
