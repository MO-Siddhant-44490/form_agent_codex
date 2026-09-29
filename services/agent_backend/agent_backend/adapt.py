"""Value adaptation: fit a profile value to what THIS field accepts, the way a
person filling the form would — before typing, not after a rejection.

A profile holds one canonical value per fact ("+91 99401 26718",
"1988-09-23", a long address). Fields differ: a 10-digit mobile box next to a
country-code picker, a "DD/MM/YYYY" date, a 50-character address line. This
module reads the field's own constraints (maxlength, pattern, input type,
inputmode, and format hints in its placeholder/label) and produces the value
to type, plus alternative formats to try if the site still rejects it.

Deterministic and conservative: it only RESHAPES the value it was given
(digits, separators, date order, abbreviations, trimming at a word boundary).
It never invents content; anything needing judgement goes to the model-backed
repair step in the driver, and after that to the user.
"""

import re
from datetime import date, datetime

from form_contracts import FormField, PageObservation

_TEXTUAL = frozenset({"text", "tel", "number", "email", "search", "url", "textarea", "date"})

_PHONE_WORDS = re.compile(
    r"\b(phone|mobile|whatsapp|contact (no|number)|cell|telephone|tel)\b", re.I
)
_PHONE_KEYS = re.compile(r"(phone|mobile|whatsapp|contact_number|tel)", re.I)
_DIGITS_HINT = re.compile(r"(\d{1,2})[\s-]*digit", re.I)
_PATTERN_DIGITS = re.compile(r"^\^?(?:\\d|\[0-9\])\{(\d{1,2})\}\$?$")
_CC_PICKER_LABEL = re.compile(r"\+\d{1,3}\b")


# -- field classification ----------------------------------------------------


def _hint_text(field: FormField) -> str:
    t = field.target
    return " ".join(
        x
        for x in (field.label, field.accessible_name, t.placeholder, t.name_attr, field.nearby_text)
        if x
    )


def is_phone_field(field: FormField, fact_key: str | None = None) -> bool:
    if field.input_type == "tel" or (field.input_mode or "").lower() == "tel":
        return True
    if field.target.autocomplete and field.target.autocomplete.startswith("tel"):
        return True
    if _PHONE_WORDS.search(_hint_text(field)):
        return True
    return bool(fact_key and _PHONE_KEYS.search(fact_key))


def expected_digits(field: FormField) -> int | None:
    """How many digits the field wants, when it says so: a \\d{10} pattern,
    a "10-digit" hint, or a small maxlength on a numeric/phone box."""
    if field.pattern:
        m = _PATTERN_DIGITS.match(field.pattern.strip())
        if m:
            return int(m.group(1))
    m = _DIGITS_HINT.search(_hint_text(field) + " " + (field.validation_message or ""))
    if m:
        return int(m.group(1))
    if field.max_length and 6 <= field.max_length <= 12:
        return field.max_length
    return None


def has_country_code_picker(observation: PageObservation | None) -> bool:
    """A separate dial-code selector ("India +91") on the page means number
    fields want the national number only."""
    if observation is None:
        return False
    for f in observation.fields:
        if f.input_type not in ("combobox", "select-one"):
            continue
        texts = [
            f.label or "",
            f.current_value or "",
            *(f.option_labels or [])[:5],
            *(f.options or [])[:5],
        ]
        if any(_CC_PICKER_LABEL.search(t) for t in texts):
            return True
    return False


# -- phone ---------------------------------------------------------------------


def _national(digits: str, want: int | None) -> str:
    """Drop a country code / trunk zero to reach the national number."""
    if want and len(digits) > want:
        return digits[-want:]
    if len(digits) == 12 and digits.startswith("91"):  # India, the common case
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


def phone_candidates(field: FormField, value: str, cc_picker: bool) -> list[str]:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return [value]
    want = expected_digits(field)
    national = _national(digits, want)
    plus = f"+{digits}" if value.strip().startswith("+") else digits
    numeric_only = field.input_type == "number" or (field.input_mode or "") == "numeric"
    ordered: list[str]
    if want or cc_picker or numeric_only:
        ordered = [national, digits, plus, value]
    else:
        # No constraint either way: keep the person's own formatting first,
        # but have the national form ready if the site rejects it.
        ordered = [value, national, digits, plus]
    return _unique(ordered)


# -- dates -----------------------------------------------------------------------

_DATE_FORMATS = {
    "yyyy-mm-dd": "%Y-%m-%d",
    "dd/mm/yyyy": "%d/%m/%Y",
    "mm/dd/yyyy": "%m/%d/%Y",
    "dd-mm-yyyy": "%d-%m-%Y",
    "dd.mm.yyyy": "%d.%m.%Y",
    "yyyy/mm/dd": "%Y/%m/%d",
}
_PARSE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d %B %Y",
    "%d %b %Y",
)


def _parse_date(value: str) -> date | None:
    v = value.strip()
    m = re.search(r"\d{4}-\d{2}-\d{2}", v)  # "23 September 1988 (1988-09-23)"
    if m:
        v = m.group(0)
    for fmt in _PARSE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def date_candidates(field: FormField, value: str) -> list[str]:
    d = _parse_date(value)
    if d is None:
        return [value]
    if field.input_type == "date":
        return [d.isoformat()]
    hint = _hint_text(field).lower()
    wanted = [fmt for key, fmt in _DATE_FORMATS.items() if key in hint]
    rest = [fmt for fmt in _DATE_FORMATS.values() if fmt not in wanted]
    return _unique([d.strftime(f) for f in wanted] + [d.strftime(f) for f in rest] + [value])


def _looks_like_date_field(field: FormField, fact_key: str | None) -> bool:
    if field.input_type == "date":
        return True
    text = _hint_text(field).lower()
    return (
        "date" in text
        or "dob" in text
        or any(k in text for k in _DATE_FORMATS)
        or bool(fact_key and "date" in fact_key)
    )


# -- length ------------------------------------------------------------------------

_ABBREVIATIONS = [
    (r"\bFirst\b", "1st"),
    (r"\bSecond\b", "2nd"),
    (r"\bThird\b", "3rd"),
    (r"\bFourth\b", "4th"),
    (r"\bStreet\b", "St"),
    (r"\bRoad\b", "Rd"),
    (r"\bAvenue\b", "Ave"),
    (r"\bLane\b", "Ln"),
    (r"\bCross\b", "Cr"),
    (r"\bMain\b", "Mn"),
    (r"\bApartments?\b", "Apt"),
    (r"\bBuilding\b", "Bldg"),
    (r"\bFloor\b", "Fl"),
    (r"\bNear\b", "Nr"),
    (r"\bOpposite\b", "Opp"),
    (r"\bSector\b", "Sec"),
    (r"\bNumber\b", "No"),
    (r"\bBlock\b", "Blk"),
    (r"\bPhase\b", "Ph"),
]


def abbreviate(value: str) -> str:
    out = value
    for pattern, short in _ABBREVIATIONS:
        out = re.sub(pattern, short, out, flags=re.I)
    return re.sub(r"\s{2,}", " ", out).strip()


def clamp(value: str, n: int) -> str:
    """At most n characters, cut at a word/comma boundary when one is close."""
    if n <= 0 or len(value) <= n:
        return value
    cut = value[:n]
    boundary = max(cut.rfind(" "), cut.rfind(","))
    if boundary >= int(n * 0.6):
        cut = cut[:boundary]
    return cut.rstrip(" ,")


def length_candidates(field: FormField, value: str) -> list[str]:
    n = field.max_length
    if not n or len(value) <= n:
        return [value]
    short = abbreviate(value)
    return _unique([clamp(short, n), clamp(value, n)])


# -- the public API ------------------------------------------------------------------


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def conforms(field: FormField, value: str) -> bool:
    """Does the value satisfy every constraint the field declares?"""
    if field.max_length and len(value) > field.max_length:
        return False
    if field.input_type == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", value):
        return False
    if field.pattern:
        try:
            if not re.fullmatch(field.pattern, value):
                return False
        except re.error:
            pass  # a JS-only regex we cannot evaluate: don't block on it
    return True


def candidates(
    field: FormField,
    value: str | None,
    fact_key: str | None = None,
    observation: PageObservation | None = None,
) -> list[str]:
    """Values to try for this field, best first. The first is what to type;
    the rest are fallbacks if the site rejects it. Non-text controls (selects,
    radios, checkboxes) are returned unchanged — option matching handles them."""
    if value is None or field.input_type not in _TEXTUAL or field.value_redacted:
        return [value] if value is not None else []
    if is_phone_field(field, fact_key):
        base = phone_candidates(field, value, has_country_code_picker(observation))
    elif _looks_like_date_field(field, fact_key) and _parse_date(value) is not None:
        base = date_candidates(field, value)
    elif field.input_type == "email":
        base = _unique([value.strip(), value.strip().lower()])
    else:
        base = [value]
    # Every candidate must also fit the length cap.
    fitted: list[str] = []
    for c in base:
        fitted.extend(length_candidates(field, c))
    ordered = _unique(fitted)
    # Prefer candidates that satisfy every declared constraint, keeping order.
    return [c for c in ordered if conforms(field, c)] + [
        c for c in ordered if not conforms(field, c)
    ]


def adapt(
    field: FormField,
    value: str | None,
    fact_key: str | None = None,
    observation: PageObservation | None = None,
) -> str | None:
    """The value to type into this field."""
    options = candidates(field, value, fact_key, observation)
    return options[0] if options else value


# -- provenance for repaired values ---------------------------------------------

_ORDINALS = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th"}


def plausible_reshape(original: str, new: str) -> bool:
    """Is `new` the SAME information as `original`, only reshaped (digits
    re-grouped, words abbreviated or dropped, re-ordered date parts)? Used to
    accept a model-proposed repair without letting new content in (invariant 11).
    """
    if not new or len(new) > len(original) + 6:
        return False
    od, nd = re.sub(r"\D", "", original), re.sub(r"\D", "", new)
    if nd and nd == od:
        return True  # same digits, different grouping
    if nd and len(nd) >= 6 and od.endswith(nd):
        return True  # national number of the same phone
    otoks = re.findall(r"[a-z0-9]+", original.lower())
    ntoks = re.findall(r"[a-z0-9]+", new.lower())
    if not ntoks:
        return False
    allowed = set(otoks) | {_ORDINALS[t] for t in otoks if t in _ORDINALS}

    def derived(tok: str) -> bool:
        if tok in allowed:
            return True
        # an abbreviation: a prefix, or the letters of a word in order (St, Rd, Bldg)
        return any(w.startswith(tok) or _subsequence(tok, w) for w in otoks if len(tok) >= 2)

    return sum(derived(t) for t in ntoks) >= 0.8 * len(ntoks)


def _subsequence(short: str, word: str) -> bool:
    it = iter(word)
    return short[0] == word[0] and all(ch in it for ch in short)


def widget_shows(field: FormField, value: str | None) -> bool:
    """Custom pickers (intl-tel-input, some design systems) expose no value —
    only a label showing the selection ("India (भारत): +91"). True when that
    label already names `value`, i.e. the widget is already set."""
    if field.input_type != "combobox" or not value or field.current_value:
        return False

    def squash(text: str) -> str:  # letters and digits only
        return "".join(ch for ch in text.lower() if ch.isalnum())

    shown, want = squash(field.label or field.accessible_name or ""), squash(value)
    return len(want) >= 2 and want in shown
