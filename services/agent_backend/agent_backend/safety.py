"""Shared safety predicates (invariant 2: credential material is never stored
as a fact, set from chat, or extracted from a document)."""

# Matched as substrings, chosen so real keys like "pincode" are unaffected.
_CREDENTIAL_HINTS = ("password", "otp", "captcha", "cvv")
_CREDENTIAL_KEYS = frozenset({"cc", "card", "card_number", "cc_number", "cvc"})


def is_credential_key(key: str) -> bool:
    """True for a fact key that names a password, OTP, CAPTCHA, CVV, or card."""
    k = key.strip().lower()
    return k in _CREDENTIAL_KEYS or any(hint in k for hint in _CREDENTIAL_HINTS)
