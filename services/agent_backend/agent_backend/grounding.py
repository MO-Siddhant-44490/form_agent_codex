"""Structural (a11y) grounding (plan.md insight #2, SeeAct: grounding is the
web-agent bottleneck). The HTML `autocomplete` attribute is a W3C-standard,
value-free signal that binds a field to its meaning far more reliably than a
scraped label — and deterministically, with no model call. This maps a field's
autocomplete token to a matching user fact.

Never grounds credential-ish tokens (invariant 2): those fields are never
filled, whatever a page claims.
"""

from form_contracts import DocumentFact, FormField

# Autocomplete tokens that name a credential/payment field — never grounded.
_CREDENTIAL_TOKENS = frozenset(
    {
        "current-password",
        "new-password",
        "one-time-code",
        "cc-number",
        "cc-csc",
        "cc-exp",
        "cc-exp-month",
        "cc-exp-year",
    }
)

# Prefix tokens that qualify a field group but not its meaning; stripped so the
# meaningful token is matched (e.g. "shipping street-address" -> "street-address").
_PREFIXES = frozenset(
    {"shipping", "billing", "home", "work", "mobile", "fax", "pager"}
)

# Normalized autocomplete token -> ordered candidate fact-key substrings (most
# specific first) and an optional value_type hint. A field is grounded to the
# first fact whose key contains a candidate substring, else the first fact of
# the hinted value_type.
_TOKEN_RULES: dict[str, tuple[tuple[str, ...], str | None]] = {
    "email": (("email", "mail"), "email"),
    "tel": (("mobile", "phone", "tel", "cell"), "phone"),
    "tel-national": (("mobile", "phone", "tel", "cell"), "phone"),
    "tel-local": (("mobile", "phone", "tel", "cell"), "phone"),
    "name": (("full_name", "full name", "fullname", "name"), None),
    "given-name": (("first_name", "first name", "given", "forename"), None),
    "additional-name": (("middle_name", "middle name", "middle"), None),
    "family-name": (("last_name", "last name", "family", "surname"), None),
    "street-address": (("street", "address"), None),
    "address-line1": (("address", "street", "line1"), None),
    "address-line2": (("address2", "sub_locality", "line2", "landmark"), None),
    "address-level2": (("city", "district", "town", "locality"), None),
    "address-level1": (("state", "province", "region"), None),
    "postal-code": (("pincode", "postal", "postcode", "zip", "pin"), None),
    "country": (("country",), None),
    "country-name": (("country",), None),
    "bday": (("dob", "birth", "date_of_birth"), "date"),
    "sex": (("gender", "sex"), None),
    "organization": (("organization", "organisation", "company", "employer", "org"), None),
}


def _normalize_token(raw: str | None) -> str | None:
    """The meaningful autocomplete token: lowercased, with `section-*` and
    group-qualifier prefixes stripped, taking the last remaining token."""
    if not raw:
        return None
    tokens = [t for t in raw.strip().lower().split() if not t.startswith("section-")]
    tokens = [t for t in tokens if t not in _PREFIXES]
    if not tokens:
        return None
    return tokens[-1]


def autocomplete_fact(
    field: FormField, facts_by_key: dict[str, DocumentFact]
) -> DocumentFact | None:
    """The user fact a field's autocomplete token grounds to, or None. Value-free
    and deterministic; a credential token yields None (never filled)."""
    token = _normalize_token(field.target.autocomplete)
    if token is None or token in _CREDENTIAL_TOKENS:
        return None
    rule = _TOKEN_RULES.get(token)
    if rule is None:
        return None
    substrings, value_type = rule

    for substring in substrings:
        for fact in facts_by_key.values():
            if substring in fact.key.strip().lower():
                return fact
    if value_type is not None:
        for fact in facts_by_key.values():
            if value_type in str(fact.value_type).lower():
                return fact
    return None
